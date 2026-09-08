import {
  Action,
  ActionPanel,
  Form,
  Toast,
  showToast,
  useNavigation,
} from "@raycast/api";
import { spawn } from "child_process";
import { homedir } from "os";
import { useEffect, useRef, useState } from "react";
import { clearStickies, getSticky, setSticky } from "../lib/stickies";
import { Workflow, WorkflowParam } from "../lib/workflows";

/** Param types that are provided by an acquirer at runtime — no user input. */
const ACQUIRED_TYPES = new Set([
  "audio-recording",
  "screenshot-manual",
  "command",
  "screen-recording",
]);

/**
 * Coerce a Form submit value to a single string.
 *
 * Raycast's TextField/TextArea/Dropdown submit values as `string`, but
 * `<Form.FilePicker>` always submits `string[]` even for single-pick mode.
 * Our CLI takes one path per --param; LocalStorage rejects non-primitive
 * values. Normalize once at the boundary so both sticky storage and argv
 * construction see plain strings.
 *
 * For arrays we take the first element (single-pick assumption). Empty
 * arrays / nullish / non-strings collapse to "".
 */
export function normalizeSubmitValue(v: unknown): string {
  if (typeof v === "string") return v;
  if (Array.isArray(v)) {
    const first = v.find((x): x is string => typeof x === "string" && x.length > 0);
    return first ?? "";
  }
  if (v == null) return "";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return "";
}

/**
 * Build the argv for `turbo workflows run <name> --param k=v ...`.
 * Empty values are omitted so the CLI can apply its own defaults.
 *
 * Accepts loose typing because Raycast's onSubmit values are
 * heterogeneous (string | string[] depending on field type).
 */
export function buildArgsForSubmit(
  workflowName: string,
  values: Record<string, unknown>
): string[] {
  const args = ["workflows", "run", workflowName];
  for (const [k, raw] of Object.entries(values)) {
    const v = normalizeSubmitValue(raw);
    if (v !== "") {
      args.push("--param", `${k}=${v}`);
    }
  }
  return args;
}

/**
 * Return the allowed file extensions for a `file`-type param, or `undefined`
 * when there is no constraint (all files allowed).
 */
export function getAllowedExtensions(param: WorkflowParam): string[] | undefined {
  if (param.type === "file" && param.extensions && param.extensions.length > 0) {
    return param.extensions;
  }
  return undefined;
}

// ── Status polling ─────────────────────────────────────────────────────────────

/**
 * Parse `turbo workflows status <name> --json` output.
 *
 * Returns the holder PID when the workflow is running, or `null` for idle /
 * malformed payloads. We swallow JSON errors here so a transient bad parse
 * never knocks the polling hook into an error state — the next tick will
 * see a clean payload.
 */
export function parseStatusOutput(raw: string): number | null {
  try {
    const info = JSON.parse(raw) as { state?: string; pid?: number | null };
    if (info.state === "running" && typeof info.pid === "number") return info.pid;
    return null;
  } catch {
    return null;
  }
}

/** Default polling interval; can be overridden by tests. */
export const STATUS_POLL_INTERVAL_MS = 1500;

/**
 * Poll `turbo workflows status <name> --json` and report the holder PID.
 *
 * Returns `null` while idle, the integer PID while running. The first poll
 * fires immediately; subsequent polls are spaced by `STATUS_POLL_INTERVAL_MS`.
 * Cleans up the interval and ignores in-flight responses after unmount.
 */
