# Reverse engineering Unity games

The `Reverse-game` fork adds `inspect_game_project` to inspect a recovered Unity
project before trying to run it. The equivalent CLI command is
`inspect-game-project`. This capability is available from this checkout after
building; do not assume the upstream npm release advertises it.

It addresses a common failure: an export can contain every sprite and a valid
scene while its launcher, collision callbacks and game controller are still
metadata stubs. Successful import does not establish recovered gameplay.

## Inspect a recovered project

```sh
npm ci
npm run build:cached
node scripts/rea.mjs inspect-game-project /absolute/path/RecoveredGame --json
```

Development uses the Node/npm versions pinned in `.nvmrc` and `package.json`.
No Unity editor, Java, Ghidra or AssetRipper installation is needed for this
inspection. The target is a local directory containing `Assets` and
`ProjectSettings`; APKs use the archive and native workflows below.

Equivalent MCP request:

```json
{
  "name": "inspect_game_project",
  "arguments": { "path": "/absolute/path/RecoveredGame" }
}
```

Read the result in three parts:

- **Observations:** exact case-collision path groups; duplicate, malformed and
  missing meta files; serialized and decoded build-scene paths; and script GUIDs
  that are unresolved in the inspected Assets index. Package GUIDs can remain
  unresolved even in a healthy project.
- **Inferences:** located C# method candidates with empty bodies, default returns
  or `throw null`. Lifecycle candidates highlight `Awake`, `Start`, `StartGame`,
  `Update`, `FixedUpdate`, `OnEnable` and `OnCollisionEnter2D`. Legitimate no-op
  methods can look identical; these are inspection leads, not proof of missing code.
- **Unknowns and coverage:** compilation, execution and game-flow equivalence
  remain unknown. The C# scanner is lexical, not a compiler. Raw strings,
  preprocessors, properties and explicit interface implementations are not
  exhaustively analyzed. Binary serialization and package contents are not decoded.

Each inspected text file has a relative source path, byte count and SHA-256.
The report's `inspection_sha256` commits to those sources, observed paths and gaps;
it is **not a digest of the complete Unity project or every asset byte**. The
Evidence subject stays null because the whole directory's bytes are not identified;
its parameters and root location retain the caller's selected project path.
The inspection does not modify `.meta`, remap GUIDs, import assets or execute code.

Defaults bound traversal to 100,000 entries, selected text reads/retention to
64 MiB, and one file to 8 MiB. Reads overlap in bounded groups and reserve their
shared byte budget before allocating. Failed/changed reads retain their reservation
conservatively. Limits and gaps affect coverage explicitly; no empty result is
treated as proof of completeness. Raise the relevant budget when required:

```sh
node scripts/rea.mjs inspect-game-project /absolute/path/RecoveredGame \
  --max-entries 200000 --max-read-bytes 134217728 --max-file-bytes 16777216 --json
```

MCP uses `max_entries`, `max_read_bytes` and `max_file_bytes`. Symbolic links are
reported and not followed. Acquisition is not an atomic filesystem snapshot.

## Move from APK evidence to recovered gameplay

1. Preserve the supplied base APK and every ABI/data split separately, including
   their digests. Use `open_binary` and `inspect_artifact` for each selected archive.
   See [Android analysis](android-analysis.md) for the distinction between archive
   contents and Java/Kotlin method analysis. Do not discard a data split because
   the base APK opens successfully.
2. Establish the Unity scripting backend from actual contents. A readable managed
   `Assembly-CSharp.dll` with CIL supports managed inspection. `libil2cpp.so` plus
   `global-metadata.dat` instead requires native analysis and metadata mapping;
   JADX's Java output is not the Unity IL2CPP gameplay source.
3. Use caller-supplied asset/metadata tools where necessary. REA does not bundle
   AssetRipper or Cpp2IL and does not install them. Preserve bundle transformations,
   original hashes and exact tool versions. A mode named “IL recovery” is not
   evidence that the selected version restores executable method bodies: inspect
   its output for empty/default/throwing implementations.
4. Use Ghidra/Hopper/IDA for selected native functions. Keep Cpp2IL RVA, declared
   file offset, ELF linked address and provider database address distinct. Reuse
   [native address inspection](cli.md) to resolve the chosen representation and
   compare the complete original method bytes before trusting a mapping. A provider
   timeout is not a decompilation result; record it and narrow the next analysis.
5. Inspect the exported project with `inspect_game_project`. Resolve path/meta
   conflicts with backups and preserved GUIDs; inspect launcher and gameplay stub
   candidates alongside the native function evidence. Preserve native-derived C#
   translations separately from newly authored bootstrap/UI/platform adapters.
6. Reconstruct the source architecture around the observed gameplay logic using
   the role below. Retain an original export and map recovered methods to their
   new responsibilities; preserve behavior and serialized bindings as the logic
   moves into maintainable modules.
