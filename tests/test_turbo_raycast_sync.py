"""Tests for `turbo raycast sync` CLI subcommand (T-12, T-fix-5)."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from turbollm import cli as turbo_cli


@pytest.fixture(autouse=True)
def _enable_beta(monkeypatch):
    """These tests exercise BETA-gated raycast/sidecar features
    (cli._beta_gate / TURBO_BETA). Force the flag on so the gated code path
    runs; the actual sync/subprocess work is mocked per-test."""
    monkeypatch.setenv("TURBO_BETA", "1")


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


def test_raycast_sync_emits_description_and_mode_on_per_workflow_commands(tmp_path):
    """Raycast's Swift decoder requires `description` and `mode` on every command.

    Without them, `ray develop` fails with:
        Could not install extension from development sources
        No value associated with key description
    """
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output

    pkg = json.loads(pkg_path.read_text())
    per_wf_cmds = [c for c in pkg["commands"] if c["name"] not in {"run-workflow", "running-workflows"}]
    assert per_wf_cmds, "expected at least one per-workflow command"
    for cmd in per_wf_cmds:
        assert cmd.get("description"), f"command {cmd['name']!r} missing description"
        assert cmd.get("mode") == "view", f"command {cmd['name']!r} missing mode=view"


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


# ---------------------------------------------------------------------------
# Per-workflow command stubs (formerly symlinks — Raycast's bundler de-dupes
# symlinks and fails to emit per-command JS, so we use real re-export files).
# ---------------------------------------------------------------------------

_STUB_MARKER = "// turbo raycast sync — per-workflow re-export stub"


def test_raycast_sync_creates_workflow_stubs(tmp_path):
    """sync creates src/<slug>.tsx re-export stubs for each workflow."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    for slug in ("transcribe-file", "record-to-obsidian"):
        stub = src_dir / f"{slug}.tsx"
        assert stub.is_file(), f"expected stub file for {slug}"
        assert not stub.is_symlink(), f"{slug} stub must be a real file, not a symlink"
        content = stub.read_text()
        assert _STUB_MARKER in content
        assert 'from "./run-workflow"' in content


def test_raycast_sync_creates_src_dir_if_missing(tmp_path):
    """sync creates the src/ directory if it does not exist yet."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")
    # Deliberately do NOT create src/

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "src").is_dir()
    stub = tmp_path / "src" / "transcribe-file.tsx"
    assert stub.is_file()
    assert _STUB_MARKER in stub.read_text()


def test_raycast_sync_migrates_legacy_symlinks_to_stubs(tmp_path):
    """A pre-existing symlink (legacy format) is replaced by a stub file."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    legacy = src_dir / "transcribe-file.tsx"
    legacy.symlink_to("run-workflow.tsx")
    assert legacy.is_symlink()

    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert legacy.is_file()
    assert not legacy.is_symlink(), "legacy symlink should have been migrated to a real stub"
    assert _STUB_MARKER in legacy.read_text()


def test_raycast_sync_removes_orphan_stubs(tmp_path):
    """sync removes src/<slug>.tsx stub files for workflows that no longer exist."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    orphan = src_dir / "old-workflow.tsx"
    orphan.write_text(_STUB_MARKER + '\nexport { default } from "./run-workflow";\n')

    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert not orphan.exists(), "orphan stub should have been removed"


def test_raycast_sync_removes_orphan_legacy_symlinks(tmp_path):
    """sync also removes pre-existing orphan symlinks (backwards compat with legacy format)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    orphan = src_dir / "old-workflow.tsx"
    orphan.symlink_to("run-workflow.tsx")

    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert not orphan.exists(), "legacy orphan symlink should have been removed"


def test_raycast_sync_does_not_remove_real_tsx_files(tmp_path):
    """sync does not remove user-authored (non-stub, non-symlink) .tsx files in src/."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    real_file = src_dir / "run-workflow.tsx"
    real_file.write_text("// real file\n")

    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert real_file.exists()
    assert real_file.read_text() == "// real file\n"


def test_raycast_sync_stubs_are_idempotent(tmp_path):
    """Running sync twice produces the same stub content."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])
        first = (src_dir / "transcribe-file.tsx").read_text()
        runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])
        second = (src_dir / "transcribe-file.tsx").read_text()

    assert first == second
    assert _STUB_MARKER in first


# ---------------------------------------------------------------------------
# T-fix-5: _generated_commands.ts generation
# ---------------------------------------------------------------------------

