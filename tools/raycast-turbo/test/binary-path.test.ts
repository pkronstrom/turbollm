import { beforeEach, describe, expect, it, vi } from "vitest";
import { exec } from "child_process";
import { accessSync } from "fs";
import { getPreferenceValues } from "@raycast/api";

vi.mock("child_process", () => ({
  exec: vi.fn(),
}));

vi.mock("fs", () => ({
  accessSync: vi.fn(),
  constants: { X_OK: 1 },
}));

// @raycast/api is aliased to test/__mocks__/raycast-api.ts via vitest.config.ts.
// We use vi.mocked() to control getPreferenceValues per test.

import { getBinaryPath } from "../src/lib/binary-path";

const mockExec = vi.mocked(exec);
const mockAccess = vi.mocked(accessSync);
const mockPrefs = vi.mocked(getPreferenceValues);

describe("getBinaryPath", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Default: no preference set, no probes match (accessSync throws for
    // every path, mirroring a nonexistent-or-non-executable file).
    mockPrefs.mockReturnValue({} as ReturnType<typeof getPreferenceValues>);
    mockAccess.mockImplementation(() => {
      throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
    });
  });

  it("returns turboPath preference when set (no probes, no which call)", async () => {
    mockPrefs.mockReturnValue({ turboPath: "/opt/custom/turbo" } as ReturnType<typeof getPreferenceValues>);

    const result = await getBinaryPath();
    expect(result).toBe("/opt/custom/turbo");
    expect(mockAccess).not.toHaveBeenCalled();
    expect(mockExec).not.toHaveBeenCalled();
  });

  it("returns ~/.local/bin/turbo when it exists on disk (no which call)", async () => {
    mockAccess.mockImplementation((p) => {
      if (String(p).endsWith("/.local/bin/turbo")) return undefined;
      throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
    });

    const result = await getBinaryPath();
    expect(result).toMatch(/\/\.local\/bin\/turbo$/);
    expect(mockExec).not.toHaveBeenCalled();
  });

  it("returns /opt/homebrew/bin/turbo when it is the only probe that matches", async () => {
    mockAccess.mockImplementation((p) => {
      if (p === "/opt/homebrew/bin/turbo") return undefined;
      throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
    });

    const result = await getBinaryPath();
    expect(result).toBe("/opt/homebrew/bin/turbo");
    expect(mockExec).not.toHaveBeenCalled();
  });

  it("does not treat an existing-but-non-executable file as a match", async () => {
    // accessSync(path, X_OK) throws EACCES for a file that exists but lacks
    // the execute bit — isExecutable() must say false, not true.
    mockAccess.mockImplementation((p) => {
      if (p === "/opt/homebrew/bin/turbo") {
        throw Object.assign(new Error("EACCES"), { code: "EACCES" });
      }
      throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
    });
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(null, "/some/exotic/path/turbo\n", "");
      return {} as ReturnType<typeof exec>;
    });

    const result = await getBinaryPath();
    expect(result).toBe("/some/exotic/path/turbo");
  });

  it("falls back to `which turbo` when no probe matches", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(null, "/some/exotic/path/turbo\n", "");
      return {} as ReturnType<typeof exec>;
    });

    const result = await getBinaryPath();
    expect(result).toBe("/some/exotic/path/turbo");
    expect(mockExec).toHaveBeenCalledOnce();
    const cmd = (mockExec.mock.calls[0] as [string, Function])[0];
    expect(cmd).toBe("which turbo");
  });

  it("throws when no probe matches and which fails", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(new Error("which: no turbo in PATH"), "", "");
      return {} as ReturnType<typeof exec>;
    });

    await expect(getBinaryPath()).rejects.toThrow("couldn't find turbo on PATH");
  });

  it("throws when no probe matches and which returns an empty string", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(null, "  \n", "");
      return {} as ReturnType<typeof exec>;
    });

    await expect(getBinaryPath()).rejects.toThrow("couldn't find turbo on PATH");
  });
});