export function useRunningPid(turboPath: string, workflowName: string): number | null {
  const [pid, setPid] = useState<number | null>(null);
  // Shared across effect re-runs (on purpose): guards against stacking a new
  // `turbo workflows status` spawn every STATUS_POLL_INTERVAL_MS on top of a
  // still-running one (a slow/hung `turbo` binary used to pile up spawns
  // indefinitely — one per tick, forever).
  const inFlightRef = useRef(false);

  useEffect(() => {
    // Scoped to THIS effect instance (not a shared ref): a response from a
    // spawn started before `workflowName`/`turboPath` changed must not
    // resurrect a stale PID for the new params after this effect re-runs.
    let cancelled = false;
    if (!turboPath) return;

    const tick = () => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;

      const proc = spawn(turboPath, ["workflows", "status", workflowName, "--json"], {
        env: { ...process.env, PATH: enrichedPath(process.env.PATH, homedir()) },
      });
      let out = "";
      proc.stdout.on("data", (c: Buffer) => (out += c.toString()));
      proc.on("close", () => {
        inFlightRef.current = false;
        if (cancelled) return;
        setPid(parseStatusOutput(out));
      });
      proc.on("error", () => {
        inFlightRef.current = false;
        // turbo binary missing or unspawnable — keep last value, retry next tick.
      });
    };

    tick();
    const id = setInterval(tick, STATUS_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [turboPath, workflowName]);

  return pid;
}

// ── Component ──────────────────────────────────────────────────────────────────

interface WorkflowFormProps {
  workflow: Workflow;
  turboPath: string;
}

export function WorkflowForm({ workflow, turboPath }: WorkflowFormProps) {
  const { pop } = useNavigation();
  // `null` until stickies have loaded — distinct from `{}` (loaded, nothing
  // stuck). Raycast's Form fields are uncontrolled: `defaultValue` only
  // takes effect at mount, so rendering fields before stickies resolve
  // (with `defaultValue=""`) means a later `setDefaults(loaded)` can never
  // retroactively populate them. Gating the fields' render on
  // `defaults !== null` is what makes stickies actually apply.
  const [defaults, setDefaults] = useState<Record<string, string> | null>(null);
  const runningPid = useRunningPid(turboPath, workflow.name);
  const isRunning = runningPid !== null;

  useEffect(() => {
    async function loadStickies() {
      const loaded: Record<string, string> = {};
      for (const param of workflow.params ?? []) {
        try {
          const val = await getSticky(workflow.name, param.name);
          if (val != null) loaded[param.name] = val;
        } catch (err) {
          // Corrupted LocalStorage entry — Raycast's Swift JSON decoder
          // surfaces these as "The data couldn't be read because it isn't
          // in the correct format." Skip the entry and surface the issue
          // as a toast instead of crashing the whole form.
          await showToast({
            style: Toast.Style.Failure,
            title: `Sticky for "${param.name}" is corrupt`,
            message: "Will be overwritten next time you submit. " + String(err),
          });
        }
      }
      setDefaults(loaded);
    }
    loadStickies();
  }, [workflow.name]);

  async function handleSubmit(values: Record<string, unknown>) {
    // Persist non-empty stickies before spawning. Coerce FilePicker arrays
    // to strings — LocalStorage.setItem accepts only `string | number |
    // boolean` and surfaces non-primitive values as the Swift Codable error
    // "The data couldn't be read because it isn't in the correct format."
    for (const [k, raw] of Object.entries(values)) {
      const v = normalizeSubmitValue(raw);
      if (v !== "") {
        try {
          await setSticky(workflow.name, k, v);
        } catch (err) {
          // Don't let a sticky-write failure block the actual workflow run.
          await showToast({
            style: Toast.Style.Failure,
            title: `Couldn't save sticky for "${k}"`,
            message: String(err),
          });
        }
      }
    }

    const args = buildArgsForSubmit(workflow.name, values);
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: `Running ${workflow.name}…`,
    });

    try {
      await runSubprocess(turboPath, args);
      toast.style = Toast.Style.Success;
      toast.title = `${workflow.name} complete`;
      pop();
    } catch (err) {
      toast.style = Toast.Style.Failure;
      toast.title = `${workflow.name} failed`;
      toast.message = String(err);
      // Stay in form on error — user can fix params and retry
    }
  }

  async function handleStop() {
    if (!runningPid) return;
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: `Stopping ${workflow.name}…`,
    });
    try {
      await runSubprocess(turboPath, ["workflows", "stop", workflow.name]);
      toast.style = Toast.Style.Success;
      toast.title = `${workflow.name} stop signaled`;
    } catch (err) {
      toast.style = Toast.Style.Failure;
      toast.title = `Couldn't stop ${workflow.name}`;
      toast.message = String(err);
    }
  }

  return (
    <Form
      isLoading={defaults === null}
      actions={
        <ActionPanel>
          {isRunning ? (
            <Action title={`Stop (pid ${runningPid})`} onAction={handleStop} />
          ) : (
            <Action.SubmitForm title="Run" onSubmit={handleSubmit} />
          )}
          <Action
            title="Reset Stickies"
            onAction={async () => {
              await clearStickies(workflow.name);
            }}
          />
        </ActionPanel>
      }
    >
      {defaults !== null &&
        (workflow.params ?? []).map((param) => renderField(param, defaults))}
    </Form>
  );
}

