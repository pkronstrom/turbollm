import { beforeEach, describe, expect, it, vi } from "vitest";
import { exec } from "child_process";
import { getPreferenceValues } from "@raycast/api";

vi.mock("child_process", () => ({
  exec: vi.fn(),
}));

// @raycast/api is aliased to test/__mocks__/raycast-api.ts via vitest.config.ts.
// We use vi.mocked() to control getPreferenceValues per test.

import { getBinaryPath } from "../src/lib/binary-path";

const mockExec = vi.mocked(exec);
const mockPrefs = vi.mocked(getPreferenceValues);

describe("getBinaryPath", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Default: no preference set
    mockPrefs.mockReturnValue({} as ReturnType<typeof getPreferenceValues>);
  });

  it("returns turboPath preference when set (no which call)", async () => {
    mockPrefs.mockReturnValue({ turboPath: "/opt/custom/turbo" } as ReturnType<typeof getPreferenceValues>);

    const result = await getBinaryPath();
    expect(result).toBe("/opt/custom/turbo");
    expect(mockExec).not.toHaveBeenCalled();
  });

  it("falls back to `which turbo` when preference is not set", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(null, "/Users/user/.local/bin/turbo\n", "");
      return {} as ReturnType<typeof exec>;
    });

    const result = await getBinaryPath();
    expect(result).toBe("/Users/user/.local/bin/turbo");
    expect(mockExec).toHaveBeenCalledOnce();
    const cmd = (mockExec.mock.calls[0] as [string, Function])[0];
    expect(cmd).toBe("which turbo");
  });

  it("throws when preference is empty and which fails", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(new Error("which: no turbo in PATH"), "", "");
      return {} as ReturnType<typeof exec>;
    });

    await expect(getBinaryPath()).rejects.toThrow("couldn't find turbo on PATH");
  });

  it("throws when which returns an empty string", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(null, "  \n", "");
      return {} as ReturnType<typeof exec>;
    });

    await expect(getBinaryPath()).rejects.toThrow("couldn't find turbo on PATH");
  });
});
