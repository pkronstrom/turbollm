import {
  Action,
  ActionPanel,
  Form,
  Toast,
  showToast,
  useNavigation,
} from "@raycast/api";
import { spawn } from "child_process";
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
 * Build the argv for `turbo workflows run <name> --param k=v ...`.
 * Empty values are omitted so the CLI can apply its own defaults.
 */
export function buildArgsForSubmit(
  workflowName: string,
  values: Record<string, string>
): string[] {
  const args = ["workflows", "run", workflowName];
  for (const [k, v] of Object.entries(values)) {
    if (v != null && v !== "") {
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

  async function handleSubmit(values: Record<string, string>) {
    // Persist non-empty stickies before spawning
    for (const [k, v] of Object.entries(values)) {
      if (v) await setSticky(workflow.name, k, v);
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

function runSubprocess(execPath: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const proc = spawn(execPath, args);
    let stderr = "";
    proc.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    proc.on("close", (code: number | null) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(stderr.trim() || `exited with code ${code ?? "unknown"}`));
      }
    });
    proc.on("error", reject);
  });
}
