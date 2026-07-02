"""Tests for `turbo sidecar` (now the macOS plugin: turbollm.plugins.mac).

The sidecar command lives in turbollm.plugins.mac.sidecar and is attached to the
core CLI only on supported machines. These tests drive the command object
directly so they run on any platform without depending on plugin auto-detection,
and patch the plugin module's own names (not turbollm.cli).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from turbollm.plugins.mac import _common
from turbollm.plugins.mac import sidecar as sidecar_mod
from turbollm.plugins.mac.sidecar import _reset_stale_state, sidecar_cmd

# Patch targets live in the sidecar module's namespace (it imports the helpers
# by name from _common) and the raycast sync side-effect.
_SIDE = "turbollm.plugins.mac.sidecar"


def _no_raycast_sync():
    """Context-free helper: patch out the Raycast sync that fires on launch so
    tests never touch the real extension directory."""
    return patch(f"{_SIDE}._sidecar_raycast_sync", MagicMock())


def _no_reset():
    """Patch out the stale-state reset so tests never SIGKILL real acquirers or
    delete the real ~/.turbollm/state."""
    return patch(f"{_SIDE}._reset_stale_state", MagicMock())


# ---------------------------------------------------------------------------
# Existing tests (updated for the new two-binary sidecar)
# ---------------------------------------------------------------------------

def test_sidecar_errors_when_swift_package_missing(tmp_path):
    missing_hud = tmp_path / "tools" / "turbo-hud"  # does not exist
    runner = CliRunner()
    with (
        patch.object(_common, "hud_dir", return_value=missing_hud),
        patch(f"{_SIDE}.hud_dir", return_value=missing_hud),
        _no_reset(),
    ):
        result = runner.invoke(sidecar_cmd, [])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_sidecar_invokes_swift_run_for_hud(tmp_path):
    """sidecar falls back to `swift run` in the HUD directory when no built binary."""
    hud_dir = tmp_path / "tools" / "turbo-hud"
    hud_dir.mkdir(parents=True)
    (hud_dir / "Package.swift").write_text("")

    runner = CliRunner()
    with (
        patch(f"{_SIDE}.subprocess.run") as mock_run,
        patch(f"{_SIDE}.hud_dir", return_value=hud_dir),
        patch(f"{_SIDE}.acquirer_dir", return_value=tmp_path / "tools" / "no-acquirer"),
        _no_raycast_sync(),
        _no_reset(),
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(sidecar_cmd, [])

    assert result.exit_code == 0
    # Last call must be `swift run` in the HUD dir.
    last_call_args, last_call_kwargs = mock_run.call_args
    assert last_call_args[0][:2] == ["swift", "run"]
    assert last_call_kwargs.get("cwd") == hud_dir


# ---------------------------------------------------------------------------
# T-15 tests
# ---------------------------------------------------------------------------

def test_sidecar_builds_acquirer_when_present(tmp_path):
    """When tools/turbo-acquirer exists, sidecar runs swift build in that dir."""
    hud_dir = tmp_path / "tools" / "turbo-hud"
    hud_dir.mkdir(parents=True)
    (hud_dir / "Package.swift").write_text("")

    acq_dir = tmp_path / "tools" / "turbo-acquirer"
    acq_dir.mkdir(parents=True)
    (acq_dir / "Package.swift").write_text("")

    runner = CliRunner()
    with (
        patch(f"{_SIDE}.subprocess.run") as mock_run,
        patch(f"{_SIDE}.hud_dir", return_value=hud_dir),
        patch(f"{_SIDE}.acquirer_dir", return_value=acq_dir),
        patch(f"{_SIDE}.local_bin", return_value=tmp_path / "local_bin"),
        patch(f"{_SIDE}.swift_build_product_path",
              side_effect=lambda d, p: tmp_path / "no-binary"),  # binary absent → skip symlink
        _no_raycast_sync(),
        _no_reset(),
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(sidecar_cmd, [])

    assert result.exit_code == 0
    calls = [c.args[0] for c in mock_run.call_args_list]
    # swift build in acquirer dir must be called.
    assert any(c[:2] == ["swift", "build"] for c in calls), f"No swift build call: {calls}"
    acq_build_calls = [c for c in mock_run.call_args_list if c.args[0][:2] == ["swift", "build"]]
    assert any(c.kwargs.get("cwd") == acq_dir for c in acq_build_calls)


def test_sidecar_creates_acquirer_symlink_when_binary_exists(tmp_path):
    """When the acquirer binary exists after build, symlink is created."""
    hud_dir = tmp_path / "tools" / "turbo-hud"
    hud_dir.mkdir(parents=True)
    (hud_dir / "Package.swift").write_text("")

    acq_dir = tmp_path / "tools" / "turbo-acquirer"
    acq_dir.mkdir(parents=True)
    (acq_dir / "Package.swift").write_text("")

    # Create a fake built binary.
    fake_bin = acq_dir / ".build" / "debug" / "turbo-acquirer"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("#!/bin/sh\necho fake")

    local_bin = tmp_path / "local_bin"
    local_bin.mkdir()

    symlinked: list[tuple] = []

    def _fake_refresh_symlink(link_path, target_path):
        symlinked.append((link_path, target_path))

    runner = CliRunner()
    with (
        patch(f"{_SIDE}.subprocess.run") as mock_run,
        patch(f"{_SIDE}.hud_dir", return_value=hud_dir),
        patch(f"{_SIDE}.acquirer_dir", return_value=acq_dir),
        patch(f"{_SIDE}.local_bin", return_value=local_bin),
        patch(f"{_SIDE}.refresh_symlink", side_effect=_fake_refresh_symlink),
        _no_raycast_sync(),
        _no_reset(),
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(sidecar_cmd, [])

    assert result.exit_code == 0
    assert len(symlinked) == 1
    link_path, target_path = symlinked[0]
    assert link_path == local_bin / "turbo-acquirer"
    assert target_path == fake_bin


def test_sidecar_skips_acquirer_build_when_package_absent(tmp_path):
    """When tools/turbo-acquirer/Package.swift is missing, no acquirer build."""
    hud_dir = tmp_path / "tools" / "turbo-hud"
    hud_dir.mkdir(parents=True)
    (hud_dir / "Package.swift").write_text("")

    runner = CliRunner()
    with (
        patch(f"{_SIDE}.subprocess.run") as mock_run,
        patch(f"{_SIDE}.hud_dir", return_value=hud_dir),
        patch(f"{_SIDE}.acquirer_dir",
              return_value=tmp_path / "tools" / "turbo-acquirer"),  # doesn't exist
        _no_raycast_sync(),
        _no_reset(),
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(sidecar_cmd, [])

    assert result.exit_code == 0
    # No swift build should have been called in the (absent) acquirer dir.
    # The HUD swift build is still expected.
    absent_acq_dir = tmp_path / "tools" / "turbo-acquirer"
    acq_build_calls = [
        c for c in mock_run.call_args_list
        if c.args[0][:2] == ["swift", "build"] and c.kwargs.get("cwd") == absent_acq_dir
    ]
    assert acq_build_calls == [], f"Unexpected acquirer build calls: {acq_build_calls}"


def test_sidecar_continues_after_acquirer_build_failure(tmp_path):
    """If acquirer build fails, sidecar warns but still launches the HUD."""
    hud_dir = tmp_path / "tools" / "turbo-hud"
    hud_dir.mkdir(parents=True)
    (hud_dir / "Package.swift").write_text("")

    acq_dir = tmp_path / "tools" / "turbo-acquirer"
    acq_dir.mkdir(parents=True)
    (acq_dir / "Package.swift").write_text("")

    call_count = [0]

    def _failing_first_run(args, **kwargs):
        m = MagicMock()
        call_count[0] += 1
        # First call (swift build in acquirer_dir) fails; subsequent calls succeed.
        m.returncode = 1 if call_count[0] == 1 else 0
        return m

    runner = CliRunner()
    with (
        patch(f"{_SIDE}.subprocess.run", side_effect=_failing_first_run),
        patch(f"{_SIDE}.hud_dir", return_value=hud_dir),
        patch(f"{_SIDE}.acquirer_dir", return_value=acq_dir),
        _no_raycast_sync(),
        _no_reset(),
    ):
        result = runner.invoke(sidecar_cmd, [])

    assert result.exit_code == 0
    assert "Warning" in result.output or "warning" in result.output.lower()


def test_sidecar_builds_and_symlinks_hud_then_launches_via_symlink(tmp_path):
    """Review fix: sidecar must `swift build` the HUD, create the
    ~/.local/bin/TurboHUD symlink, and launch via the symlink (not via
    `swift run`) so the HUD's binary identity is stable for TCC grants."""
    hud_dir = tmp_path / "tools" / "turbo-hud"
    hud_dir.mkdir(parents=True)
    (hud_dir / "Package.swift").write_text("")

    # Fake-built HUD binary so the symlink path resolves.
    fake_hud_bin = hud_dir / ".build" / "debug" / "TurboHUD"
    fake_hud_bin.parent.mkdir(parents=True)
    fake_hud_bin.write_text("#!/bin/sh\necho fake HUD")

    local_bin = tmp_path / "local_bin"
    local_bin.mkdir()

    symlinked: list[tuple] = []

    def _fake_refresh_symlink(link_path, target_path):
        symlinked.append((link_path, target_path))

    runner = CliRunner()
    with (
        patch(f"{_SIDE}.subprocess.run") as mock_run,
        patch(f"{_SIDE}.hud_dir", return_value=hud_dir),
        patch(f"{_SIDE}.acquirer_dir",
              return_value=tmp_path / "tools" / "no-acquirer"),
        patch(f"{_SIDE}.local_bin", return_value=local_bin),
        patch(f"{_SIDE}.refresh_symlink", side_effect=_fake_refresh_symlink),
        _no_raycast_sync(),
        _no_reset(),
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(sidecar_cmd, [])

    assert result.exit_code == 0

    # The HUD must have been built (swift build in hud_dir).
    hud_build_calls = [
        c for c in mock_run.call_args_list
        if c.args[0][:2] == ["swift", "build"] and c.kwargs.get("cwd") == hud_dir
    ]
    assert hud_build_calls, f"Expected `swift build` in HUD dir; got {mock_run.call_args_list}"

    # The TurboHUD symlink must have been created.
    hud_symlinks = [s for s in symlinked if s[0] == local_bin / "TurboHUD"]
    assert hud_symlinks, f"Expected ~/.local/bin/TurboHUD symlink; got {symlinked}"
    assert hud_symlinks[0][1] == fake_hud_bin

    # The HUD must have been launched via the symlinked path, NOT `swift run`.
    last_call = mock_run.call_args_list[-1]
    assert last_call.args[0] == [str(local_bin / "TurboHUD")], (
        f"HUD launch must go through the symlink; got {last_call}"
    )


def test_reset_stale_state_kills_acquirers_and_clears_activity_files(tmp_path):
    """Reset SIGKILLs orphaned acquirers (which may ignore SIGTERM) and removes
    dead-owner activity-*.json so the HUD doesn't show a phantom recording."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "activity-abc.json").write_text("{}")
    (state / "activity-def.json").write_text("{}")
    (state / "keep.txt").write_text("unrelated")  # must be left alone

    calls = []

    def _fake_run(args, **kwargs):
        calls.append(args)
        return MagicMock(returncode=0)

    with (
        patch.object(sidecar_mod.subprocess, "run", side_effect=_fake_run),
        patch.object(sidecar_mod, "_state_dir", return_value=state),
    ):
        _reset_stale_state()

    # -x (exact process-name match), not -f (full command-line match) — -f
    # would also match e.g. an editor open on tools/turbo-acquirer/ in this
    # very repo.
    assert calls == [["pkill", "-9", "-x", "turbo-acquirer"]]
    # Owner-less (no owner_pid) files can't have a live owner — removed.
    assert not (state / "activity-abc.json").exists()
    assert not (state / "activity-def.json").exists()
    assert (state / "keep.txt").exists()


def test_reset_stale_state_preserves_activity_of_live_owner(tmp_path):
    """An activity file whose owner_pid is still alive must survive a reset —
    otherwise a running workflow vanishes from the HUD and its subsequent
    activity.update_activity calls silently no-op."""
    import json
    import os

    state = tmp_path / "state"
    state.mkdir()
    live = state / "activity-live.json"
    live.write_text(json.dumps({"owner_pid": os.getpid()}))
    dead = state / "activity-dead.json"
    dead.write_text(json.dumps({"owner_pid": 999999}))  # unlikely to exist

    with (
        patch.object(sidecar_mod.subprocess, "run", return_value=MagicMock(returncode=0)),
        patch.object(sidecar_mod, "_state_dir", return_value=state),
    ):
        _reset_stale_state()

    assert live.exists()
    assert not dead.exists()


def test_refresh_symlink_creates_new_symlink(tmp_path):
    """refresh_symlink creates a new symlink when none exists."""
    target = tmp_path / "binary"
    target.write_text("bin")
    link = tmp_path / "link"

    _common.refresh_symlink(link, target)
    assert link.is_symlink()
    assert os.readlink(str(link)) == str(target)


def test_refresh_symlink_replaces_existing_symlink(tmp_path):
    """refresh_symlink replaces an old symlink."""
    old_target = tmp_path / "old_binary"
    old_target.write_text("old")
    new_target = tmp_path / "new_binary"
    new_target.write_text("new")
    link = tmp_path / "link"
    link.symlink_to(old_target)

    _common.refresh_symlink(link, new_target)
    assert link.is_symlink()
    assert os.readlink(str(link)) == str(new_target)


def test_refresh_symlink_replaces_regular_file_at_link_path(tmp_path):
    """A plain regular file occupying link_path (not a symlink) must be
    replaced rather than raising FileExistsError."""
    target = tmp_path / "binary"
    target.write_text("bin")
    link = tmp_path / "link"
    link.write_text("stray real file, not a symlink")  # e.g. a stray touch

    _common.refresh_symlink(link, target)
    assert link.is_symlink()
    assert os.readlink(str(link)) == str(target)


def test_refresh_symlink_refuses_to_delete_real_directory(tmp_path):
    """A real directory at link_path must be left alone — refresh_symlink
    should not silently rm -rf it to make room for a symlink."""
    target = tmp_path / "binary"
    target.write_text("bin")
    link = tmp_path / "link"
    link.mkdir()

    with pytest.raises(OSError):
        _common.refresh_symlink(link, target)
    assert link.is_dir()
    assert not link.is_symlink()
