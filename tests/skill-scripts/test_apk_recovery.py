"""Exercise real ZIP acquisition, split provenance and checkpoint boundaries."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[2] / 'skill-src/reverse-engineer-anything/scripts/apk_recovery.py'


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as output:
        for name, data in entries:
            output.writestr(name, data)
    return buffer.getvalue()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'Game.xapk'
        self.workspace = self.root / 'Analysis'

    def run_helper(self, *arguments):
        return subprocess.run([sys.executable, str(SCRIPT), '--input', str(self.source),
                               '--workspace', str(self.workspace), '--no-tool-config', *arguments], capture_output=True, text=True)

    def valid_package(self):
        self.source.write_bytes(archive([
            ('base.apk', archive([('assets/bin/Data/globalgamemanagers', b'\x002022.3.62f2\x00'),
                                  ('assets/bin/Data/Managed/Metadata/global-metadata.dat', b'metadata')])),
            ('split.apk', archive([('lib/arm64-v8a/libil2cpp.so', b'library')])),
            ('main.obb', b'unexpanded original evidence')]))

    def test_split_provenance_resume_and_changed_evidence(self):
        self.valid_package()
        first = self.run_helper()
        self.assertEqual(first.returncode, 0, first.stderr)
        state = json.loads((self.workspace / 'recovery-state.json').read_text())
        self.assertEqual(state['intake']['inference'], 'il2cpp')
        self.assertEqual(state['intake']['observed']['unity_version_candidates'], ['2022.3.62f2'])
        self.assertTrue(any(item['archive_member'] == 'main.obb' for item in state['intake']['inventory']))
        self.assertEqual(state['playability'], 'unverified')
        original = (self.workspace / 'recovery-state.json').read_bytes()
        resumed = self.run_helper()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn('Reused verified intake', resumed.stdout)
        self.assertEqual((self.workspace / 'recovery-state.json').read_bytes(), original)
        library = self.workspace / state['intake']['observed']['il2cpp_libraries'][0]
        library.write_bytes(b'changed')
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertIn('Changed or missing checkpoint', (self.workspace / 'failure.json').read_text())

    def test_added_checkpoint_file_is_not_silently_reused(self):
        self.valid_package()
        self.assertEqual(self.run_helper().returncode, 0)
        (self.workspace / 'Merged' / 'unexpected.cs').write_text('new file')
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Intake file inventory changed', result.stderr)

    def test_changed_input_is_not_mixed_with_previous_build(self):
        self.valid_package()
        self.assertEqual(self.run_helper().returncode, 0)
        self.source.write_bytes(archive([('different', b'build')]))
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Input identity changed', result.stderr)

    def test_conflicting_splits_preserve_both_sources_and_block_export(self):
        name = 'assets/bin/Data/globalgamemanagers'
        self.source.write_bytes(archive([('a.apk', archive([(name, b'first')])),
                                        ('b.apk', archive([(name, b'second')]))]))
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        state = json.loads((self.workspace / 'recovery-state.json').read_text())
        conflict = state['intake']['conflicts'][0]
        self.assertEqual((self.workspace / conflict['first']).read_bytes(), b'first')
        self.assertEqual((self.workspace / conflict['other']).read_bytes(), b'second')
        self.assertNotIn('AssetExport', state)

    def test_equal_split_files_retain_each_source(self):
        name = 'assets/bin/Data/globalgamemanagers'
        self.source.write_bytes(archive([('a.apk', archive([(name, b'same')])),
                                        ('b.apk', archive([(name, b'same')]))]))
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.workspace / 'recovery-state.json').read_text())
        self.assertEqual(len(state['intake']['merged_sources'][0]['sources']), 2)

    def test_traversal_symlinks_and_case_collisions_fail_closed(self):
        for index, member in enumerate(['../outside', '/absolute', 'dir\\outside']):
            with self.subTest(member=member):
                self.workspace = self.root / f'Analysis{index}'
                self.source.write_bytes(archive([(member, b'untrusted')]))
                result = self.run_helper()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Unsupported archive member', result.stderr)
        self.assertFalse((self.root / 'outside').exists())
        self.workspace = self.root / 'Symlink'
        info = zipfile.ZipInfo('linked')
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        self.source.write_bytes(archive([(info, b'../outside')]))
        self.assertIn('Unsupported archive member', self.run_helper().stderr)
        self.workspace = self.root / 'CaseCollision'
        self.source.write_bytes(archive([('Data', b'one'), ('data', b'two')]))
        self.assertIn('case-colliding', self.run_helper().stderr)

    def test_expansion_budget_counts_nested_apks(self):
        self.valid_package()
        result = self.run_helper('--max-members', '4')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('expansion budget exceeded', result.stderr)
        self.assertFalse((self.workspace / 'recovery-state.json').exists())

    def test_existing_development_project_is_never_overwritten(self):
        self.valid_package()
        self.workspace.mkdir()
        sentinel = self.workspace / 'user-work.txt'
        sentinel.write_text('keep')
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertIn('Initial workspace must be empty', result.stderr)

    def test_malformed_checkpoint_paths_are_rejected(self):
        self.valid_package()
        self.assertEqual(self.run_helper().returncode, 0)
        manifest = self.workspace / 'recovery-state.json'
        state = json.loads(manifest.read_text())
        state['intake']['files'][0]['path'] = '../outside'
        manifest.write_text(json.dumps(state))
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Malformed checkpoint path', result.stderr)

    def test_missing_tool_retains_successful_intake(self):
        self.valid_package()
        result = self.run_helper('--cpp2il', str(self.root / 'not-installed'))
        self.assertNotEqual(result.returncode, 0)
        state = json.loads((self.workspace / 'recovery-state.json').read_text())
        self.assertIn('intake', state)
        self.assertNotIn('MetadataExport', state)
        self.assertTrue((self.workspace / 'failure.json').exists())


if __name__ == '__main__':
    unittest.main()
