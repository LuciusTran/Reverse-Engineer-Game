# APK intake and autonomous game recovery

Use this procedure when a user supplies an APK/XAPK/APKS for a playable Unity
recovery. Once that scope is authorized, continue from intake to source recovery,
UI, composition and player verification. The helper executes acquisition and
optional exports; the agent performs subsequent evidence-driven recovery.
There is no general IL2CPP-to-original-C# converter.

## Start from the supplied file

The bundled helper requires Python 3.11+ and a POSIX host (verified on Linux).
From this fork's checkout:

```sh
python3 skill-src/reverse-engineer-anything/scripts/apk_recovery.py \
  --input /absolute/path/Game.xapk \
  --workspace /absolute/path/Game-Recovery
```

From an installed skill, use `scripts/apk_recovery.py` relative to that skill's
folder instead. No Node build, MCP registration or external analysis engine is
required for intake. For separately supplied splits repeat `--input`. APK/XAPK,
APKS and ZIP files are supported as containers; OBBs and nested non-APK archives
are retained as evidence and need separate format-aware analysis.

To also export assets and IL2CPP declarations using existing executables:

```sh
python3 skill-src/reverse-engineer-anything/scripts/apk_recovery.py \
  --input /absolute/path/Game.xapk \
  --workspace /absolute/path/Game-Recovery \
  --assetripper /absolute/path/AssetRipper.GUI.Free \
  --cpp2il /absolute/path/Cpp2IL \
  --abi arm64-v8a
```

Use actual installed executables. No downloads or installations occur. Omit a
tool flag when unavailable and continue other analysis. The AssetRipper adapter
checks its headless OpenAPI operations. The Cpp2IL adapter checks installed flags,
output formats and processors; it uses `attributeanalyzer,attributeinjector` with `diffable-cs` to
retain native method attributes. Tool hashes, commands, capability output and logs
are preserved. Other versions/APIs require an inspected adapter change, not an
assumption that all versions accept these arguments.

For repeated local imports, put existing executable paths in
`~/.config/rea/game-recovery-tools.json` (local configuration, never commit):

```json
{
  "assetripper": "/absolute/path/AssetRipper.GUI.Free",
  "cpp2il": "/absolute/path/Cpp2IL"
}
```

The helper reads this file when present. Explicit tool flags override its values;
`--tools-file PATH` selects another file, `--no-tool-config` ignores local defaults,
and `--intake-only` acquires evidence without launching export tools. Without a
Unity data layout asset export is skipped; without IL2CPP evidence metadata export
is skipped. Absence of a stage does not mean it was recovered by another route.

Unity version strings are observed in the first 64 KiB of `globalgamemanagers` or
`data.unity3d`. When absent/ambiguous, establish the version from original evidence
and pass `--unity-version VERSION`. Multiple ABIs require explicit selection with
`--abi`. An `Assembly-CSharp.dll` filename is only a managed candidate: inspect
actual CIL before choosing managed decompilation. Non-Unity/encrypted packages
remain classified unknown rather than forcing an IL2CPP workflow.

## Outputs and checkpoints

- `Inputs/`: immutable input copies, bound to original caller paths and digests.
- `Packages/`: individually extracted containers and APK splits.
- `Merged/`: copy of Unity data and native libraries, with every member's source
  recorded. Differing or case-conflicting members block export, without losing
  their individual evidence. Equal members retain all source references.
- `AssetExport/`: raw Unity export; copy it to a separate development project.
- `MetadataExport/`: Cpp2IL metadata declarations, not recovered implementations.
- `recovery-state.json`: member inventory, file hashes, observed candidates,
  inference, limits, verified completed stages and next action.
- `AGENTS.md`: portable game-recovery role for subsequent agent turns.
- `Logs/` and `failure.json`: commands, diagnostics and retained partial results.

Rerun the same command to verify and reuse completed stages. Add tool flags later
without reacquiring unchanged input. Changed inputs, tool hashes, parameters or
checkpoint files require a new workspace; never mix two game builds. An incomplete
stage remains available for diagnosis and is not reused as complete. Use a new
workspace after an interrupted/failed stage. Acquisition budgets count both outer
and nested ZIP expansion (default 200,000 members/8 GiB); raise `--max-members` or
`--max-bytes` explicitly for larger evidence. ZIP paths, links, duplicate names,
encryption and split conflicts are checked; no APK code is executed by intake.
A process lock prevents concurrent writers to the same workspace.

## Continue automatically as the agent

Read the generated `AGENTS.md` and [game recovery](game-recovery.md). Reuse any
existing recovery/status records. Inspect the raw Unity export with
`inspect_game_project` or `node scripts/rea.mjs inspect-game-project PATH --json`.
Choose managed or native tools based on observed bodies/backend. Retain original
exports, map native coordinates and verify whole bodies before translation.
Follow original scene/component/callback links to recover both gameplay and UI.
Maintain method provenance and a flow/state map in the working project.

Continue through the authorized player flow, not just these helper stages. If a
tool or a subsystem is unavailable, retain the reason and recover independent
systems. Avoid repeatedly asking the user for routine implementation choices.
Do not fabricate native behavior to eliminate gaps. Commit substantive milestones
and push only when authorized; installing a skill does not authorize publishing
future game assets or changing unrelated service settings.

For future requests the user may simply supply the file and say:

> Use $reverse-engineer-anything to recover a playable Unity project from this APK.
> Recover maximum original assets, gameplay and UI; minimize authored code.

Automatic skill selection depends on the host agent's discovery rules. Installing
this skill supplies instructions/helpers; it does not launch a watcher, register
an MCP server, or create a self-running APK conversion service.
