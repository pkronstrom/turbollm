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
import { useEffect, useState } from "react";
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

/**
 * Normalize a stored sticky value for `<Form.FilePicker>`'s `defaultValue`,
 * which expects `string[]` (or `undefined` to skip).
 *
 * Raycast's FilePicker submits values as `string[]`, but our setSticky stores
 * via `LocalStorage.setItem<string>` — so older stickies from this extension
 * may have landed as either:
 *   - a JSON-encoded array (when the raw submit value flowed through unchanged)
 *   - a plain string path (when the value was coerced via template literal)
 * Accept both shapes and produce a `string[]` for the FilePicker.
 */
export function filePickerDefault(raw: unknown): string[] | undefined {
  if (Array.isArray(raw)) {
    const paths = raw.filter((p): p is string => typeof p === "string" && p.length > 0);
    return paths.length ? paths : undefined;
  }
  if (typeof raw === "string" && raw.length > 0) {
    return [raw];
  }
  return undefined;
}

// ── Component ──────────────────────────────────────────────────────────────────

interface WorkflowFormProps {
  workflow: Workflow;
  turboPath: string;
}

export function WorkflowForm({ workflow, turboPath }: WorkflowFormProps) {
  const { pop } = useNavigation();
  const [defaults, setDefaults] = useState<Record<string, string>>({});

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

  return (
    <Form
      actions={
        <ActionPanel>
          <Action.SubmitForm title="Run" onSubmit={handleSubmit} />
          <Action
            title="Reset Stickies"
            onAction={async () => {
              await clearStickies(workflow.name);
            }}
          />
        </ActionPanel>
      }
    >
      {(workflow.params ?? []).map((param) => renderField(param, defaults))}
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
      return (
        <Form.FilePicker
          key={param.name}
          id={param.name}
          title={param.name}
          allowMultipleSelection={false}
          extensions={getAllowedExtensions(param)}
          defaultValue={filePickerDefault(defaults[param.name])}
        />
      );
    case "directory":
      return (
        <Form.FilePicker
          key={param.name}
          id={param.name}
          title={param.name}
          allowMultipleSelection={false}
          canChooseFiles={false}
          canChooseDirectories
          defaultValue={filePickerDefault(defaults[param.name])}
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
