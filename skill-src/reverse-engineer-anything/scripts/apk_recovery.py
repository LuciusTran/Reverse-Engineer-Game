#!/usr/bin/env python3
"""Acquire APK evidence and run supplied Unity recovery tools in a new workspace.

This helper exports assets and metadata, never invents gameplay implementations.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile


class RecoveryError(Exception):
    """An actionable input, evidence, or tool failure."""


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)


def validate_state(state):
    if not isinstance(state, dict) or type(state.get('schema_version')) is not int or state['schema_version'] != 1:
        raise RecoveryError('Unsupported recovery-state schema; preserve it and select a new workspace')
    for name in ('intake', 'AssetExport', 'MetadataExport'):
        if name not in state:
            continue
        section = state[name]
        if not isinstance(section, dict) or not isinstance(section.get('files'), list) or not section['files']:
            raise RecoveryError('Malformed checkpoint section: ' + name)
        for item in section['files']:
            if not isinstance(item, dict):
                raise RecoveryError('Malformed checkpoint file record: ' + name)
            path, size, sha = item.get('path'), item.get('bytes'), item.get('sha256')
            if (not isinstance(path, str) or not path or PurePosixPath(path).is_absolute() or
                    any(part in ('', '.', '..') for part in path.split('/')) or '\\' in path or
                    type(size) is not int or size < 0 or not isinstance(sha, str) or
                    re.fullmatch('[0-9a-f]{64}', sha) is None):
                raise RecoveryError('Malformed checkpoint path, size or digest: ' + name)
        if name == 'intake':
            if not isinstance(section.get('inputs'), list) or not isinstance(section.get('observed'), dict) or not isinstance(section.get('conflicts'), list) or not isinstance(section.get('merged_sources'), list):
                raise RecoveryError('Malformed intake checkpoint metadata')
        elif not isinstance(section.get('identity'), dict):
            raise RecoveryError('Malformed tool identity: ' + name)


def file_record(path, root):
    return {'path': str(path.relative_to(root)), 'bytes': path.stat().st_size,
            'sha256': digest(path)}


def tree_records(folder, root):
    records = []
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise RecoveryError('Unexpected output symlink: ' + str(path))
        if path.is_file():
            records.append(file_record(path, root))
    return records


def verify_files(records, root):
    for record in records:
        path = root / record['path']
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record['bytes'] or digest(path) != record['sha256']:
            raise RecoveryError('Changed or missing checkpoint file: ' + str(path) + '; use a new workspace')


def extract_archive(archive, destination, root, budget):
    records, seen = [], set()
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        budget['members'] += len(members)
        budget['bytes'] += sum(member.file_size for member in members)
        if budget['members'] > budget['max_members'] or budget['bytes'] > budget['max_bytes']:
            raise RecoveryError('Archive expansion budget exceeded; select larger explicit --max-members/--max-bytes')
        for member in members:
            name = member.filename
            parts = PurePosixPath(name).parts
            mode = member.external_attr >> 16
            if (not parts or name.startswith('/') or '\\' in name or '\x00' in name or
                    any(part in ('.', '..') or ':' in part for part in name.rstrip('/').split('/')) or
                    stat.S_ISLNK(mode) or member.flag_bits & 1):
                raise RecoveryError('Unsupported archive member (path, symlink, or encryption): ' + repr(name))
            key = name.rstrip('/').casefold()
            if key in seen:
                raise RecoveryError('Duplicate or case-colliding archive member: ' + name)
            seen.add(key)
            target = destination.joinpath(*parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise RecoveryError('Non-regular archive member: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if target.stat().st_size != member.file_size:
                raise RecoveryError('ZIP member size mismatch: ' + name)
            records.append({**file_record(target, root), 'archive_member': name,
                            'archive': str(archive.relative_to(root))})
    return records


def acquire(args, root, state):
    sources = [path.expanduser().resolve(strict=True) for path in args.input]
    if any(not path.is_file() or path.suffix.lower() not in ('.apk', '.xapk', '.apks', '.zip') for path in sources):
        raise RecoveryError('Inputs must be APK, XAPK, APKS or ZIP files; pass each supplied split with --input')
    identity = [{'source': str(path), 'bytes': path.stat().st_size, 'sha256': digest(path)} for path in sources]
    state['selected_inputs'] = [str(path) for path in args.input]
    if 'intake' in state:
        if state['intake']['inputs'] != identity:
            raise RecoveryError('Input identity changed; use a new workspace for another build')
        verify_files(state['intake']['files'], root)
        expected = {item['path'] for item in state['intake']['files']}
        actual = {item['path'] for name in ('Inputs', 'Packages', 'Merged') for item in tree_records(root / name, root)}
        if expected != actual:
            raise RecoveryError('Intake file inventory changed; use a new workspace')
        print('Reused verified intake checkpoint', flush=True)
        return state['intake']
    for name in ('Inputs', 'Packages', 'Merged'):
        if (root / name).exists():
            raise RecoveryError('Incomplete intake at ' + str(root / name) + '; retain it for diagnosis and use a new workspace')
        (root / name).mkdir()
    records, inventory = [], []
    budget = {'members': 0, 'bytes': 0, 'max_members': args.max_members, 'max_bytes': args.max_bytes}
    for index, (source, info) in enumerate(zip(sources, identity)):
        copied = root / 'Inputs' / (str(index) + '-' + source.name)
        shutil.copyfile(source, copied)
        if digest(copied) != info['sha256']:
            raise RecoveryError('Input changed during acquisition: ' + str(source))
        records.append(file_record(copied, root))
        outer = extract_archive(copied, root / 'Packages' / str(index), root, budget)
        inventory.extend(outer)
        # Split containers retain both the original container and each extracted APK.
        if source.suffix.lower() != '.apk':
            for nested_index, item in enumerate(outer):
                if item['archive_member'].lower().endswith('.apk'):
                    nested = root / item['path']
                    inventory.extend(extract_archive(nested, root / 'Packages' / f'{index}-split-{nested_index}', root, budget))
    records.extend({key: item[key] for key in ('path', 'bytes', 'sha256')} for item in inventory)
    merged, conflicts = {}, []
    for item in inventory:
        name = item['archive_member']
        if not (name.startswith('assets/bin/Data/') or name.startswith('lib/')):
            continue
        key = name.casefold()
        if key in merged:
            if merged[key]['sha256'] != item['sha256'] or merged[key]['name'] != name:
                conflicts.append({'member': name, 'first': merged[key]['sources'][0], 'other': item['path']})
            else:
                merged[key]['sources'].append(item['path'])
            continue
        destination = root / 'Merged' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / item['path'], destination)
        merged[key] = {**file_record(destination, root), 'name': name, 'sources': [item['path']]}
    records.extend({key: item[key] for key in ('path', 'bytes', 'sha256')} for item in merged.values())
    libraries = [item['path'] for item in merged.values() if item['name'].endswith('/libil2cpp.so')]
    metadata = [item['path'] for item in merged.values() if item['name'].endswith('/global-metadata.dat')]
    assemblies = [item['path'] for item in merged.values() if item['name'].endswith('/Assembly-CSharp.dll')]
    versions = set()
    for item in merged.values():
        if PurePosixPath(item['name']).name in ('globalgamemanagers', 'data.unity3d'):
            with (root / item['path']).open('rb') as stream:
                versions.update(match.decode('ascii') for match in re.findall(rb'\b\d{4}\.\d+\.\d+[abfp]\d+\b', stream.read(65536)))
    result = {'inputs': identity, 'files': records, 'inventory': inventory,
              'merged_sources': list(merged.values()), 'conflicts': conflicts,
              'observed': {'il2cpp_libraries': libraries, 'metadata': metadata,
                           'managed_assembly_candidates': assemblies, 'unity_version_candidates': sorted(versions)},
              'inference': 'il2cpp' if libraries and metadata else 'managed-candidate' if assemblies else 'unknown',
              'limits': {'max_members': args.max_members, 'max_bytes': args.max_bytes, 'counted_members': budget['members'], 'expanded_bytes': budget['bytes']},
              'unknowns': ['CIL bodies are not validated by filenames.', 'OBB, encrypted and remote content are not unpacked by this helper.',
                           'Asset coverage, recovered implementations and playability require further analysis.']}
    state['intake'] = result
    write_json(root / 'recovery-state.json', state)
    return result


def run_capture(argv, log, timeout):
    with log.open('wb') as output:
        process = subprocess.Popen(argv, cwd=log.parent, stdout=output, stderr=subprocess.STDOUT)
        try:
            status = process.wait(timeout=timeout)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if status:
        raise RecoveryError(f'Tool exited {status}; inspect {log}')


def tool_path(path):
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RecoveryError('Tool must be an existing executable: ' + str(resolved))
    return resolved


def stage(root, state, name, tool, parameters, execute):
    identity = {'tool': str(tool), 'tool_sha256': digest(tool), 'parameters': parameters}
    previous = state.get(name)
    if previous:
        if previous['identity'] != identity:
            raise RecoveryError('Tool or stage parameters changed for ' + name + '; use a new workspace')
        verify_files(previous['files'], root)
        if tree_records(root / name, root) != previous['files']:
            raise RecoveryError('Output file inventory changed for ' + name + '; use a new workspace')
        print('Reused verified ' + name + ' checkpoint', flush=True)
        return
    destination = root / name
    if destination.exists():
        raise RecoveryError('Incomplete stage retained at ' + str(destination) + '; use a new workspace')
    destination.mkdir()
    print('Running ' + name, flush=True)
    execute(destination)
    files = tree_records(destination, root)
    if not files:
        raise RecoveryError('Tool produced no files: ' + str(destination))
    state[name] = {'identity': identity, 'files': files,
                   'scope': 'asset export' if name == 'AssetExport' else 'metadata declarations; executable bodies unverified'}
    write_json(root / 'recovery-state.json', state)


def export_assets(tool, source, destination, root, timeout):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'
    log = root / 'Logs' / 'assetripper.log'
    argv = [str(tool), '--headless', '--port', str(port), '--log-path', str(root / 'Logs' / 'assetripper-internal.log')]
    write_json(root / 'Logs' / 'assetripper-command.json', argv)
    with log.open('wb') as console:
        process = subprocess.Popen(argv, cwd=tool.parent, stdout=console, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + min(timeout, 90)
            while True:
                if process.poll() is not None:
                    raise RecoveryError('AssetRipper exited during startup; inspect ' + str(log))
                try:
                    with urllib.request.urlopen(endpoint + '/openapi.json', timeout=1) as response:
                        schema = json.load(response)
                    required = ('/LoadFolder', '/Export/UnityProject')
                    if (not isinstance(schema, dict) or not isinstance(schema.get('paths'), dict) or
                            any(not isinstance(schema['paths'].get(path), dict) or 'post' not in schema['paths'][path] for path in required)):
                        raise RecoveryError('Installed AssetRipper API differs; inspect OpenAPI before adapting the driver')
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise RecoveryError('AssetRipper startup timeout; inspect ' + str(log))
                    time.sleep(.25)
            write_json(root / 'Logs' / 'assetripper-openapi.json', schema)
            for operation, path in [('/LoadFolder', source), ('/Export/UnityProject', destination)]:
                request = urllib.request.Request(endpoint + operation, data=urllib.parse.urlencode({'path': str(path)}).encode(), method='POST')
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    response.read()
            if not any(destination.rglob('ProjectVersion.txt')) or not any(destination.rglob('*.unity')):
                raise RecoveryError('AssetRipper did not export project settings and scenes; inspect logs and retained output')
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def execute(args, root, state):
    intake = acquire(args, root, state)
    if intake['conflicts']:
        raise RecoveryError('Split files conflict; inspect intake.conflicts in recovery-state.json before selecting evidence')
    observed = intake['observed']
    if args.assetripper and any(item['name'].startswith('assets/bin/Data/') for item in intake['merged_sources']):
        tool = tool_path(args.assetripper)
        stage(root, state, 'AssetExport', tool, {},
              lambda output: export_assets(tool, root / 'Merged', output, root, args.timeout))
    if args.cpp2il and intake['inference'] == 'il2cpp':
        tool = tool_path(args.cpp2il)
        versions = observed['unity_version_candidates']
        version = args.unity_version or (versions[0] if len(versions) == 1 else None)
        binaries = [path for path in observed['il2cpp_libraries'] if args.abi is None or f'/lib/{args.abi}/' in path]
        if len(binaries) != 1 or len(observed['metadata']) != 1 or not version:
            raise RecoveryError('Cpp2IL needs one library, one metadata file, and an observed/explicit Unity version; select --abi/--unity-version or recover missing splits')
        help_log = root / 'Logs' / 'cpp2il-help.log'
        run_capture([str(tool), '--help'], help_log, 30)
        flags = ('--force-binary-path', '--force-metadata-path', '--force-unity-version', '--output-as', '--output-to', '--use-processor')
        if any(flag not in help_log.read_text(errors='replace') for flag in flags):
            raise RecoveryError('Installed Cpp2IL CLI differs; inspect ' + str(help_log))
        formats_log = root / 'Logs' / 'cpp2il-formats.log'
        processors_log = root / 'Logs' / 'cpp2il-processors.log'
        run_capture([str(tool), '--list-output-formats'], formats_log, 30)
        run_capture([str(tool), '--list-processors'], processors_log, 30)
        if 'diffable-cs' not in formats_log.read_text(errors='replace') or any(processor not in processors_log.read_text(errors='replace') for processor in ('attributeanalyzer', 'attributeinjector')):
            raise RecoveryError('Cpp2IL requires diffable-cs, attributeanalyzer and attributeinjector; inspect captured capabilities')
        parameters = {'binary': binaries[0], 'metadata': observed['metadata'][0], 'unity_version': version,
                      'output_format': 'diffable-cs', 'processor': 'attributeanalyzer,attributeinjector'}
        def cpp_export(output):
            argv = [str(tool), '--force-binary-path', str(root / binaries[0]),
                    '--force-metadata-path', str(root / observed['metadata'][0]), '--force-unity-version', version,
                    '--use-processor', 'attributeanalyzer,attributeinjector', '--output-as', 'diffable-cs', '--output-to', str(output)]
            write_json(root / 'Logs' / 'cpp2il-command.json', argv)
            run_capture(argv, root / 'Logs' / 'cpp2il.log', args.timeout)
            if not any('[Address(RVA' in path.read_text(errors='replace') for path in output.rglob('*.cs')):
                raise RecoveryError('Cpp2IL emitted no native Address attributes; inspect capabilities and retained declarations')
        stage(root, state, 'MetadataExport', tool, parameters, cpp_export)
    state['next_action'] = 'Agent: follow AGENTS.md; map native/managed logic and original UI, recover implementations, then verify the complete player flow.'
    state['playability'] = 'unverified'
    write_json(root / 'recovery-state.json', state)
    if (root / 'failure.json').exists():
        (root / 'failure.json').rename(root / 'Logs' / ('previous-failure-' + str(time.time_ns()) + '.json'))
    print(json.dumps({'workspace': str(root), 'backend_inference': intake['inference'],
                      'completed': [name for name in ('intake', 'AssetExport', 'MetadataExport') if name in state],
                      'next_action': state['next_action']}, indent=2))


@contextlib.contextmanager
def workspace_lock(root):
    if os.name != 'posix':
        raise RecoveryError('This driver currently requires a POSIX host; use Linux/WSL or adapt and verify host locking')
    import fcntl
    with (root / '.recovery.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecoveryError('Another recovery process owns workspace: ' + str(root)) from None
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', action='append', type=Path, required=True, help='Repeat for explicitly supplied splits')
    parser.add_argument('--workspace', type=Path, required=True, help='New external analysis folder; identical inputs resume verified stages')
    parser.add_argument('--assetripper', type=Path, help='Existing AssetRipper GUI executable with headless HTTP API')
    parser.add_argument('--cpp2il', type=Path, help='Existing Cpp2IL executable; exports metadata declarations')
    parser.add_argument('--tools-file', type=Path, help='Local tool-path JSON; defaults to ~/.config/rea/game-recovery-tools.json if present')
    parser.add_argument('--no-tool-config', action='store_true', help='Use only tool paths explicitly passed on this command')
    parser.add_argument('--intake-only', action='store_true', help='Acquire evidence without running export tools')
    parser.add_argument('--abi', help='Select one ABI when multiple libraries exist')
    parser.add_argument('--unity-version', help='Explicit version when automatic observation is ambiguous')
    parser.add_argument('--timeout', type=int, default=1800, help='Per tool operation timeout in seconds')
    parser.add_argument('--max-members', type=int, default=200000, help='Aggregate outer and nested ZIP member budget')
    parser.add_argument('--max-bytes', type=int, default=8 * 1024**3, help='Aggregate outer and nested uncompressed ZIP byte budget')
    args = parser.parse_args()
    if min(args.timeout, args.max_members, args.max_bytes) <= 0:
        parser.error('Timeout and expansion budgets must be positive')
    config = args.tools_file or Path.home() / '.config/rea/game-recovery-tools.json'
    if args.tools_file and args.no_tool_config:
        parser.error('--tools-file and --no-tool-config are mutually exclusive')
    if not args.no_tool_config and not args.intake_only and (args.tools_file or config.exists()):
        try:
            settings = json.loads(config.read_text())
            if not isinstance(settings, dict) or set(settings) - {'assetripper', 'cpp2il'} or any(not isinstance(value, str) or not value for value in settings.values()):
                raise ValueError('Expected an object containing only nonempty assetripper/cpp2il executable paths')
            for key, value in settings.items():
                if getattr(args, key) is None:
                    setattr(args, key, Path(value))
        except (OSError, ValueError) as error:
            parser.error('Invalid tools file ' + str(config) + ': ' + str(error))
    if args.intake_only:
        args.assetripper, args.cpp2il = None, None
    root = args.workspace.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with workspace_lock(root):
            manifest = root / 'recovery-state.json'
            if manifest.exists():
                state = json.loads(manifest.read_text())
                validate_state(state)
            else:
                if set(path.name for path in root.iterdir()) != {'.recovery.lock'}:
                    raise RecoveryError('Initial workspace must be empty; existing projects are never overwritten')
                state = {'schema_version': 1, 'selected_workspace': str(args.workspace), 'workspace': str(root)}
            (root / 'Logs').mkdir(exist_ok=True)
            instructions = root / 'AGENTS.md'
            if not instructions.exists():
                shutil.copyfile(Path(__file__).resolve().parents[1] / 'assets' / 'game-recovery-AGENTS.md', instructions)
            try:
                execute(args, root, state)
            except Exception as error:
                write_json(root / 'failure.json', {'type': type(error).__name__, 'reason': str(error), 'state': 'partial evidence retained; completion not claimed'})
                raise
    except (RecoveryError, OSError, ValueError, zipfile.BadZipFile, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
