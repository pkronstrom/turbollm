"""Silence-aware splitting for long-audio transcription via mlx-audio.

Why this exists: mlx-audio's parakeet backend chunks internally (default
``chunk_duration=30`` s) and merges chunk outputs via longest-common-subsequence
on the overlap. Each merge boundary is a chance to drop tokens, and on
multi-hour audio (~hundreds of chunks) the cumulative loss matters. Pre-cutting
the audio at silences sidesteps the merge entirely: each chunk is independent
and its sentences are simply offset-shifted onto a single timeline.

Public entry point: ``transcribe_split`` orchestrates ffprobe → silencedetect
→ chunk extraction → per-chunk POST to ``/v1/audio/transcriptions`` → merge.
Everything else is helpers exposed for tests.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .multipart import build_multipart


# ---------------------------------------------------------------------------
# Audio probing
# ---------------------------------------------------------------------------

_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.+-]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.+-]+)\s*\|\s*silence_duration:\s*([0-9.+-]+)")


def probe_duration_seconds(path: Path, *, ffprobe_bin: str = "ffprobe") -> float:
    """Return audio duration in seconds via ffprobe. Raises on failure."""
    proc = subprocess.run(
        [ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(proc.stdout.strip())


def find_silences(
    path: Path,
    *,
    noise_db: float = -30.0,
    min_silence_s: float = 0.8,
    ffmpeg_bin: str = "ffmpeg",
) -> list[tuple[float, float]]:
    """Return list of (silence_start_s, silence_end_s) windows.

    ffmpeg's silencedetect writes results to stderr regardless of -loglevel.
    We discard the audio output (-f null /dev/null) and parse stderr.
    """
    proc = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-nostats", "-i", str(path),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(
            "[split] silence detection failed, falling back to fixed-interval cuts",
            file=sys.stderr,
        )
        return []
    silences: list[tuple[float, float]] = []
    current_start: float | None = None
    for line in proc.stderr.splitlines():
        m_start = _SILENCE_START_RE.search(line)
        if m_start:
            current_start = float(m_start.group(1))
            continue
        m_end = _SILENCE_END_RE.search(line)
        if m_end and current_start is not None:
            silences.append((current_start, float(m_end.group(1))))
            current_start = None
    return silences


# ---------------------------------------------------------------------------
# Chunk planning
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Chunk:
    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_chunks(
    duration: float,
    silences: list[tuple[float, float]],
    *,
    target_s: float = 300.0,
    max_s: float = 600.0,
    min_chunk_s: float = 30.0,
) -> list[Chunk]:
    """Build a chunk plan that prefers silence midpoints near ``target_s``.

    Algorithm: walk forward in time. For each next chunk starting at ``t``,
    look at silence midpoints in ``(t + min_chunk_s, t + max_s]``. Pick the
    one nearest ``t + target_s``; if none exists, hard-cut at ``t + max_s``.
    The final chunk extends to ``duration``.
    """
    chunks: list[Chunk] = []
    t = 0.0
    idx = 0
    eps = 1e-6
    while t + eps < duration:
        max_end = min(t + max_s, duration)
        ideal = t + target_s
        candidates = [
            (ss + se) / 2.0
            for ss, se in silences
            if t + min_chunk_s < (ss + se) / 2.0 <= max_end
        ]
        if candidates:
            end = min(candidates, key=lambda c: abs(c - ideal))
            if duration - end < min_chunk_s:
                # A silence-picked cut can still leave a sub-min_chunk_s tail
                # (e.g. a silence sitting just before the very end of the
                # audio) — fold it into this chunk rather than emitting a
                # tiny trailing one.
                end = duration
        elif duration - t <= max_s + min_chunk_s:
            # Remaining audio fits in one chunk (possibly slightly over max_s
            # to avoid a tiny tail).
            end = duration
        else:
            end = max_end
        chunks.append(Chunk(index=idx, start=t, end=end))
        idx += 1
        t = end
    return chunks


# ---------------------------------------------------------------------------
# Chunk extraction + per-chunk POST
# ---------------------------------------------------------------------------

def extract_chunk_wav(
    src: Path,
    chunk: Chunk,
    dst: Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> None:
    """Slice [chunk.start, chunk.end] from ``src`` into ``dst`` via ffmpeg.

    Uses fast `-ss` before `-i` + `-c copy`. For PCM WAV input this is
    sample-addressable, so accuracy is bounded by frame headers (negligible).
    """
    subprocess.run(
        [ffmpeg_bin, "-y", "-loglevel", "error",
         "-ss", f"{chunk.start:.3f}", "-to", f"{chunk.end:.3f}",
         "-i", str(src), "-c", "copy", str(dst)],
        check=True,
    )


def transcribe_one_chunk(
    chunk_wav_bytes: bytes,
    filename: str,
    *,
    model_id: str,
    port: int,
    language: Optional[str] = None,
    timeout: int = 600,
    urlopen=urllib.request.urlopen,
) -> dict:
    """POST a single chunk to mlx-audio and return the parsed JSON dict.

    ``urlopen`` is injectable so tests can replace the HTTP call.
    """
    fields = {"model": model_id, "response_format": "verbose_json"}
    if language:
        fields["language"] = language
    body, ctype = build_multipart(fields, "file", filename, "audio/wav", chunk_wav_bytes)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": ctype},
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"text": raw, "sentences": []}


# ---------------------------------------------------------------------------
# Merge: shift sentences by chunk offset and concatenate
# ---------------------------------------------------------------------------

def merge_chunk_results(results: list[tuple[Chunk, dict]]) -> dict:
    """Stitch per-chunk verbose_json blobs into one timeline-shifted blob.

    Output shape mirrors mlx-audio's single-call response:
    ``{"text": str, "sentences": list, "chunks": list[chunk_meta]}``

    Each sentence's ``start``/``end`` is shifted by its chunk's absolute
    offset; tokens inside sentences are likewise shifted when present.

    A chunk entry whose data dict carries an ``"error"`` key (a failed
    transcription — see ``transcribe_split``) contributes no text/sentences
    but still gets a ``chunks_meta`` entry (with the error message) so the
    failure is visible in the merged result instead of silently vanishing.
    When one or more chunks failed, a top-level ``"failed_chunks"`` count is
    added. Neither key appears when every chunk succeeded, so the all-success
    shape is unchanged (backward compatible).
    """
    out_text_parts: list[str] = []
    out_sentences: list[dict] = []
    chunks_meta: list[dict] = []
    failed_chunks = 0
    for chunk, data in results:
        if "error" in data:
            failed_chunks += 1
            chunks_meta.append({
                "index": chunk.index,
                "start": chunk.start,
                "end": chunk.end,
                "duration": chunk.duration,
                "sentences": 0,
                "text_chars": 0,
                "error": data["error"],
            })
            continue
        text = (data.get("text") or "").strip()
        sents = data.get("sentences") or []
        # Support both shapes (sentences from parakeet, segments from whisper).
        if not sents and data.get("segments"):
            sents = data["segments"]
        shifted = []
        for s in sents:
            shifted_s = dict(s)
            if "start" in shifted_s and shifted_s["start"] is not None:
                shifted_s["start"] = float(shifted_s["start"]) + chunk.start
            if "end" in shifted_s and shifted_s["end"] is not None:
                shifted_s["end"] = float(shifted_s["end"]) + chunk.start
            toks = s.get("tokens")
            if toks:
                shifted_s["tokens"] = []
                for tok in toks:
                    new_tok = dict(tok)
                    if "start" in new_tok and new_tok["start"] is not None:
                        new_tok["start"] = float(new_tok["start"]) + chunk.start
                    if "end" in new_tok and new_tok["end"] is not None:
                        new_tok["end"] = float(new_tok["end"]) + chunk.start
                    shifted_s["tokens"].append(new_tok)
            shifted.append(shifted_s)
        out_sentences.extend(shifted)
        if text:
            out_text_parts.append(text)
        chunks_meta.append({
            "index": chunk.index,
            "start": chunk.start,
            "end": chunk.end,
            "duration": chunk.duration,
            "sentences": len(shifted),
            "text_chars": len(text),
        })
    out = {
        "text": " ".join(out_text_parts),
        "sentences": out_sentences,
        "chunks": chunks_meta,
    }
    if failed_chunks:
        out["failed_chunks"] = failed_chunks
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SplitOptions:
    target_s: float = 300.0
    max_s: float = 600.0
    silence_db: float = -30.0
    silence_min_s: float = 0.8


def transcribe_split(
    audio_path: Path,
    *,
    model_id: str,
    port: int,
    options: SplitOptions = SplitOptions(),
    language: Optional[str] = None,
    progress: Callable[[str], None] = lambda s: print(s, file=sys.stderr),
    ffprobe_bin: str = "ffprobe",
    ffmpeg_bin: str = "ffmpeg",
    urlopen=urllib.request.urlopen,
) -> dict:
    """End-to-end: probe → silencedetect → plan → per-chunk POST → merge.

    Returns a verbose_json-shaped dict identical to a single-call response.
    Failures on individual chunks are logged and don't abort the run (rest of
    the audio is still transcribed) — but they're not silently dropped
    either: each failed chunk gets a `chunks_meta` entry with an `"error"`
    field, and the result carries a top-level `"failed_chunks"` count, via
    `merge_chunk_results`.
    """
    duration = probe_duration_seconds(audio_path, ffprobe_bin=ffprobe_bin)
    progress(f"[split] duration={duration:.1f}s")

    silences = find_silences(
        audio_path,
        noise_db=options.silence_db,
        min_silence_s=options.silence_min_s,
        ffmpeg_bin=ffmpeg_bin,
    )
    progress(f"[split] silences_found={len(silences)} (noise={options.silence_db}dB, min={options.silence_min_s}s)")

    plan = plan_chunks(
        duration, silences,
        target_s=options.target_s, max_s=options.max_s,
    )
    progress(f"[split] chunks={len(plan)} (target={options.target_s}s, max={options.max_s}s)")

    results: list[tuple[Chunk, dict]] = []
    with tempfile.TemporaryDirectory(prefix="turbo-split-") as td:
        td_path = Path(td)
        for chunk in plan:
            chunk_path = td_path / f"chunk-{chunk.index:04d}.wav"
            try:
                extract_chunk_wav(audio_path, chunk, chunk_path, ffmpeg_bin=ffmpeg_bin)
                wav_bytes = chunk_path.read_bytes()
                data = transcribe_one_chunk(
                    wav_bytes,
                    filename=chunk_path.name,
                    model_id=model_id,
                    port=port,
                    language=language,
                    urlopen=urlopen,
                )
                n_sent = len(data.get("sentences") or data.get("segments") or [])
                progress(
                    f"[split] chunk {chunk.index + 1}/{len(plan)} "
                    f"[{chunk.start:7.1f}–{chunk.end:7.1f}]s → "
                    f"{n_sent} sentences, {len(data.get('text') or '')} chars"
                )
                results.append((chunk, data))
            except Exception as e:  # noqa: BLE001 — best-effort per chunk
                progress(
                    f"[split] chunk {chunk.index + 1}/{len(plan)} FAILED "
                    f"[{chunk.start:.1f}–{chunk.end:.1f}]s: {e}"
                )
                results.append((chunk, {"error": str(e)}))

    return merge_chunk_results(results)
