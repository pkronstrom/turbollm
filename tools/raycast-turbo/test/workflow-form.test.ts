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
  filePickerDefault,
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

// ── T-21: acquired-param handling ─────────────────────────────────────────────

describe("acquired param handling", () => {
  it("acquired params absent from values produce no --param flags", () => {
    // Acquired params (audio-recording, screen-recording, etc.) render as
    // Form.Description — no form field, so they don't appear in submit values.
    const args = buildArgsForSubmit("record-to-obsidian", {
      vault: "/vault",
      // no "audio" key — it was an acquired param with no form field
    });
    expect(args).not.toContain("audio");
    expect(args).toEqual([
      "workflows",
      "run",
      "record-to-obsidian",
      "--param",
      "vault=/vault",
    ]);
  });

  it("submit with only acquired params (no user-fillable fields) produces minimal args", () => {
    const args = buildArgsForSubmit("record-only", {});
    expect(args).toEqual(["workflows", "run", "record-only"]);
  });
});


// ── filePickerDefault ──────────────────────────────────────────────────────────
//
// Form.FilePicker's defaultValue is `string[]`. Submitted values from
// FilePicker are also string[]. setSticky persists whatever it receives;
// stored stickies may therefore be either a string (legacy) or string[]
// (post-storage round-trip via FilePicker submit). Cover both.

describe("filePickerDefault", () => {
  it("wraps a plain string sticky into a single-element array", () => {
    expect(filePickerDefault("/Users/me/vault")).toEqual(["/Users/me/vault"]);
  });

  it("passes through a string[] sticky unchanged", () => {
    expect(filePickerDefault(["/Users/me/vault"])).toEqual(["/Users/me/vault"]);
  });

  it("returns undefined for empty string", () => {
    expect(filePickerDefault("")).toBeUndefined();
  });

  it("returns undefined for empty array", () => {
    expect(filePickerDefault([])).toBeUndefined();
  });

  it("returns undefined for nullish", () => {
    expect(filePickerDefault(undefined)).toBeUndefined();
    expect(filePickerDefault(null)).toBeUndefined();
  });

  it("filters out non-string entries in arrays", () => {
    expect(filePickerDefault(["/path", null, "", 42, "/other"])).toEqual([
      "/path",
      "/other",
    ]);
  });

  it("returns undefined when all array entries are filtered out", () => {
    expect(filePickerDefault(["", null, undefined])).toBeUndefined();
  });
});


// ── normalizeSubmitValue ──────────────────────────────────────────────────────
//
// Form.FilePicker submits string[]. LocalStorage rejects non-primitives.
// Our CLI expects single string per --param. Coerce at the boundary.

import { normalizeSubmitValue } from "../src/components/workflow-form";

describe("normalizeSubmitValue", () => {
  it("returns strings unchanged", () => {
    expect(normalizeSubmitValue("hello")).toBe("hello");
    expect(normalizeSubmitValue("")).toBe("");
  });

  it("returns first non-empty entry from a string array", () => {
    expect(normalizeSubmitValue(["/Users/me/vault"])).toBe("/Users/me/vault");
    expect(normalizeSubmitValue(["/a", "/b"])).toBe("/a");
    expect(normalizeSubmitValue(["", "/b"])).toBe("/b");
  });

  it("returns empty string for empty array", () => {
    expect(normalizeSubmitValue([])).toBe("");
  });

  it("returns empty string for nullish", () => {
    expect(normalizeSubmitValue(null)).toBe("");
    expect(normalizeSubmitValue(undefined)).toBe("");
  });

  it("coerces numbers and booleans to string", () => {
    expect(normalizeSubmitValue(42)).toBe("42");
    expect(normalizeSubmitValue(true)).toBe("true");
  });

  it("returns empty string for objects (defensive)", () => {
    expect(normalizeSubmitValue({})).toBe("");
  });
});

describe("buildArgsForSubmit with array values (FilePicker submits)", () => {
  it("unwraps single-element string arrays into the --param value", () => {
    const args = buildArgsForSubmit("record-to-obsidian", {
      vault: ["/Users/me/Obsidian"],
    } as unknown as Record<string, string>);
    expect(args).toEqual([
      "workflows",
      "run",
      "record-to-obsidian",
      "--param",
      "vault=/Users/me/Obsidian",
    ]);
  });

  it("omits empty arrays", () => {
    const args = buildArgsForSubmit("record-to-obsidian", {
      vault: [],
      file: "/tmp/x.wav",
    } as unknown as Record<string, string>);
    expect(args).toEqual([
      "workflows",
      "run",
      "record-to-obsidian",
      "--param",
      "file=/tmp/x.wav",
    ]);
  });
});
