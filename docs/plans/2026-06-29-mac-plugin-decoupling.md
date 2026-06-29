# Mac sidecar/Raycast as an in-tree plugin — Design

**Date:** 2026-06-29
**Status:** Draft (awaiting review)

## Goal

Make the macOS Swift addon (TurboHUD + turbo-acquirer) and the Raycast extension
**optional plugins** so that core `turbollm` is usable and distributable on its
own. The plugin self-registers its commands *when present and supported*,
mirroring the hook-based plugin pattern used by the sibling `dodo-tasks` project.

**What "decoupled" means here (chosen tradeoff: one package, optional extra):**
The Mac orchestration code is isolated into a self-contained `turbollm/plugins/mac/`
submodule that is **inert** unless macOS + Swift + the `tools/` source tree are
all present. The published wheel *still physically contains* this small,
dependency-free plugin code — but `cli.py` and every core command path carry
none of it, and it never imports or runs on an unsupported machine. This is the
"one package, optional extra" boundary the user selected over a fully separate
package. The submodule boundary is deliberately clean so that, if true
wheel-level exclusion is wanted later, `plugins/mac/` can be lifted into a
separate `turbollm-mac` distribution with no core changes.

Two outcomes:

1. **Run it** — `turbo sidecar` builds both Swift packages, symlinks them, syncs
   Raycast, and launches the HUD. (Both Swift packages already build clean on
   Swift 6.2; the final GUI launch + TCC permission grants are a manual user
   step.)
2. **Decouple** — relocate the `sidecar` + `raycast` command code out of
   `cli.py` core into a `turbollm/plugins/mac/` plugin that auto-registers only
   on capable machines.

## Non-goals

- No code-signing / notarization / `.app` bundling (still future work).
- No change to the Swift packages or the Raycast extension themselves.
- No multi-plugin registry, enable/disable config, or manifest files (dodo has
  these for ~6 plugins; turbollm has exactly one — that machinery would be
  over-engineering here). A minimal loader is sufficient and can grow later.

## Background: current state

- Swift packages live in `tools/turbo-hud/` and `tools/turbo-acquirer/`; the
  Raycast extension in `tools/raycast-turbo/`. These are **not** shipped in the
  core wheel today (hatchling packages only `src/turbollm/`).
- The *orchestration* lives in `src/turbollm/cli.py`: the `sidecar` command, the
  `raycast` command group, ~300 lines of Raycast-sync logic, and a clutch of
  private helpers. This is the coupling we remove.
- Activation is gated today by `TURBO_BETA=1` via `_beta_gate()`.

## Design

### New structure

```
src/turbollm/
  cli.py                    # core only; sidecar/raycast code removed
  plugins/
    __init__.py             # loader: register_all(cli)
    mac/
      __init__.py           # is_supported() + register_root_commands(cli)  [lazy imports]
      sidecar.py            # `turbo sidecar` command (moved verbatim from cli.py)
      raycast.py            # `turbo raycast` group + sync logic (moved from cli.py)
tools/turbo-hud, turbo-acquirer, raycast-turbo   # unchanged — plugin builds from here
```

### Loader — `plugins/__init__.py`

`register_all(cli)` iterates an **explicit** plugin list — `_PLUGINS = [mac]` —
not a filesystem scan (one plugin doesn't warrant dodo's dynamic discovery; the
list is trivially extensible when a second plugin appears). For each plugin, if
`plugin.is_supported()` returns `True`, it calls
`plugin.register_root_commands(cli)`. Each plugin is wrapped in its own
`try/except`:

- On failure, core `turbo` is **never** broken — the exception is swallowed so
  the command simply doesn't appear.
- But swallowing can hide a real bug (e.g. `sidecar` missing on a capable Mac due
  to a typo). So when `TURBO_PLUGIN_DEBUG=1` is set, the loader prints the
  traceback to stderr. A loader test asserts that a plugin raising during
  registration (a) doesn't break `cli` and (b) surfaces under the debug flag.

**Registration order / mechanics (explicit):** `cli.py` calls
`from turbollm.plugins import register_all; register_all(cli)` as the **last
statement of the module body** — after every core command/group is defined and
attached, at import time. It is *not* inside any `if __name__ == "__main__"`
guard (the entry point is `turbollm.cli:cli`), so it runs for both the installed
`turbo` script and `python -m turbollm.cli`. Commands added via
`cli.add_command(...)` are seen by `TurboGroup.list_commands` as built-ins, so
they appear in `turbo --help` normally.

### Mac plugin — `plugins/mac/__init__.py`

Lightweight module (no top-level platform imports). Exposes:

- `is_supported() -> bool` — **all** of: `sys.platform == "darwin"`,
  `shutil.which("swift")` present, **and** the `tools/turbo-hud` source dir
  actually exists (the dir the commands build/symlink from). The `tools/` check
  is the key tightening: an installed wheel on a Mac *with* Swift but *without*
  the `tools/` tree must report `False`, otherwise `sidecar`/`raycast` would
  appear and then fail when `_hud_dir()` / `_raycast_extension_dir()` resolve to
  a missing path. On Linux / a core-only install → `False`, so the commands
  never appear and never error.
- `register_root_commands(cli)` — lazily imports `sidecar.py` / `raycast.py`
  and does `cli.add_command(sidecar_cmd)` + `cli.add_command(raycast_grp)`.

