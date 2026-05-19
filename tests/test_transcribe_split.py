"""Tests for the silence-aware split transcription pipeline.

Covers:
- plan_chunks chooses silence midpoints near the target window.
- plan_chunks hard-cuts at max_s when no silence is available.
- merge_chunk_results offsets each chunk's sentences onto a single timeline.
- transcribe_split orchestrator wires the pieces together with mocked
  ffprobe/ffmpeg/urlopen.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from turbollm import transcribe_split as ts


# ---------------------------------------------------------------------------
# plan_chunks
# ---------------------------------------------------------------------------


def test_plan_chunks_prefers_silence_near_target():
    """When silences land near target_s, plan picks the nearest one."""
    duration = 1000.0
    # Silences at 250s, 305s (5s after target), 600s
    silences = [(248.0, 252.0), (303.0, 307.0), (598.0, 602.0)]
    plan = ts.plan_chunks(duration, silences, target_s=300.0, max_s=600.0)
    # First chunk should end at midpoint of (303, 307) = 305 (nearest 300)
    assert plan[0].start == 0.0
    assert abs(plan[0].end - 305.0) < 0.01, f"got {plan[0].end}"
    # Second chunk: from 305, target 605, max 905. Silence at 600 mid=600 is
    # only valid candidate.
    assert abs(plan[1].start - 305.0) < 0.01
    assert abs(plan[1].end - 600.0) < 0.01
    # Third chunk: from 600 to duration (400s remaining < max).
    assert abs(plan[2].start - 600.0) < 0.01
    assert plan[2].end == duration


def test_plan_chunks_hard_cuts_when_no_silence():
    """No silences → chunks are exactly max_s long (last chunk may be short)."""
    plan = ts.plan_chunks(duration=1500.0, silences=[],
                          target_s=300.0, max_s=600.0)
    # Walker: t=0 → max_end=600, no silence, remaining (1500) > max_s+min ⇒ end=600
    # t=600 → max_end=1200, remaining 900 > max_s+min ⇒ end=1200
    # t=1200 → remaining 300 <= max_s+min ⇒ end=1500
    assert [(c.start, c.end) for c in plan] == [
        (0.0, 600.0), (600.0, 1200.0), (1200.0, 1500.0),
    ]


def test_plan_chunks_single_chunk_for_short_audio():
    """Audio shorter than max_s yields a single chunk covering everything."""
    plan = ts.plan_chunks(duration=200.0, silences=[],
                          target_s=300.0, max_s=600.0)
    assert len(plan) == 1
    assert plan[0].start == 0.0 and plan[0].end == 200.0


def test_plan_chunks_skips_silences_too_close_to_start():
    """Silences inside [t, t+min_chunk_s] are ignored so we don't make tiny chunks."""
    # Silence at 10s would be at t+10s — under min_chunk_s=30 default. Should
    # be skipped; planner picks the next available silence or hard-cuts.
    plan = ts.plan_chunks(duration=1000.0,
                          silences=[(8.0, 12.0), (298.0, 302.0)],
                          target_s=300.0, max_s=600.0)
    # First chunk ends at the 298–302 midpoint (300), not the 8–12 silence.
    assert abs(plan[0].end - 300.0) < 0.01


# ---------------------------------------------------------------------------
# merge_chunk_results
# ---------------------------------------------------------------------------


def test_merge_chunk_results_offsets_sentence_timestamps():
    """Each chunk's sentence start/end shifts by the chunk's absolute offset."""
    c0 = ts.Chunk(0, 0.0, 100.0)
    c1 = ts.Chunk(1, 100.0, 200.0)
    r0 = {"text": "Hello.", "sentences": [
        {"start": 0.5, "end": 1.2, "text": "Hello.", "tokens": [
            {"start": 0.5, "end": 1.2, "text": "Hello."}
        ]},
    ]}
    r1 = {"text": "World.", "sentences": [
        {"start": 3.0, "end": 4.0, "text": "World."},
    ]}
    out = ts.merge_chunk_results([(c0, r0), (c1, r1)])

    assert out["text"] == "Hello. World."
    assert len(out["sentences"]) == 2
    # Sentence 0 unchanged (offset 0).
    assert out["sentences"][0]["start"] == 0.5
    assert out["sentences"][0]["end"] == 1.2
    # Token also offset-aware.
    assert out["sentences"][0]["tokens"][0]["start"] == 0.5
    # Sentence 1 shifted by +100s.
    assert out["sentences"][1]["start"] == 103.0
    assert out["sentences"][1]["end"] == 104.0
    # chunks meta carries per-chunk info.
    assert len(out["chunks"]) == 2
    assert out["chunks"][0]["sentences"] == 1
    assert out["chunks"][1]["start"] == 100.0


def test_merge_chunk_results_falls_back_to_segments_field():
    """If a chunk returned `segments` instead of `sentences`, merge still works."""
    c0 = ts.Chunk(0, 50.0, 150.0)
    r0 = {"text": "Hi.", "segments": [
        {"start": 2.0, "end": 3.0, "text": "Hi."},
    ]}
    out = ts.merge_chunk_results([(c0, r0)])
    assert out["sentences"][0]["start"] == 52.0
    assert out["sentences"][0]["end"] == 53.0