/** Dispatch a single Form field based on param type. */
function renderField(param: WorkflowParam, defaults: Record<string, string>) {
  if (ACQUIRED_TYPES.has(param.type)) {
    return (
      <Form.Description
        key={param.name}
        title={param.name}
        text="Captured at runtime"
      />
    );
  }

  const defaultValue = defaults[param.name] ?? param.default ?? "";

  switch (param.type) {
    case "string":
      return (
        <Form.TextField
          key={param.name}
          id={param.name}
          title={param.name}
          defaultValue={defaultValue}
        />
      );
    case "text":
      return (
        <Form.TextArea
          key={param.name}
          id={param.name}
          title={param.name}
          defaultValue={defaultValue}
        />
      );
    case "enum":
      return (
        <Form.Dropdown
          key={param.name}
          id={param.name}
          title={param.name}
          defaultValue={defaultValue}
        >
          {(param.enum_values ?? []).map((v) => (
            <Form.Dropdown.Item key={v} value={v} title={v} />
          ))}
        </Form.Dropdown>
      );
    case "file":
    case "directory":
      // Once a sticky value exists, render as a TextField. Reason: Raycast's
      // FilePicker steals Enter to open the picker dialog, so the happy path
      // (open form → Enter → run) is broken whenever a file/directory field
      // is in the form. After the first pick, the path is known — the user
      // can edit the string directly and Enter submits.
      if (defaults[param.name]) {
        return (
          <Form.TextField
            key={param.name}
            id={param.name}
            title={param.name}
            defaultValue={defaults[param.name]}
          />
        );
      }
      return (
        <Form.FilePicker
          key={param.name}
          id={param.name}
          title={param.name}
          allowMultipleSelection={false}
          canChooseFiles={param.type === "file"}
          canChooseDirectories={param.type === "directory"}
          info={getAllowedExtensions(param)?.map((ext) => `.${ext}`).join(", ")}
        />
      );
    default:
      return (
        <Form.TextField
          key={param.name}
          id={param.name}
          title={param.name}
          defaultValue={defaultValue}
        />
      );
  }
}

// ── Subprocess helper ──────────────────────────────────────────────────────────

/**
 * Build a PATH that includes the typical locations Raycast's stripped login
 * env misses: Homebrew (`/opt/homebrew/bin` on Apple Silicon,
 * `/usr/local/bin` on Intel) and the user's `~/.local/bin`. Workflow scripts
 * spawn `pi`, `turbo`, `ffmpeg`, etc. by bare name; without these prefixes
 * the scripts fail with command-not-found and `set -e` aborts with the
 * next command's exit code (Click commonly yields 2 for "usage error").
 */
export function enrichedPath(envPath: string | undefined, home: string): string {
  const extras = [`${home}/.local/bin`, "/opt/homebrew/bin", "/usr/local/bin"];
  const existing = (envPath ?? "").split(":").filter(Boolean);
  const seen = new Set(existing);
  for (const p of extras) {
    if (!seen.has(p)) {
      existing.push(p);
      seen.add(p);
    }
  }
  return existing.join(":");
}

function runSubprocess(execPath: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      PATH: enrichedPath(process.env.PATH, homedir()),
    };
    const proc = spawn(execPath, args, { env });
    let stderr = "";
    let stdout = "";
    proc.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    proc.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    proc.on("close", (code: number | null) => {
      if (code === 0) {
        resolve();
      } else {
        // Surface stderr first (the usual error channel); fall back to
        // stdout (some Click errors emit there) before reporting just the
        // bare exit code.
        const detail = [stderr.trim(), stdout.trim()].filter(Boolean).join("\n---\n");
        reject(new Error(detail || `exited with code ${code ?? "unknown"}`));
      }
    });
    proc.on("error", reject);
  });
}
