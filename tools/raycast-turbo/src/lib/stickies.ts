import { LocalStorage } from "@raycast/api";

/** Build the LocalStorage key for a workflow param sticky. */
function key(workflowName: string, paramName: string): string {
  return `workflow.${workflowName}.param.${paramName}`;
}

/** Read a sticky value for a workflow param. Returns `undefined` if not set. */
export async function getSticky(
  workflowName: string,
  paramName: string
): Promise<string | undefined> {
  return LocalStorage.getItem<string>(key(workflowName, paramName));
}

/** Write a sticky value for a workflow param. */
export async function setSticky(
  workflowName: string,
  paramName: string,
  value: string
): Promise<void> {
  return LocalStorage.setItem(key(workflowName, paramName), value);
}

/**
 * Clear all sticky values for a workflow (does not affect other workflows).
 */
export async function clearStickies(workflowName: string): Promise<void> {
  const all = await LocalStorage.allItems();
  const prefix = `workflow.${workflowName}.param.`;
  await Promise.all(
    Object.keys(all)
      .filter((k) => k.startsWith(prefix))
      .map((k) => LocalStorage.removeItem(k))
  );
}
