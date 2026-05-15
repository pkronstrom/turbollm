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
import uuid as _uuid
from pathlib import Path


STATE_DIR = Path.home() / ".turbollm" / "state"


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
    STATE_DIR.mkdir(parents=True, exist_ok=True)
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
    activity_path(aid).write_text(_json.dumps(data, indent=2))
    return aid


def update_activity(activity_id: str, **fields) -> None:
    """Merge `fields` into the activity file. Silently no-ops if missing."""
    path = activity_path(activity_id)
    if not path.exists():
        return
    data = _json.loads(path.read_text())
    data.update({k: v for k, v in fields.items() if v is not None})
    path.write_text(_json.dumps(data, indent=2))


def clear_activity(activity_id: str) -> None:
    """Delete an activity file. No-op if it doesn't exist."""
    path = activity_path(activity_id)
    if path.exists():
        path.unlink()


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
        if pid and not _pid_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        items.append(data)
    return items


def _pid_alive(pid: int) -> bool:
    """Check whether `pid` is still running. Uses signal-0 trick — no permissions check."""
    try:
        _os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still counts as alive
        return True
    return True
