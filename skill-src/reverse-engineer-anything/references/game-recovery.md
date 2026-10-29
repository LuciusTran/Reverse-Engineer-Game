# Recovering a Unity game

## Game recovery and refactoring role

When the user requests a playable recovery or restructuring of recovered game
source, reconstruct the logic into a maintainable development codebase. This is
required within that requested scope: an asset export or a demonstration scene
around stub controllers is insufficient. A read-only inspection remains read-only.

Before restructuring, map the original entry points, state transitions, subsystem
dependencies and update/event order from the supplied source or binary evidence.
Separate observations, inferred relationships and unresolved behavior. For each
recovered system, keep a link from its source implementation to original methods,
coordinates, configuration and any reference execution used for verification.

Choose module boundaries from the game's actual responsibilities. Keep bootstrap
and scenes responsible for composition; give gameplay state and simulation an
explicit owner, with input, presentation, persistence and platform adapters at
clear boundaries where needed. Avoid moving an entire game into a monolithic
host or imposing the same folder/class template on unrelated games. Retain a
mapping from original methods to extracted responsibilities when reorganizing.

Refactoring must preserve observed behavior, including independent conditionals,
action order, timer boundaries, coordinate spaces and persistent state. Keep
unusual original behavior unless a behavior change is requested, and label an
authored replacement explicitly. For Unity, preserve MonoScript/asset GUIDs and
serialized fields/references during moves; retain an original export separately.

Verify the claimed systems against original evidence and exercise the complete
requested player flow. Reference vectors or native instruction execution are
useful for delicate rules; source-reviewed regressions and player tests establish
different scopes. Do not treat compilation, test totals or a working demo as an
accuracy percentage. Deliver the playable entry point, subsystem/state map,
recovery provenance, verification results and concrete unresolved behavior.
Write authored documentation, comments and UI in English unless requested otherwise;
preserve original evidence and serialized identifiers verbatim. Keep recovered
game projects and assets outside the REA tooling repository.

For executable APK/XAPK intake, reusable checkpoints and agent continuation, read
[APK automation](apk-automation.md). The portable recovery role is bundled in
[the workspace instructions template](../assets/game-recovery-AGENTS.md).

## Select and inspect the target

Use `inspect_game_project` on the caller's exported project directory when the
connected server advertises it. CLI fallback from this fork's built checkout:
`node scripts/rea.mjs inspect-game-project /absolute/project --json`.
This is a target-free read-only operation; do not open a native target first.
The upstream npm release may not include the fork's capability.

Read exact case/meta/GUID and build-scene observations before proposing repairs.
Keep original paths, GUIDs and backups when a repair is authorized. The inspection
does not itself repair files. Unresolved Assets-index script GUIDs may belong to
Packages, and inert-looking C# methods are heuristic candidates, not proven stubs.
Use the reported file/line and digest for review. Do not imply that a successfully
imported scene contains functioning gameplay.

For APK inputs, inventory each base/ABI/data split separately with archive tools.
JADX covers Java/Kotlin, not native Unity IL2CPP gameplay. Establish whether real
CIL is present before choosing managed decompilation. An IL2CPP native image and
metadata require native analysis and an authenticated address mapping. Keep
Cpp2IL's RVA/file offset, linked native address and provider database address
distinct. Verify original bytes, including method extents, before translation.

AssetRipper and Cpp2IL are external caller-supplied tools. A DLL or C# file with
correct class names may still contain empty/default/throw-null bodies. Verify
the actual tool version's output rather than trusting an “IL recovery” mode name.
REA does not automatically convert IL2CPP to a playable C# project.

When a playable restoration is requested, trace a complete flow from launcher
through input, physics/game rules, scoring, loss, retry and save/load. Preserve
method-level native evidence and distinguish translated logic from authored UI,
bootstrap and platform adapters. Record unresolved coroutines, effects and service
dependencies. Do not invent behavior and label it decompiled source. Verify the
actual Unity player and the selected target platform before claiming equivalence.

The static project tool returns its budgets and every skipped path. Symlinks,
binary serialization, packages and unsupported C# syntax can leave incomplete
coverage. Do not convert missing coverage into proof that code or assets are absent.

## Recover the UI as a system

Trace original panels, navigation/back actions, modal confirmations, HUD state,
settings, selection/store and progression alongside gameplay. Recover serialized
hierarchy, atlas coordinates, font glyph metrics/kerning, clipping, anchors, draw
depth, colliders, callback target/payload and transitions before designing an
adapter. Locate native coroutine and localization/literal dependencies. Keep
original references and IDs; changes to legacy rendering require explicit adapter
coverage. Verify callbacks through the real pointer/collider dispatch path and
exercise navigation between panels. A screenshot or a newly drawn menu does not
establish original UI recovery. Include unresolved progression and service state
in the flow map instead of silently marking those screens complete.
