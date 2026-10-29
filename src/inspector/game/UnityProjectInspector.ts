import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, opendir, realpath } from "node:fs/promises";
import { extname, isAbsolute, join, relative, sep } from "node:path";
import { z } from "zod";
import type { GameProjectInspectionPort } from "../../application/game/GameProjectInspectionPort.js";
import type { ExecutionOptions } from "../../application/AnalysisProvider.js";
import { AnalysisError } from "../../domain/analysisErrorBase.js";
import {
  AnalysisAccessDeniedError,
  AnalysisCancelledError,
  AnalysisProtocolError,
  AnalysisUnsupportedTargetError,
} from "../../domain/analysisErrorCore.js";
import {
  gameProjectReportSchema,
  GAME_PROJECT_PROVIDER,
  type GameProjectInput,
  type GameProjectReport,
} from "../../domain/gameProject.js";
import { err, ok } from "../../domain/result.js";
import { inspectCSharpStubCandidates } from "./CSharpStubCandidates.js";

const OPERATION = "inspect_game_project";
const TEXT_EXTENSIONS = new Set([
  ".meta",
  ".cs",
  ".unity",
  ".prefab",
  ".asset",
]);
const LIFECYCLE = new Set([
  "Awake",
  "Start",
  "Update",
  "FixedUpdate",
  "OnEnable",
  "OnCollisionEnter2D",
  "StartGame",
]);
const digest = (text: string | Buffer) =>
  createHash("sha256").update(text).digest("hex");
const codeOf = (cause: unknown) => {
  const parsed = z.object({ code: z.string() }).safeParse(cause);
  return parsed.success ? parsed.data.code : undefined;
};
const inside = (root: string, target: string) => {
  const path = relative(root, target);
  return path !== ".." && !path.startsWith(".." + sep) && !isAbsolute(path);
};
const portable = (path: string) => path.split(sep).join("/");

/** Unity serialization/file-layout boundary; never opens an editor or writes assets. */
export class UnityProjectInspector implements GameProjectInspectionPort {
  readonly identity = GAME_PROJECT_PROVIDER;

  /** Inspect a bounded project tree and retain exact paths and text-file digests. */
  async inspect(input: GameProjectInput, options?: ExecutionOptions) {
    try {
      return ok(await this.readProject(input, options));
    } catch (cause: unknown) {
      if (cause instanceof AnalysisError) return err(cause);
      const code = codeOf(cause);
      if (code === "EACCES" || code === "EPERM")
        return err(
          new AnalysisAccessDeniedError(OPERATION, input.path, code, { cause }),
        );
      if (code === "ENOENT" || code === "ENOTDIR")
        return err(
          new AnalysisUnsupportedTargetError(
            OPERATION,
            input.path,
            "Expected an existing Unity directory containing Assets and ProjectSettings",
            { cause },
          ),
        );
      return err(
        new AnalysisProtocolError(
          `Cannot inspect game project ${input.path}: ${cause instanceof Error ? cause.message : String(cause)}`,
          { cause },
        ),
      );
    }
  }