**No back-imports from `cli.py`.** To avoid a circular import (cli.py imports
the loader at module end while the plugin would import cli.py), the plugin
modules import their dependencies directly: `load_registry` from
`turbollm.registry`, stdlib (`json`, `os`, `subprocess`, `shutil`, `pathlib`)
directly, and their own `Console()` instance (or a shared `turbollm._console`
module if we'd rather have one Console — decided during implementation, but the
rule is: the plugin never does `from turbollm.cli import ...`).

### What moves to the plugin

Only the Swift/Raycast-coupled surface:

- `turbo sidecar` command (`sidecar_cmd`)
- `turbo raycast` group (`raycast_grp`) + `_do_raycast_sync`, `_sidecar_raycast_sync`
- Helpers used only by the above: `_hud_dir`, `_acquirer_dir`,
  `_raycast_extension_dir`, `_swift_build_product_path`, `_refresh_symlink`,
  `_local_bin`, `_to_title_case`, `_RAYCAST_FIXED_COMMANDS`.

(Each helper's home is confirmed by grepping its call sites during
implementation — any helper also used by a core command stays in core or moves
to a shared spot.)

### What stays in core

`workflows`, `activities`, and `hud status` command groups. Rationale:

- `workflows` is a first-class CLI feature — `turbo workflows run transcribe-file
  --param file=x.wav` works from the terminal with no Swift.
- `activities` (read) + `hud status` (write) are two halves of a pure-Python
  activity-state API (`activity.py`) over `~/.turbollm/state/`. They *feed* the
  HUD but don't *depend* on it. Moving them would break scripts that announce
  progress (`turbo hud status set`) on any machine where the plugin is
  unsupported.

**Known residual macOS coupling in core (harden, don't move):** `workflows
config` shells out to `/usr/bin/defaults` (macOS UserDefaults) via the
`_defaults_read/_write/_delete` helpers. On Linux that binary doesn't exist, so
the bare `subprocess.run([...])` raises `FileNotFoundError` rather than failing
gracefully (unlike `workflows._read_hud_override()` /
`prune._read_hud_param_sticky()`, which already guard it). This is a
pre-existing latent bug, not introduced by this refactor, but since the spec
claims a clean "core runs anywhere" story we harden it as part of the work: wrap
the three `_defaults_*` helpers so a missing `/usr/bin/defaults` (or non-darwin)
degrades to "no stickies" instead of crashing. (The stickies are a HUD
convenience; their absence off-Mac is correct behavior.)

### Removed

- `_beta_gate()` and the `TURBO_BETA` env gate — replaced entirely by
  `is_supported()` auto-detection.

### pyproject.toml

Add a documented opt-in alias:

```toml
[project.optional-dependencies]
mac = []   # no hard Python deps — the plugin shells out to `swift` / `ray`
```

This is a discoverability/intent marker (`uv tool install "turbollm[mac]"`);
activation itself is capability-based, not extra-based, because pip extras
cannot be introspected at runtime.

## Error handling

- Unsupported platform → plugin simply not registered; no error, no command.
- Plugin import/registration raises → caught per-plugin in the loader; core
  `turbo` continues unaffected. Surface the traceback only when a debug env var
  (e.g. `TURBO_PLUGIN_DEBUG=1`) is set.
- `turbo sidecar` when Swift build fails or HUD binary missing → existing
  in-command handling is preserved (it already prints clear errors and falls
  back to `swift run`).

## Testing

**Key constraint:** registration happens at `turbollm.cli` *import* time, so a
test cannot `import turbollm.cli` and *then* monkeypatch `is_supported()` — the
commands are already attached-or-absent. Tests must therefore drive registration
deterministically rather than relying on import-time patching:

- `tests/test_sidecar_cli.py` and `tests/test_turbo_raycast_sync.py`: drop the
  `TURBO_BETA=1` monkeypatch. Exercise the commands by either (a) calling
  `turbollm.plugins.mac.register_root_commands(fresh_group)` onto a fresh
  `click.Group` and invoking through that, or (b) importing the command objects
  directly from `turbollm.plugins.mac.sidecar` / `.raycast`. Sync-logic tests
  target `turbollm.plugins.mac.raycast._do_raycast_sync`. This makes the tests
  run on CI (possibly Linux) without depending on host platform detection.
- New `tests/test_plugin_loader.py`, all against a **fresh** `click.Group` (never
  the import-time `cli`): (a) `register_all` attaches mac commands when
  `mac.is_supported` is patched `True`; (b) attaches nothing and raises nothing
  when `False`; (c) a plugin whose `register_root_commands` raises does not break
  the group **and** its traceback is printed when `TURBO_PLUGIN_DEBUG=1`.
- New small test that the hardened `_defaults_*` helpers return empty / no-op
  (don't raise) when `/usr/bin/defaults` is absent — simulate via monkeypatching
  `subprocess.run` to raise `FileNotFoundError`.
- Run the Swift test suites (`swift test` in both packages) and the Raycast
  `vitest` suite to confirm the move didn't disturb them (it shouldn't — those
  trees are untouched).

## Docs

- README "Sidecars (BETA)" section: replace the `export TURBO_BETA=1` opt-in
  with the auto-detect behavior and the `turbollm[mac]` install note. Keep the
  TCC-permission guidance.

## Rollout / verification

1. Refactor + tests green (Python, Swift, Raycast).
2. `turbo --help` on this Mac shows `sidecar` + `raycast`; conceptually absent on
   a non-darwin/core-only install (verified via `is_supported()` unit test).
3. Manual: `turbo sidecar` builds, symlinks, launches HUD; user grants
   Microphone / Screen Recording on first capture; the 3 workflows
   (`transcribe-file`, `record-to-obsidian`, `record-meeting-with-screen`) fire
   from the menu bar.