7. Verify a complete player flow: launch → enter game → input → collision/merge →
   scoring → loss → retry, then save/load and the secondary systems being claimed.
   Unity compilation alone does not verify physics, UI or gameplay equivalence.
   Keep desktop and Android execution claims separate.

The repository supports analysis and evidence for this process. It does not
provide an automatic APK-to-playable-C# converter. A recovered game can require
method-by-method translation, coroutine recovery and replacement of unavailable
platform integrations. Record unresolved behavior instead of inventing native facts.

## Game source reconstruction role

For a requested playable restoration or restructuring of recovered game source,
the agent must act as a **game recovery and refactoring engineer**. The outcome is
a development codebase whose logic can be understood, modified and verified.
Exporting assets or implementing a demonstration host around unimplemented
controllers does not fulfill that request. This role does not broaden a read-only
analysis request into permission to modify a project.

### Recover responsibilities before choosing a structure

Trace the entry point through input, simulation, interactions, scoring/progression,
loss, retry and persistence to the extent those systems exist in the selected
game. Identify who owns each state field, who changes it and when; include
coroutines, callbacks and frame/event ordering when behavior depends on them.
Record evidence locations, inferences and unresolved dependencies in a subsystem
map. Use the original source when supplied, or native/managed methods and packaged
configuration when reconstructing from a binary.

Use that map to choose boundaries, rather than imposing a fixed architecture on
every game. A scene or bootstrap should compose systems; it should not become the
only owner of input, simulation, rules, UI and storage. Extract responsibilities
when they have distinct state or lifecycle ownership, and keep their interfaces
small enough to trace a feature end to end. Avoid layers or framework migrations
that do not help recover or develop the observed behavior.

Keep original artifacts and raw exports outside the working implementation so
later comparisons retain a stable reference. Maintain a method-to-source mapping
through refactoring, including code moved into helpers or composed across modules.
Classify implementations as recovered translations, adaptations with a stated
coverage boundary, or newly authored integrations. A clean class layout does not
make authored behavior original source.

### Preserve semantics and engine bindings

Treat restructuring and behavior changes as separate decisions. Preserve branch
order and independent conditions, state guards, strict/inclusive thresholds,
timer advancement, numeric units and coordinate spaces. Keep an observed original
quirk until the user requests a change; do not silently merge conditions or replace
an unusual equation with a more familiar one. Mark unresolved behavior explicitly.

For Unity, keep MonoScript and asset GUIDs, serialized field identities, prefab
links and scene references intact when moving scripts or extracting logic.
Retain engine components as serialization/lifecycle adapters when moving their
rules into ordinary C# classes. Make update ownership explicit so the original
component and a new host do not both advance the same state. A claimed save/continue
flow must retain the timers, flags, inventory and progression fields that affect
resumed behavior, not only the player's position and score.

### Establish what the recovered source actually does

Use original reference inputs/outputs, managed execution or selected native
instruction execution for rules where independent comparison is available.
When only static evidence is available, distinguish source-reviewed regressions
from execution of the original runtime. Exercise the composed player as well:
launch, play, interaction, scoring, loss, retry and the secondary systems included
in the requested scope. Keep editor, desktop, Android and physical input claims
separate; compilation and test counts do not establish an accuracy percentage.

Deliver the project entry point and required editor/runtime version, the subsystem
and state map, recovery provenance, verification commands/results and a list of
remaining behavior gaps. If the user asks for a complete playable recovery,
resolve the requested flow instead of stopping after import; describe any
unresolved systems accurately rather than marking them recovered. Write authored
documentation, comments and UI in English unless the user requests another
language, while retaining original serialized identifiers and evidence verbatim.

These are agent workflow requirements, not a new automatic recovery engine.
`inspect_game_project` remains read-only Unity inspection. Other engines require
their own supported evidence and runtime workflow. Recovered game source, assets
and binaries belong in a separate project, not in this tooling repository.

## Verification and contribution scope

The static inspector has filesystem regressions for meta/case problems, GUID
references, scene selection, byte/entry budgets, symlinks, literal masking and
cancellation. A boundary test validates the SDK-advertised JSON Schemas and runs
the same temporary project through the CLI and MCP with identical Evidence.

```sh
npm run test:local -- src/inspector/game/UnityProjectInspector.test.ts
npm run test:focused -- tests/boundary/mcp/gameProjectInspectionMcp.test.ts
npm run check:fast
```

These checks establish static inspection behavior. They are not real Unity,
Ghidra or Android runtime conformance. Do not commit recovered APKs, game assets,
native libraries, editor caches or third-party game source to this tooling repo.
Real-project verification for this fork was performed on Linux. The filesystem
case-collision regression runs only when the host preserves both case variants;
it cannot establish that observation on a case-insensitive filesystem.
