"""Safe-by-default pruning of turbollm's intermediate artifacts.

Removes things turbo produces but does not need to keep: post-transcription
audio recordings, stale activity files, unheld + aged workflow lock files,
orphaned screen-recording temp directories. Never touches anything under
the Obsidian vault, and never removes an artifact that a live process is
still using.

Each scanner returns a list of `PruneTarget(path, size_bytes)`. Callers
aggregate via `gather_prune_targets()` and either render a dry-run summary
or call `execute_prune()` to delete.
"""
from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
from typing import Iterable

from turbollm.activity import pid_alive as _is_pid_alive


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PruneTarget:
    path: pathlib.Path
    size_bytes: int
    is_dir: bool = False


# Lock files written by a recently-finished workflow may be re-acquired within
# milliseconds by another invocation. Don't bin one until it has been quiet
# for at least this long.
_LOCK_MIN_AGE_SECONDS = 3600

# Screen-recording temp dirs that get orphaned by crashes. Wait at least this
# long before treating one as stale — the user might have a recording in
# progress and not yet have an activity file written for it.
_SESSION_DIR_MIN_AGE_SECONDS = 3600


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------


def default_recordings_dir() -> pathlib.Path:
    inbox = os.environ.get("TURBO_AUDIO_INBOX")
    if inbox:
        return pathlib.Path(inbox)
    return pathlib.Path.home() / "Recordings" / "turbo"


def default_vault() -> pathlib.Path:
    """Best-guess vault location.

    Resolution order:
      1. ``$OBSIDIAN_VAULT`` (explicit)
      2. ``com.turbollm.hud`` UserDefaults key
         ``workflow.record-to-obsidian.param.vault`` — written by the HUD /
         Raycast when the user picks a vault for the meeting-recording flow.
      3. ``~/Documents/Obsidian``
    """
    vault = os.environ.get("OBSIDIAN_VAULT")
    if vault:
        return pathlib.Path(vault)
    sticky = _read_hud_param_sticky("record-to-obsidian", "vault")
    if sticky:
        return pathlib.Path(sticky)
    return pathlib.Path.home() / "Documents" / "Obsidian"


def _read_hud_param_sticky(workflow_name: str, param_name: str) -> str | None:
    """Look up a per-(workflow, param) sticky value from the HUD's UserDefaults.

    Uses the `workflow.<wf>.param.<name>` key convention written by
    `turbo workflows config` and the Raycast extension. Returns None on any
    failure (non-macOS, key missing, defaults unavailable).
    """
    key = f"workflow.{workflow_name}.param.{param_name}"
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", "com.turbollm.hud", key],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def default_state_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".turbollm" / "state"


def default_run_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".turbollm" / "run"


def default_session_tmp_root() -> pathlib.Path:
    """Where screen-recording temp dirs land (see workflows._acquirer_argv)."""
    return pathlib.Path(tempfile.gettempdir())


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------


def scan_transcribed_recordings(
    recordings_dir: pathlib.Path,
    vault: pathlib.Path,
) -> list[PruneTarget]:
    """Audio recordings whose transcript already lives in the vault.

    A recording at `<recordings_dir>/<stem>.wav` is considered transcribed
    when `<vault>/Meetings/<stem>.md` exists. Untranscribed recordings are
    left alone — losing audio with no surviving transcript is unrecoverable.
    """
    if not recordings_dir.is_dir():
        return []
    meetings = vault / "Meetings"
    if not meetings.is_dir():
        return []
    results: list[PruneTarget] = []
    for wav in sorted(recordings_dir.glob("*.wav")):
        if not wav.is_file():
            continue
        if (meetings / f"{wav.stem}.md").exists():
            try:
                size = wav.stat().st_size
            except OSError:
                continue
            results.append(PruneTarget(path=wav, size_bytes=size))
    return results


def scan_stale_activities(state_dir: pathlib.Path) -> list[PruneTarget]:
    """Activity JSONs whose owner_pid no longer maps to a running process."""
    if not state_dir.is_dir():
        return []
    results: list[PruneTarget] = []
    for entry in sorted(state_dir.glob("activity-*.json")):
        if not entry.is_file():
            continue
        try:
            raw = entry.read_text()
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            # Malformed activity file — safe to remove; can't have a live owner.
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            results.append(PruneTarget(path=entry, size_bytes=size))
            continue
        pid = data.get("owner_pid")
        if not isinstance(pid, int) or not _is_pid_alive(pid):
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            results.append(PruneTarget(path=entry, size_bytes=size))
    return results


def scan_stale_locks(run_dir: pathlib.Path) -> list[PruneTarget]:
    """Workflow lock files that are unheld AND quiet for at least 1 hour.

    The age gate prevents racing with a workflow that finished microseconds
    ago: another invocation may be about to re-acquire the same lock.
    """
    if not run_dir.is_dir():
        return []
    results: list[PruneTarget] = []
    cutoff = time.time() - _LOCK_MIN_AGE_SECONDS
    for entry in sorted(run_dir.glob("*.lock")):
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff:
            continue
        if not _flock_is_unheld(entry):
            continue
        results.append(PruneTarget(path=entry, size_bytes=stat.st_size))
    return results


