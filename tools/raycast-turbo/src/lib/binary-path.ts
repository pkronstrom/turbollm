import { exec } from "child_process";
import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { getPreferenceValues } from "@raycast/api";

interface Preferences {
  turboPath?: string;
}

function execAsync(cmd: string): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    exec(cmd, (err, stdout, stderr) => {
      if (err) {
        reject(err);
      } else {
        resolve({ stdout, stderr });
      }
    });
  });
}

function isExecutable(path: string): boolean {
  try {
    return existsSync(path);
  } catch {
    return false;
  }
}

/**
 * Resolve the `turbo` binary path.
 *
 * Raycast extensions run with the macOS login environment — they do NOT
 * inherit shell-profile PATH munging (no `~/.zshrc`/`~/.zprofile`), so a
 * bare `which turbo` typically misses `~/.local/bin/`. Probe the canonical
 * turbollm install locations directly before falling back to `which`.
 *
 * Resolution order:
 * 1. `turboPath` extension preference (if set)
 * 2. `~/.local/bin/turbo` (turbo sidecar's symlink location)
 * 3. `/usr/local/bin/turbo` (manual install)
 * 4. `/opt/homebrew/bin/turbo` (Homebrew on Apple Silicon)
 * 5. `which turbo` on PATH
 * 6. Throw an error with a hint to set the preference
 */
export async function getBinaryPath(): Promise<string> {
  const prefs = getPreferenceValues<Preferences>();
  if (prefs.turboPath) {
    return prefs.turboPath;
  }

  const probes = [
    join(homedir(), ".local", "bin", "turbo"),
    "/usr/local/bin/turbo",
    "/opt/homebrew/bin/turbo",
  ];
  for (const path of probes) {
    if (isExecutable(path)) {
      return path;
    }
  }

  let stdout: string;
  try {
    ({ stdout } = await execAsync("which turbo"));
  } catch {
    throw new Error(
      "couldn't find turbo on PATH — set turboPath in extension preferences"
    );
  }

  const resolved = stdout.trim();
  if (!resolved) {
    throw new Error(
      "couldn't find turbo on PATH — set turboPath in extension preferences"
    );
  }
  return resolved;
}
