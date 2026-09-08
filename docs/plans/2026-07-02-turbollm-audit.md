# Code Audit: turbollm

**Date**: 2026-07-02
**Scope**: Whole repo — Python core (`src/turbollm/`, ~5.3k lines), Swift sidecar (`tools/turbo-acquirer/`, ~2k lines), Raycast extension (`tools/raycast-turbo/`). Audited by four parallel review agents (CLI/workflows, providers/harnesses, transcribe/prune/activity/plugins, tools), cross-verified against call sites. Test suite: 226/226 passing.
**Health Score**: Needs Work — architecture and hygiene are genuinely good; the issues are concentrated in a handful of lifecycle/precedence bugs, one of which (context-window truth) is systemic.

## Status update — 2026-09-08

This is a historical audit. Follow-up commits `c96315d`, `e916432`, and
`ec96d99` implemented Python, acquirer, and Raycast fixes. For example,
`registry.effective_context` now exists and workflow locking opens without
truncation before acquiring the lock. Do not treat the original findings below
as a current unresolved backlog. The separate wheel-packaging defect is addressed
in the September stabilization work; see `../stabilization-2026-09-08.md`.

## Executive Summary

The codebase is well-architected for its size: pluggable providers/harnesses via Protocols with lazy imports, a lean plugin system, kernel-flock workflow locking, atomic activity-file writes, and consistently documented *why*-comments. Tests all pass.

The dominant finding is **one systemic design gap**: the effective context window has no single source of truth. The picker writes it to `server.max_tokens`, the port stamp records `context_default`, the provider serves yet another precedence, and pi/opencode each reinvent (and disagree on) their own fallback chains. This one gap produces at least five distinct bugs — including the exact "harness autocompacts at the wrong size / server rejects mid-session" failure the port-stamp guard was built to prevent. Fixing it is one shared helper (`effective_context(model)`) used everywhere.

Beyond that: two destructive-cleanup bugs (`pkill -f` can kill your editor; `prune` can delete an in-progress recording), a lock-file truncation bug that makes running workflows unstoppable, shell injection in inline workflow params, and real undefined behavior in the Swift screen-capture pipeline. Quick wins are plentiful — most fixes are small and mechanical.

## Findings by Category

### Bugs — Critical / High

**BUG-1. Context-window truth is scattered across four modules (systemic).**
- `picker.py:258` writes the picked context to `server.max_tokens`; `cli.py:360` stamps `context_default_tokens(m)` (which prefers `context_default` *over* `server.max_tokens`); `providers/vllm_mlx.py:51` serves the opposite precedence (`srv.max_tokens` first). The stamp guard at `cli.py:956-966` then validates harnesses against the wrong number in both directions.
- `harnesses/pi.py:60-69` puts `context_default` above `server.max_tokens`, so the picker's context choice never reaches pi (no model defines `pi.context_window` in models.toml). Pick 16K → server serves 16K, pi believes 64K → mid-session rejection. Pick 192K → pi needlessly compacts at 64K.
- `harnesses/opencode.py:26-27` ignores both and always reports 32768/8192.
- `picker.py:216-222` claims omlx honors `max_tokens`; `providers/omlx.py:41-72` never reads it — the picker's context selector is UI theater for omlx.
- **Fix (one move):** add `registry.effective_context(model)` mirroring the provider precedence; use it in the serve command, the port stamp, pi, opencode, and the picker.

**BUG-2. `turbo sidecar` runs `pkill -9 -f turbo-acquirer` — kills unrelated processes.** `plugins/mac/sidecar.py:34-36`. `-f` matches full command lines: `vim .../turbo-acquirer/...`, a `swift build` in that directory, or a grep get SIGKILLed. This repo *is* the acquirer's dev tree, so killing your own editor is realistic. Fix: `pkill -9 -x turbo-acquirer`.

**BUG-3. `turbo prune` can delete an in-progress recording.** `prune.py:203-226`. Session-dir staleness uses the *directory* mtime, which doesn't change when the acquirer appends to the movie/wav inside. A recording longer than 1h looks stale and gets `rmtree`d mid-recording — the exact case the age gate claims to prevent. Fix: use the newest mtime of any file inside (the code already walks the dir for sizing), or cross-check live activity files.

