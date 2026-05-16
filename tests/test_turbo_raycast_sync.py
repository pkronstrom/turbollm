"""Tests for `turbo raycast sync` CLI subcommand (T-12)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from turbollm import cli as turbo_cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_REGISTRY = {
    "workflows": {
        "transcribe-file": {
            "description": "Transcribe an audio file",
            "command": 'turbo transcribe "{{file}}"',
            "params": [{"name": "file", "type": "file"}],
        },
        "record-to-obsidian": {
            "description": "Record and transcribe to Obsidian",
            "command": "echo ok",
            "params": [],
        },
    }
}

_FIXED_COMMANDS = [
    {
        "name": "run-workflow",
        "title": "Run Turbollm Workflow",
        "description": "Pick and run a workflow",
        "mode": "view",
    },
    {
        "name": "running-workflows",
        "title": "Running Workflows",
        "description": "List in-flight acquisitions",
        "mode": "view",
    },
]


def _make_package_json(commands: list[dict]) -> dict:
    return {
        "name": "raycast-turbo",
        "version": "1.0.0",
        "commands": commands,
    }


# ---------------------------------------------------------------------------
# T-12 Step 1: sync adds per-workflow commands
# ---------------------------------------------------------------------------

def test_raycast_sync_writes_per_workflow_commands(tmp_path):
    """Given workflows from the registry, sync writes one command entry per workflow."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    pkg = json.loads(pkg_path.read_text())
    commands = pkg["commands"]

    names = [cmd["name"] for cmd in commands]
    # Both fixed commands preserved.
    assert "run-workflow" in names
    assert "running-workflows" in names
    # Per-workflow commands added.
    assert "transcribe-file" in names
    assert "record-to-obsidian" in names


def test_raycast_sync_title_is_title_case_of_slug(tmp_path):
    """Each per-workflow command title is the slug converted to Title Case."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    pkg = json.loads(pkg_path.read_text())
    by_name = {cmd["name"]: cmd for cmd in pkg["commands"]}

    assert by_name["transcribe-file"]["title"] == "Transcribe File"
    assert by_name["record-to-obsidian"]["title"] == "Record To Obsidian"


def test_raycast_sync_subtitle_is_workflow_description(tmp_path):
    """Each per-workflow command subtitle is the workflow description."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    pkg = json.loads(pkg_path.read_text())
    by_name = {cmd["name"]: cmd for cmd in pkg["commands"]}

    assert by_name["transcribe-file"]["subtitle"] == "Transcribe an audio file"
    assert by_name["record-to-obsidian"]["subtitle"] == "Record and transcribe to Obsidian"


# ---------------------------------------------------------------------------
# T-12 Step 1: idempotency
# ---------------------------------------------------------------------------

def test_raycast_sync_is_idempotent(tmp_path):
    """Running sync twice produces byte-identical package.json output."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])
        first_content = pkg_path.read_text()

        runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])
        second_content = pkg_path.read_text()

    assert first_content == second_content


# ---------------------------------------------------------------------------
# T-12 Step 1: orphan removal
# ---------------------------------------------------------------------------

def test_raycast_sync_removes_orphan_commands(tmp_path):
    """Commands for workflows that no longer exist are removed from package.json."""
    orphan_cmd = {"name": "old-workflow", "title": "Old Workflow", "subtitle": "Gone"}
    initial_commands = _FIXED_COMMANDS + [orphan_cmd]
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(initial_commands), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    pkg = json.loads(pkg_path.read_text())
    names = [cmd["name"] for cmd in pkg["commands"]]
    assert "old-workflow" not in names
    # Fixed commands still present.
    assert "run-workflow" in names
    assert "running-workflows" in names


def test_raycast_sync_preserves_fixed_commands_intact(tmp_path):
    """Fixed command entries are preserved exactly (all their fields unchanged)."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    pkg = json.loads(pkg_path.read_text())
    by_name = {cmd["name"]: cmd for cmd in pkg["commands"]}

    # All fields of the fixed commands preserved verbatim.
    for fixed in _FIXED_COMMANDS:
        assert by_name[fixed["name"]] == fixed


# ---------------------------------------------------------------------------
# T-12: --quiet suppresses output
# ---------------------------------------------------------------------------

def test_raycast_sync_quiet_suppresses_output(tmp_path):
    """--quiet flag produces no stdout output."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(
            turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path), "--quiet"]
        )

    assert result.exit_code == 0
    assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# T-12: preserves non-commands fields in package.json
# ---------------------------------------------------------------------------

def test_raycast_sync_preserves_other_package_json_fields(tmp_path):
    """Sync only touches the commands array; other package.json fields are untouched."""
    pkg = {
        "name": "raycast-turbo",
        "version": "2.5.0",
        "description": "My extension",
        "commands": list(_FIXED_COMMANDS),
    }
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(pkg, indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    updated = json.loads(pkg_path.read_text())
    assert updated["name"] == "raycast-turbo"
    assert updated["version"] == "2.5.0"
    assert updated["description"] == "My extension"


# ---------------------------------------------------------------------------
# T-12: empty workflow list clears per-workflow commands
# ---------------------------------------------------------------------------

def test_raycast_sync_with_no_workflows_keeps_only_fixed(tmp_path):
    """When there are no workflows, only the fixed commands remain."""
    wf_cmd = {"name": "some-workflow", "title": "Some Workflow", "subtitle": ""}
    initial_commands = _FIXED_COMMANDS + [wf_cmd]
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(initial_commands), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value={}):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    pkg = json.loads(pkg_path.read_text())
    names = [cmd["name"] for cmd in pkg["commands"]]
    assert "some-workflow" not in names
    assert "run-workflow" in names
    assert "running-workflows" in names
