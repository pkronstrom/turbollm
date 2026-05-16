import { describe, expect, it, vi } from "vitest";

// @raycast/api is aliased via vitest.config.ts.
// Mock child_process and stickies so the module loads without side effects.
vi.mock("child_process", () => ({
  spawn: vi.fn(() => ({ stderr: { on: vi.fn() }, on: vi.fn() })),
}));

vi.mock("../src/lib/stickies", () => ({
  getSticky: vi.fn(async () => undefined),
  setSticky: vi.fn(async () => undefined),
  clearStickies: vi.fn(async () => undefined),
}));

import {
  buildArgsForSubmit,
  getAllowedExtensions,
} from "../src/components/workflow-form";
import type { WorkflowParam } from "../src/lib/workflows";

// ── buildArgsForSubmit ─────────────────────────────────────────────────────────

describe("buildArgsForSubmit", () => {
  it("produces the expected argv array with multiple params", () => {
    const args = buildArgsForSubmit("transcribe-file", {
      file: "/tmp/x.wav",
      vault: "/vault",
    });
    expect(args).toEqual([
      "workflows",
      "run",
      "transcribe-file",
      "--param",
      "file=/tmp/x.wav",
      "--param",
      "vault=/vault",
    ]);
  });

  it("omits empty string values", () => {
    const args = buildArgsForSubmit("transcribe-file", {
      file: "/tmp/x.wav",
      vault: "",
    });
    expect(args).toEqual([
      "workflows",
      "run",
      "transcribe-file",
      "--param",
      "file=/tmp/x.wav",
    ]);
  });

  it("produces only the subcommand when all values are empty", () => {
    const args = buildArgsForSubmit("my-workflow", { a: "", b: "" });
    expect(args).toEqual(["workflows", "run", "my-workflow"]);
  });

  it("handles a single param correctly", () => {
    const args = buildArgsForSubmit("record-to-obsidian", { vault: "/Users/me/Obsidian" });
    expect(args).toEqual([
      "workflows",
      "run",
      "record-to-obsidian",
      "--param",
      "vault=/Users/me/Obsidian",
    ]);
  });
});

// ── getAllowedExtensions ───────────────────────────────────────────────────────

describe("getAllowedExtensions", () => {
  it("returns extensions list for a file param with extensions", () => {
    const param: WorkflowParam = {
      name: "file",
      type: "file",
      extensions: ["m4a", "wav", "mp3"],
    };
    expect(getAllowedExtensions(param)).toEqual(["m4a", "wav", "mp3"]);
  });

  it("returns undefined for a directory param", () => {
    const param: WorkflowParam = { name: "vault", type: "directory" };
    expect(getAllowedExtensions(param)).toBeUndefined();
  });

  it("returns undefined for a file param with no extensions list", () => {
    const param: WorkflowParam = { name: "file", type: "file" };
    expect(getAllowedExtensions(param)).toBeUndefined();
  });

  it("returns undefined for a file param with an empty extensions array", () => {
    const param: WorkflowParam = { name: "file", type: "file", extensions: [] };
    expect(getAllowedExtensions(param)).toBeUndefined();
  });

  it("returns undefined for a string param", () => {
    const param: WorkflowParam = { name: "title", type: "string" };
    expect(getAllowedExtensions(param)).toBeUndefined();
  });
});
