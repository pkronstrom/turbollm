import { resolve } from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      // @raycast/api ships only types (no JS runtime); redirect to a stub for tests.
      "@raycast/api": resolve(__dirname, "test/__mocks__/raycast-api.ts"),
    },
  },
  test: {
    globals: true,
    environment: "node",
  },
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "react",
  },
});
