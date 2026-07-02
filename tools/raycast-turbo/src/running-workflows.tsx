import { Action, ActionPanel, Detail, List, Toast, showToast } from "@raycast/api";
import { readFile, readdir, unlink } from "fs/promises";
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
 * Returns whether `pid` refers to a process we can still see. `kill(pid, 0)`
 * sends no signal — it only probes for existence/permission.
 *
 * `ESRCH` means the process is definitely gone: the activity file outlived
 * its owner (e.g. the acquirer was hard-killed before it could clean up its
 * own state file). Any other error (notably `EPERM`, meaning the process
 * exists but belongs to another user) still counts as "alive" — we only
 * want to flag entries we're sure are stale.
 */
export function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    return code !== "ESRCH";
  }
}

/**
 * Send SIGTERM to the acquirer process at `pid`.
 *
 * Uses `process.kill` directly (no `/bin/kill` shell-out) so a bad PID
 * surfaces as a synchronous, catchable error instead of a `child_process`
 * round-trip.
 */
export function sendSigterm(pid: number): void {
  process.kill(pid, "SIGTERM");
}

/** Path to the on-disk activity file backing `activity`. */
export function activityFilePath(
  activity: AcquirerActivity,
  stateDir: string = DEFAULT_STATE_DIR
): string {
  return join(stateDir, `activity-${activity.id}.json`);
}

/**
 * Delete a stale activity file (its owner process is confirmed gone via
 * `isProcessAlive`). Used to clean up phantom "Running Workflows" entries
 * left behind by a crash or hard-kill.
 */
export async function cleanStaleActivity(
  activity: AcquirerActivity,
  stateDir: string = DEFAULT_STATE_DIR
): Promise<void> {
  await unlink(activityFilePath(activity, stateDir));
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

  async function handleStop(act: AcquirerActivity) {
    try {
      sendSigterm(act.owner_pid);
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: `Couldn't stop ${act.label}`,
        message: String(err),
      });
      return;
    }
    await load();
  }

  async function handleCleanStale(act: AcquirerActivity) {
    try {
      await cleanStaleActivity(act);
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Couldn't clean up stale entry",
        message: String(err),
      });
      return;
    }
    await load();
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
      {(activities ?? []).map((act) => {
        // A PID can be recycled by the OS after its original owner exits —
        // an activity file whose owner is gone (BUG-25) must not offer to
        // "Stop" whatever unrelated process now holds that PID.
        const alive = isProcessAlive(act.owner_pid);
        return (
          <List.Item
            key={act.id}
            title={`${act.label} (PID ${act.owner_pid})${alive ? "" : " — stale"}`}
            subtitle={`Started ${act.started_at}`}
            actions={
              <ActionPanel>
                {alive ? (
                  <Action title="Stop" onAction={() => handleStop(act)} />
                ) : (
                  <Action
                    title="Clean up Stale Entry"
                    onAction={() => handleCleanStale(act)}
                  />
                )}
                <Action title="Reload" onAction={load} />
              </ActionPanel>
            }
          />
        );
      })}
    </List>
  );
}
