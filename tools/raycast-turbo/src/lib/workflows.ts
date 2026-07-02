import { execAsync } from "./exec-async";

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
