import {
  Action,
  ActionPanel,
  Detail,
  List,
  environment,
  useNavigation,
} from "@raycast/api";
import { useEffect, useState } from "react";
import { WorkflowForm } from "./components/workflow-form";
import { getBinaryPath } from "./lib/binary-path";
import { Workflow, listWorkflows } from "./lib/workflows";
import { GENERATED_COMMANDS } from "./_generated_commands";

/**
 * Resolve whether the current command was launched as a per-workflow shortcut.
 *
 * Returns the workflow name to preselect, or null if this is the generic
 * "run-workflow" command (where the user should see the full list).
 */
export function resolvePreselectedWorkflow(
  commandName: string,
  commands: Record<string, string>
): string | null {
  return commands[commandName] ?? null;
}

export default function RunWorkflow() {
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [turboPath, setTurboPath] = useState<string>("");
  const { push } = useNavigation();

  const preselectedName = resolvePreselectedWorkflow(environment.commandName, GENERATED_COMMANDS);

  useEffect(() => {
    load();
  }, []);

  // When launched from a per-workflow command, push the form as soon as data is ready.
  useEffect(() => {
    if (preselectedName && workflows !== null && turboPath) {
      const wf = workflows.find((w) => w.name === preselectedName);
      if (wf) {
        push(<WorkflowForm workflow={wf} turboPath={turboPath} />);
      }
    }
  }, [workflows, turboPath, preselectedName]);

  async function load() {
    setError(null);
    setWorkflows(null);
    try {
      const path = await getBinaryPath();
      setTurboPath(path);
      const wfs = await listWorkflows(path);
      setWorkflows(wfs);
    } catch (err) {
      setError(String(err));
    }
  }

  if (error !== null) {
    return (
      <Detail
        markdown={`## Error loading workflows\n\n\`\`\`\n${error}\n\`\`\``}
        actions={
          <ActionPanel>
            <Action title="Reload" onAction={load} />
          </ActionPanel>
        }
      />
    );
  }

  // When launched via a per-workflow Raycast command (record-to-obsidian,
  // transcribe-file, etc.), show a Detail loading view rather than the full
  // workflows list. Avoids a brief flash of the list before the useEffect
  // pushes the form on top.
  if (preselectedName) {
    return <Detail isLoading={workflows === null} markdown="Loading…" />;
  }

  return (
    <List isLoading={workflows === null}>
      {(workflows ?? []).map((wf) => (
        <List.Item
          key={wf.name}
          title={wf.name}
          subtitle={wf.description}
          actions={
            <ActionPanel>
              <Action
                title="Run Workflow"
                onAction={() =>
                  push(<WorkflowForm workflow={wf} turboPath={turboPath} />)
                }
              />
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}
