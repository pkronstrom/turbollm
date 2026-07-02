"""Tests for the safe-by-default `turbo prune` machinery.

Covers each category scanner in isolation (so each can be reasoned about
without the others polluting the fixture) plus the aggregate + execute
helpers in `turbollm.prune`.
"""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import time

import pytest

from turbollm import prune


# ── recordings ────────────────────────────────────────────────────────────────


def test_recordings_scanner_picks_only_transcribed(tmp_path: pathlib.Path):
    """A .wav is pruneable iff `<vault>/Meetings/<stem>.md` exists."""
    rec = tmp_path / "rec"
    rec.mkdir()
    vault = tmp_path / "vault"
    meetings = vault / "Meetings"
    meetings.mkdir(parents=True)

    transcribed = rec / "alpha.wav"
    transcribed.write_bytes(b"AAAA")
    (meetings / "alpha.md").write_text("# alpha")

    untranscribed = rec / "beta.wav"
    untranscribed.write_bytes(b"BBBB")

    targets = prune.scan_transcribed_recordings(rec, vault)
    paths = {t.path for t in targets}
    assert transcribed in paths
    assert untranscribed not in paths


def test_recordings_scanner_returns_empty_when_dir_missing(tmp_path: pathlib.Path):
    assert prune.scan_transcribed_recordings(tmp_path / "nope", tmp_path / "v") == []


def test_recordings_scanner_returns_empty_when_meetings_dir_missing(tmp_path: pathlib.Path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "x.wav").write_bytes(b"X")
    # vault exists but has no Meetings/
    (tmp_path / "vault").mkdir()
    assert prune.scan_transcribed_recordings(rec, tmp_path / "vault") == []


