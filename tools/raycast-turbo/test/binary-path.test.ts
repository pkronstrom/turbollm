import { beforeEach, describe, expect, it, vi } from "vitest";
import { exec } from "child_process";
import { existsSync } from "fs";
import { getPreferenceValues } from "@raycast/api";

vi.mock("child_process", () => ({
  exec: vi.fn(),
}));

vi.mock("fs", () => ({
  existsSync: vi.fn(),
}));

// @raycast/api is aliased to test/__mocks__/raycast-api.ts via vitest.config.ts.
// We use vi.mocked() to control getPreferenceValues per test.

import { getBinaryPath } from "../src/lib/binary-path";

const mockExec = vi.mocked(exec);
const mockExists = vi.mocked(existsSync);
const mockPrefs = vi.mocked(getPreferenceValues);

describe("getBinaryPath", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Default: no preference set, no probes match.
    mockPrefs.mockReturnValue({} as ReturnType<typeof getPreferenceValues>);
    mockExists.mockReturnValue(false);
  });

  it("returns turboPath preference when set (no probes, no which call)", async () => {
    mockPrefs.mockReturnValue({ turboPath: "/opt/custom/turbo" } as ReturnType<typeof getPreferenceValues>);

    const result = await getBinaryPath();
    expect(result).toBe("/opt/custom/turbo");
    expect(mockExists).not.toHaveBeenCalled();
    expect(mockExec).not.toHaveBeenCalled();
  });

  it("returns ~/.local/bin/turbo when it exists on disk (no which call)", async () => {
    mockExists.mockImplementation((p) => String(p).endsWith("/.local/bin/turbo"));

    const result = await getBinaryPath();
    expect(result).toMatch(/\/\.local\/bin\/turbo$/);
    expect(mockExec).not.toHaveBeenCalled();
  });

  it("returns /opt/homebrew/bin/turbo when it is the only probe that matches", async () => {
    mockExists.mockImplementation((p) => p === "/opt/homebrew/bin/turbo");

    const result = await getBinaryPath();
    expect(result).toBe("/opt/homebrew/bin/turbo");
    expect(mockExec).not.toHaveBeenCalled();
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
