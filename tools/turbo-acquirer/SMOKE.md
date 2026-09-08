# Turbo-Acquirer Manual Smoke Tests

## Execution record — 2026-09-08

Both Swift test suites passed. The current debug acquirer was then exercised
against real hardware, outside the filesystem sandbox. Its permissions probe
reported `microphone: authorized` and `screenRecording: true`.

| CLI probe | Observed result |
|---|---|
| mic-only, SIGTERM after 6 s | Exit 0; 380,928-byte valid WAVE, mono 16 kHz Float32, 5.888 s |
| system+mic, SIGTERM after 6 s | Exit 0; 364,544-byte valid WAVE, mono 16 kHz Float32, 5.632 s |
| full-display, SIGTERM after 6 s | Exit 0; one 1728×1117 PNG; manifest duration 4,486 ms |
| fixed region `200,200,640,480` | Exit 0; one 640×480 PNG; manifest duration 4,469 ms |

PNG signatures, dimensions, and frame timestamps within the manifest duration
were checked. Audio metadata was validated with `afinfo`; this does not establish
audible content quality or separation of the system and microphone sources.
Temporary recordings were removed after inspection. Screen duration excludes
startup and the capture-area overlay.

Still open: native window selection, interactive region dragging, HUD and
Raycast Stop, full capture-to-Obsidian output, and permission-denied/migration
UI scenarios. Computer Use was blocked waiting for ChatGPT's Accessibility and
Screen Recording permissions; no permission settings were changed.

---

Steps that require real audio hardware, real SCStream, or an active TCC
grant cannot be driven by `pytest`. Run through this checklist after
`turbo sidecar` completes (which builds both binaries and writes symlinks
to `~/.local/bin/`).

All steps assume the binaries are available on PATH via the sidecar
symlinks. Adjust paths if you are running from the build tree directly.

---

## 1. Permissions state

```bash
turbo-acquirer permissions-state
```

Expected: JSON printed to stdout, exit 0. Example:

```json
{"microphone":"authorized","screenRecording":true}
```

If `microphone` is `notDetermined`, proceed to step 2 to trigger a real
grant prompt.

---

## 2. mic-only recording (5 s fixture)

```bash
turbo-acquirer record-audio --scope mic-only --output /tmp/smoke-mic.wav &
ACQUIRER_PID=$!
sleep 5
kill -TERM $ACQUIRER_PID
wait $ACQUIRER_PID
```

Expected:
- First run triggers macOS Microphone permission dialog. Grant it.
- `/tmp/smoke-mic.wav` exists after the process exits.
- File is non-zero in size (`ls -lh /tmp/smoke-mic.wav`).
- File is a valid WAV: `file /tmp/smoke-mic.wav` should say "RIFF (little-endian) data, WAVE audio".
- Process exits 0: `echo $?` should print `0`.

---

## 3. system+mic recording (5 s fixture)

```bash
turbo-acquirer record-audio --scope system+mic --output /tmp/smoke-sysMic.wav &
ACQUIRER_PID=$!
sleep 5
kill -TERM $ACQUIRER_PID
wait $ACQUIRER_PID
```

Expected:
- First run triggers macOS Screen Recording permission dialog. Grant it.
- `/tmp/smoke-sysMic.wav` exists, is non-zero, is a valid WAV.
- Process exits 0.

If Screen Recording was already denied: the process should exit non-zero
and stderr should contain `permissionDenied`. Re-grant in System Settings
→ Privacy & Security → Screen Recording, then rerun.

---

## 4. record-to-obsidian via turbo CLI (headless end-to-end)

This is the key Phase 1 scenario: the Python CLI orchestrates `turbo-acquirer`
and the whole pipeline, without the HUD in the loop.

```bash
# Set your vault path (or rely on the default $HOME/Documents/Obsidian).
export OBSIDIAN_VAULT="$HOME/Documents/Obsidian"

# Run the workflow — will call turbo-acquirer to record audio.
turbo workflows run record-to-obsidian
```

At the prompt (or automatically after `turbo-acquirer` starts recording):
- Let it record for 5+ seconds.
- Send SIGINT or press Ctrl+C to stop recording.

Expected:
- A file `$OBSIDIAN_VAULT/Meetings/<slug>.md` is created.
- A file `$OBSIDIAN_VAULT/Meetings/<slug>.raw.md` is created.
- `<slug>.raw.md` contains lines in `[mm:ss] <text>` format.
- `<slug>.md` contains YAML frontmatter (`---`) and a summary section.

---

## 5. record-to-obsidian via HUD menu (HUD integration)

With TurboHUD running (launched via `turbo sidecar`):

1. Click the lightning icon in the menu bar.
2. Select `record-to-obsidian` → Run.
3. Observe the menu icon changes to indicate recording is active.
4. Wait 5+ seconds.
5. Click Stop in the menu.

Expected:
- Same file output as step 4.
- Menu icon returns to idle state after Stop.

---

## 6. TCC migration notice (first-launch scenario)

