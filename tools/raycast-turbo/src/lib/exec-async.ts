import { exec } from "child_process";

/**
 * Wraps `child_process.exec` in a promise that preserves `{stdout, stderr}`
 * and attaches `stderr` to the rejected error (not exposed on
 * `NodeJS.ErrnoException` by default), regardless of `promisify.custom`
 * presence — which also makes this mockable in vitest.
 *
 * Shared by `lib/binary-path.ts` and `lib/workflows.ts` — both used to carry
 * their own copy of this wrapper; this is the single source of truth now.
 */
export function execAsync(cmd: string): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    exec(cmd, (err, stdout, stderr) => {
      if (err) {
        const wrapped: NodeJS.ErrnoException & { stderr?: string } = err;
        wrapped.stderr = stderr;
        reject(wrapped);
      } else {
        resolve({ stdout, stderr });
      }
    });
  });
}
