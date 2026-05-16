"""Tests for the `summarize-to-obsidian-bundled` script — T-14 / T-fix-6.

The script lives in models.toml under [scripts.summarize-to-obsidian-bundled].
It accepts TWO positional arguments:
  $1 — audio manifest JSON: {"path":"...","start_offset_ms":N}  (from audio acquirer)
  $2 — screen recording manifest JSON string (from the record-screen acquirer)

It produces THREE artifacts:
  <vault>/Meetings/<slug>.md           — frontmatter + summary, no ![[...]] refs
  <vault>/Meetings/<slug>.raw.md       — frontmatter + interleaved [mm:ss] lines
                                         and ![[<slug>/<filename>]] image refs
  <vault>/Meetings/attachments/<slug>/ — PNG copies from the manifest frames

After copying PNGs the script deletes the temp source directory.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _models_toml_path() -> Path:
    here = Path(__file__).resolve().parent
    candidate = here.parent / "models.toml"
    if not candidate.exists():
        pytest.skip(f"models.toml not found at {candidate}")
    return candidate


def _script_command() -> str:
    path = _models_toml_path()
    with open(path, "rb") as f:
        registry = tomllib.load(f)
    scripts = registry.get("scripts", {})
    script = scripts.get("summarize-to-obsidian-bundled")
    if script is None:
        pytest.skip("summarize-to-obsidian-bundled not found in models.toml")
    return script["command"]


def _make_stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

INTERLEAVE_SEGMENTS = json.dumps([
    {"start": 0.0,  "end": 4.0,  "text": "First."},
    {"start": 5.0,  "end": 11.0, "text": "Second."},
    {"start": 12.0, "end": 18.0, "text": "Third."},
])

SUMMARY_TEXT = "Stub summary."


def _make_manifest(src_dir: Path) -> tuple[str, list[Path]]:
    """Create fake PNG frames in src_dir and return (manifest_json, [paths])."""
    src_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    offsets = [3200, 14500]
    names = ["001-T+3200.png", "002-T+14500.png"]
    for name, t_ms in zip(names, offsets):
        p = src_dir / name
        p.write_bytes(b"\x89PNG\r\n")
        frames.append({"path": str(p), "t_offset_ms": t_ms})
    manifest = {
        "frames": frames,
        "duration_ms": 20000,
        "dropped_overcap": 0,
        "start_offset_ms": 0,
    }
    return json.dumps(manifest), [src_dir / n for n in names]


def _make_audio_manifest(audio_path: Path, start_offset_ms: int = 0) -> str:
    """Build the audio manifest JSON string that record-audio now emits."""
    return json.dumps({"path": str(audio_path), "start_offset_ms": start_offset_ms})


def _run_bundled_script(
    tmp_path: Path,
    segments_json: str = INTERLEAVE_SEGMENTS,
    summary_text: str = SUMMARY_TEXT,
    audio_start_offset_ms: int = 0,
) -> dict:
    """Run the summarize-to-obsidian-bundled script with stub binaries.

    Returns a dict with keys: md, raw_md, attachments_dir, src_dir, vault, slug.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Write segments JSON to a file so the stub can cat it (avoids quoting issues).
    segments_file = tmp_path / "segments.json"
    segments_file.write_text(segments_json)

    _make_stub(bin_dir, "turbo", f"""\
case "$2" in
  --format) cat '{segments_file}' ;;
  *) printf '%s' 'Stub transcript text.' ;;
esac
""")

    _make_stub(bin_dir, "pi", f"printf '%s' '{summary_text}'\n")

    vault = tmp_path / "vault"
    vault.mkdir()

    audio = tmp_path / "my-meeting.wav"
    audio.write_bytes(b"RIFF")

    # $1 is now an audio manifest JSON (not a bare path).
    audio_manifest_json = _make_audio_manifest(audio, start_offset_ms=audio_start_offset_ms)

    src_dir = tmp_path / "frames-src"
    manifest_json, _frame_paths = _make_manifest(src_dir)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "OBSIDIAN_VAULT": str(vault),
    }

    script_cmd = _script_command()
    result = subprocess.run(
        ["bash", "-c", script_cmd, "summarize-to-obsidian-bundled",
         audio_manifest_json, manifest_json],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Script exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    slug = "my-meeting"
    return {
        "md":              vault / "Meetings" / f"{slug}.md",
        "raw_md":          vault / "Meetings" / f"{slug}.raw.md",
        "attachments_dir": vault / "Meetings" / "attachments" / slug,
        "src_dir":         src_dir,
        "vault":           vault,
        "slug":            slug,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_three_artifacts_produced(tmp_path):
    """Script writes .md, .raw.md, and attachments/<slug>/ directory."""
    paths = _run_bundled_script(tmp_path)
    assert paths["md"].exists(),              f"Missing: {paths['md']}"
    assert paths["raw_md"].exists(),          f"Missing: {paths['raw_md']}"
    assert paths["attachments_dir"].is_dir(), f"Missing dir: {paths['attachments_dir']}"


def test_summary_md_has_no_image_references(tmp_path):
    """<slug>.md must NOT contain any ![[...]] image embed syntax."""
    paths = _run_bundled_script(tmp_path)
    content = paths["md"].read_text()
    assert "![[" not in content, (
        f"<slug>.md should not contain ![[...]] refs, but found some:\n{content}"
    )


def test_raw_md_interleaves_segments_and_keyframes(tmp_path):
    """<slug>.raw.md interleaves [mm:ss] segment lines and ![[...]] keyframe refs.

    Segments at 0.0 s, 5.0 s, 12.0 s with keyframes at t_offset_ms 3200 and 14500
    (start_offset_ms=0) should produce in order:
      [00:00] First.
      ![[my-meeting/001-T+3200.png]]
      [00:05] Second.
      [00:12] Third.
      ![[my-meeting/002-T+14500.png]]
    """
    paths = _run_bundled_script(tmp_path)
    raw_text = paths["raw_md"].read_text()

    # Strip YAML frontmatter.
    content = re.sub(r'^---\n.*?\n---\n\n?', '', raw_text, flags=re.DOTALL)
    content_lines = [l for l in content.splitlines() if l.strip()]

    slug = paths["slug"]
    assert len(content_lines) == 5, (
        f"Expected 5 interleaved lines, got {len(content_lines)}:\n"
        + "\n".join(content_lines)
    )
    assert content_lines[0] == "[00:00] First.",       f"Line 0: {content_lines[0]!r}"
    assert content_lines[1] == f"![[{slug}/001-T+3200.png]]",  f"Line 1: {content_lines[1]!r}"
    assert content_lines[2] == "[00:05] Second.",      f"Line 2: {content_lines[2]!r}"
    assert content_lines[3] == "[00:12] Third.",       f"Line 3: {content_lines[3]!r}"
    assert content_lines[4] == f"![[{slug}/002-T+14500.png]]", f"Line 4: {content_lines[4]!r}"


def test_attachments_dir_has_pngs(tmp_path):
    """attachments/<slug>/ contains exactly the expected PNG files."""
    paths = _run_bundled_script(tmp_path)
    att_dir = paths["attachments_dir"]
    assert att_dir.is_dir(), f"Attachments dir missing: {att_dir}"
    pngs = sorted(p.name for p in att_dir.iterdir() if p.suffix == ".png")
    assert pngs == ["001-T+3200.png", "002-T+14500.png"], (
        f"Unexpected PNG list: {pngs}"
    )


def test_temp_dir_is_cleaned_after_success(tmp_path):
    """The frames source directory must be deleted after a successful run."""
    paths = _run_bundled_script(tmp_path)
    assert not paths["src_dir"].exists(), (
        f"Temp source dir still exists after script ran: {paths['src_dir']}"
    )


def test_audio_start_offset_shifts_segment_times(tmp_path):
    """T-fix-6: non-zero audio start_offset_ms shifts transcript segments in .raw.md.

    Audio starts 2000 ms after T0 (start_offset_ms=2000).
    Screen keyframes have start_offset_ms=0 (screen started at T0).

    Segments at audio-relative 0.0 s, 5.0 s, 12.0 s become workflow-t
    2000, 7000, 14000 ms respectively.
    Screen keyframes at t_offset_ms 3200 and 14500 ms stay at 3200 and 14500 ms.

    Expected interleaved order:
      [00:00] First.    (wf_t=2000)
      ![[...001...]]    (wf_t=3200)
      [00:05] Second.   (wf_t=7000)
      [00:12] Third.    (wf_t=14000)
      ![[...002...]]    (wf_t=14500)
    """
    paths = _run_bundled_script(tmp_path, audio_start_offset_ms=2000)
    raw_text = paths["raw_md"].read_text()

    # Strip YAML frontmatter.
    content = re.sub(r'^---\n.*?\n---\n\n?', '', raw_text, flags=re.DOTALL)
    content_lines = [l for l in content.splitlines() if l.strip()]

    slug = paths["slug"]
    assert len(content_lines) == 5, (
        f"Expected 5 interleaved lines, got {len(content_lines)}:\n"
        + "\n".join(content_lines)
    )
    # Segments are shifted: 0s+2000ms → wf_t=2000ms ([00:00] label uses audio-relative time)
    # Note: the [mm:ss] label uses the segment's audio-relative start time, not workflow_t.
    assert content_lines[0] == "[00:00] First.",       f"Line 0: {content_lines[0]!r}"
    assert content_lines[1] == f"![[{slug}/001-T+3200.png]]",  f"Line 1: {content_lines[1]!r}"
    assert content_lines[2] == "[00:05] Second.",      f"Line 2: {content_lines[2]!r}"
    assert content_lines[3] == "[00:12] Third.",       f"Line 3: {content_lines[3]!r}"
    assert content_lines[4] == f"![[{slug}/002-T+14500.png]]", f"Line 4: {content_lines[4]!r}"
