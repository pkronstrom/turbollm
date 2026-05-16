import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFile, readdir } from "fs/promises";
import { exec } from "child_process";

vi.mock("fs/promises", () => ({
  readdir: vi.fn(),
  readFile: vi.fn(),
}));

vi.mock("child_process", () => ({
  exec: vi.fn(),
}));

// @raycast/api aliased via vitest.config.ts
import { scanActivities, sendSigterm } from "../src/running-workflows";

const mockReaddir = vi.mocked(readdir);
const mockReadFile = vi.mocked(readFile);
const mockExec = vi.mocked(exec);

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
    vi.resetAllMocks();
  });

  it("calls /bin/kill -TERM with the given PID", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(null, "", "");
      return {} as ReturnType<typeof exec>;
    });

    await sendSigterm(41010);
    expect(mockExec).toHaveBeenCalledOnce();
    const cmd = (mockExec.mock.calls[0] as [string, Function])[0];
    expect(cmd).toBe("/bin/kill -TERM 41010");
  });

  it("rejects when kill fails", async () => {
    mockExec.mockImplementation((_cmd, cb) => {
      (cb as Function)(new Error("kill: 99999: No such process"), "", "");
      return {} as ReturnType<typeof exec>;
    });

    await expect(sendSigterm(99999)).rejects.toThrow("No such process");
  });
});
