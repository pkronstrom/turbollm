import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// Mock @raycast/api so all transitive imports work without the Raycast runtime.
vi.mock("@raycast/api", () => ({
  List: Object.assign(
    (props: Record<string, unknown>) => props,
    { Item: (props: Record<string, unknown>) => props }
  ),
  Detail: (props: Record<string, unknown>) => props,
  Action: Object.assign(
    (props: Record<string, unknown>) => props,
    { SubmitForm: (props: Record<string, unknown>) => props }
  ),
  ActionPanel: (props: Record<string, unknown>) => props,
  Form: Object.assign(
    (props: Record<string, unknown>) => props,
    {
      TextField: (props: Record<string, unknown>) => props,
      TextArea: (props: Record<string, unknown>) => props,
      Dropdown: Object.assign(
        (props: Record<string, unknown>) => props,
        { Item: (props: Record<string, unknown>) => props }
      ),
      FilePicker: (props: Record<string, unknown>) => props,
      Description: (props: Record<string, unknown>) => props,
    }
  ),
  showToast: vi.fn(),
  Toast: { Style: { Animated: "animated", Success: "success", Failure: "failure" } },
  useNavigation: () => ({ pop: vi.fn(), push: vi.fn() }),
  LocalStorage: {
    getItem: vi.fn(),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    allItems: vi.fn(),
  },
  getPreferenceValues: vi.fn(() => ({})),
}));

vi.mock("child_process", () => ({
  exec: vi.fn(),
  spawn: vi.fn(() => ({
    stderr: { on: vi.fn() },
    on: vi.fn(),
  })),
}));

describe("extension scaffold", () => {
  it("package.json has required structure", () => {
    const raw = readFileSync(join(__dirname, "..", "package.json"), "utf8");
    const pkg = JSON.parse(raw) as {
      name: string;
      commands: Array<{ name: string; mode: string }>;
    };
    expect(pkg.name).toBe("turbollm");
    expect(Array.isArray(pkg.commands)).toBe(true);
    expect(pkg.commands).toHaveLength(1);
    expect(pkg.commands[0].name).toBe("run-workflow");
    expect(pkg.commands[0].mode).toBe("view");
  });

  it("tsconfig.json is valid JSON", () => {
    const raw = readFileSync(join(__dirname, "..", "tsconfig.json"), "utf8");
    expect(() => JSON.parse(raw)).not.toThrow();
  });

  it("run-workflow.tsx exports a default React component", async () => {
    const mod = await import("../src/run-workflow");
    expect(typeof mod.default).toBe("function");
  });
});
