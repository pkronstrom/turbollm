import { exec } from "child_process";

export interface WorkflowParam {
  name: string;
  type: string;
  description?: string;
  required?: boolean;
  default?: string;
  enum_values?: string[];
  extensions?: string[];
}

export interface Workflow {
  name: string;
  description?: string;
  params?: WorkflowParam[];
}

/** Wraps exec with a promise that preserves {stdout, stderr} regardless of
 *  promisify.custom presence (making it mockable in vitest). */
function execAsync(cmd: string): Promise<{ stdout: string; stderr: string }> {
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

/** Run `<turboPath> workflows list --json` and return the parsed array. */
export async function listWorkflows(turboPath: string): Promise<Workflow[]> {
  let stdout: string;
  try {
    ({ stdout } = await execAsync(`"${turboPath}" workflows list --json`));
  } catch (err) {
    const e = err as NodeJS.ErrnoException & { stderr?: string };
    throw new Error(
      `turbo workflows list --json failed: ${e.stderr ?? e.message ?? String(err)}`
    );
  }
  return JSON.parse(stdout) as Workflow[];
}
