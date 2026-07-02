"""Tests for `turbo workflows config` — T-14.

The command reads / writes / clears sticky param values in the
com.turbollm.hud UserDefaults suite via /usr/bin/defaults.

We monkey-patch the three helpers (_defaults_read, _defaults_write,
_defaults_delete, _defaults_read_all_for_workflow) so tests are fully
offline and do not touch real UserDefaults.
"""
from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from turbollm import cli as turbo_cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke(*args):
    runner = CliRunner()
    return runner.invoke(turbo_cli.cli, ["workflows", "config", *args])


# ---------------------------------------------------------------------------
# T-14 Scenario: set a sticky value
# ---------------------------------------------------------------------------

def test_config_set_value_writes_to_defaults():
    """Setting param=value calls _defaults_write with the correct key."""
    with (
        patch.object(turbo_cli, "_defaults_write") as mock_write,
        patch.object(turbo_cli, "_defaults_delete") as _,
    ):
        result = _invoke("record-to-obsidian", "vault=/foo/bar")
    assert result.exit_code == 0, result.output
    mock_write.assert_called_once_with(
        turbo_cli._WORKFLOWS_CONFIG_SUITE,
        "workflow.record-to-obsidian.param.vault",
        "/foo/bar",
    )


def test_config_set_confirms_in_output():
    """Setting a value prints confirmation."""
    with (
        patch.object(turbo_cli, "_defaults_write"),
        patch.object(turbo_cli, "_defaults_delete"),
    ):
        result = _invoke("record-to-obsidian", "vault=/my/vault")
    assert "vault" in result.output
    assert "/my/vault" in result.output


# ---------------------------------------------------------------------------
# T-14 Scenario: list stickies
# ---------------------------------------------------------------------------

def test_config_list_shows_stickies():
    """No arg → list all stickies for the workflow."""
    stickies = {"vault": "/Users/me/Obsidian", "title": "Meeting"}
    with patch.object(
        turbo_cli, "_defaults_read_all_for_workflow", return_value=stickies
    ):
        result = _invoke("record-to-obsidian")
    assert result.exit_code == 0, result.output
    assert "vault" in result.output
    assert "/Users/me/Obsidian" in result.output
    assert "title" in result.output


def test_config_list_empty_shows_message():
    """No stickies → friendly message."""
    with patch.object(
        turbo_cli, "_defaults_read_all_for_workflow", return_value={}
    ):
        result = _invoke("record-to-obsidian")
    assert result.exit_code == 0
    assert "No sticky" in result.output


# ---------------------------------------------------------------------------
# T-14 Scenario: clear a sticky value
# ---------------------------------------------------------------------------

def test_config_clear_calls_defaults_delete():
    """param= (empty value) calls _defaults_delete."""
    with (
        patch.object(turbo_cli, "_defaults_write") as mock_write,
        patch.object(turbo_cli, "_defaults_delete") as mock_delete,
    ):
        result = _invoke("record-to-obsidian", "vault=")
    assert result.exit_code == 0, result.output
    mock_delete.assert_called_once_with(
        turbo_cli._WORKFLOWS_CONFIG_SUITE,
        "workflow.record-to-obsidian.param.vault",
    )
    mock_write.assert_not_called()


def test_config_clear_confirms_in_output():
    """Clearing a value prints confirmation."""
    with (
        patch.object(turbo_cli, "_defaults_write"),
        patch.object(turbo_cli, "_defaults_delete"),
    ):
        result = _invoke("record-to-obsidian", "vault=")
    assert "Cleared" in result.output or "vault" in result.output


# ---------------------------------------------------------------------------
# T-14 Scenario: multiple assignments in one call
# ---------------------------------------------------------------------------

def test_config_set_multiple_params():
    """Multiple PARAM=VALUE args each produce a write."""
    with (
        patch.object(turbo_cli, "_defaults_write") as mock_write,
        patch.object(turbo_cli, "_defaults_delete"),
    ):
        result = _invoke("my-wf", "a=1", "b=2")
    assert result.exit_code == 0, result.output
    assert mock_write.call_count == 2
    keys_written = {c.args[1] for c in mock_write.call_args_list}
    assert "workflow.my-wf.param.a" in keys_written
    assert "workflow.my-wf.param.b" in keys_written


# ---------------------------------------------------------------------------
# T-14 Scenario: invalid assignment
# ---------------------------------------------------------------------------

def test_config_invalid_assignment_exits_nonzero():
    """Assignment without = sign causes exit code 2."""
    result = _invoke("my-wf", "notanassignment")
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# T-14: key format matches HUD's Settings.swift convention
# ---------------------------------------------------------------------------

def test_defaults_key_format_matches_hud_convention():
    """The generated key must be workflow.<wf>.param.<param>."""
    key = turbo_cli._defaults_key("record-to-obsidian", "vault")
    assert key == "workflow.record-to-obsidian.param.vault"


def test_defaults_suite_matches_hud_suite():
    """The suite name must match Settings.defaultSuiteName in Settings.swift."""
    assert turbo_cli._WORKFLOWS_CONFIG_SUITE == "com.turbollm.hud"


# ---------------------------------------------------------------------------
# Off-macOS hardening: /usr/bin/defaults is absent on Linux. The sticky helpers
# must degrade to "no sticky" / no-op instead of raising FileNotFoundError.
# ---------------------------------------------------------------------------

def test_defaults_read_returns_none_when_defaults_binary_missing():
    with patch.object(turbo_cli.subprocess, "run", side_effect=FileNotFoundError):
        assert turbo_cli._defaults_read("com.turbollm.hud", "k") is None


def test_defaults_read_all_returns_empty_when_defaults_binary_missing():
    with patch.object(turbo_cli.subprocess, "run", side_effect=FileNotFoundError):
        assert turbo_cli._defaults_read_all_for_workflow("com.turbollm.hud", "wf") == {}


def test_defaults_write_and_delete_do_not_raise_when_binary_missing():
    with patch.object(turbo_cli.subprocess, "run", side_effect=FileNotFoundError):
        # Neither should raise.
        turbo_cli._defaults_write("com.turbollm.hud", "k", "v")
        turbo_cli._defaults_delete("com.turbollm.hud", "k")


def test_defaults_write_prints_clean_error_on_called_process_error(capsys):
    """A non-zero `/usr/bin/defaults write` (e.g. malformed suite/key) must
    surface a clean message, not an uncaught CalledProcessError traceback."""
    import subprocess

    def fail(*a, **kw):
        raise subprocess.CalledProcessError(1, ["/usr/bin/defaults", "write"])

    with patch.object(turbo_cli.subprocess, "run", side_effect=fail):
        turbo_cli._defaults_write("com.turbollm.hud", "k", "v")  # must not raise

    captured = capsys.readouterr()
    assert "Failed to write sticky" in captured.out