To test the migration notice on a machine that has already granted
permissions to `turbo-acquirer`:

```bash
# Clear the flag in UserDefaults (macOS standard user domain).
defaults delete com.apple.UserNotifications com.turbollm.migration-notice-v1 2>/dev/null || true
# Also clear the HUD-side flag.
defaults delete com.turbollm.hud migration_notice_shown_v1 2>/dev/null || true
```

Then reset the acquirer's mic grant temporarily:

1. System Settings → Privacy & Security → Microphone.
2. Toggle off `turbo-acquirer` (or the HUD if that's what holds the grant today).
3. Launch TurboHUD.

Expected: a macOS notification appears with the message:
"Turbo's recording backend moved to a new binary. You'll be asked to
grant Microphone (and Screen Recording, if you use system+mic scope)
on first recording."

Subsequent HUD launches should not show the notice again.

---

## 7. Permission-denied error paths

```bash
# With mic revoked (System Settings → Privacy → Microphone → revoke turbo-acquirer):
turbo-acquirer record-audio --scope mic-only --output /tmp/denied.wav
```

Expected: process exits non-zero; stderr contains `permissionDenied`.

---

## Pass criteria

All 7 steps above complete without unexpected errors and match the expected
behaviors described. Document any deviations as issues before merging.

---

## record-meeting-with-screen end-to-end (Phase 2)

Full workflow smoke: audio + screen recording → transcription → bundled
Obsidian note with interleaved keyframes. Run all three launch paths.

### Prerequisites

- `turbo sidecar` has run (binaries on PATH, Raycast extension synced).
- Screen Recording + Microphone permissions granted.
- `$OBSIDIAN_VAULT` set or `~/Documents/Obsidian` exists.

### Path A — HUD menu

1. Start `turbo sidecar` (menu-bar lightning icon appears).
2. Click the lightning icon → select **record-meeting-with-screen** → **▶ Run**.
3. Change windows / switch slides for ~30 seconds so the screen recorder
   captures several distinct keyframes.
4. Click **Stop** in the HUD menu.
5. Confirm three artifacts exist:
   - `$OBSIDIAN_VAULT/Meetings/<slug>.md` — YAML frontmatter + LLM summary,
     no `![[...]]` image references.
   - `$OBSIDIAN_VAULT/Meetings/<slug>.raw.md` — frontmatter + interleaved
     `[mm:ss] <transcript>` lines and `![[<slug>/<name>.png]]` image refs.
   - `$OBSIDIAN_VAULT/Meetings/attachments/<slug>/` — directory of PNG
     keyframe files referenced by `<slug>.raw.md`.

### Path B — terminal CLI

```bash
export OBSIDIAN_VAULT="$HOME/Documents/Obsidian"
turbo workflows run record-meeting-with-screen
```

- Let it record for ~30 seconds (switch windows/slides during recording).
- Press **Ctrl+C** (or send SIGINT) to stop.
- Confirm the same three artifacts appear under `$OBSIDIAN_VAULT/Meetings/`.

### Path C — Raycast

1. Open Raycast → search **"Record Meeting With Screen"**.
2. Select the command → fill in the vault path if prompted → **Submit**.
3. Switch windows / change slides for ~30 seconds.
4. Click **Stop** from the running-workflows view or the HUD.
5. Confirm the same three artifacts.

### Pass criteria

- All three launch paths produce `.md`, `.raw.md`, and `attachments/<slug>/`.
- `<slug>.raw.md` contains at least one `![[<slug>/....png]]` line
  (i.e. at least one keyframe was captured).
- `<slug>.md` contains no `![[...]]` references.
- The temp screen-recording directory (`/tmp/turbo-session-*/`) is cleaned
  up after the run.
- No orphaned `turbo-acquirer` or `python3` processes remain after Stop.

---

## record-screen standalone (Phase 2)

Smoke test for the `record-screen` subcommand in isolation — without the
full bundling pipeline.

```bash
mkdir -p /tmp/test-record-screen

# Start recording (runs until SIGTERM/SIGINT).
turbo-acquirer record-screen \
  --output-dir /tmp/test-record-screen \
  --scope full-display &
ACQUIRER_PID=$!

# Change windows or switch apps for ~5 seconds so keyframes are captured.
sleep 5

# Stop by sending SIGTERM.
kill -TERM $ACQUIRER_PID
wait $ACQUIRER_PID
```

Expected:

- Process exits 0 (or with status matching SIGTERM — both are acceptable).
- `stdout` contains a JSON manifest, for example:
  ```json
  {"dropped_overcap":0,"duration_ms":5123,"frames":[...],"start_offset_ms":0}
  ```
- `/tmp/test-record-screen/` contains one or more `*.png` keyframe files
  whose paths match the `frames[*].path` entries in the manifest.
- Each frame's `t_offset_ms` is non-negative and less than `duration_ms`.

Cleanup:

```bash
rm -rf /tmp/test-record-screen
```