def test_recordings_scanner_reports_size(tmp_path: pathlib.Path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (tmp_path / "vault" / "Meetings").mkdir(parents=True)
    payload = b"x" * 1234
    (rec / "size.wav").write_bytes(payload)
    (tmp_path / "vault" / "Meetings" / "size.md").write_text("ok")

    [target] = prune.scan_transcribed_recordings(rec, tmp_path / "vault")
    assert target.size_bytes == len(payload)


# ── activities ────────────────────────────────────────────────────────────────


def test_activities_scanner_keeps_live(tmp_path: pathlib.Path):
    """Files whose owner_pid is alive must be left alone."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "activity-live.json").write_text(json.dumps({"owner_pid": os.getpid()}))
    assert prune.scan_stale_activities(state) == []


def test_activities_scanner_removes_dead(tmp_path: pathlib.Path):
    state = tmp_path / "state"
    state.mkdir()
    dead_path = state / "activity-dead.json"
    dead_pid = _spawn_throwaway_pid()
    dead_path.write_text(json.dumps({"owner_pid": dead_pid}))
    targets = prune.scan_stale_activities(state)
    assert [t.path for t in targets] == [dead_path]


def test_activities_scanner_treats_malformed_as_stale(tmp_path: pathlib.Path):
    """A garbage activity file can't possibly have a live owner."""
    state = tmp_path / "state"
    state.mkdir()
    garbage = state / "activity-garbage.json"
    garbage.write_text("not json {{{")
    targets = prune.scan_stale_activities(state)
    assert [t.path for t in targets] == [garbage]


def test_activities_scanner_returns_empty_when_dir_missing(tmp_path: pathlib.Path):
    assert prune.scan_stale_activities(tmp_path / "missing") == []


# ── locks ─────────────────────────────────────────────────────────────────────


def test_locks_scanner_keeps_held(tmp_path: pathlib.Path):
    """A lock file held by a live fd must not be pruneable."""
    run = tmp_path / "run"
    run.mkdir()
    lock_path = run / "held.lock"
    lock_path.write_text("")
    # Age it past the minimum so only the held-ness gates it out.
    _set_old_mtime(lock_path)
    holder = open(lock_path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert prune.scan_stale_locks(run) == []
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_locks_scanner_keeps_recent(tmp_path: pathlib.Path):
    """An unheld lock that's only seconds old is NOT pruneable (race guard)."""
    run = tmp_path / "run"
    run.mkdir()
    lock_path = run / "recent.lock"
    lock_path.write_text("")
    # mtime defaults to now; do nothing
    assert prune.scan_stale_locks(run) == []


def test_locks_scanner_removes_aged_unheld(tmp_path: pathlib.Path):
    run = tmp_path / "run"
    run.mkdir()
    lock_path = run / "stale.lock"
    lock_path.write_text("")
    _set_old_mtime(lock_path)
    [target] = prune.scan_stale_locks(run)
    assert target.path == lock_path


def test_locks_scanner_returns_empty_when_dir_missing(tmp_path: pathlib.Path):
    assert prune.scan_stale_locks(tmp_path / "missing") == []


# ── session dirs ──────────────────────────────────────────────────────────────


def test_session_dirs_scanner_picks_aged_only(tmp_path: pathlib.Path):
    """`turbo-session-*` dirs older than 1h are pruneable; recent ones aren't."""
    fresh = tmp_path / "turbo-session-fresh"
    fresh.mkdir()
    aged = tmp_path / "turbo-session-aged"
    aged.mkdir()
    _set_old_mtime(aged)

    targets = prune.scan_orphan_session_dirs(tmp_path)
    paths = {t.path for t in targets}
    assert aged in paths
    assert fresh not in paths
    [t] = [t for t in targets if t.path == aged]
    assert t.is_dir is True


def test_session_dirs_scanner_ignores_unrelated_dirs(tmp_path: pathlib.Path):
    unrelated = tmp_path / "some-other-thing"
    unrelated.mkdir()
    _set_old_mtime(unrelated)
    assert prune.scan_orphan_session_dirs(tmp_path) == []


def test_session_dirs_scanner_reports_size(tmp_path: pathlib.Path):
    d = tmp_path / "turbo-session-x"
    d.mkdir()
    nested = d / "nested"
    nested.mkdir()
    a = d / "a.png"
    a.write_bytes(b"a" * 100)
    b = nested / "b.png"
    b.write_bytes(b"b" * 250)
    # Staleness is judged by the newest file mtime inside the dir, not the
    # dir's own mtime — age everything.
    _set_old_mtime(d)
    _set_old_mtime(a)
    _set_old_mtime(b)
    [target] = prune.scan_orphan_session_dirs(tmp_path)
    assert target.size_bytes == 350


def test_session_dirs_scanner_protects_dir_with_recently_touched_file(tmp_path: pathlib.Path):
    """A session dir with an old dir-mtime but a recently-written file inside
    (e.g. the acquirer still appending to the recording) must NOT be pruned —
    the dir mtime alone doesn't change while a file inside it is written to."""
    d = tmp_path / "turbo-session-live"
    d.mkdir()
    _set_old_mtime(d)
    live_file = d / "recording.wav"
    live_file.write_bytes(b"still recording")  # fresh mtime (now)

    assert prune.scan_orphan_session_dirs(tmp_path) == []


def test_session_dirs_scanner_empty_dir_falls_back_to_dir_mtime(tmp_path: pathlib.Path):
    """An empty aged dir has no files to check — falls back to the dir's own mtime."""
    d = tmp_path / "turbo-session-empty"
    d.mkdir()
    _set_old_mtime(d)
    targets = prune.scan_orphan_session_dirs(tmp_path)
    assert [t.path for t in targets] == [d]


# ── aggregator + execute ──────────────────────────────────────────────────────


def test_gather_returns_all_categories(tmp_path: pathlib.Path):
    """gather_prune_targets returns the canonical category set even when empty."""
    targets = prune.gather_prune_targets(
        recordings_dir=tmp_path / "rec",
        vault=tmp_path / "vault",
        state_dir=tmp_path / "state",
        run_dir=tmp_path / "run",
        tmp_root=tmp_path / "tmp",
    )
    assert set(targets.keys()) == set(prune.CATEGORY_ORDER)
    assert all(items == [] for items in targets.values())


def test_execute_prune_removes_files_and_dirs(tmp_path: pathlib.Path):
    file_target = tmp_path / "file.bin"
    file_target.write_bytes(b"hello")
    dir_target = tmp_path / "dir"
    dir_target.mkdir()
    (dir_target / "nested.bin").write_bytes(b"world")

    deleted, freed = prune.execute_prune([
        prune.PruneTarget(path=file_target, size_bytes=5),
        prune.PruneTarget(path=dir_target, size_bytes=5, is_dir=True),
    ])
    assert deleted == 2
    assert freed == 10
    assert not file_target.exists()
    assert not dir_target.exists()


def test_execute_prune_tolerates_missing_targets(tmp_path: pathlib.Path):
    """A target that disappears between scan and delete is silently skipped."""
    gone = tmp_path / "gone.bin"
    deleted, freed = prune.execute_prune([prune.PruneTarget(path=gone, size_bytes=10)])
    assert deleted == 0
    assert freed == 0


def test_execute_prune_result_unpacks_as_two_tuple_and_carries_failures(tmp_path: pathlib.Path):
    """PruneResult stays unpackable as (deleted, freed) for existing callers
    (e.g. cli.py's `deleted, freed = execute_prune(...)`), while new callers
    can additionally read `.failures`."""
    ok = tmp_path / "ok.bin"
    ok.write_bytes(b"x" * 3)
    result = prune.execute_prune([prune.PruneTarget(path=ok, size_bytes=3)])
    deleted, freed = result  # must not raise
    assert (deleted, freed) == (1, 3)
    assert result.failures == []


def test_execute_prune_reports_oserror_failures_instead_of_swallowing(tmp_path: pathlib.Path, monkeypatch):
    """A deletion that raises OSError (other than FileNotFoundError) must be
    collected in `.failures`, not silently dropped."""
    target = tmp_path / "stubborn.bin"
    target.write_bytes(b"x")

    def _boom(self):
        raise PermissionError("nope")

    monkeypatch.setattr(pathlib.Path, "unlink", _boom)
    result = prune.execute_prune([prune.PruneTarget(path=target, size_bytes=1)])
    deleted, freed = result
    assert deleted == 0
    assert freed == 0
    assert len(result.failures) == 1
    assert result.failures[0][0] == target


def test_execute_prune_reprobes_lock_before_unlink_and_skips_if_reheld(tmp_path: pathlib.Path):
    """A lock file re-acquired by a workflow between scan and delete (the
    split-brain race) must not be unlinked out from under its new holder."""
    lock_path = tmp_path / "reacquired.lock"
    lock_path.write_text("")
    holder = open(lock_path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = prune.execute_prune([prune.PruneTarget(path=lock_path, size_bytes=0)])
        deleted, freed = result
        assert deleted == 0
        assert freed == 0
        assert len(result.failures) == 1
        assert result.failures[0][0] == lock_path
        assert lock_path.exists()  # not deleted — still held
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_execute_prune_deletes_lock_confirmed_still_unheld(tmp_path: pathlib.Path):
    """An unheld lock file is deleted normally (the re-probe doesn't false-positive)."""
    lock_path = tmp_path / "still-unheld.lock"
    lock_path.write_text("")
    result = prune.execute_prune([prune.PruneTarget(path=lock_path, size_bytes=0)])
    deleted, freed = result
    assert deleted == 1
    assert not lock_path.exists()
    assert result.failures == []


# ── CLI command ───────────────────────────────────────────────────────────────


def test_cli_dry_run_prints_table_and_does_not_delete(tmp_path: pathlib.Path):
    """`turbo prune --dry-run` lists candidates without deleting them."""
    from click.testing import CliRunner

    from turbollm.cli import cli

    rec = tmp_path / "rec"
    rec.mkdir()
    vault = tmp_path / "vault"
    (vault / "Meetings").mkdir(parents=True)
    wav = rec / "x.wav"
    wav.write_bytes(b"x" * 42)
    (vault / "Meetings" / "x.md").write_text("ok")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "prune", "--dry-run",
        "--audio-dir", str(rec),
        "--vault", str(vault),
    ])
    assert result.exit_code == 0, result.output
    assert wav.exists()  # still there
    assert "x.wav" in result.output


def test_cli_real_run_deletes(tmp_path: pathlib.Path):
    """Without --dry-run, `turbo prune` actually removes the targets."""
    from click.testing import CliRunner

    from turbollm.cli import cli

    rec = tmp_path / "rec"
    rec.mkdir()
    vault = tmp_path / "vault"
    (vault / "Meetings").mkdir(parents=True)
    wav = rec / "x.wav"
    wav.write_bytes(b"x" * 42)
    (vault / "Meetings" / "x.md").write_text("ok")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "prune",
        "--audio-dir", str(rec),
        "--vault", str(vault),
    ])
    assert result.exit_code == 0, result.output
    assert not wav.exists()


def test_cli_nothing_to_prune_short_circuits(tmp_path: pathlib.Path, monkeypatch):
    """When all scanners come back empty, command exits cleanly with a tiny note."""
    from click.testing import CliRunner

    from turbollm.cli import cli

    # Route every scanner to empty tmp dirs so nothing matches.
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(cli, [
        "prune", "--dry-run",
        "--audio-dir", str(tmp_path / "rec"),
        "--vault", str(tmp_path / "vault"),
    ])
    assert result.exit_code == 0, result.output
    assert "Nothing to prune" in result.output


# ── helpers ───────────────────────────────────────────────────────────────────


def _spawn_throwaway_pid() -> int:
    """Spawn a child that exits immediately; reap it; return its now-dead PID.

    Reusing PIDs is theoretically possible but vanishingly unlikely between
    `wait()` and the assertion in the same test (the kernel doesn't re-issue
    a PID until the slot has been recycled — typically minutes).
    """
    import subprocess
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _set_old_mtime(p: pathlib.Path) -> None:
    """Backdate p's mtime to 2 hours ago (well past every scanner's min age)."""
    two_hours_ago = time.time() - 2 * 3600
    os.utime(p, (two_hours_ago, two_hours_ago))
