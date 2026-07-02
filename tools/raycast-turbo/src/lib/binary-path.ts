import { accessSync, constants } from "fs";
import { homedir } from "os";
import { join } from "path";
import { getPreferenceValues } from "@raycast/api";
import { execAsync } from "./exec-async";

interface Preferences {
  turboPath?: string;
}

/**
 * True if `path` exists AND is executable by the current user.
 * `existsSync` alone would say "yes" for a non-executable file (e.g. a
 * stray non-executable `turbo` left behind by a failed install), so probing
 * with `X_OK` is what the function name actually promises.
 */
function isExecutable(path: string): boolean {
  try {
    accessSync(path, constants.X_OK);
    return true;
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
