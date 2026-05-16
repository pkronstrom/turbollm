import { beforeEach, describe, expect, it, vi } from "vitest";
import { exec } from "child_process";

// @raycast/api aliased via vitest.config.ts (workflows.ts doesn't import it, but
// other transitively loaded modules might).
vi.mock("child_process", () => ({
  exec: vi.fn(),
}));

import { listWorkflows } from "../src/lib/workflows";

const mockExec = vi.mocked(exec);

describe("listWorkflows", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("parses CLI JSON array correctly", async () => {
    const payload = [
      { name: "transcribe-file", description: "Audio → text", params: [] },
      { name: "record-to-obsidian", description: "Record and summarise", params: [] },
    ];
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(null, JSON.stringify(payload), "");
      return {} as ReturnType<typeof exec>;
    });

    const result = await listWorkflows("/usr/local/bin/turbo");
    expect(result).toHaveLength(2);
    expect(result[0].name).toBe("transcribe-file");
    expect(result[1].name).toBe("record-to-obsidian");
  });

  it("surfaces error message on subprocess failure", async () => {
    const stderr = "turbo: command not found";
    mockExec.mockImplementation((_cmd, cb) => {
      const err = Object.assign(new Error("exit 127"), { stderr });
      (cb as Function)(err, "", stderr);
      return {} as ReturnType<typeof exec>;
    });

    await expect(listWorkflows("/usr/local/bin/turbo")).rejects.toThrow("failed");
  });

  it("builds the correct CLI invocation including path and subcommand", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(null, "[]", "");
      return {} as ReturnType<typeof exec>;
    });

    await listWorkflows("/custom/path/turbo");
    expect(mockExec).toHaveBeenCalledOnce();
    const cmd = (mockExec.mock.calls[0] as [string, Function])[0];
    expect(cmd).toContain("workflows list --json");
    expect(cmd).toContain("custom/path/turbo");
  });
});
