import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const port = 8899;
const baseUrl = `http://127.0.0.1:${port}`;
const startupTimeoutMs = 180_000;

let startup: Promise<void> | undefined;

async function servedModel(): Promise<string | undefined> {
  try {
    const response = await fetch(`${baseUrl}/v1/models`);
    if (!response.ok) return undefined;
    const body = (await response.json()) as {
      data?: Array<{ id?: string }>;
    };
    return body.data?.[0]?.id;
  } catch {
    return undefined;
  }
}

async function waitForModel(modelId: string): Promise<void> {
  const deadline = Date.now() + startupTimeoutMs;
  while (Date.now() < deadline) {
    const active = await servedModel();
    if (active === modelId) return;
    if (active) {
      throw new Error(
        `Turbo server is already serving ${active} on port ${port}; ` +
          `select it or switch the model in the Turbo panel first.`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(
    `Timed out waiting for turbo serve ${modelId} on port ${port}.`,
  );
}

async function ensureServer(modelId: string): Promise<void> {
  const active = await servedModel();
  if (active === modelId) return;
  if (active) {
    throw new Error(
      `Turbo server is already serving ${active} on port ${port}; ` +
        `select it or switch the model in the Turbo panel first.`,
    );
  }

  const child = spawn("turbo", ["serve", modelId, "--port", String(port)], {
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  await waitForModel(modelId);
}

export default function turboAutoserve(pi: ExtensionAPI): void {
  pi.on("before_provider_request", async (_event, ctx) => {
    const model = ctx.models.current();
    if (!model || model.provider !== "turbo") return;

    if (!startup) {
      startup = ensureServer(model.id).finally(() => {
        startup = undefined;
      });
    }
    await startup;
  });
}
