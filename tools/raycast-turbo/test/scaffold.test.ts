import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// @raycast/api is aliased to test/__mocks__/raycast-api.ts via vitest.config.ts.
// child_process is mocked so importing run-workflow.tsx doesn't error.
vi.mock("child_process", () => ({
  exec: vi.fn(),
  spawn: vi.fn(() => ({ stderr: { on: vi.fn() }, on: vi.fn() })),
}));

describe("extension scaffold", () => {
  it("package.json has required structure", () => {
    const raw = readFileSync(join(__dirname, "..", "package.json"), "utf8");
    const pkg = JSON.parse(raw) as {
      name: string;
      commands: Array<{ name: string; mode: string }>;
    };
    expect(pkg.name).toBe("turbollm");
    expect(Array.isArray(pkg.commands)).toBe(true);
    // Fixed commands: run-workflow + running-workflows (plus any per-workflow commands added by turbo raycast sync)
    const fixedNames = pkg.commands.map((c: { name: string }) => c.name);
    expect(fixedNames).toContain("run-workflow");
    expect(fixedNames).toContain("running-workflows");
    const runWorkflow = pkg.commands.find((c: { name: string }) => c.name === "run-workflow");
    expect(runWorkflow.mode).toBe("view");
  });

  it("tsconfig.json is valid JSON", () => {
    const raw = readFileSync(join(__dirname, "..", "tsconfig.json"), "utf8");
    expect(() => JSON.parse(raw)).not.toThrow();
  });

  it("run-workflow.tsx exports a default React component", async () => {
    const mod = await import("../src/run-workflow");
    expect(typeof mod.default).toBe("function");
  });
});