def test_raycast_sync_writes_generated_commands_ts(tmp_path):
    """sync writes src/_generated_commands.ts with a workflow-name map."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        result = runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    gen_path = tmp_path / "src" / "_generated_commands.ts"
    assert gen_path.exists(), "_generated_commands.ts must be written"
    content = gen_path.read_text()
    assert "GENERATED_COMMANDS" in content
    assert '"transcribe-file": "transcribe-file"' in content
    assert '"record-to-obsidian": "record-to-obsidian"' in content


def test_raycast_sync_generated_commands_ts_valid_structure(tmp_path):
    """The generated _generated_commands.ts has the expected TypeScript structure."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    content = (tmp_path / "src" / "_generated_commands.ts").read_text()
    assert content.startswith("// Auto-generated"), "should start with auto-generated comment"
    assert "export const GENERATED_COMMANDS: Record<string, string> = {" in content
    assert content.strip().endswith("};"), "should end with closing brace-semicolon"


def test_raycast_sync_generated_commands_ts_empty_when_no_workflows(tmp_path):
    """_generated_commands.ts has an empty map when no workflows are configured."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value={}):
        runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    content = (tmp_path / "src" / "_generated_commands.ts").read_text()
    assert "GENERATED_COMMANDS" in content
    # With no workflows, the map body should be empty.
    assert '": "' not in content


def test_raycast_sync_generated_commands_ts_is_sorted(tmp_path):
    """Workflow entries in _generated_commands.ts are sorted alphabetically."""
    pkg_path = tmp_path / "package.json"
    pkg_path.write_text(json.dumps(_make_package_json(_FIXED_COMMANDS), indent=2) + "\n")

    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY):
        runner.invoke(turbo_cli.cli, ["raycast", "sync", "--extension-dir", str(tmp_path)])

    content = (tmp_path / "src" / "_generated_commands.ts").read_text()
    record_idx = content.index("record-to-obsidian")
    transcribe_idx = content.index("transcribe-file")
    assert record_idx < transcribe_idx, "entries should be sorted alphabetically"


# ---------------------------------------------------------------------------
# T-26: sidecar auto-sync
# ---------------------------------------------------------------------------

def test_sidecar_auto_syncs_raycast_on_startup(tmp_path):
    """turbo sidecar syncs the Raycast extension on every launch.

    After sidecar starts, package.json commands match the workflow registry.
    """
    from unittest.mock import MagicMock

    # Fake HUD dir (sidecar checks its existence + Package.swift).
    hud_dir = tmp_path / "turbo-hud"
    hud_dir.mkdir()
    (hud_dir / "Package.swift").touch()

    # Fake Raycast extension dir with a pre-existing package.json.
    ext_dir = tmp_path / "raycast-turbo"
    ext_dir.mkdir()
    pkg = _make_package_json(_FIXED_COMMANDS)
    (ext_dir / "package.json").write_text(json.dumps(pkg, indent=2) + "\n")

    runner = CliRunner()
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with (
        patch("turbollm.cli.subprocess.run", return_value=mock_proc),
        patch("turbollm.cli._hud_dir", return_value=hud_dir),
        patch("turbollm.cli._raycast_extension_dir", return_value=ext_dir),
        patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY),
    ):
        result = runner.invoke(turbo_cli.cli, ["sidecar", "--no-build"])

    assert result.exit_code == 0, result.output

    pkg_after = json.loads((ext_dir / "package.json").read_text())
    names = [cmd["name"] for cmd in pkg_after["commands"]]

    # Fixed commands preserved.
    assert "run-workflow" in names
    assert "running-workflows" in names
    # Per-workflow commands from registry added.
    assert "transcribe-file" in names
    assert "record-to-obsidian" in names


def test_sidecar_skips_sync_gracefully_when_extension_dir_absent(tmp_path):
    """When the Raycast extension dir does not exist, sidecar starts without error."""
    from unittest.mock import MagicMock

    hud_dir = tmp_path / "turbo-hud"
    hud_dir.mkdir()
    (hud_dir / "Package.swift").touch()

    nonexistent_ext_dir = tmp_path / "raycast-turbo-MISSING"

    runner = CliRunner()
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with (
        patch("turbollm.cli.subprocess.run", return_value=mock_proc),
        patch("turbollm.cli._hud_dir", return_value=hud_dir),
        patch("turbollm.cli._raycast_extension_dir", return_value=nonexistent_ext_dir),
        patch("turbollm.cli.load_registry", return_value=_FAKE_REGISTRY),
    ):
        result = runner.invoke(turbo_cli.cli, ["sidecar", "--no-build"])

    assert result.exit_code == 0, result.output
    assert "skipping sync" in result.output