**BUG-4. Workflow lock truncation makes running workflows unstoppable.** `workflows.py:463`. `open(path, "w")` truncates the lock file *before* attempting flock. A second `turbo workflows run X` erases the running instance's PID stamp; `workflows stop` then finds no PID. Fix: `os.open(O_CREAT|O_RDWR)`, flock, *then* ftruncate+write.

**BUG-5. Harness attaches to a known-incompatible server after warning.** `cli.py:970-980` + `cli.py:781`. After detecting an incompatible backend on the port and warning, the fall-through still attaches (e.g. pi pointed at an mlx-audio ASR server). Fix: refuse or pick a free port.

**BUG-6. Shell injection / breakage in inline workflow commands.** `workflows.py:588,608`. `{{param}}` is spliced unquoted into `sh -c` strings. A path with a space breaks; `--param file='x; rm -rf ~'` executes. Script-reference workflows are safe (positional args). Fix: `shlex.quote()` expanded values or pass via env vars.

**BUG-7. Swift: dangling pointers to stack locals in the screen pipeline (UB).** `RecordScreen.swift:221-238,512-513,560,579`. `&frames`/`&droppedOvercap` inout-to-pointer conversions are stored and dereferenced from other queues after the call — documented undefined behavior plus a cross-thread exclusivity violation. Works today by accident. Fix: a small shared `final class FrameCollector` with the existing lock.

**BUG-8. Swift: pipe deadlock in `command` subcommand.** `CommandSubcommand.swift:36-39`. `waitUntilExit()` before reading pipes: >64KB of output blocks the child on `write(2)` forever. Fix: drain both pipes before waiting.

### Bugs — Medium

**BUG-9. Second `turbo serve` on a busy port clobbers then deletes the live server's stamp.** `cli.py:360-364`. Stamp is written before bind; on bind failure the `finally` deletes the *surviving* server's stamp. Fix: refuse when `_server_is_running(port)`; only clear a stamp whose pid is `os.getpid()`.

**BUG-10. Picker strips `enable_thinking = false` registry defaults.** `picker.py:79,273-282`. `reasoning_on` initializes from `can_reason`, not the configured default; confirming a Gemma 4 model without touching `t` silently flips thinking ON. Also: the toggle writes only `server.default_chat_template_kwargs`, which only vllm-mlx consumes — it's a no-op on gguf (`server.enable_thinking`) and mlx-vlm (no mapping) (`gguf.py:94`, `mlx_vlm.py:50-64`).

**BUG-11. opencode drops model selection when a user config exists.** `opencode.py:49-54`. Only `provider` is merged into an existing `opencode.json`; `model.chat = turbo/<id>` is dropped, so opencode launches with the user's previous (likely remote) default model.

**BUG-12. gguf draft model pulled to `draft_local_path` is never found at serve time.** `gguf.py:136-144` vs `208-216`. `pull()` downloads to the local dir; `_draft_gguf_file()` only checks the hub cache — speculative decoding silently off, no warning (vllm-mlx and mlx-vlm both warn).

**BUG-13. Unknown `tool_call_shim` value bricks port pairing.** `mlx_vlm.py:29` vs `:73`. Any truthy value shifts the upstream port +10000, but only `"gemma4_bare"` spawns the proxy — health poll on the base port never succeeds, and the log tail shows a healthy server. Fix: compute the shim once; shift only for recognized values; error loudly otherwise.

**BUG-14. Tool-call proxy hijacks prose containing `call:name{...}`.** `mlx_vlm_tool_proxy.py:87-92`. Substring match: a model *explaining* the syntax gets its answer destroyed and a spurious tool executed; trailing text folds into the last arg. Fix: full-match anchor on the stripped message.

**BUG-15. Proxy loses upstream error bodies on streamed requests; upstream-down crashes the handler thread.** `mlx_vlm_tool_proxy.py:212-231`. Non-200 responses get rewritten into empty SSE; `URLError` is uncaught (client sees connection reset instead of 502). Also no `urlopen` timeout, and only `JSONDecodeError` is caught around the rewrite.

**BUG-16. Sidecar reset deletes activity files of live processes.** `plugins/mac/sidecar.py:38-43`. Unlinks every `activity-*.json` regardless of owner liveness; a running workflow vanishes from the HUD and its updates silently no-op. Fix: reuse the dead-PID logic `activity.py`/`prune.py` already have.

