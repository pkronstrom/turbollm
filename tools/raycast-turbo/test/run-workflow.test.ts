import { beforeEach, describe, expect, it, vi } from "vitest";
import { exec } from "child_process";
import { getBinaryPath } from "../src/lib/binary-path";
import { listWorkflows } from "../src/lib/workflows";
import { resolvePreselectedWorkflow } from "../src/run-workflow";

// @raycast/api aliased via vitest.config.ts.
vi.mock("child_process", () => ({
  exec: vi.fn(),
  spawn: vi.fn(() => ({ stderr: { on: vi.fn() }, on: vi.fn() })),
}));

vi.mock("../src/lib/binary-path", () => ({
  getBinaryPath: vi.fn(),
}));

vi.mock("../src/lib/workflows", () => ({
  listWorkflows: vi.fn(),
}));

vi.mock("../src/lib/stickies", () => ({
  getSticky: vi.fn(async () => undefined),
  setSticky: vi.fn(async () => undefined),
  clearStickies: vi.fn(async () => undefined),
}));

vi.mock("../src/_generated_commands", () => ({
  GENERATED_COMMANDS: {
    "transcribe-file": "transcribe-file",
    "record-to-obsidian": "record-to-obsidian",
  },
}));

const mockGetBinaryPath = vi.mocked(getBinaryPath);
const mockListWorkflows = vi.mocked(listWorkflows);

describe("run-workflow: default export is a React component", () => {
  it("exports a default function", async () => {
    const mod = await import("../src/run-workflow");
    expect(typeof mod.default).toBe("function");
  });
});

describe("run-workflow: error-path — Detail view on subprocess failure", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("getBinaryPath rejection propagates as error string", async () => {
    mockGetBinaryPath.mockRejectedValue(
      new Error("couldn't find turbo on PATH — set turboPath in extension preferences")
    );
    await expect(getBinaryPath()).rejects.toThrow("couldn't find turbo on PATH");
  });

  it("listWorkflows rejection propagates as error string", async () => {
    mockGetBinaryPath.mockResolvedValue("/usr/local/bin/turbo");
    mockListWorkflows.mockRejectedValue(
      new Error("turbo workflows list --json failed: TOML syntax error")
    );

    await expect(
      (async () => {
        const path = await getBinaryPath();
        await listWorkflows(path);
      })()
    ).rejects.toThrow("TOML syntax error");
  });

  it("error message contains stderr from subprocess failure", async () => {
    const stderrMsg = "Error: invalid TOML at line 42";
    mockListWorkflows.mockRejectedValue(new Error(`turbo workflows list --json failed: ${stderrMsg}`));

    let caught = "";
    try {
      await listWorkflows("/turbo");
    } catch (err) {
      caught = String(err);
    }
    expect(caught).toContain("TOML");
  });
});

describe("run-workflow: successful load", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("listWorkflows returns parsed workflow array", async () => {
    const wfs = [
      { name: "transcribe-file", description: "Audio → text", params: [] },
      { name: "record-to-obsidian", description: "Record", params: [] },
    ];
    mockGetBinaryPath.mockResolvedValue("/usr/local/bin/turbo");
    mockListWorkflows.mockResolvedValue(wfs);

    const path = await getBinaryPath();
    const result = await listWorkflows(path);
    expect(result).toHaveLength(2);
    expect(result[0].name).toBe("transcribe-file");
  });
});

describe("resolvePreselectedWorkflow: per-workflow command dispatch", () => {
  const commands = {
    "transcribe-file": "transcribe-file",
    "record-to-obsidian": "record-to-obsidian",
  };

  it("returns the workflow name when commandName matches a per-workflow command", () => {
    expect(resolvePreselectedWorkflow("transcribe-file", commands)).toBe("transcribe-file");
    expect(resolvePreselectedWorkflow("record-to-obsidian", commands)).toBe("record-to-obsidian");
  });

  it("returns null when commandName is the generic run-workflow command", () => {
    expect(resolvePreselectedWorkflow("run-workflow", commands)).toBeNull();
  });

  it("returns null when commandName is not in the map", () => {
    expect(resolvePreselectedWorkflow("unknown-command", commands)).toBeNull();
  });

  it("returns null for an empty GENERATED_COMMANDS map", () => {
    expect(resolvePreselectedWorkflow("transcribe-file", {})).toBeNull();
  });
});
