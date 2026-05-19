"""Tests for `turbo transcribe` -- T-11 (cluster F-AP).

Covers:
- verbose_json always sent to mlx-audio (server always gets response_format=verbose_json)
- --format text emits concatenated text (default behaviour unchanged)
- --format segments emits segments JSON array
- --format segments with text-only response synthesises one segment + warns on stderr
"""
import json
import sys
import io
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from turbollm import cli as turbo_cli


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAKE_MODEL = {
    "name": "fake-asr",
    "hf_repo": "fake/asr",
    "backend": "mlx-audio",
}


class _FakeProvider:
    name = "mlx-audio"
    install_hint = "install mlx-audio"

    def is_downloaded(self, model):
        return True

    def is_available(self):
        return True

    def get_model_id(self, model):
        return "fake/asr"

    def build_serve_cmd(self, model, port):
        return ["fake-server", "--port", str(port)]


def _make_urlopen_mock(response_body: str):
    """Return a context manager mock that yields a response with the given body."""
    resp = MagicMock()
    resp.read.return_value = response_body.encode("utf-8")
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _invoke_transcribe(tmp_path, extra_args=(), response_json=None):
    """Helper: run `turbo transcribe` against a temporary WAV file.

    Patches out all network / model resolution so tests are fully offline.
    Returns (result, stdout_lines, stderr_captured) where:
    - result is the Click CliRunner result
    - stdout_lines is the CliRunner combined output (stdout + Rich stderr mixed)
    - stderr_captured is the raw text written to sys.stderr during the run
    """
    wav = tmp_path / "test.wav"
    # Write a minimal valid WAV header so the file exists (content doesn't matter
    # since we mock urlopen before bytes are actually sent).
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    raw = json.dumps(response_json) if response_json is not None else ""
    urlopen_mock = _make_urlopen_mock(raw)

    # Intercept sys.stderr writes that happen inside the transcribe handler
    # (the fallback warning uses sys.stderr.write directly).
    captured_stderr = io.StringIO()

    runner = CliRunner()
    with (
        patch.object(turbo_cli, "_get_running_model", return_value=None),
        patch.object(turbo_cli, "resolve_model", return_value=FAKE_MODEL),
        patch.object(turbo_cli, "_get_provider_for", return_value=_FakeProvider()),
        patch.object(turbo_cli, "_server_is_running", return_value=True),
        patch.object(turbo_cli.urllib.request, "urlopen", return_value=urlopen_mock),
        patch.object(turbo_cli.sys, "stderr", captured_stderr),
    ):
        result = runner.invoke(
            turbo_cli.cli,
            ["transcribe", str(wav), *extra_args],
        )
    return result, captured_stderr.getvalue()


def _extract_json_from_output(output: str):
    """Extract the JSON portion from CliRunner output that may contain Rich console lines."""
    # Find the first '[' or '{' that starts JSON; everything before is Rich status output.
    for i, ch in enumerate(output):
        if ch in ("[", "{"):
            json_part = output[i:]
            # Strip any trailing non-JSON lines (e.g. warnings written to same stream).
            # Try progressively shorter slices until one parses.
            for end in range(len(json_part), 0, -1):
                try:
                    return json.loads(json_part[:end])
                except json.JSONDecodeError:
                    continue
    raise ValueError(f"No JSON found in output: {output!r}")


def _extract_text_lines(output: str):
    """Return lines from CliRunner output that don't look like Rich status lines."""
    result_lines = []
    for line in output.splitlines():
        # Skip Rich console lines (server status, POST log lines).
        if line.startswith("Server ") or line.startswith("POST ") or line.startswith("Start"):
            continue
        result_lines.append(line)
    return "\n".join(result_lines).strip()


# ---------------------------------------------------------------------------
# T-11 Scenario: text format unchanged from user POV
# ---------------------------------------------------------------------------


def test_format_text_emits_concatenated_text(tmp_path):
    """--format text (default) emits the `text` field from the verbose response."""
    response = {
        "text": "First sentence. Second sentence.",
        "segments": [
            {"start": 0.0, "end": 2.1, "text": "First sentence."},
            {"start": 2.1, "end": 4.0, "text": " Second sentence."},
        ],
    }
    result, _ = _invoke_transcribe(tmp_path, response_json=response)
    assert result.exit_code == 0, result.output
    assert "First sentence. Second sentence." in result.output


def test_format_text_is_default(tmp_path):
    """Invoking without --format is equivalent to --format text."""
    response = {"text": "Hello.", "segments": [{"start": 0.0, "end": 1.0, "text": "Hello."}]}
    result, _ = _invoke_transcribe(tmp_path, response_json=response)
    assert result.exit_code == 0
    assert "Hello." in result.output


# ---------------------------------------------------------------------------
# T-11 Scenario: --format segments emits the segments array
# ---------------------------------------------------------------------------