**BUG-17. `workflows stop` signals the launcher's process group.** `workflows.py:531-537`. `killpg(getpgid(parent))` can SIGTERM the HUD/Raycast host if turbo wasn't spawned in its own group; the child shell is in a new session and unreachable by this killpg anyway. Fix: `os.kill(pid, sig)` on the parent; the forwarder handles the tree.

**BUG-18. Server/acquirer teardown can hang forever or leave zombies.** `cli.py:842-844` (`server.wait()` no timeout, no kill escalation), `workflows.py:242,266-269` (`communicate()` no timeout; `kill()` never reaped), `mlx_vlm_tool_proxy.py:276-278` (`wait(timeout=5)` uncaught → leaks upstream).

**BUG-19. `turbo rm` claims success on legacy-dir models but deletes nothing.** `cli.py:299-312`. Only `_hf_cache_path` is removed; `~/.turbollm/models/...` layout survives and still lists as downloaded.

**BUG-20. ffmpeg pre-convert failure leaks temp wav + raw traceback.** `cli.py:556-564`. `cleanup_wav` assigned after `check=True`; also `find_silences` (`transcribe_split.py:58-63`) ignores ffmpeg failure and silently degrades to hard cuts.

**BUG-21. `workflows config` stickies are never read by CLI `workflows run`.** `cli.py:1183-1221` vs `workflows.py:127-231`. `resolve_params` doesn't consult the UserDefaults keys the config command writes (prune and the HUD do read them). Fold sticky reads into `resolve_params` or document.

**BUG-22. Prune lock-file deletion race (split-brain).** `prune.py:178-200` + `257-277`. flock probed at scan time, deleted after confirmation; a workflow re-acquiring in between gets its held lock unlinked → next run locks a fresh inode → two instances "hold" the lock. Fix: re-probe (or take the flock) at unlink time.

