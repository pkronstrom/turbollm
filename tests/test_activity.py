import json
import os
from pathlib import Path

import pytest

from turbollm import activity


def _redirect_state_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point activity.STATE_DIR at a tmp dir so tests don't touch ~/.turbollm."""
    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")
    return tmp_path / "state"


def test_start_activity_writes_file(monkeypatch, tmp_path):
    state_dir = _redirect_state_dir(monkeypatch, tmp_path)
    aid = activity.start_activity(kind="workflow", label="Test run", icon="play")
    files = list(state_dir.glob("activity-*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["id"] == aid
    assert data["kind"] == "workflow"
    assert data["label"] == "Test run"
    assert data["icon"] == "play"
    assert data["owner_pid"] == os.getpid()
    assert "started_at" in data


def test_update_activity_merges_fields(monkeypatch, tmp_path):
    _redirect_state_dir(monkeypatch, tmp_path)
    aid = activity.start_activity(kind="workflow", label="First")
    activity.update_activity(aid, label="Second", phase="processing")
    data = json.loads(activity.activity_path(aid).read_text())
    assert data["label"] == "Second"
    assert data["phase"] == "processing"
    assert data["kind"] == "workflow"  # preserved


def test_clear_activity_deletes_file(monkeypatch, tmp_path):
    state_dir = _redirect_state_dir(monkeypatch, tmp_path)
    aid = activity.start_activity(kind="workflow", label="X")
    activity.clear_activity(aid)
    assert list(state_dir.glob("activity-*.json")) == []


def test_clear_activity_idempotent_on_missing(monkeypatch, tmp_path):
    _redirect_state_dir(monkeypatch, tmp_path)
    activity.clear_activity("nonexistent-id")  # must not raise


def test_list_activities_returns_all(monkeypatch, tmp_path):
    _redirect_state_dir(monkeypatch, tmp_path)
    activity.start_activity(kind="workflow", label="A")
    activity.start_activity(kind="server", label="B")
    items = activity.list_activities()
    assert len(items) == 2
    kinds = {it["kind"] for it in items}
    assert kinds == {"workflow", "server"}


def test_list_activities_gcs_dead_pid(monkeypatch, tmp_path):
    state_dir = _redirect_state_dir(monkeypatch, tmp_path)
    state_dir.mkdir(parents=True)
    # Write a fake entry with a PID that's almost certainly dead
    fake = {
        "id": "fake-dead",
        "kind": "workflow",
        "label": "Dead",
        "started_at": "2026-01-15T10:00:00Z",
        "owner_pid": 999999,  # unlikely to exist
        "icon": None, "color": None, "phase": None,
    }
    (state_dir / "activity-fake-dead.json").write_text(json.dumps(fake))

    items = activity.list_activities()
    # GC happens transparently; the dead entry is purged
    assert not any(it["id"] == "fake-dead" for it in items)
    assert not (state_dir / "activity-fake-dead.json").exists()


def test_list_activities_keeps_alive_pid(monkeypatch, tmp_path):
    _redirect_state_dir(monkeypatch, tmp_path)
    aid = activity.start_activity(kind="workflow", label="Alive")  # uses os.getpid()
    items = activity.list_activities()
    assert any(it["id"] == aid for it in items)


def test_update_activity_tolerates_file_removed_concurrently(monkeypatch, tmp_path):
    """update_activity must not raise if the file vanished between an
    existence check and the read (e.g. removed by clear_activity or
    `turbo prune` running concurrently) — it should just no-op."""
    _redirect_state_dir(monkeypatch, tmp_path)
    # No start_activity call — activity_path("ghost") never existed.
    activity.update_activity("ghost", label="whatever")  # must not raise


def test_clear_activity_is_atomic_missing_ok(monkeypatch, tmp_path):
    """clear_activity uses unlink(missing_ok=True) rather than
    exists()-then-unlink, so a file removed between the two by another
    process can't cause a FileNotFoundError here."""
    state_dir = _redirect_state_dir(monkeypatch, tmp_path)
    aid = activity.start_activity(kind="workflow", label="X")
    # Simulate another process having already removed it.
    activity.activity_path(aid).unlink()
    activity.clear_activity(aid)  # must not raise
    assert list(state_dir.glob("activity-*.json")) == []


def test_pid_alive_rejects_zero_and_negative_pid():
    """pid 0 addresses the caller's own process group and negative pids
    address a process group — os.kill(pid, 0) on either "succeeds" without
    checking any single real process, so pid_alive must special-case them
    rather than reporting them as alive."""
    assert activity.pid_alive(0) is False
    assert activity.pid_alive(-1) is False
    assert activity.pid_alive(-999) is False


def test_pid_alive_true_for_self():
    assert activity.pid_alive(os.getpid()) is True
