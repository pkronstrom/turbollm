"""File-based activity registry for cross-process HUD state visibility.

Each long-running turbollm operation writes a JSON file
`~/.turbollm/state/activity-<id>.json` at start, updates it on phase
transitions, and deletes it on exit. The Swift HUD (and any future frontend)
watches this directory to reflect running activity in its status icon.

See internal HUD design spec ("Cross-frontend
activity model") for the schema and motivation.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import os as _os
import tempfile as _tempfile
import uuid as _uuid
from pathlib import Path


STATE_DIR = Path.home() / ".turbollm" / "state"


def _atomic_write(path: Path, data: str) -> None:
    """Write `data` to `path` atomically (write to tmp + rename).

    Prevents partial reads by other processes when the activity file is being
    updated mid-tick by the HUD's filesystem watcher.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = _tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with _os.fdopen(fd, "w") as f:
            f.write(data)
        _os.replace(tmp_name, path)
    except Exception:
        try:
            _os.unlink(tmp_name)
        except OSError:
            pass
        raise


def activity_path(activity_id: str) -> Path:
    return STATE_DIR / f"activity-{activity_id}.json"


def start_activity(
    kind: str,
    label: str,
    *,
    icon: str | None = None,
    color: str | None = None,
    phase: str | None = None,
    activity_id: str | None = None,
) -> str:
    """Write a new activity file. Returns the activity id."""
    aid = activity_id or f"{kind}-{_uuid.uuid4().hex[:12]}"
    data = {
        "id": aid,
        "kind": kind,
        "label": label,
        "icon": icon,
        "color": color,
        "phase": phase,
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "owner_pid": _os.getpid(),
    }
    _atomic_write(activity_path(aid), _json.dumps(data, indent=2))
    return aid


def update_activity(activity_id: str, **fields) -> None:
    """Merge `fields` into the activity file. Silently no-ops if missing.

    Reads via try/except rather than exists()-then-read to avoid a TOCTOU
    race: the file can be removed (e.g. by `clear_activity` in another
    thread/process, or `turbo prune`) between an existence check and the
    read that follows it.
    """
    path = activity_path(activity_id)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return
    data = _json.loads(raw)
    data.update({k: v for k, v in fields.items() if v is not None})
    _atomic_write(path, _json.dumps(data, indent=2))


def clear_activity(activity_id: str) -> None:
    """Delete an activity file. No-op if it doesn't exist."""
    activity_path(activity_id).unlink(missing_ok=True)


def list_activities() -> list[dict]:
    """Return all current activities. GCs stale entries (dead PID) as a side effect."""
    if not STATE_DIR.exists():
        return []
    items: list[dict] = []
    for path in STATE_DIR.glob("activity-*.json"):
        try:
            data = _json.loads(path.read_text())
        except (OSError, _json.JSONDecodeError):
            continue
        pid = data.get("owner_pid")
        if pid and not pid_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        items.append(data)
    return items


def pid_alive(pid: int) -> bool:
    """Check whether `pid` is still running.

    Uses the signal-0 trick: `kill(pid, 0)` sends no actual signal but still
    performs the kernel's existence + permission check, per POSIX kill(2). A
    `PermissionError` means the process exists but we lack rights to signal
    it — that still counts as alive.

    `pid <= 0` is guarded explicitly: 0 means "every process in the caller's
    process group" and negative values address a process group, so
    `os.kill(pid, 0)` on either would report "alive" without checking any
    single real process.

    Caveat: macOS recycles PIDs over time (~30k cycle), so a long-running
    activity file whose owner died days ago could theoretically have its PID
    re-issued to an unrelated process and look "alive" here. In practice the
    24h-staleness filter + sequential PID issuance make this negligible.
    """
    if pid <= 0:
        return False
    try:
        _os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still counts as alive
        return True
    return True
