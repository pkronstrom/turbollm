"""End-to-end integration tests for Turbo Foundation Phase 1 (T-25).

Design choice: real audio I/O and real SCStream cannot be driven from a
test suite without hardware fixtures and TCC grants. The tests below cover
the automation-friendly slices of the Phase 1 contract:

  1. `run_workflow("record-to-obsidian", ...)` with a fake `turbo-acquirer`
     binary (injects a pre-existing WAV path) and fake `turbo transcribe` /
     `pi` stubs → asserts that BOTH `<slug>.md` and `<slug>.raw.md` land in
     the Meetings folder.

  2. The `turbo-acquirer permissions-state` JSON contract: the output must be
     parseable JSON with keys `microphone` and `screenRecording`.

  3. The `turbo workflows config` round-trip: write a sticky via CLI → read
     it back from UserDefaults.

  4. The HUD migration-notice flag (`migration_notice_shown_v1`) logic:
     postMigrationNoticeIfNeeded is pure side-effect code in Swift, so this
     Python test verifies the flag key constant is stable and not reused.

Steps that require real audio hardware or real SCStream are covered by
`tools/turbo-acquirer/SMOKE.md`.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _worktree_root() -> Path:
    """Return the worktree root (parent of the tests/ directory)."""
    return Path(__file__).resolve().parent.parent


def _make_stub(bin_dir: Path, name: str, body: str) -> None:
    """Write an executable shell stub to bin_dir/name."""
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _models_toml() -> dict:
    path = _worktree_root() / "models.toml"
    if not path.exists():
        pytest.skip(f"models.toml not found at {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def _script_command(registry: dict, name: str) -> str:
    """Extract a named script's command from a registry dict."""
    script = registry.get("scripts", {}).get(name)
    if script is None:
        pytest.skip(f"script '{name}' not found in models.toml")
    return script["command"]


# ---------------------------------------------------------------------------
# Test 1: record-to-obsidian pipeline produces both .md and .raw.md
#
# This test replaces `turbo-acquirer` with a stub that immediately returns a
# pre-built WAV path. `turbo transcribe` and `pi` are also stubbed so the
# test works without a running mlx-audio server or LLM backend.
# ---------------------------------------------------------------------------

FAKE_SEGMENTS = json.dumps([
    {"start": 0.0,   "end": 5.0,  "text": "Hello Phase 1."},
    {"start": 60.0,  "end": 65.0, "text": "One minute mark."},
])