**BUG-23. Swift: SignalHandling force-exit backstop (commit 6ee6254) has three defects.** `SignalHandling.swift:54-57`. (1) The 3s clock starts at signal arrival, so a healthy PNG-drain teardown can be hard-killed mid-flush; (2) `_exit(0)` skips `defer`s — activity file leaks (phantom HUD entries, feeds BUG-25) and the WAV header may be left invalid; (3) exit code 0 masks the truncation. Fix: start the clock when teardown is observed wedged (or ~10s), nonzero exit code. The `sig_atomic_t` comment justifies the wrong property (it's a cross-thread flag now, not signal context).

**BUG-24. Swift: mic-denied crashes; multi-stream device OOB read; mid-recording stream death invisible.** `RecordAudio.swift:62-68,272-274` (ObjC exception instead of `permissionDenied("Microphone")`); `AudioSourcePicker.swift:82-92` (fixed-size copy of variable-length `AudioBufferList`, OOB for aggregate devices); `RecordAudio.swift:358`/`RecordScreen.swift:266` (nil SCStream delegate + no engine-config observer → silently truncated captures).

**BUG-25. Raycast: Stop action unhandled rejection + PID-recycling hazard.** `running-workflows.tsx:57-64,124-131`. Shells out to `/bin/kill` with interpolated PID, no try/catch; stale activity files (from BUG-23) show phantom workflows whose Stop kills whatever now owns the recycled PID. Fix: `process.kill(pid, "SIGTERM")` in try/catch; liveness-filter (`kill(pid, 0)`), treat ESRCH as stale.

### Bugs — Low (grouped)

- `activity.py:77-88` TOCTOU (`exists()` then read/unlink) → `FileNotFoundError`; use try/except + `unlink(missing_ok=True)`.
- `activity.py:112-127` `_pid_alive` mishandles pid ≤ 0 (`os.kill(0,0)` always "alive"); `prune.py:285-301` has the correct copy — deduplicate to it.
- `plugins/mac/_common.py:53-57` `refresh_symlink` fails if the link path is a regular file.
- `plugins/mac/raycast.py:69-70` `--extension-dir` nonexistent → raw traceback (`click.Path(exists=True)`).
- `cli.py:441` Ctrl-C mid-stream traceback (`except Exception` misses `KeyboardInterrupt`); models probe at `:379` has no timeout.
- `cli.py:347` can print "Run: turbo pull None" (picker + `--backend` override).
- `cli.py:818-823` startup "timeout" is iteration-count (sleep 1s + up to 2s probe per loop → 120 ≈ 6 min); use a monotonic deadline.
- `cli.py:601,652` split-mode transcribe ignores `-f srt/vtt` and emits JSON.
- `cli.py:619` `urllib.error` used without importing it (works by side effect).
- `workflows.py:507-509` status probe takes `LOCK_EX` briefly → can spuriously refuse a starting workflow.
- `workflows.py:222` `auto` templates can't reference background-acquired params — fails at runtime instead of validation.
- `picker.py:127-142` bare ESC hangs `_read_key` (blocking read of next byte); use `select()`.
- `registry.py:161-167` `_hf_snapshot_path` can return a stray file (`.DS_Store`) as "latest snapshot"; filter `is_dir()`.
- `omlx.py:31-39` symlink management crashes on a real directory at the link path (after a multi-GB download).
- `mlx_vlm.py:122-127` vs `:147-156` `get_model_id` can return the empty `local_path` while the server was started on the HF snapshot → every request 404s.
- `vllm_mlx.py:188-197` (glob) vs `mlx_vlm.py:177-178` (rglob): `is_downloaded` disagrees across backends for the same layout.
- `harnesses/claude_code.py:22` (unverified) points `ANTHROPIC_BASE_URL` at the OpenAI-compatible server; no provider serves `/v1/messages` — smoke-test `turbo claude`.
- Swift: region `--region garbage` parses to a silent 1×1 recording; secondary-display regions record/flash the wrong content; window vs region scale inconsistency (`RecordScreen.swift:95,259,332-337,410-443`). Screenshot subcommand reports failures as user cancellation (`ScreenshotSubcommand.swift:73-93`). PNG write failures still land in the manifest (`RecordScreen.swift:575-579`). `useRunningPid` piles up status processes with no in-flight guard (`workflow-form.tsx:112-137`); sticky defaults likely never populate mounted fields (`workflow-form.tsx:151-177`).

### Dead Code

- `cli.py:49-58` `_picker_stats` — no callers anywhere. Delete.
- `registry.py:47-49` — `if "/" in raw: return raw` followed by `return raw`; the conditional is a no-op.
- `picker.py` — the `started` tuple element is always True and discarded; the returned `overrides` dict (incl. `reasoning_on`) is discarded by its only caller — the docstring describes propagation that doesn't exist (wiring it up would fix half of BUG-10).
- `picker.py:133-136` dead try/except around a read that can't raise there.
- `mlx_vlm.py:140` unreachable `["mlx_vlm.server"]` fallback (gated by `is_available()`).
- Swift: `Permissions.swift:27-34` in-process 1s-TTL cache in a process that lives milliseconds — can never hit; `buildContentFilter` ignores its `scope`/`content` params; static `pickerObserver` duplicates a local; `main.swift` usage string omits `record-screen` while the error branch includes it.
- Raycast: unused `useNavigation` import in `running-workflows.tsx:1`.

### Code Smells

- `cli.py` at 1471 lines is the god-file; `transcribe` alone is ~160 lines with a 70-line nested closure — extract `_emit_transcript()` and the ffmpeg pre-convert as top-level helpers.
- Backend choice lists hardcoded three times (`cli.py:318,1015,1347`) while `_ALL_BACKENDS`/`_CHAT_BACKENDS` constants exist — and the copies have already diverged (mlx-audio omitted in two).
- `workflows.py:9-19` underscore-aliased stdlib imports (`import os as _os`) — noise in an internal module.
- `cli.py:772-778` misplaced "docstring" after the first statement — a no-op expression.
- `_os_environ_copy` (`workflows.py:644-645`) — one-line wrapper used once.

### DRY Violations

- **`_build_multipart` duplicated verbatim** — `transcribe_split.py:158-176` and `cli.py:453` (the docstring admits it). Hand-rolled multipart in two places is two places for encoding bugs.
- **`omlx.pull` ≈ `vllm_mlx.pull` minus `local_path`** (~40 identical lines of listing/progress/summary; already drifted). Extract `download_repo_with_progress()` — highest-value extraction.
- **`_configured_path` triplicated** (gguf/vllm_mlx/mlx_vlm) with two different expansion idioms.
- **HF quieting boilerplate ×5** across all providers → one `quiet_hf()`.
- **`_model_path`/draft resolution near-duplicated** (vllm_mlx vs mlx_vlm) and already diverged (glob vs rglob).
- **pi/opencode context resolution** both reimplemented, both wrong (BUG-1) — the dedup *is* the bug fix.
- **Two divergent `pid_alive`** (`activity.py` wrong, `prune.py` right).
- **Harness option decorators**: `_make_harness_command` and `run_cmd` duplicate an identical 5-option block (`cli.py:1010-1030` vs `1343-1362`).
- Swift: `computeStartOffsetMs` byte-identical in RecordScreen/RecordAudio; timestamp formatting duplicated (and diverged — the screenshot copy lacks the POSIX-locale fix); the "async Task + semaphore" bridge appears 4× → one `runBlocking<T>()` helper.
- Raycast: `execAsync` duplicated in two libs; `raycast.py:104` re-literalizes `_RAYCAST_FIXED_COMMANDS`.
- Not worth unifying: per-backend `build_serve_cmd` flag-building — genuinely backend-specific.

### Coupling & Modularity

- Providers import underscore-private registry helpers (`_hf_cache_path` et al.) from six call sites — they're de-facto public; promote to a `hf_cache` module or drop the underscores.
- The picker hardcodes backend server-key semantics (`gguf → context`, else `max_tokens`) and is already wrong for omlx; this belongs on the Provider protocol (`context_override(tokens) -> dict`).
- The picker also writes harness config internals (`pi.context_window`, `opencode.context_length`); combined with each harness's own fallback chain, context truth lives in four modules (see BUG-1).
- `pi.py` falls back to `defaults.opencode` config — cross-harness borrowing.
- The mlx-vlm proxy's ±10000 port-pairing convention is invisible to cli.py's health check (see BUG-13).
- `registry._bootstrap_user_registry` copies the bundled TOML once and never reconciles — users on a snapshot silently miss upstream model fixes; and `BUNDLED_TOML` resolves three parents up, which only works for editable installs (a wheel install yields an empty registry).

### Clarity & Maintainability

Mostly strong (see below). The notable gaps: the picker docstring describing override propagation that doesn't exist; the `sig_atomic_t` comment justifying the wrong property post-6ee6254; `_pid_alive`'s docstring incorrectly claiming no permission check; the "best-effort" thinking toggle presented in the UI as functional per-model.

### Error Handling

- Swallowed with consequences: `execute_prune`'s `except OSError: continue` (deletions silently under-reported); gguf's missing-draft silence (peers warn); `try? pngData.write` + unconditional manifest append; `try? audioFile.write` (disk-full → silently truncated WAV); `AudioUnitSetProperty` result ignored (bad `--device-uid` → wrong device recorded); silent active-window→full-display downgrade when AX permission is missing.
- Uncaught on user-editable input: `json.loads` on `~/.pi/agent/models.json` (`pi.py:105`, also unlocked read-modify-write) and on `opencode.json` (`opencode.py:50`); `str.format` on TOML harness cmd/env values — any literal `{` raises with no hint (`harnesses/__init__.py:48-50`); `_defaults_write` non-zero exit → raw traceback.
- Missing translation: all five providers let `huggingface_hub` network/auth errors escape as raw tracebacks.
- `hermes.py:29-49`: new top-level state hermes creates inside the temp HOME is destroyed on exit.
- Swift: SCStream setup failure exits 0 with an empty manifest (parent can't distinguish from a real empty recording); an `AVAudioFormat` failure is reported as a Screen Recording permission error.

### Areas of Strength

- **Plugin system** (`plugins/__init__.py` + `mac/__init__.py`): explicit list, lazy imports, debug escape hatch, honest `is_supported()` — a model of a small plugin system that resists over-engineering.
- **Provider/Harness Protocols with lazy imports** keep `turbo --help` fast; TOML-driven `GenericHarness` makes most new harnesses zero-code.
- **`transcribe_split.py`** is exemplary: injectable I/O, pure `plan_chunks`/`merge_chunk_results`, docstring explains *why*.
- **`activity.py` atomic writes** (mkstemp + `os.replace`) are exactly right for a file watched by another process, and prune's scanners rely on that correctly.
- **Workflow flock discipline** — kernel lock self-releasing on crash, probe-without-hold status — is the right primitive (the bugs are lifecycle slips, not design flaws). Same verdict for the **port stamp** concept.
- **prune's safe-by-default philosophy** is consistently executed and documented per scanner.
- **stdout/stderr discipline** (data vs status) is consistently applied with written rationale — pipeline safety as a first-class concern.
- **models.toml as documentation**: known-broken flags kept as commented entries with upstream issue links; sampling presets cite sources.
- **Swift test seams** (env fakes, permission overrides, injectable dispatch) make a TCC-gated CLI testable; the SignalHandling rewrite's *design* (SIG_IGN + kqueue DispatchSource) is correct — only the backstop timing/exit semantics need work.
- **Institutional-memory comments** throughout (Raycast symlink de-dupe rationale, AVAudioConverter regression note, SCStream retention rationale).

## Priority Matrix

| Issue | Severity | Effort | Recommended Action |
|-------|----------|--------|--------------------|
| BUG-1 context-truth scatter | High | Medium | One `effective_context()` helper; use in serve/stamp/pi/opencode/picker |
| BUG-2 `pkill -f` | High | Small | `pkill -9 -x turbo-acquirer` |
| BUG-3 prune deletes live recording | High | Small | Inner-file mtime + activity cross-check |
| BUG-4 lock truncation | High | Small | open O_RDWR, flock, then truncate |
| BUG-5 attach to incompatible server | High | Small | Refuse / free port on fall-through |
| BUG-6 inline param shell injection | High | Small | `shlex.quote()` or env vars |
| BUG-7 Swift pointer UB | High | Small | `FrameCollector` class |
| BUG-8 command pipe deadlock | High | Small | Drain pipes before `waitUntilExit` |
| BUG-9..25 medium bugs | Medium | Small–Med | Per-item fixes above |
| Teardown hangs/zombies (BUG-18) | Medium | Small | timeout → kill → wait everywhere |
| DRY: pull/multipart/pid_alive/paths | Medium | Medium | Extract shared helpers |
| Dead code (all) | Low | Small | Delete |
| Wheel-install registry break | Medium | Small | Package models.toml as package data |
| Error-message polish (HF errors, JSON parse) | Low | Small | try/except translation |

## Recommended Cleanup Plan

### Phase 1: Quick Wins (small, mechanical, high impact)
- BUG-2 (`pkill -x`), BUG-4 (lock open mode), BUG-6 (`shlex.quote`), BUG-7 (FrameCollector), BUG-8 (pipe drain), BUG-16 (sidecar liveness check), BUG-19 (rm legacy path), activity TOCTOU + `pid_alive` dedup, `refresh_symlink`, `click.Path(exists=True)`, `import urllib.error`, `is_dir()` snapshot filter.
- Delete all dead code (one commit).
- Teardown hardening sweep (BUG-18): every `terminate()` gets `wait(timeout)` → `kill()` → `wait()`.

### Phase 2: Core Improvements
- **BUG-1**: introduce `registry.effective_context(model)` and route serve cmd, port stamp, pi, opencode, and picker through it. This is the highest-leverage single change in the repo.
- BUG-3 (prune mtime), BUG-22 (lock deletion race), BUG-5 (incompatible-server refusal), BUG-9 (serve-on-busy-port refusal + stamp ownership), BUG-21 (stickies in `resolve_params`).
- Picker: honest thinking toggle (init from config, per-backend translation or hide) — fixes BUG-10.
- Proxy hardening: anchored `call:` match (BUG-14), status-gated rewrite + URLError→502 + timeouts (BUG-15), shim validation (BUG-13).
- DRY extractions: `download_repo_with_progress`, shared `_build_multipart`, `_configured_path`/`quiet_hf` in a `hf_cache` module (also un-privatizes the registry helpers), shared harness option decorators.
- Swift: backstop timing + nonzero `_exit` code (BUG-23), mic permission check (BUG-24), Raycast `process.kill` + liveness filter (BUG-25).

### Phase 3: Architectural Changes
- Move backend-specific knowledge out of the picker onto the Provider protocol (`context_override()`, thinking-toggle translation).
- Package `models.toml` as package data + versioned reconcile for `_bootstrap_user_registry` (wheel installs currently get an empty registry).
- Split `cli.py`: transcribe and workflows-config command groups into their own modules.
- Swift: shared `runBlocking` bridge, SCStream delegate + engine-config observer, `CIContext` reuse, bounded PNG queue.
- Decide `turbo claude`'s fate: verify `/v1/messages` support or remove/document the harness.