  private async readProject(
    input: GameProjectInput,
    options?: ExecutionOptions,
  ): Promise<GameProjectReport> {
    const checkCancelled = () => {
      if (options?.signal?.aborted) throw new AnalysisCancelledError(OPERATION);
    };
    checkCancelled();
    const root = await realpath(input.path);
    for (const name of ["Assets", "ProjectSettings"]) {
      const stat = await lstat(join(root, name));
      if (!stat.isDirectory())
        throw new AnalysisUnsupportedTargetError(
          OPERATION,
          input.path,
          `${name} must be a directory, not a link or file`,
        );
    }
    const paths = new Set<string>();
    const sources: GameProjectReport["sources"] = [];
    const skipped: GameProjectReport["coverage"]["skipped"] = [];
    const texts = new Map<string, string>();
    let count = 0,
      bytes = 0,
      inventoryComplete = true,
      textComplete = true;
    const skip = (path: string, reason: string) => {
      skipped.push({ path, reason });
      textComplete = false;
    };
    const pending = ["Assets", "ProjectSettings"];
    while (pending.length > 0) {
      checkCancelled();
      const directory = pending.pop();
      if (directory === undefined) break;
      if (!inside(root, await realpath(join(root, directory)))) {
        skip(directory, "directory-resolves-outside-project");
        inventoryComplete = false;
        continue;
      }
      const entries = await opendir(join(root, directory));
      for await (const entry of entries) {
        checkCancelled();
        if (count >= input.max_entries) {
          skip(directory, "entry-budget-exhausted");
          inventoryComplete = false;
          pending.length = 0;
          break;
        }
        count++;
        const local = join(directory, entry.name),
          path = portable(local);
        paths.add(path);
        if (entry.isSymbolicLink()) {
          skip(path, "symbolic-link-not-followed");
          continue;
        }
        if (entry.isDirectory()) {
          pending.push(local);
          continue;
        }
        if (!entry.isFile()) {
          skip(path, "non-regular-file");
          continue;
        }
        if (
          !TEXT_EXTENSIONS.has(extname(path)) &&
          path !== "ProjectSettings/ProjectVersion.txt"
        )
          continue;
        const absolute = join(root, local);
        const handle = await open(
          absolute,
          constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
        ).catch((cause: unknown) => {
          const code = codeOf(cause);
          if (
            code !== undefined &&
            ["ENOENT", "ENOTDIR", "EACCES", "EPERM", "ELOOP"].includes(code)
          ) {
            skip(path, `file-open-failed:${code}`);
            return undefined;
          }
          throw cause;
        });
        if (handle === undefined) continue;
        try {
          const before = await handle.stat();
          if (!before.isFile()) {
            skip(path, "not-a-regular-file");
            continue;
          }
          if (before.size > input.max_file_bytes) {
            skip(path, "file-text-budget-exceeded");
            continue;
          }
          if (before.size > input.max_read_bytes - bytes) {
            skip(path, "total-text-budget-exhausted");
            continue;
          }
          const buffer = Buffer.alloc(before.size);
          let offset = 0;
          while (offset < buffer.length) {
            checkCancelled();
            const read = await handle.read(
              buffer,
              offset,
              Math.min(65536, buffer.length - offset),
              offset,
            );
            if (read.bytesRead === 0) break;
            offset += read.bytesRead;
          }
          bytes += offset;
          const after = await handle.stat();
          if (
            offset !== before.size ||
            after.size !== before.size ||
            after.mtimeMs !== before.mtimeMs ||
            after.ctimeMs !== before.ctimeMs
          ) {
            skip(path, "file-changed-during-read");
            continue;
          }
          if (buffer.includes(0)) {
            skip(path, "binary-serialization-not-decoded");
            continue;
          }
          let text: string;
          try {
            text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
          } catch {
            skip(path, "non-utf8-text-not-decoded");
            continue;
          }
          texts.set(path, text);
          sources.push({ path, bytes: buffer.length, sha256: digest(buffer) });
        } finally {
          await handle.close();
        }
      }
    }
    sources.sort((a, b) => a.path.localeCompare(b.path, "en"));
    skipped.sort((a, b) => a.path.localeCompare(b.path, "en"));
    const observations = analyze(paths, texts, inventoryComplete);
    const stubCandidates: GameProjectReport["inferences"]["stub_candidates"] =
      [];
    const unknowns = [
      "Compilation, Play Mode, game-flow equivalence and original method-body recovery are not established by static inspection.",
      "Unresolved GUIDs may belong to Packages or external assemblies; absence from Assets is not proof of a missing script.",
      "Directory acquisition is not an atomic snapshot; the inspection digest commits to the listed inspected text files, paths and gaps, not every asset byte.",
    ];
    let csharpFiles = 0;
    for (const [path, text] of texts)
      if (path.endsWith(".cs")) {
        csharpFiles++;
        const scan = inspectCSharpStubCandidates(path, text);
        stubCandidates.push(...scan.candidates);
        if (scan.uncertain) {
          textComplete = false;
          unknowns.push(
            `C# lexical coverage is incomplete for ${path}; raw strings or unmatched syntax require a C# compiler/parser.`,
          );
        }
      }
    stubCandidates.sort(
      (a, b) => a.path.localeCompare(b.path, "en") || a.line - b.line,
    );
    const report = {
      project_path: input.path,
      engine: "unity" as const,
      editor_version:
        texts
          .get("ProjectSettings/ProjectVersion.txt")
          ?.match(/^m_EditorVersion:\s*(\S+)/mu)?.[1] ?? null,
      inspection_sha256: digest(
        JSON.stringify({ paths: [...paths].sort(), sources, skipped }),
      ),
      sources,
      coverage: {
        directory_inventory: inventoryComplete
          ? ("complete" as const)
          : ("partial" as const),
        selected_text: textComplete
          ? ("complete" as const)
          : ("partial" as const),
        csharp_analysis: "heuristic" as const,
        inspected_text_bytes: bytes,
        enumerated_entries: count,
        skipped,
      },
      observations: { ...observations, csharp_files: csharpFiles },
      inferences: {
        stub_candidates: stubCandidates,
        lifecycle_candidates: stubCandidates.filter((c) =>
          LIFECYCLE.has(c.name),
        ),
      },
      unknowns,
      limitations: [
        "Unity exported-project profile only; no APK extraction, editor launch, decompiler invocation or asset mutation.",
        "Empty/default-return/throw-null methods are heuristic candidates: legitimate no-op code can have the same representation. Properties, explicit interface methods, preprocessors and full C# semantics are not exhaustively parsed.",
        "Only Assets and ProjectSettings are enumerated. Packages, Library, native plugins and binary-serialized asset contents are not resolved.",
        "Case collision matching uses Unicode lowercasing without path normalization; source paths are retained exactly.",
      ],
    };
    checkCancelled();
    return gameProjectReportSchema.parse(report);
  }
}

