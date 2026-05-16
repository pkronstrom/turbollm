"""Tests for `turbo prune` CLI subcommand (T-13)."""
from __future__ import annotations

import time
from pathlib import Path

from click.testing import CliRunner

from turbollm import cli as turbo_cli


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_recording(audio_dir: Path, vault: Path, slug: str, *, age_secs: float = 0) -> None:
    """Create the full set of artifacts for a recording slug."""
    # Audio file
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav = audio_dir / f"{slug}.wav"
    wav.write_bytes(b"RIFF" * 10)

    # Vault markdown
    meetings = vault / "Meetings"
    meetings.mkdir(parents=True, exist_ok=True)
    (meetings / f"{slug}.md").write_text(f"# {slug}\n")
    (meetings / f"{slug}.raw.md").write_text(f"raw {slug}\n")

    # Attachments dir
    attach = meetings / "attachments" / slug
    attach.mkdir(parents=True, exist_ok=True)
    (attach / "frame1.png").write_bytes(b"\x89PNG" * 5)

    if age_secs > 0:
        target_mtime = time.time() - age_secs
        for p in [wav, meetings / f"{slug}.md", meetings / f"{slug}.raw.md"]:
            import os
            os.utime(p, (target_mtime, target_mtime))


def _make_orphan_md(vault: Path, slug: str, *, age_secs: float = 0) -> None:
    """Create a summary markdown without a matching .wav (orphan)."""
    meetings = vault / "Meetings"
    meetings.mkdir(parents=True, exist_ok=True)
    md = meetings / f"{slug}.md"
    md.write_text(f"# {slug}\n")
    if age_secs > 0:
        target_mtime = time.time() - age_secs
        import os
        os.utime(md, (target_mtime, target_mtime))


def _invoke(args: list[str]) -> object:
    runner = CliRunner()
    return runner.invoke(turbo_cli.cli, args)


# ---------------------------------------------------------------------------
# T-13 Step 1: dry-run lists candidates without deleting
# ---------------------------------------------------------------------------

