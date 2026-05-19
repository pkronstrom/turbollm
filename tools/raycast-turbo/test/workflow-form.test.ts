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


// ── enrichedPath ──────────────────────────────────────────────────────────────
//
// Raycast's stripped login env typically lacks `/opt/homebrew/bin` and
// `~/.local/bin`. Scripts that shell out to `pi`, `turbo`, etc. by bare
// name fail with command-not-found and `set -e` aborts. Augment PATH at
// the spawn layer so workflow scripts find the expected binaries.

import { enrichedPath } from "../src/components/workflow-form";

// ── parseStatusOutput ─────────────────────────────────────────────────────────
//
// Drives the Run/Stop action swap. Must never throw and must collapse every
// non-running shape to `null` so the UI stays in the "Run" state by default.

import { parseStatusOutput } from "../src/components/workflow-form";

describe("parseStatusOutput", () => {
  it("returns the pid when the workflow is running", () => {
    expect(parseStatusOutput('{"state":"running","pid":42}')).toBe(42);
  });

  it("returns null when idle", () => {
    expect(parseStatusOutput('{"state":"idle","pid":null}')).toBeNull();
  });

  it("returns null when running but pid is missing or not a number", () => {
    expect(parseStatusOutput('{"state":"running","pid":null}')).toBeNull();
    expect(parseStatusOutput('{"state":"running"}')).toBeNull();
    expect(parseStatusOutput('{"state":"running","pid":"42"}')).toBeNull();
  });

  it("returns null on malformed JSON", () => {
    expect(parseStatusOutput("")).toBeNull();
    expect(parseStatusOutput("not json")).toBeNull();
    expect(parseStatusOutput("{")).toBeNull();
  });

  it("returns null when state is something unexpected", () => {
    expect(parseStatusOutput('{"state":"weird","pid":99}')).toBeNull();
  });
});


describe("enrichedPath", () => {
  it("appends ~/.local/bin, /opt/homebrew/bin, /usr/local/bin", () => {
    const result = enrichedPath("/usr/bin:/bin", "/Users/test");
    const parts = result.split(":");
    expect(parts).toContain("/Users/test/.local/bin");
    expect(parts).toContain("/opt/homebrew/bin");
    expect(parts).toContain("/usr/local/bin");
  });

  it("preserves existing PATH entries first", () => {
    const result = enrichedPath("/usr/bin:/bin", "/Users/test");
    const parts = result.split(":");
    expect(parts[0]).toBe("/usr/bin");
    expect(parts[1]).toBe("/bin");
  });

  it("does not duplicate entries already present", () => {
    const result = enrichedPath("/opt/homebrew/bin:/usr/bin", "/Users/test");
    const parts = result.split(":");
    expect(parts.filter((p) => p === "/opt/homebrew/bin")).toHaveLength(1);
  });

  it("handles undefined PATH (no existing entries)", () => {
    const result = enrichedPath(undefined, "/Users/test");
    expect(result).toBe(
      "/Users/test/.local/bin:/opt/homebrew/bin:/usr/local/bin"
    );
  });

  it("handles empty PATH", () => {
    const result = enrichedPath("", "/Users/test");
    expect(result).toBe(
      "/Users/test/.local/bin:/opt/homebrew/bin:/usr/local/bin"
    );
  });
});
