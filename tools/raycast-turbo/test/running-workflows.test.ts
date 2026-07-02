import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFile, readdir, unlink } from "fs/promises";

vi.mock("fs/promises", () => ({
  readdir: vi.fn(),
  readFile: vi.fn(),
  unlink: vi.fn(),
}));

// @raycast/api aliased via vitest.config.ts
import {
  activityFilePath,
  cleanStaleActivity,
  isProcessAlive,
  scanActivities,
  sendSigterm,
} from "../src/running-workflows";

const mockReaddir = vi.mocked(readdir);
const mockReadFile = vi.mocked(readFile);
const mockUnlink = vi.mocked(unlink);

describe("scanActivities", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns acquirer-kind activities from the state directory", async () => {
    const activity1 = {
      id: "abc-1",
      kind: "acquirer",
      label: "audio",
      owner_pid: 41010,
      started_at: "2026-01-15T10:00:00Z",
    };
    const activity2 = {
      id: "abc-2",
      kind: "acquirer",
      label: "screen",
      owner_pid: 41011,
      started_at: "2026-01-15T10:00:01Z",
    };

    mockReaddir.mockResolvedValue(["abc-1.json", "abc-2.json"] as unknown as Awaited<ReturnType<typeof readdir>>);
    mockReadFile
      .mockResolvedValueOnce(JSON.stringify(activity1) as unknown as Awaited<ReturnType<typeof readFile>>)
      .mockResolvedValueOnce(JSON.stringify(activity2) as unknown as Awaited<ReturnType<typeof readFile>>);

    const result = await scanActivities("/tmp/state");
    expect(result).toHaveLength(2);
    expect(result[0].label).toBe("audio");
    expect(result[0].owner_pid).toBe(41010);
    expect(result[1].label).toBe("screen");
  });

  it("filters out non-acquirer activities", async () => {
    const workflow = { id: "wf-1", kind: "workflow", label: "wf", owner_pid: 9999, started_at: "" };
    const acquirer = { id: "ac-1", kind: "acquirer", label: "audio", owner_pid: 1234, started_at: "" };

    mockReaddir.mockResolvedValue(["wf-1.json", "ac-1.json"] as unknown as Awaited<ReturnType<typeof readdir>>);
    mockReadFile
      .mockResolvedValueOnce(JSON.stringify(workflow) as unknown as Awaited<ReturnType<typeof readFile>>)
      .mockResolvedValueOnce(JSON.stringify(acquirer) as unknown as Awaited<ReturnType<typeof readFile>>);

    const result = await scanActivities("/tmp/state");
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("ac-1");
  });

  it("returns empty array when directory does not exist", async () => {
    mockReaddir.mockRejectedValue(Object.assign(new Error("ENOENT"), { code: "ENOENT" }));

    const result = await scanActivities("/nonexistent/state");
    expect(result).toEqual([]);
  });

  it("returns empty array for empty state directory", async () => {
    mockReaddir.mockResolvedValue([] as unknown as Awaited<ReturnType<typeof readdir>>);

    const result = await scanActivities("/tmp/state");
    expect(result).toEqual([]);
  });

  it("skips non-json files", async () => {
    mockReaddir.mockResolvedValue(["README.md", "activity.json"] as unknown as Awaited<ReturnType<typeof readdir>>);
    const act = { id: "a", kind: "acquirer", label: "audio", owner_pid: 100, started_at: "" };
    mockReadFile.mockResolvedValueOnce(JSON.stringify(act) as unknown as Awaited<ReturnType<typeof readFile>>);

    const result = await scanActivities("/tmp/state");
    expect(result).toHaveLength(1);
    expect(mockReadFile).toHaveBeenCalledTimes(1);
  });

  it("skips malformed JSON files", async () => {
    mockReaddir.mockResolvedValue(["bad.json", "good.json"] as unknown as Awaited<ReturnType<typeof readdir>>);
    mockReadFile
      .mockResolvedValueOnce("not valid json" as unknown as Awaited<ReturnType<typeof readFile>>)
      .mockResolvedValueOnce(
        JSON.stringify({ id: "g", kind: "acquirer", label: "audio", owner_pid: 200, started_at: "" }) as unknown as Awaited<ReturnType<typeof readFile>>
      );

    const result = await scanActivities("/tmp/state");
    expect(result).toHaveLength(1);
  });
});

describe("sendSigterm", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("sends SIGTERM to the given PID via process.kill", () => {
    const killSpy = vi.spyOn(process, "kill").mockReturnValue(true);

    sendSigterm(41010);

    expect(killSpy).toHaveBeenCalledOnce();
    expect(killSpy).toHaveBeenCalledWith(41010, "SIGTERM");
  });

  it("throws when process.kill fails (e.g. no such process)", () => {
    vi.spyOn(process, "kill").mockImplementation(() => {
      throw Object.assign(new Error("kill ESRCH"), { code: "ESRCH" });
    });

    expect(() => sendSigterm(99999)).toThrow("kill ESRCH");
  });
});

describe("isProcessAlive", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns true when process.kill(pid, 0) succeeds", () => {
    vi.spyOn(process, "kill").mockReturnValue(true);
    expect(isProcessAlive(123)).toBe(true);
  });

  it("returns false when process.kill throws ESRCH (no such process)", () => {
    vi.spyOn(process, "kill").mockImplementation(() => {
      throw Object.assign(new Error("kill ESRCH"), { code: "ESRCH" });
    });
    expect(isProcessAlive(99999)).toBe(false);
  });

  it("returns true when process.kill throws EPERM (process exists, not ours)", () => {
    vi.spyOn(process, "kill").mockImplementation(() => {
      throw Object.assign(new Error("kill EPERM"), { code: "EPERM" });
    });
    expect(isProcessAlive(1)).toBe(true);
  });
});

describe("activityFilePath", () => {
  it("derives the on-disk path from the activity id", () => {
    const path = activityFilePath(
      { id: "abc-123", kind: "acquirer", label: "audio", owner_pid: 1, started_at: "" },
      "/tmp/state"
    );
    expect(path).toBe("/tmp/state/activity-abc-123.json");
  });
});

describe("cleanStaleActivity", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("unlinks the activity file for the given activity", async () => {
    mockUnlink.mockResolvedValue(undefined);
    const act = { id: "stale-1", kind: "acquirer", label: "audio", owner_pid: 42, started_at: "" };

    await cleanStaleActivity(act, "/tmp/state");

    expect(mockUnlink).toHaveBeenCalledWith("/tmp/state/activity-stale-1.json");
  });

  it("propagates unlink failures", async () => {
    mockUnlink.mockRejectedValue(new Error("EACCES"));
    const act = { id: "stale-2", kind: "acquirer", label: "audio", owner_pid: 42, started_at: "" };

    await expect(cleanStaleActivity(act, "/tmp/state")).rejects.toThrow("EACCES");
  });
});
