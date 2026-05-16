import { Action, ActionPanel, Detail, List, useNavigation } from "@raycast/api";
import { exec } from "child_process";
import { readFile, readdir } from "fs/promises";
import { homedir } from "os";
import { join } from "path";
import { useEffect, useState } from "react";

// ── Activity model ─────────────────────────────────────────────────────────────

export interface AcquirerActivity {
  id: string;
  kind: string;
  label: string;
  owner_pid: number;
  started_at: string;
  parent_id?: string;
  [key: string]: unknown;
}

const DEFAULT_STATE_DIR = join(homedir(), ".turbollm", "state");

/**
 * Read `stateDir` and return all activity files whose `kind` is `"acquirer"`.
 * Returns an empty array when the directory doesn't exist or cannot be read.
 */
export async function scanActivities(
  stateDir: string = DEFAULT_STATE_DIR
): Promise<AcquirerActivity[]> {
  let entries: string[];
  try {
    entries = await readdir(stateDir);
  } catch {
    // Dir doesn't exist or isn't readable → no running workflows
    return [];
  }

  const activities: AcquirerActivity[] = [];
  for (const entry of entries) {
    if (!entry.endsWith(".json")) continue;
    try {
      const raw = await readFile(join(stateDir, entry), "utf8");
      const act = JSON.parse(raw) as AcquirerActivity;
      if (act.kind === "acquirer") {
        activities.push(act);
      }
    } catch {
      // Skip malformed files
    }
  }
  return activities;
}

/**
 * Send SIGTERM to the acquirer process at `pid`.
 * Uses `/bin/kill` directly so it doesn't require `turbo` on PATH.
 */
export function sendSigterm(pid: number): Promise<void> {
  return new Promise((resolve, reject) => {
    exec(`/bin/kill -TERM ${pid}`, (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

// ── Component ──────────────────────────────────────────────────────────────────

export default function RunningWorkflows() {
  const [activities, setActivities] = useState<AcquirerActivity[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setError(null);
    setActivities(null);
    try {
      const acts = await scanActivities();
      setActivities(acts);
    } catch (err) {
      setError(String(err));
    }
  }

  if (error !== null) {
    return (
      <Detail
        markdown={`## Error scanning activities\n\n\`\`\`\n${error}\n\`\`\``}
        actions={
          <ActionPanel>
            <Action title="Reload" onAction={load} />
          </ActionPanel>
        }
      />
    );
  }

  if (activities !== null && activities.length === 0) {
    return (
      <List>
        <List.Item
          title="No workflows running"
          subtitle="Start a workflow to see it here"
          actions={
            <ActionPanel>
              <Action title="Reload" onAction={load} />
            </ActionPanel>
          }
        />
      </List>
    );
  }

  return (
    <List isLoading={activities === null}>
      {(activities ?? []).map((act) => (
        <List.Item
          key={act.id}
          title={`${act.label} (PID ${act.owner_pid})`}
          subtitle={`Started ${act.started_at}`}
          actions={
            <ActionPanel>
              <Action
                title="Stop"
                onAction={async () => {
                  await sendSigterm(act.owner_pid);
                  await load();
                }}
              />
              <Action title="Reload" onAction={load} />
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}
