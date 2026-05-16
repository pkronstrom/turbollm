import { exec } from "child_process";
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

/**
 * Resolve the `turbo` binary path.
 *
 * Resolution order:
 * 1. `turboPath` extension preference (if set)
 * 2. `which turbo` on PATH
 * 3. Throw an error with a hint to set the preference
 */
export async function getBinaryPath(): Promise<string> {
  const prefs = getPreferenceValues<Preferences>();
  if (prefs.turboPath) {
    return prefs.turboPath;
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