def test_merge_chunk_results_drops_empty_chunks_silently():
    """A chunk with empty text + no sentences contributes nothing to the merged text."""
    c0 = ts.Chunk(0, 0.0, 100.0)
    c1 = ts.Chunk(1, 100.0, 200.0)
    out = ts.merge_chunk_results([
        (c0, {"text": "First.", "sentences": [{"start": 0, "end": 1, "text": "First."}]}),
        (c1, {"text": "", "sentences": []}),
    ])
    assert out["text"] == "First."
    assert len(out["sentences"]) == 1
    # Empty chunk still represented in meta (visible for debugging).
    assert out["chunks"][1]["sentences"] == 0
    assert out["chunks"][1]["text_chars"] == 0


# ---------------------------------------------------------------------------
# transcribe_split orchestrator (mocked subprocess + urlopen)
# ---------------------------------------------------------------------------


def test_transcribe_split_orchestrator_end_to_end(tmp_path):
    """Mock ffprobe/ffmpeg/urlopen and verify the merged output spans all chunks
    with correctly-offset sentence timestamps."""
    fake_audio = tmp_path / "long.wav"
    fake_audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")  # presence-only stub

    def fake_run(cmd, **kwargs):
        """Stand in for ffprobe + ffmpeg invocations."""
        program = Path(cmd[0]).name
        if program == "ffprobe":
            return MagicMock(stdout="900.0\n", stderr="", returncode=0)
        if program == "ffmpeg":
            # silencedetect path: identifiable by `-af`.
            if "-af" in cmd:
                stderr = (
                    "[silencedetect @ 0x1] silence_start: 295.0\n"
                    "[silencedetect @ 0x1] silence_end: 297.0 | silence_duration: 2.0\n"
                    "[silencedetect @ 0x1] silence_start: 595.0\n"
                    "[silencedetect @ 0x1] silence_end: 597.0 | silence_duration: 2.0\n"
                )
                return MagicMock(stdout="", stderr=stderr, returncode=0)
            # extract-chunk path: write a tiny fake wav so .read_bytes works.
            out_path = Path(cmd[-1])
            out_path.write_bytes(b"FAKE")
            return MagicMock(stdout="", stderr="", returncode=0)
        raise AssertionError(f"unexpected program: {program}")

    # Fake urlopen: each call returns a verbose_json blob with one sentence.
    call_count = {"n": 0}
    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        idx = call_count["n"]
        payload = json.dumps({
            "text": f"Chunk {idx}.",
            "sentences": [{"start": 1.0, "end": 2.0, "text": f"Chunk {idx}.",
                           "tokens": [{"start": 1.0, "end": 2.0, "text": f"Chunk {idx}."}]}],
        }).encode("utf-8")
        resp = MagicMock()
        resp.read.return_value = payload
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    with patch("turbollm.transcribe_split.subprocess.run", side_effect=fake_run):
        out = ts.transcribe_split(
            fake_audio,
            model_id="parakeet-stub",
            port=9999,
            options=ts.SplitOptions(target_s=300.0, max_s=600.0),
            progress=lambda _s: None,
            urlopen=fake_urlopen,
        )

    # plan_chunks for duration=900 with silences at 296,596 → 3 chunks.
    assert len(out["chunks"]) == 3
    # Sentence 0 starts at 1.0 (chunk0 offset 0).
    assert out["sentences"][0]["start"] == 1.0
    # Sentence 1 inside chunk1 (start ≈ 296) → offset+1.0 = ~297.
    assert abs(out["sentences"][1]["start"] - 297.0) < 0.5
    # Sentence 2 inside chunk2 (start ≈ 596) → offset+1.0 = ~597.
    assert abs(out["sentences"][2]["start"] - 597.0) < 0.5
    # Tokens were offset too.
    assert out["sentences"][1]["tokens"][0]["start"] == out["sentences"][1]["start"]
    # Text concatenated in chunk order.
    assert out["text"] == "Chunk 1. Chunk 2. Chunk 3."


def test_transcribe_split_continues_on_chunk_failure(tmp_path):
    """A failing chunk is logged but doesn't abort; remaining chunks still produce output."""
    fake_audio = tmp_path / "mid.wav"
    fake_audio.write_bytes(b"RIFF")

    def fake_run(cmd, **kwargs):
        program = Path(cmd[0]).name
        if program == "ffprobe":
            return MagicMock(stdout="900.0\n", stderr="", returncode=0)
        if program == "ffmpeg":
            if "-af" in cmd:
                return MagicMock(stdout="", stderr="", returncode=0)  # no silences
            Path(cmd[-1]).write_bytes(b"WAV")
            return MagicMock(stdout="", stderr="", returncode=0)
        raise AssertionError

    call_count = {"n": 0}
    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated network error")
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            "text": "Second.",
            "sentences": [{"start": 0.5, "end": 1.0, "text": "Second."}],
        }).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    progress_log: list[str] = []
    with patch("turbollm.transcribe_split.subprocess.run", side_effect=fake_run):
        out = ts.transcribe_split(
            fake_audio, model_id="m", port=1,
            options=ts.SplitOptions(target_s=300.0, max_s=600.0),
            progress=progress_log.append,
            urlopen=fake_urlopen,
        )

    # Only the second chunk produced output; first chunk failed but didn't abort.
    assert out["text"] == "Second."
    assert any("FAILED" in line for line in progress_log)
    assert len(out["chunks"]) == 1  # only succeeded chunk recorded