def scan_orphan_session_dirs(tmp_root: pathlib.Path) -> list[PruneTarget]:
    """Screen-recording temp dirs (`turbo-session-*`) older than 1 hour.

    Created by `workflows._acquirer_argv` for screen-recording params; normally
    deleted by the bundled workflow on success. Survivors are crash debris.
    The age gate avoids killing an in-progress recording whose workflow hasn't
    finished yet.

    Staleness is judged by the *newest* mtime of any file inside the dir, not
    the dir's own mtime: the acquirer appends to files inside the dir while
    recording, which doesn't bump the directory entry's mtime. Using the dir
    mtime alone would let a long (>1h) in-progress recording look stale and
    get rmtree'd mid-recording — the exact case this age gate exists to
    prevent. Falls back to the dir's own mtime when it's empty.
    """
    if not tmp_root.is_dir():
        return []
    results: list[PruneTarget] = []
    cutoff = time.time() - _SESSION_DIR_MIN_AGE_SECONDS
    for entry in sorted(tmp_root.glob("turbo-session-*")):
        if not entry.is_dir():
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        newest = _newest_mtime_in_dir(entry, fallback=stat.st_mtime)
        if newest > cutoff:
            continue
        size = _dir_size_bytes(entry)
        results.append(PruneTarget(path=entry, size_bytes=size, is_dir=True))
    return results


# ---------------------------------------------------------------------------
# Aggregation + execution
# ---------------------------------------------------------------------------


CATEGORY_ORDER = ("recordings", "activities", "locks", "session_dirs")


def gather_prune_targets(
    *,
    recordings_dir: pathlib.Path | None = None,
    vault: pathlib.Path | None = None,
    state_dir: pathlib.Path | None = None,
    run_dir: pathlib.Path | None = None,
    tmp_root: pathlib.Path | None = None,
) -> dict[str, list[PruneTarget]]:
    """Run all scanners and return the targets keyed by category."""
    return {
        "recordings": scan_transcribed_recordings(
            recordings_dir or default_recordings_dir(),
            vault or default_vault(),
        ),
        "activities": scan_stale_activities(state_dir or default_state_dir()),
        "locks": scan_stale_locks(run_dir or default_run_dir()),
        "session_dirs": scan_orphan_session_dirs(tmp_root or default_session_tmp_root()),
    }


class PruneResult(tuple):
    """(deleted_count, bytes_freed) tuple, plus a `.failures` list.

    Subclassing `tuple` (rather than adding a 3rd positional element) keeps
    `deleted, freed = execute_prune(...)` working for existing callers while
    still letting new callers read `.failures` for deletions that errored out
    (as opposed to targets that simply vanished, which aren't failures).
    """

    def __new__(cls, deleted: int, freed: int, failures: list[tuple[pathlib.Path, str]]):
        self = super().__new__(cls, (deleted, freed))
        self.failures = failures
        return self


def execute_prune(targets: Iterable[PruneTarget]) -> PruneResult:
    """Delete every target. Returns a `PruneResult` (deleted_count, bytes_freed).

    Targets that disappear between scan and delete (races against a live
    workflow) are silently skipped — we only counted them as removable.

    A `.lock` target is re-probed for an unheld flock immediately before
    unlinking: `scan_stale_locks` only checked at scan time, and a workflow
    may have re-acquired the lock in the (potentially long, user-confirmation
    gated) window since. Deleting a held lock out from under its holder is a
    split-brain — two "owners" of the same logical lock. If it's now held,
    the deletion is skipped and recorded as a failure rather than silently
    dropped, so callers can surface it.

    Other deletion failures (permission errors, etc.) are likewise collected
    into `.failures` as `(path, error_message)` pairs instead of being
    swallowed — `except OSError: continue` was silently under-reporting
    deletions.
    """
    deleted = 0
    freed = 0
    failures: list[tuple[pathlib.Path, str]] = []
    for t in targets:
        if not t.is_dir and t.path.suffix == ".lock" and t.path.exists():
            if not _flock_is_unheld(t.path):
                failures.append((t.path, "lock re-acquired since scan; skipped"))
                continue
        try:
            if t.is_dir:
                shutil.rmtree(t.path)
            else:
                t.path.unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            failures.append((t.path, str(e)))
            continue
        deleted += 1
        freed += t.size_bytes
    return PruneResult(deleted, freed, failures)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# `_is_pid_alive` used to be a hand-rolled copy that mishandled pid <= 0
# (`os.kill(0, 0)` signals the caller's own process group and always
# succeeds, so pid 0 looked "alive"). `activity.pid_alive` has the correct
# version — import it under the old name so call sites here don't need to
# change.


def _flock_is_unheld(path: pathlib.Path) -> bool:
    """Probe whether the flock on `path` is currently unheld.
    Acquires + releases LOCK_EX|LOCK_NB; never blocks; never stamps."""
    try:
        fd = open(path, "r")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        return True
    finally:
        fd.close()


def _dir_size_bytes(path: pathlib.Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (pathlib.Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def _newest_mtime_in_dir(path: pathlib.Path, *, fallback: float) -> float:
    """Newest mtime of any file under `path`, or `fallback` if it has none."""
    newest = fallback
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                mtime = (pathlib.Path(root) / name).stat().st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
    return newest
