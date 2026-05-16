import {
  Action,
  ActionPanel,
  Detail,
  List,
  useNavigation,
} from "@raycast/api";
import { useEffect, useState } from "react";
import { WorkflowForm } from "./components/workflow-form";
import { getBinaryPath } from "./lib/binary-path";
import { Workflow, listWorkflows } from "./lib/workflows";

export default function RunWorkflow() {
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [turboPath, setTurboPath] = useState<string>("");
  const { push } = useNavigation();

  useEffect(() => {
    load();
  }, []);

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
