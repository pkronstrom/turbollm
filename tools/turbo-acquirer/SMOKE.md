# Turbo-Acquirer Manual Smoke Tests

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

1. Click the turtle icon in the menu bar.
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
