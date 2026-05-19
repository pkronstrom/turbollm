"""Tests for the `summarize-to-obsidian` script — T-12.

The script lives in models.toml under [scripts.summarize-to-obsidian].  We
extract its `command` field and exercise it directly via `bash -c` with a
minimal fixture audio file.  All external calls (`turbo transcribe` and `pi`)
are replaced by stub executables written to a temp directory that is prepended
to $PATH.

Scenarios covered:
- Both <slug>.md and <slug>.raw.md are written to the Obsidian vault.
- <slug>.raw.md lines match the [mm:ss] <text> format for multi-segment input.
- Single-segment (fallback) input formats correctly: [00:00] <text>.
- The main <slug>.md still contains the YAML frontmatter + summary.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
import tomllib  # Python 3.11+; falls back to tomli below
try:
    import tomllib  # noqa: F811
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _models_toml_path() -> Path:
    """Locate models.toml relative to this test file."""
    here = Path(__file__).resolve().parent
    candidate = here.parent / "models.toml"
    if not candidate.exists():
        pytest.skip(f"models.toml not found at {candidate}")
    return candidate


def _script_command() -> str:
    """Extract the summarize-to-obsidian command from models.toml."""
    path = _models_toml_path()
    with open(path, "rb") as f:
        registry = tomllib.load(f)
    scripts = registry.get("scripts", {})
    script = scripts.get("summarize-to-obsidian")
    if script is None:
        pytest.skip("summarize-to-obsidian not found in models.toml")
    return script["command"]


def _make_stub(bin_dir: Path, name: str, body: str) -> None:
    """Write an executable stub script to bin_dir/name."""
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MULTI_SEGMENT_RESPONSE = json.dumps([
    {"start": 0.0,   "end": 5.0,  "text": "Hello world."},
    {"start": 65.5,  "end": 70.0, "text": "One minute in."},
    {"start": 125.0, "end": 130.0, "text": "Two minutes five."},
])

SINGLE_SEGMENT_RESPONSE = json.dumps([
    {"start": 0.0, "end": None, "text": "Full transcript as one segment."},
])


def _run_script(
    tmp_path: Path,
    segments_json: str,
    transcript_text: str = "Stub transcript text.",
    summary_text: str = "Stub summary.",
    *,
    shape: str = "segments",
) -> dict[str, Path]:
    """Run the summarize-to-obsidian script with stub binaries.

    ``shape`` controls the key name carrying the segment list — "segments" for
    whisper-shaped backends, "sentences" for parakeet's mlx-audio shape. The
    workflow must accept either.

    Returns a dict with keys ``md``, ``raw_md`` pointing at the output files.
    """
    # Stub binaries directory (prepended to PATH).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Workflow now calls `turbo transcribe --format verbose_json <file>` once
    # and parses text + segments from the same blob. The stub returns the
    # combined shape regardless of args.
    assert shape in ("segments", "sentences"), shape
    blob_payload = json.dumps({
        "text": transcript_text,
        shape: json.loads(segments_json),
    })
    blob_file = tmp_path / "blob.json"
    blob_file.write_text(blob_payload)

    # Single `turbo` stub that dispatches on the first arg:
    #   - `turbo transcribe ...` → emit the verbose_json blob
    #   - `turbo pi ...`         → emit the summary text (consumes stdin transcript)
    _make_stub(bin_dir, "turbo", f"""\
case "$1" in
  transcribe) cat '{blob_file}' ;;
  pi)         cat > /dev/null; printf '%s' "{summary_text}" ;;
  *)          echo "stub: unknown subcommand $1" >&2; exit 2 ;;
esac
""")

    # Set up a fake vault.
    vault = tmp_path / "vault"
    vault.mkdir()

    # Fake audio file (content irrelevant; the stubs ignore it).
    audio = tmp_path / "my-meeting.wav"
    audio.write_bytes(b"RIFF")

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "OBSIDIAN_VAULT": str(vault),
    }

    script_cmd = _script_command()
    result = subprocess.run(
        ["bash", "-c", script_cmd, "summarize-to-obsidian", str(audio)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Script exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    slug = "my-meeting"
    return {
        "md":     vault / "Meetings" / f"{slug}.md",
        "raw_md": vault / "Meetings" / f"{slug}.raw.md",
    }


# ---------------------------------------------------------------------------
# T-12 Step 1 tests
# ---------------------------------------------------------------------------

def test_both_md_and_raw_md_are_written(tmp_path):
    """Script writes both <slug>.md and <slug>.raw.md."""
    paths = _run_script(tmp_path, MULTI_SEGMENT_RESPONSE)
    assert paths["md"].exists(), f"Missing: {paths['md']}"
    assert paths["raw_md"].exists(), f"Missing: {paths['raw_md']}"


def test_raw_md_contains_timestamp_lines_for_multi_segment(tmp_path):
    """<slug>.raw.md has [mm:ss] <text> lines for each segment."""
    paths = _run_script(tmp_path, MULTI_SEGMENT_RESPONSE)
    lines = paths["raw_md"].read_text().strip().splitlines()
    assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}: {lines}"

    # First segment: 0 s → [00:00]
    assert lines[0].startswith("[00:00]"), f"Line 0 wrong: {lines[0]!r}"
    assert "Hello world." in lines[0]

    # Second segment: 65.5 s → 1 m 5 s → [01:05]
    assert lines[1].startswith("[01:05]"), f"Line 1 wrong: {lines[1]!r}"
    assert "One minute in." in lines[1]

    # Third segment: 125 s → 2 m 5 s → [02:05]
    assert lines[2].startswith("[02:05]"), f"Line 2 wrong: {lines[2]!r}"
    assert "Two minutes five." in lines[2]


def test_raw_md_single_segment_fallback_formats_correctly(tmp_path):
    """Single-segment (fallback) input → [00:00] <text>."""
    paths = _run_script(tmp_path, SINGLE_SEGMENT_RESPONSE)
    lines = paths["raw_md"].read_text().strip().splitlines()
    assert len(lines) == 1, f"Expected 1 line, got: {lines}"
    assert lines[0].startswith("[00:00]"), f"Wrong format: {lines[0]!r}"
    assert "Full transcript as one segment." in lines[0]


def test_raw_md_accepts_parakeet_sentences_shape(tmp_path):
    """When the transcribe blob carries `sentences[]` instead of `segments[]`
    (mlx-audio parakeet shape), the workflow must still produce [mm:ss] lines."""
    paths = _run_script(tmp_path, MULTI_SEGMENT_RESPONSE, shape="sentences")
    lines = paths["raw_md"].read_text().strip().splitlines()
    assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}: {lines}"
    assert lines[0].startswith("[00:00]") and "Hello world." in lines[0]
    assert lines[1].startswith("[01:05]") and "One minute in." in lines[1]
    assert lines[2].startswith("[02:05]") and "Two minutes five." in lines[2]


def test_main_md_has_yaml_frontmatter(tmp_path):
    """<slug>.md must begin with YAML frontmatter (--- block)."""
    paths = _run_script(tmp_path, MULTI_SEGMENT_RESPONSE)
    content = paths["md"].read_text()
    assert content.startswith("---\n"), f"No frontmatter: {content[:80]!r}"
    # Check required frontmatter keys.
    assert "date:" in content
    assert "source:" in content
    assert "tags:" in content


def test_main_md_contains_summary(tmp_path):
    """<slug>.md contains the summary section."""
    paths = _run_script(tmp_path, MULTI_SEGMENT_RESPONSE, summary_text="Stub summary.")
    content = paths["md"].read_text()
    assert "Stub summary." in content