const analyze = (
  paths: Set<string>,
  texts: Map<string, string>,
  complete: boolean,
): Omit<GameProjectReport["observations"], "csharp_files"> => {
  const folded = new Map<string, string[]>(),
    guids = new Map<string, string[]>();
  const missingMeta: string[] = [],
    invalidMeta: string[] = [];
  for (const path of [...paths].sort()) {
    const key = path.toLowerCase();
    const siblings = folded.get(key);
    if (siblings === undefined) folded.set(key, [path]);
    else siblings.push(path);
    if (
      complete &&
      path.startsWith("Assets/") &&
      !path.endsWith(".meta") &&
      !paths.has(path + ".meta")
    )
      missingMeta.push(path);
  }
  for (const [path, text] of texts)
    if (path.endsWith(".meta")) {
      const matches = [...text.matchAll(/^guid:\s*([a-fA-F0-9]{32})\s*$/gmu)];
      const guid =
        matches.length === 1 ? matches[0]?.[1]?.toLowerCase() : undefined;
      if (guid === undefined) invalidMeta.push(path);
      else {
        const siblings = guids.get(guid);
        if (siblings === undefined) guids.set(guid, [path]);
        else siblings.push(path);
      }
    }
  const unresolved: GameProjectReport["observations"]["unresolved_script_references"] =
    [];
  for (const [path, text] of texts)
    if (/\.(?:unity|prefab|asset)$/u.test(path)) {
      let lineOffset = 0,
        line = 1;
      for (const match of text.matchAll(
        /^\s*m_Script:\s*\{[^}\n]*\bguid:\s*([a-fA-F0-9]{32})[^}\n]*\}/gmu,
      )) {
        const guid = match[1]?.toLowerCase();
        while (lineOffset < match.index) {
          if (text[lineOffset] === "\n") line++;
          lineOffset++;
        }
        if (guid && !/^0+$/u.test(guid) && !guids.has(guid))
          unresolved.push({
            path,
            guid,
            line,
          });
      }
    }
  const scenes: GameProjectReport["observations"]["build_scenes"] = [];
  const settings = texts.get("ProjectSettings/EditorBuildSettings.asset");
  if (settings)
    for (const match of settings.matchAll(
      /^\s*- enabled:\s*([01])\s*\n\s*path:\s*([^\r\n]*)/gmu,
    )) {
      const serializedPath = (match[2] ?? "").trim();
      const decoded = decodePath(serializedPath);
      const path = decoded ?? serializedPath;
      const valid =
        path.startsWith("Assets/") &&
        !path.split("/").some((part) => part === "." || part === "..") &&
        !path.includes("\\");
      scenes.push({
        path,
        serialized_path: serializedPath,
        enabled: match[1] === "1",
        state:
          decoded === undefined
            ? "unknown"
            : !valid
              ? "invalid-path"
              : paths.has(path)
                ? "present"
                : complete
                  ? "missing"
                  : "unknown",
      });
    }
  unresolved.sort(
    (a, b) => a.path.localeCompare(b.path, "en") || a.line - b.line,
  );
  return {
    case_collisions: [...folded.values()].filter((p) => p.length > 1),
    duplicate_guids: [...guids]
      .filter(([, p]) => p.length > 1)
      .sort(([a], [b]) => a.localeCompare(b, "en"))
      .map(([guid, p]) => ({ guid, paths: p.sort() })),
    missing_meta: missingMeta,
    invalid_meta: invalidMeta.sort(),
    build_scenes: scenes,
    build_settings_state:
      settings === undefined ? "not-inspected" : "inspected",
    unresolved_script_references: unresolved,
  };
};

const decodePath = (value: string): string | undefined => {
  if (value.startsWith('"')) {
    try {
      const decoded: unknown = JSON.parse(value);
      return typeof decoded === "string" ? decoded : undefined;
    } catch {
      return undefined;
    }
  }
  if (value.startsWith("'"))
    return value.endsWith("'")
      ? value.slice(1, -1).replaceAll("''", "'")
      : undefined;
  return value;
};
