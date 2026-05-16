/**
 * Stub for @raycast/api used during vitest runs.
 *
 * Raycast ships no JS runtime — only types. vitest/vite cannot resolve the
 * package as a real module, so vitest.config.ts aliases "@raycast/api" to this
 * file. Each `vi.fn()` stub can be overridden per-test via `vi.mocked(…)`.
 */
import { vi } from "vitest";

// ── Components ───────────────────────────────────────────────────────────────

export const List = Object.assign(
  (_props: Record<string, unknown>) => null,
  { Item: (_props: Record<string, unknown>) => null }
);

export const Detail = (_props: Record<string, unknown>) => null;

export const Action = Object.assign(
  (_props: Record<string, unknown>) => null,
  { SubmitForm: (_props: Record<string, unknown>) => null }
);

export const ActionPanel = (_props: Record<string, unknown>) => null;

export const Form = Object.assign(
  (_props: Record<string, unknown>) => null,
  {
    TextField: (_props: Record<string, unknown>) => null,
    TextArea: (_props: Record<string, unknown>) => null,
    Dropdown: Object.assign(
      (_props: Record<string, unknown>) => null,
      { Item: (_props: Record<string, unknown>) => null }
    ),
    FilePicker: (_props: Record<string, unknown>) => null,
    Description: (_props: Record<string, unknown>) => null,
  }
);

// ── Hooks & utilities ─────────────────────────────────────────────────────────

export const useNavigation = vi.fn(() => ({
  pop: vi.fn(),
  push: vi.fn(),
}));

export const showToast = vi.fn(async (_opts: unknown) => ({
  style: "",
  title: "",
  message: "",
}));

export const Toast = {
  Style: { Animated: "animated", Success: "success", Failure: "failure" },
};

// ── Storage & preferences ─────────────────────────────────────────────────────

export const LocalStorage = {
  getItem: vi.fn(async (_key: string): Promise<string | undefined> => undefined),
  setItem: vi.fn(async (_key: string, _value: string): Promise<void> => undefined),
  removeItem: vi.fn(async (_key: string): Promise<void> => undefined),
  allItems: vi.fn(async (): Promise<Record<string, string>> => ({})),
};

export const getPreferenceValues = vi.fn(<T>(): T => ({} as T));

// ── Environment ────────────────────────────────────────────────────────────────
// Mutable object so tests can override `commandName` per-test.

export const environment = {
  commandName: "run-workflow",
};