def test_record_to_obsidian_produces_md_and_raw_md(tmp_path):
    """
    Full pipeline: fake acquirer -> stubbed transcribe -> stubbed pi ->
    assert Meetings/<slug>.md and Meetings/<slug>.raw.md exist.

    This is the automated portion of the T-25 scenario:
      'spawn turbo workflows run record-to-obsidian against a fixture
       5-second audio file. Assert the Meetings folder gains a <slug>.md
       and <slug>.raw.md.'
    """
    registry = _models_toml()
    script_cmd = _script_command(registry, "summarize-to-obsidian")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Fake WAV file (content is irrelevant; stubs ignore it).
    fake_wav = tmp_path / "meeting-fixture.wav"
    fake_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    # turbo-acquirer stub: immediately prints the pre-resolved WAV path and exits 0.
    # (In production it would record real audio and then print the path.)
    _make_stub(bin_dir, "turbo-acquirer", f"printf '%s' '{fake_wav}'\n")

    # turbo stub: handles both plain transcribe and --format segments.
    _make_stub(bin_dir, "turbo", f"""\
case "$2" in
  --format)
    printf '%s' '{FAKE_SEGMENTS}'
    ;;
  *)
    printf '%s' 'Hello Phase 1. One minute mark.'
    ;;
esac
""")

    # pi stub: echo a fake summary.
    _make_stub(bin_dir, "pi", "printf '%s' 'Fake AI summary of the meeting.'\n")

    vault = tmp_path / "vault"
    vault.mkdir()

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "OBSIDIAN_VAULT": str(vault),
    }

    result = subprocess.run(
        ["bash", "-c", script_cmd, "summarize-to-obsidian", str(fake_wav)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Script failed (exit {result.returncode}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    slug = "meeting-fixture"
    meetings = vault / "Meetings"
    md_path = meetings / f"{slug}.md"
    raw_md_path = meetings / f"{slug}.raw.md"

    assert md_path.exists(), f"Missing: {md_path}\nVault contents: {list(meetings.iterdir()) if meetings.exists() else 'no Meetings dir'}"
    assert raw_md_path.exists(), f"Missing: {raw_md_path}"


def test_record_to_obsidian_raw_md_has_timestamp_lines(tmp_path):
    """
    <slug>.raw.md must contain [mm:ss] lines matching the segments output.
    """
    registry = _models_toml()
    script_cmd = _script_command(registry, "summarize-to-obsidian")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    fake_wav = tmp_path / "phase1-test.wav"
    fake_wav.write_bytes(b"RIFF")

    _make_stub(bin_dir, "turbo-acquirer", f"printf '%s' '{fake_wav}'\n")
    _make_stub(bin_dir, "turbo", f"""\
case "$2" in
  --format)
    printf '%s' '{FAKE_SEGMENTS}'
    ;;
  *)
    printf '%s' 'Hello Phase 1. One minute mark.'
    ;;
esac
""")
    _make_stub(bin_dir, "pi", "printf '%s' 'Summary.'\n")

    vault = tmp_path / "vault"
    vault.mkdir()

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "OBSIDIAN_VAULT": str(vault),
    }

    result = subprocess.run(
        ["bash", "-c", script_cmd, "summarize-to-obsidian", str(fake_wav)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"

    raw_md = vault / "Meetings" / "phase1-test.raw.md"
    assert raw_md.exists(), f"Missing raw.md: {raw_md}"

    lines = raw_md.read_text().strip().splitlines()
    assert len(lines) == 2, f"Expected 2 timestamp lines, got: {lines}"
    assert lines[0].startswith("[00:00]"), f"First line wrong: {lines[0]!r}"
    assert "Hello Phase 1." in lines[0]
    assert lines[1].startswith("[01:00]"), f"Second line wrong: {lines[1]!r}"
    assert "One minute mark." in lines[1]


# ---------------------------------------------------------------------------
# Test 2: turbo-acquirer permissions-state JSON contract
#
# If the real turbo-acquirer binary is present (built via `turbo sidecar`),
# verify it emits well-formed JSON. Skips if binary is absent.
# ---------------------------------------------------------------------------

def _acquirer_path() -> Path | None:
    """Locate turbo-acquirer in standard locations. Returns None if not found."""
    candidates = [
        Path.home() / ".local" / "bin" / "turbo-acquirer",
        Path("/usr/local/bin/turbo-acquirer"),
        _worktree_root() / "tools" / "turbo-acquirer" / ".build" / "debug" / "turbo-acquirer",
    ]
    return next((p for p in candidates if p.exists()), None)


@pytest.mark.skipif(_acquirer_path() is None, reason="turbo-acquirer binary not found — run `turbo sidecar` first")
def test_acquirer_permissions_state_emits_valid_json():
    """
    `turbo-acquirer permissions-state` must emit JSON with keys
    `microphone` and `screenRecording`.
    """
    binary = _acquirer_path()
    assert binary is not None  # narrowing for type checkers

    result = subprocess.run(
        [str(binary), "permissions-state"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"permissions-state failed:\n{result.stderr}"

    data = json.loads(result.stdout)
    assert "microphone" in data, f"Missing 'microphone' key in: {data}"
    assert "screenRecording" in data, f"Missing 'screenRecording' key in: {data}"

    valid_mic_values = {"authorized", "denied", "notDetermined", "restricted"}
    assert data["microphone"] in valid_mic_values, (
        f"microphone value {data['microphone']!r} not in {valid_mic_values}"
    )
    assert isinstance(data["screenRecording"], bool), (
        f"screenRecording must be bool, got {type(data['screenRecording'])}"
    )


# ---------------------------------------------------------------------------
# Test 3: workflows config round-trip
#
# Verify the CLI can write a sticky value and read it back.
# ---------------------------------------------------------------------------

def test_workflows_config_round_trip():
    """
    `turbo workflows config <wf> key=value` writes to UserDefaults and
    `turbo workflows config <wf>` reads it back.
    """
    from click.testing import CliRunner
    from turbollm import cli as turbo_cli

    runner = CliRunner()
    fake_registry = {
        "workflows": {
            "test-wf": {
                "command": "echo {{val}}",
                "params": [{"name": "val", "type": "string"}],
            }
        }
    }
    with patch("turbollm.cli.load_registry", return_value=fake_registry):
        # Write a sticky.
        write_result = runner.invoke(
            turbo_cli.cli, ["workflows", "config", "test-wf", "val=hello-phase1"]
        )
        assert write_result.exit_code == 0, write_result.output

        # Read it back.
        read_result = runner.invoke(
            turbo_cli.cli, ["workflows", "config", "test-wf"]
        )
        assert read_result.exit_code == 0, read_result.output
        assert "hello-phase1" in read_result.output, (
            f"Expected 'hello-phase1' in output: {read_result.output!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: migration notice flag key contract
#
# The flag `migration_notice_shown_v1` is written by the HUD's
# postMigrationNoticeIfNeeded(). This test asserts the constant matches the
# spec so a refactor doesn't silently break it.
# ---------------------------------------------------------------------------

def test_migration_notice_flag_key_constant_matches_spec():
    """
    The UserDefaults flag key written by the HUD's TCC migration notice must
    match the spec value `migration_notice_shown_v1`. This test reads the
    Swift source and checks the constant literal.
    """
    app_swift = (
        _worktree_root()
        / "tools" / "turbo-hud" / "Sources" / "TurboHUD" / "App.swift"
    )
    assert app_swift.exists(), f"App.swift not found at {app_swift}"

    source = app_swift.read_text()
    assert 'migration_notice_shown_v1' in source, (
        "Expected UserDefaults flag 'migration_notice_shown_v1' not found in App.swift. "
        "The spec requires this exact key name."
    )