def test_prune_dry_run_lists_slugs_without_deleting(tmp_path):
    """Dry-run prints slug lines but leaves all files intact."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    slug = "2026-05-01-100000"
    _make_recording(audio_dir, vault, slug, age_secs=86400 * 10)

    result = _invoke([
        "prune",
        "--older-than", "3d",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    assert slug in result.output

    # Files must still exist after dry-run.
    assert (audio_dir / f"{slug}.wav").exists()
    assert (vault / "Meetings" / f"{slug}.md").exists()


def test_prune_dry_run_output_has_parseable_lines(tmp_path):
    """Each slug line starts with the slug (matches '^[0-9]{4}-' for grep -cE)."""
    import re
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    slugs = [
        "2026-05-01-100000",
        "2026-05-02-100000",
        "2026-05-03-100000",
    ]
    for slug in slugs:
        _make_recording(audio_dir, vault, slug, age_secs=86400 * 10)

    result = _invoke([
        "prune",
        "--older-than", "3d",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    slug_lines = [
        line for line in result.output.splitlines()
        if re.match(r"^\d{4}-", line)
    ]
    assert len(slug_lines) == 3


def test_prune_dry_run_line_format_has_three_fields(tmp_path):
    """Each slug line has format '<slug>  <size>  <n-files>'."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    slug = "2026-05-01-100000"
    _make_recording(audio_dir, vault, slug, age_secs=86400 * 10)

    result = _invoke([
        "prune",
        "--older-than", "3d",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    slug_line = next(l for l in result.output.splitlines() if l.startswith(slug))
    parts = slug_line.split()
    assert len(parts) == 3
    assert parts[0] == slug
    assert parts[1].isdigit()  # size in bytes
    assert parts[2].isdigit()  # number of files


# ---------------------------------------------------------------------------
# T-13 Step 1: --apply deletes files
# ---------------------------------------------------------------------------

def test_prune_apply_deletes_files(tmp_path):
    """--apply removes all candidate files for eligible slugs."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    slug = "2026-05-01-100000"
    _make_recording(audio_dir, vault, slug, age_secs=86400 * 10)

    result = _invoke([
        "prune",
        "--older-than", "3d",
        "--apply",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    assert not (audio_dir / f"{slug}.wav").exists()
    assert not (vault / "Meetings" / f"{slug}.md").exists()
    assert not (vault / "Meetings" / f"{slug}.raw.md").exists()
    assert not (vault / "Meetings" / "attachments" / slug).exists()


def test_prune_apply_reports_freed_size(tmp_path):
    """--apply output matches 'Deleted N slug(s), freed M MB'."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    slug = "2026-05-01-100000"
    _make_recording(audio_dir, vault, slug, age_secs=86400 * 10)

    result = _invoke([
        "prune",
        "--older-than", "3d",
        "--apply",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    assert "Deleted 1 slug(s)" in result.output
    assert "freed" in result.output
    assert "MB" in result.output


# ---------------------------------------------------------------------------
# T-13 Step 1: --older-than + --keep-last mutual exclusivity
# ---------------------------------------------------------------------------

def test_prune_both_flags_exits_nonzero(tmp_path):
    """Specifying both --older-than and --keep-last exits non-zero."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    result = _invoke([
        "prune",
        "--older-than", "7d",
        "--keep-last", "5",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code != 0
    assert "--older-than and --keep-last are mutually exclusive" in result.stderr


def test_prune_no_flags_exits_nonzero(tmp_path):
    """Specifying neither --older-than nor --keep-last exits non-zero."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    result = _invoke([
        "prune",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code != 0
    assert "must specify one of --older-than or --keep-last" in result.stderr


# ---------------------------------------------------------------------------
# T-13 Step 1: --older-than duration parsing
# ---------------------------------------------------------------------------

def test_prune_older_than_6w_is_42_days(tmp_path):
    """--older-than 6w selects recordings older than 42 days."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    old_slug = "2026-01-01-100000"
    new_slug = "2026-01-15-100000"

    _make_recording(audio_dir, vault, old_slug, age_secs=86400 * 50)  # 50 days old → eligible
    _make_recording(audio_dir, vault, new_slug, age_secs=86400 * 5)   # 5 days old → not eligible

    result = _invoke([
        "prune",
        "--older-than", "6w",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    assert old_slug in result.output
    assert new_slug not in result.output


def test_prune_older_than_various_suffixes(tmp_path):
    """Duration suffixes d, w, m, y all parse correctly."""
    from turbollm.cli import _parse_duration_days

    assert _parse_duration_days("7d") == 7
    assert _parse_duration_days("2w") == 14
    assert _parse_duration_days("3m") == 90
    assert _parse_duration_days("1y") == 365
    assert _parse_duration_days("6w") == 42


def test_prune_older_than_invalid_suffix_exits_nonzero(tmp_path):
    """Invalid duration suffix exits non-zero with an error message."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    result = _invoke([
        "prune",
        "--older-than", "5x",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code != 0
    assert "invalid duration '5x'" in result.stderr


def test_prune_parse_duration_invalid_raises():
    """_parse_duration_days raises ValueError for invalid input."""
    import pytest
    from turbollm.cli import _parse_duration_days

    with pytest.raises(ValueError, match="invalid duration"):
        _parse_duration_days("abc")

    with pytest.raises(ValueError, match="invalid duration"):
        _parse_duration_days("5x")

    with pytest.raises(ValueError, match="invalid duration"):
        _parse_duration_days("")


# ---------------------------------------------------------------------------
# T-13 Step 1: --keep-last keeps N newest
# ---------------------------------------------------------------------------

def test_prune_keep_last_retains_newest(tmp_path):
    """--keep-last 3 keeps the 3 newest; the 2 oldest are marked for deletion."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    slugs_by_age = [
        ("2026-04-01-100000", 86400 * 40),   # oldest
        ("2026-04-10-100000", 86400 * 30),
        ("2026-04-20-100000", 86400 * 20),
        ("2026-04-30-100000", 86400 * 10),
        ("2026-05-10-100000", 86400 * 1),    # newest
    ]
    for slug, age in slugs_by_age:
        _make_recording(audio_dir, vault, slug, age_secs=age)

    result = _invoke([
        "prune",
        "--keep-last", "3",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    # Two oldest should be eligible for pruning.
    assert "2026-04-01-100000" in result.output
    assert "2026-04-10-100000" in result.output
    # Three newest should NOT appear in the prune list.
    assert "2026-04-20-100000" not in result.output
    assert "2026-04-30-100000" not in result.output
    assert "2026-05-10-100000" not in result.output


def test_prune_keep_last_apply_deletes_oldest(tmp_path):
    """--keep-last 3 --apply deletes the 2 oldest recordings."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    slugs_by_age = [
        ("2026-04-01-100000", 86400 * 40),
        ("2026-04-10-100000", 86400 * 30),
        ("2026-04-20-100000", 86400 * 20),
        ("2026-04-30-100000", 86400 * 10),
        ("2026-05-10-100000", 86400 * 1),
    ]
    for slug, age in slugs_by_age:
        _make_recording(audio_dir, vault, slug, age_secs=age)

    result = _invoke([
        "prune",
        "--keep-last", "3",
        "--apply",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    # Oldest two should be deleted.
    assert not (audio_dir / "2026-04-01-100000.wav").exists()
    assert not (audio_dir / "2026-04-10-100000.wav").exists()
    # Newest three should remain.
    assert (audio_dir / "2026-04-20-100000.wav").exists()
    assert (audio_dir / "2026-04-30-100000.wav").exists()
    assert (audio_dir / "2026-05-10-100000.wav").exists()


# ---------------------------------------------------------------------------
# T-13 Step 1: orphan markdown is detected
# ---------------------------------------------------------------------------

def test_prune_orphan_markdown_detected(tmp_path):
    """Markdown files with no matching .wav are still listed for pruning."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    orphan_slug = "2026-04-01-100000"
    _make_orphan_md(vault, orphan_slug, age_secs=86400 * 35)  # 35 days old

    result = _invoke([
        "prune",
        "--older-than", "30d",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    assert orphan_slug in result.output


def test_prune_orphan_markdown_deleted_on_apply(tmp_path):
    """With --apply, orphan markdown files are removed."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    orphan_slug = "2026-04-01-100000"
    _make_orphan_md(vault, orphan_slug, age_secs=86400 * 35)

    result = _invoke([
        "prune",
        "--older-than", "30d",
        "--apply",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output
    assert not (vault / "Meetings" / f"{orphan_slug}.md").exists()


# ---------------------------------------------------------------------------
# T-13: no eligible recordings
# ---------------------------------------------------------------------------

def test_prune_no_eligible_exits_cleanly(tmp_path):
    """When no recordings match the criteria, exit 0 with informative message."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    # Recording is only 1 day old — not eligible for 7d prune.
    _make_recording(audio_dir, vault, "2026-01-15-100000", age_secs=86400)

    result = _invoke([
        "prune",
        "--older-than", "7d",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output


def test_prune_empty_dirs_exits_cleanly(tmp_path):
    """When audio-dir and vault don't exist, exit 0 gracefully."""
    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    result = _invoke([
        "prune",
        "--older-than", "7d",
        "--audio-dir", str(audio_dir),
        "--vault", str(vault),
    ])

    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# T-13: helper unit tests
# ---------------------------------------------------------------------------

def test_slug_candidate_files_returns_existing(tmp_path):
    """_slug_candidate_files only returns paths that exist."""
    from turbollm.cli import _slug_candidate_files

    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"
    slug = "2026-05-01-100000"

    # Create only the audio file and summary markdown.
    audio_dir.mkdir()
    (audio_dir / f"{slug}.wav").write_bytes(b"RIFF")
    meetings = vault / "Meetings"
    meetings.mkdir(parents=True)
    (meetings / f"{slug}.md").write_text("# test\n")

    files = _slug_candidate_files(audio_dir, vault, slug)
    names = {f.name for f in files}
    assert f"{slug}.wav" in names
    assert f"{slug}.md" in names
    # raw.md and attachments dir don't exist, so not returned.
    assert f"{slug}.raw.md" not in names


def test_discover_prune_slugs_includes_orphans(tmp_path):
    """_discover_prune_slugs includes markdown-only slugs (orphans)."""
    from turbollm.cli import _discover_prune_slugs

    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"

    audio_dir.mkdir()
    (audio_dir / "2026-05-01-100000.wav").write_bytes(b"RIFF")

    meetings = vault / "Meetings"
    meetings.mkdir(parents=True)
    (meetings / "2026-04-01-100000.md").write_text("# orphan\n")  # orphan

    slugs = _discover_prune_slugs(audio_dir, vault)
    assert "2026-05-01-100000" in slugs
    assert "2026-04-01-100000" in slugs


def test_discover_prune_slugs_skips_raw_md(tmp_path):
    """_discover_prune_slugs ignores .raw.md files when discovering slugs."""
    from turbollm.cli import _discover_prune_slugs

    audio_dir = tmp_path / "audio"
    vault = tmp_path / "vault"
    meetings = vault / "Meetings"
    meetings.mkdir(parents=True)

    # Only a .raw.md exists, no .md summary, no .wav.
    (meetings / "2026-04-01-100000.raw.md").write_text("raw only\n")

    slugs = _discover_prune_slugs(audio_dir, vault)
    assert "2026-04-01-100000" not in slugs
