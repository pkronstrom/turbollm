import { beforeEach, describe, expect, it, vi } from "vitest";
import { LocalStorage } from "@raycast/api";
import { clearStickies, getSticky, setSticky } from "../src/lib/stickies";

// @raycast/api is aliased to test/__mocks__/raycast-api.ts via vitest.config.ts.
// We control LocalStorage by setting up a real in-memory store each test.

const mockGet = vi.mocked(LocalStorage.getItem);
const mockSet = vi.mocked(LocalStorage.setItem);
const mockRemove = vi.mocked(LocalStorage.removeItem);
const mockAll = vi.mocked(LocalStorage.allItems);

describe("stickies", () => {
  let store: Record<string, string>;

  beforeEach(() => {
    store = {};
    vi.resetAllMocks();

    mockGet.mockImplementation(async (k) => store[k as string] as unknown as undefined);
    mockSet.mockImplementation(async (k, v) => {
      store[k as string] = String(v);
    });
    mockRemove.mockImplementation(async (k) => {
      delete store[k as string];
    });
    mockAll.mockImplementation(async () => ({ ...store }));
  });

  it("setSticky writes value and getSticky reads it back", async () => {
    await setSticky("transcribe-file", "file", "/tmp/x.wav");
    const val = await getSticky("transcribe-file", "file");
    expect(val).toBe("/tmp/x.wav");
  });

  it("getSticky returns undefined for an unset key", async () => {
    const val = await getSticky("transcribe-file", "missing-param");
    expect(val).toBeUndefined();
  });

  it("clearStickies removes only the target workflow's keys", async () => {
    await setSticky("transcribe-file", "file", "/tmp/x.wav");
    await setSticky("transcribe-file", "vault", "/vault");
    await setSticky("record-to-obsidian", "vault", "/other-vault");

    await clearStickies("transcribe-file");

    expect(await getSticky("transcribe-file", "file")).toBeUndefined();
    expect(await getSticky("transcribe-file", "vault")).toBeUndefined();
    // Other workflow's stickies should be untouched
    expect(await getSticky("record-to-obsidian", "vault")).toBe("/other-vault");
  });

  it("clearStickies is a no-op when workflow has no stickies", async () => {
    await setSticky("record-to-obsidian", "vault", "/vault");
    await clearStickies("transcribe-file"); // nothing to clear
    expect(await getSticky("record-to-obsidian", "vault")).toBe("/vault");
  });

  it("sticky keys use the workflow.<wf>.param.<param> format", async () => {
    await setSticky("my-workflow", "my-param", "val");
    expect(mockSet).toHaveBeenCalledWith("workflow.my-workflow.param.my-param", "val");
  });
});