def test_format_segments_emits_json_array(tmp_path):
    """--format segments emits the verbose_json segments as a JSON array."""
    response = {
        "text": "First. Second.",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "First."},
            {"start": 2.0, "end": 4.0, "text": "Second."},
        ],
    }
    result, _ = _invoke_transcribe(tmp_path, extra_args=["--format", "segments"], response_json=response)
    assert result.exit_code == 0, result.output
    parsed = _extract_json_from_output(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    for seg in parsed:
        assert "start" in seg
        assert "end" in seg
        assert "text" in seg


def test_format_segments_is_valid_json_for_jq(tmp_path):
    """The segments output must parse cleanly (jq-compatible)."""
    response = {
        "text": "A. B. C.",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "A."},
            {"start": 1.0, "end": 2.0, "text": "B."},
            {"start": 2.0, "end": 3.0, "text": "C."},
        ],
    }
    result, _ = _invoke_transcribe(tmp_path, extra_args=["--format", "segments"], response_json=response)
    assert result.exit_code == 0
    parsed = _extract_json_from_output(result.output)
    assert len(parsed) == 3


# ---------------------------------------------------------------------------
# T-11 Scenario: text-only server fallback synthesises one segment
# ---------------------------------------------------------------------------


def test_format_segments_fallback_synthesises_one_segment(tmp_path):
    """When the server returns text-only (no segments), synthesise one segment."""
    response = {"text": "Hello world."}
    result, _ = _invoke_transcribe(tmp_path, extra_args=["--format", "segments"], response_json=response)
    assert result.exit_code == 0, result.output
    parsed = _extract_json_from_output(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    seg = parsed[0]
    assert seg["start"] == 0
    assert seg["end"] is None
    assert seg["text"] == "Hello world."


def test_format_segments_fallback_emits_stderr_warning(tmp_path):
    """The fallback path must emit the warning to stderr."""
    response = {"text": "Hello world."}
    # CliRunner captures both stdout and stderr writes into result.output.
    # The warning is written via sys.stderr.write which CliRunner routes to its
    # output buffer — check there.
    result, _ = _invoke_transcribe(tmp_path, extra_args=["--format", "segments"], response_json=response)
    assert "warning: mlx-audio returned neither segments nor sentences" in result.output


# ---------------------------------------------------------------------------
# parakeet shape: server returns `sentences[]` instead of `segments[]`
# ---------------------------------------------------------------------------


def test_format_segments_maps_parakeet_sentences_to_segments(tmp_path):
    """When the server returns parakeet-shape `sentences[]`, --format segments
    must project each sentence to {start, end, text} so jq pipelines see the
    same shape regardless of backend."""
    response = {
        "text": "Hello. World now.",
        "sentences": [
            {"start": 0.32, "end": 1.05, "text": "Hello.", "tokens": [{"text": "Hello."}]},
            {"start": 1.40, "end": 2.80, "text": " World now.", "tokens": [{"text": "World now."}]},
        ],
    }
    result, _ = _invoke_transcribe(tmp_path, extra_args=["--format", "segments"], response_json=response)
    assert result.exit_code == 0, result.output
    parsed = _extract_json_from_output(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0] == {"start": 0.32, "end": 1.05, "text": "Hello."}
    assert parsed[1] == {"start": 1.40, "end": 2.80, "text": "World now."}
    # No warning when we have a usable shape.
    assert "warning" not in result.output.lower()


# ---------------------------------------------------------------------------
# T-11: verbose_json always sent internally
# ---------------------------------------------------------------------------


def test_verbose_json_is_always_sent_to_server(tmp_path):
    """The request to mlx-audio must always use response_format=verbose_json."""
    wav = tmp_path / "test.wav"
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    response = {"text": "Hi.", "segments": [{"start": 0.0, "end": 1.0, "text": "Hi."}]}
    urlopen_mock = _make_urlopen_mock(json.dumps(response))

    captured_bodies = []

    def capturing_urlopen(req, **kwargs):
        # Decode the multipart body and record it for inspection.
        if hasattr(req, "data") and req.data:
            captured_bodies.append(req.data.decode("latin-1", errors="replace"))
        return urlopen_mock

    runner = CliRunner()
    with (
        patch.object(turbo_cli, "_get_running_model", return_value=None),
        patch.object(turbo_cli, "resolve_model", return_value=FAKE_MODEL),
        patch.object(turbo_cli, "_get_provider_for", return_value=_FakeProvider()),
        patch.object(turbo_cli, "_server_is_running", return_value=True),
        patch.object(turbo_cli.urllib.request, "urlopen", side_effect=capturing_urlopen),
    ):
        result = runner.invoke(turbo_cli.cli, ["transcribe", str(wav), "--format", "text"])

    assert result.exit_code == 0, result.output
    assert captured_bodies, "urlopen was not called"
    body_text = captured_bodies[0]
    assert "verbose_json" in body_text
