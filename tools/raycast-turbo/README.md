# turbollm Raycast Extension

A [Raycast](https://raycast.com) extension for running and monitoring turbollm workflows directly from the macOS command palette.

## Prerequisites

- [Raycast](https://raycast.com) installed
- `turbo` CLI on PATH (or configured via the `turboPath` preference — see below)
- Node.js 18+ and npm

## Setup

```bash
cd tools/raycast-turbo
npm install
ray develop
```

`ray develop` adds the extension to Raycast in developer mode. Changes to source files hot-reload automatically.

## Commands

### Run Turbollm Workflow

The main command. Opens a searchable list of every workflow defined in `models.toml`. Select one to open a form pre-populated with any sticky values from previous runs. Fill in params and press **Run** — the extension spawns `turbo workflows run <workflow> --param key=value …` in the background and shows a toast when it finishes.

### Running Workflows

Shows all in-flight acquirer activities (audio recordings, screen captures) read from `~/.turbollm/state/`. Each row shows the workflow label, PID, and start time. A **Stop** action sends `SIGTERM` to the process and refreshes the list.

### Per-workflow commands (auto-generated)

After running `turbo raycast sync`, one additional Raycast command is added for each workflow — for example, **Transcribe File** or **Record To Obsidian**. Invoking a per-workflow command skips the picker and opens the workflow's form directly. These commands appear in Raycast search alongside the two fixed commands.

## Extension preference: `turboPath`

| Setting | Description |
|---------|-------------|
| **Turbo binary path** | Absolute path to the `turbo` binary. Leave blank to auto-detect via `PATH`. |

Set this when `turbo` is installed outside the default `PATH` that Raycast sees (common with `uv tool install` into `~/.local/bin`). Example: `/Users/you/.local/bin/turbo`.

## Keeping commands in sync: `turbo raycast sync`

Run this whenever workflows are added or removed from `models.toml`:

```bash
turbo raycast sync --extension-dir tools/raycast-turbo
```

It does three things in one shot:

1. **Updates `package.json` `commands` array** — adds one entry per workflow, removes entries for workflows that no longer exist, and preserves the two fixed commands (`run-workflow`, `running-workflows`) exactly.
2. **Creates/refreshes per-workflow symlinks** — writes `src/<workflow-slug>.tsx → run-workflow.tsx` for each workflow. Raycast routes each command to its own source file; the symlinks let all per-workflow commands share a single implementation. Orphan symlinks (for deleted workflows) are removed.
3. **Writes `src/_generated_commands.ts`** — a type-safe `Record<string, string>` map used by `run-workflow.tsx` to detect which workflow to preselect when launched from a per-workflow shortcut.

Add `--quiet` to suppress output (used by `turbo sidecar` on every launch).

## Launch flow

When you invoke a workflow from Raycast — either via the generic **Run Turbollm Workflow** picker or a per-workflow shortcut — the extension calls `turbo workflows list --json` to fetch the current workflow definitions, then renders a typed form for that workflow's params. `string`, `text`, `enum`, `file`, and `directory` params each get an appropriate input; `audio-recording`, `screenshot-manual`, `command`, and `screen-recording` params are shown as read-only notes ("Captured at runtime") because they require the HUD or CLI acquirer. On submit, the extension spawns `turbo workflows run <workflow> --param key=value …` as a background subprocess and surfaces success or failure via a Raycast toast.

## Development

```bash
# Run unit tests (no Raycast runtime needed)
npm test

# Type-check
npx tsc --noEmit
```

Tests use [vitest](https://vitest.dev) with `@raycast/api` aliased to a stub file at `test/__mocks__/raycast-api.ts`, so they run in plain Node without a Raycast environment.
