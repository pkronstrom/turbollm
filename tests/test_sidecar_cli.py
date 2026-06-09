"""Tests for `turbo sidecar` — original + T-15 extensions.

T-15 adds turbo-acquirer build + symlink creation alongside the HUD build.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from turbollm import cli as turbo_cli


@pytest.fixture(autouse=True)
def _enable_beta(monkeypatch):
    """These tests exercise BETA-gated sidecar/acquirer features
    (cli._beta_gate / TURBO_BETA). Force the flag on so the gated code path
    runs; the actual swift/subprocess work is mocked per-test."""
    monkeypatch.setenv("TURBO_BETA", "1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_run(returncode: int = 0):
    """Return a Mock for subprocess.run whose .returncode attribute is set."""
    m = MagicMock()
    m.return_value.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# Existing tests (updated for the new two-binary sidecar)
# ---------------------------------------------------------------------------

def test_sidecar_errors_when_swift_package_missing(monkeypatch, tmp_path):
    fake_repo_root = tmp_path / "fake-repo"
    fake_repo_root.mkdir()
    # No tools/turbo-hud/ inside fake_repo_root
    runner = CliRunner()
    with patch("turbollm.cli.Path") as mock_path:
        mock_path.return_value.resolve.return_value.parent.parent.parent = fake_repo_root
        result = runner.invoke(turbo_cli.cli, ["sidecar"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_sidecar_invokes_swift_run_for_hud(tmp_path):
    """sidecar always ends with `swift run` in the HUD directory."""
    hud_dir = tmp_path / "tools" / "turbo-hud"
    hud_dir.mkdir(parents=True)
    (hud_dir / "Package.swift").write_text("")

    runner = CliRunner()
    with (
        patch("turbollm.cli.subprocess.run") as mock_run,
        patch.object(turbo_cli, "_hud_dir", return_value=hud_dir),
        patch.object(turbo_cli, "_acquirer_dir", return_value=tmp_path / "tools" / "no-acquirer"),
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(turbo_cli.cli, ["sidecar"])

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
        patch("turbollm.cli.subprocess.run") as mock_run,
        patch.object(turbo_cli, "_hud_dir", return_value=hud_dir),
        patch.object(turbo_cli, "_acquirer_dir", return_value=acq_dir),
        patch.object(turbo_cli, "_local_bin", return_value=tmp_path / "local_bin"),
        patch.object(turbo_cli, "_swift_build_product_path",
                     side_effect=lambda d, p: tmp_path / "no-binary"),  # binary absent → skip symlink
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(turbo_cli.cli, ["sidecar"])

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
        patch("turbollm.cli.subprocess.run") as mock_run,
        patch.object(turbo_cli, "_hud_dir", return_value=hud_dir),
        patch.object(turbo_cli, "_acquirer_dir", return_value=acq_dir),
        patch.object(turbo_cli, "_local_bin", return_value=local_bin),
        patch.object(turbo_cli, "_refresh_symlink", side_effect=_fake_refresh_symlink),
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(turbo_cli.cli, ["sidecar"])

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
        patch("turbollm.cli.subprocess.run") as mock_run,
        patch.object(turbo_cli, "_hud_dir", return_value=hud_dir),
        patch.object(turbo_cli, "_acquirer_dir",
                     return_value=tmp_path / "tools" / "turbo-acquirer"),  # doesn't exist
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(turbo_cli.cli, ["sidecar"])

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
        patch("turbollm.cli.subprocess.run", side_effect=_failing_first_run),
        patch.object(turbo_cli, "_hud_dir", return_value=hud_dir),
        patch.object(turbo_cli, "_acquirer_dir", return_value=acq_dir),
    ):
        result = runner.invoke(turbo_cli.cli, ["sidecar"])

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
        patch("turbollm.cli.subprocess.run") as mock_run,
        patch.object(turbo_cli, "_hud_dir", return_value=hud_dir),
        patch.object(turbo_cli, "_acquirer_dir",
                     return_value=tmp_path / "tools" / "no-acquirer"),
        patch.object(turbo_cli, "_local_bin", return_value=local_bin),
        patch.object(turbo_cli, "_refresh_symlink", side_effect=_fake_refresh_symlink),
    ):
        mock_run.return_value.returncode = 0
        result = runner.invoke(turbo_cli.cli, ["sidecar"])

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


def test_refresh_symlink_creates_new_symlink(tmp_path):
    """_refresh_symlink creates a new symlink when none exists."""
    target = tmp_path / "binary"
    target.write_text("bin")
    link = tmp_path / "link"

    turbo_cli._refresh_symlink(link, target)
    assert link.is_symlink()
    assert os.readlink(str(link)) == str(target)


def test_refresh_symlink_replaces_existing_symlink(tmp_path):
    """_refresh_symlink replaces an old symlink."""
    old_target = tmp_path / "old_binary"
    old_target.write_text("old")
    new_target = tmp_path / "new_binary"
    new_target.write_text("new")
    link = tmp_path / "link"
    link.symlink_to(old_target)

    turbo_cli._refresh_symlink(link, new_target)
    assert link.is_symlink()
    assert os.readlink(str(link)) == str(new_target)
