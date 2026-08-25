import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from turbollm import cli as turbo_cli


class _Provider:
    name = "fake-backend"
    install_hint = "install fake-backend"

    def is_downloaded(self, model):
        return True

    def is_available(self):
        return True

    def build_serve_cmd(self, model, port):
        return ["fake-server", "--port", str(port)]


class _FailedProcess:
    returncode = 2

    def poll(self):
        return self.returncode

    def terminate(self):
        pass

    def wait(self):
        return self.returncode


def test_run_with_server_prints_backend_log_when_startup_fails(capsys, tmp_path):
    model = {"name": "Broken Model", "hf_repo": "fake/broken", "backend": "fake"}
    backend_log = "bind: address already in use\ntraceback line\n"

    def fake_popen(_cmd, **kwargs):
        stream = kwargs.get("stdout")
        if hasattr(stream, "write"):
            stream.write(backend_log)
            stream.flush()
        return _FailedProcess()

    with patch.object(turbo_cli, "_server_is_running", return_value=False), patch.object(
        turbo_cli, "_get_provider_for", return_value=_Provider()
    ), patch.object(turbo_cli.time, "sleep", return_value=None), patch.object(
        turbo_cli.subprocess, "Popen", side_effect=fake_popen
    ), patch.object(turbo_cli, "TURBOLLM_STATE_DIR", tmp_path / "state"):
        with pytest.raises(SystemExit):
            turbo_cli._run_with_server(model, 8899, lambda _m, _p: None)

    captured = capsys.readouterr()
    # _run_with_server routes all status/error lines to stderr so they cannot
    # leak into stdout-piping callers (e.g. `turbo transcribe | pi`). Backend
    # log dumps go through the same stderr console.
    err = captured.err
    assert captured.out == ""
    assert "Backend log" in err
    assert "bind: address already in use" in err


class _RunningProcess:
    """Fake Popen that never exits on its own — used to exercise the
    startup-success path and the teardown (terminate/wait) sequence."""

    def __init__(self):
        self._terminated = False
        self.killed = False
        self.wait_calls = []

    def poll(self):
        return None if not self._terminated else 0

    def terminate(self):
        self._terminated = True

    def kill(self):
        self.killed = True
        self._terminated = True

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return 0


def test_run_with_server_writes_and_clears_port_stamp(tmp_path):
    """Fix for BUG (item 6): harness/transcribe auto-started servers must get
    a port stamp too, not just `turbo serve` — otherwise the --context-window
    guard on attaching harnesses silently skips validation. The stamp must
    also be cleared once the launch_fn returns."""
    model = {
        "name": "Good Model",
        "hf_repo": "fake/good",
        "backend": "fake",
        "context_default": 65536,
    }
    proc = _RunningProcess()
    state_dir = tmp_path / "state"

    seen_stamp = {}

    def fake_launch(_m, _p):
        # Capture the stamp contents while the server is "up".
        stamp_path = turbo_cli._port_stamp_path(8899)
        seen_stamp.update(turbo_cli.json.loads(stamp_path.read_text()))
        return 0

    with patch.object(
        turbo_cli, "_server_is_running", side_effect=[False, True, True]
    ), patch.object(turbo_cli, "_get_provider_for", return_value=_Provider()), patch.object(
        turbo_cli.time, "sleep", return_value=None
    ), patch.object(turbo_cli.subprocess, "Popen", return_value=proc), patch.object(
        turbo_cli, "TURBOLLM_STATE_DIR", state_dir
    ):
        rc = turbo_cli._run_with_server(model, 8899, fake_launch)

    assert rc == 0
    assert seen_stamp.get("max_tokens") == 65536
    # Stamp is cleared once we own it and the run completes.
    assert not (state_dir / "port-8899.json").exists()


def test_run_with_server_teardown_escalates_to_kill_on_timeout(tmp_path):
    """Fix for BUG-18: a server that ignores SIGTERM (terminate()) must be
    force-killed after a bounded wait instead of hanging teardown forever."""
    model = {"name": "Good Model", "hf_repo": "fake/good", "backend": "fake"}

    class _HangingProcess(_RunningProcess):
        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if timeout is not None and not self.killed:
                raise subprocess.TimeoutExpired(cmd="fake-server", timeout=timeout)
            return 0

        def poll(self):
            # Stays "alive" (None) until kill() has been called.
            return None if not self.killed else 0

    proc = _HangingProcess()

    with patch.object(
        turbo_cli, "_server_is_running", side_effect=[False, True, True]
    ), patch.object(turbo_cli, "_get_provider_for", return_value=_Provider()), patch.object(
        turbo_cli.time, "sleep", return_value=None
    ), patch.object(turbo_cli.subprocess, "Popen", return_value=proc), patch.object(
        turbo_cli, "TURBOLLM_STATE_DIR", tmp_path / "state"
    ):
        turbo_cli._run_with_server(model, 8899, lambda _m, _p: 0)

    assert proc.killed, "teardown must escalate to kill() after wait(timeout=...) expires"


# ---------------------------------------------------------------------------
# `_clear_port_stamp_if_owned` — losing-race protection (BUG-9)
# ---------------------------------------------------------------------------


def test_clear_port_stamp_if_owned_deletes_own_stamp(tmp_path):
    with patch.object(turbo_cli, "TURBOLLM_STATE_DIR", tmp_path):
        turbo_cli._write_port_stamp(9999, "some-alias", max_tokens=1000)
        turbo_cli._clear_port_stamp_if_owned(9999)
        assert not turbo_cli._port_stamp_path(9999).exists()


def test_clear_port_stamp_if_owned_leaves_other_pid_stamp(tmp_path):
    """A losing `turbo serve` (bind failed because the port was already busy)
    must not delete the winning process's stamp in its own `finally` cleanup."""
    with patch.object(turbo_cli, "TURBOLLM_STATE_DIR", tmp_path):
        stamp_path = turbo_cli._port_stamp_path(9999)
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text(turbo_cli.json.dumps({"alias": "winner", "pid": 424242}))
        turbo_cli._clear_port_stamp_if_owned(9999)
        assert stamp_path.exists()


# ---------------------------------------------------------------------------
# `turbo serve` refuses a busy port (BUG-9) instead of clobbering the stamp
# ---------------------------------------------------------------------------


def test_serve_refuses_busy_port(monkeypatch, tmp_path):
    from click.testing import CliRunner

    model = {"name": "Good Model", "hf_repo": "fake/good", "backend": "vllm-mlx"}
    monkeypatch.setattr(turbo_cli, "resolve_model", lambda _m: model)
    monkeypatch.setattr(turbo_cli, "_get_provider_for", lambda _m: _Provider())
    monkeypatch.setattr(turbo_cli, "_server_is_running", lambda _p: True)
    monkeypatch.setattr(turbo_cli, "TURBOLLM_STATE_DIR", tmp_path)

    called = {"run": False}
    monkeypatch.setattr(turbo_cli.subprocess, "run", lambda *a, **k: called.update(run=True))

    runner = CliRunner()
    result = runner.invoke(turbo_cli.cli, ["serve", "fake/good", "--port", "9999"])
    assert result.exit_code != 0
    assert "already serving" in result.output
    assert called["run"] is False, "must refuse before ever invoking the server subprocess"


def test_serve_pull_hint_uses_alias_not_none_when_from_picker(monkeypatch, tmp_path):
    """Regression: when the model comes from the picker (no CLI `model` arg),
    the not-downloaded hint must never render as the literal 'turbo pull None'."""
    from click.testing import CliRunner

    model = {"name": "Good Model", "hf_repo": "fake/good", "backend": "vllm-mlx"}

    class _NotDownloadedProvider(_Provider):
        def is_downloaded(self, _m):
            return False

    monkeypatch.setattr(turbo_cli, "pick_model", lambda **kw: ("good-alias", model))
    monkeypatch.setattr(turbo_cli, "_get_provider_for", lambda _m: _NotDownloadedProvider())
    monkeypatch.setattr(turbo_cli, "TURBOLLM_STATE_DIR", tmp_path)

    runner = CliRunner()
    result = runner.invoke(turbo_cli.cli, ["serve"])
    assert result.exit_code != 0
    assert "turbo pull None" not in result.output
    assert "turbo pull good-alias" in result.output


# ---------------------------------------------------------------------------
# `turbo rm` also deletes the legacy `~/.turbollm/models/...` layout (BUG-19)
# ---------------------------------------------------------------------------


def test_rm_deletes_primary_legacy_and_draft_artifacts(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from turbollm import registry as turbo_registry

    model = {
        "name": "Good Model",
        "hf_repo": "some-org/some-model",
        "draft_hf_repo": "some-org/some-draft",
        "backend": "vllm-mlx",
    }
    monkeypatch.setattr(turbo_cli, "resolve_model", lambda _m: model)
    monkeypatch.setattr(turbo_cli, "_get_provider_for", lambda _m: _Provider())
    monkeypatch.setattr(
        turbo_cli,
        "load_registry",
        lambda: {"models": {"good-model": model}},
    )

    hf_cache_root = tmp_path / "hf_cache"
    legacy_root = tmp_path / "legacy"
    monkeypatch.setattr(turbo_registry, "HF_CACHE", hf_cache_root)
    monkeypatch.setattr(turbo_registry, "LEGACY_DIR", legacy_root)

    hf_dir = turbo_registry._hf_cache_path(model["hf_repo"])
    legacy_dir = turbo_registry._legacy_path(model["hf_repo"])
    draft_dir = turbo_registry._hf_cache_path(model["draft_hf_repo"])
    hf_dir.mkdir(parents=True)
    (hf_dir / "weights.bin").write_bytes(b"x" * 10)
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "weights.bin").write_bytes(b"y" * 10)
    draft_dir.mkdir(parents=True)
    (draft_dir / "draft.bin").write_bytes(b"z" * 10)

    runner = CliRunner()
    result = runner.invoke(turbo_cli.cli, ["rm", "some-org/some-model", "--yes"])
    assert result.exit_code == 0, result.output
    assert not hf_dir.exists()
    assert not legacy_dir.exists()
    assert not draft_dir.exists()
    assert "draft" in result.output


# ---------------------------------------------------------------------------
# `_dispatch_harness` refuses an incompatible running server instead of
# silently attaching to it after falling through to the picker (BUG-5)
# ---------------------------------------------------------------------------


def test_dispatch_harness_refuses_incompatible_running_server(monkeypatch):
    running_model = {"name": "ASR Model", "hf_repo": "fake/asr", "backend": "mlx-audio"}

    monkeypatch.setattr(turbo_cli, "load_registry", lambda: {"harnesses": {"pi": {}}})
    monkeypatch.setattr(turbo_cli, "get_defaults", lambda: {})
    monkeypatch.setattr(turbo_cli, "_server_is_running", lambda _p: True)
    monkeypatch.setattr(turbo_cli, "_get_running_model", lambda _p: running_model)

    def _fail_pick_model(**kw):
        raise AssertionError(
            "must refuse outright, not fall through to the picker and then "
            "still attach to the incompatible server on the same port"
        )

    monkeypatch.setattr(turbo_cli, "pick_model", _fail_pick_model)
    run_calls = []
    monkeypatch.setattr(turbo_cli, "_run_harness", lambda *a, **k: run_calls.append((a, k)))

    with pytest.raises(SystemExit):
        turbo_cli._dispatch_harness("pi", None, 8899, None, None)

    assert run_calls == []


def test_pi_rejects_reasoning_level_unsupported_by_model(monkeypatch):
    from click.testing import CliRunner

    model = {
        "name": "Qwen3.8 27B Q8 + MTP",
        "hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "backend": "gguf",
        "can_reason": True,
        "pi": {
            "thinking": "medium",
            "thinking_levels": ["low", "medium", "xhigh"],
        },
    }
    registry = {
        "defaults": {"port": 8899},
        "harnesses": {"pi": {"binary": "pi"}},
        "models": {"qwen38-27b-q8-mtp": model},
    }
    monkeypatch.setattr(turbo_cli, "load_registry", lambda: registry)
    monkeypatch.setattr(turbo_cli, "resolve_model", lambda _raw: model)

    result = CliRunner().invoke(
        turbo_cli.cli,
        ["pi", "qwen38-27b-q8-mtp", "--thinking", "high"],
    )

    assert result.exit_code == 2
    assert (
        "Qwen3.8 27B Q8 + MTP supports Pi thinking levels: low, medium, xhigh"
        in result.output
    )


# ---------------------------------------------------------------------------
# `turbo chat`: /v1/models probe timeout/error handling, KeyboardInterrupt
# mid-stream, and symmetric history after a failed turn (item 12)
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    """Minimal stand-in for the `with urlopen(...) as response:` SSE stream
    `chat()` iterates over — one chunk containing the full double-newline
    delimited SSE payload is enough to exercise the parser."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._chunks)


def _sse_bytes(content: str) -> bytes:
    delta = json.dumps({"choices": [{"delta": {"content": content}}]})
    return f"data: {delta}\n\ndata: [DONE]\n\n".encode()


def _models_probe_response():
    resp = MagicMock()
    resp.read.return_value = json.dumps({"data": [{"id": "fake-model"}]}).encode()
    return resp


def test_chat_probe_failure_exits_cleanly_without_traceback():
    from click.testing import CliRunner

    def fake_urlopen(req, timeout=None, **kwargs):
        raise TimeoutError("timed out")

    runner = CliRunner()
    with (
        patch.object(turbo_cli, "_server_is_running", return_value=True),
        patch.object(turbo_cli.urllib.request, "urlopen", side_effect=fake_urlopen),
    ):
        result = runner.invoke(turbo_cli.cli, ["chat"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "Could not reach server" in result.output


def test_chat_pops_unanswered_user_message_after_stream_failure():
    """Regression: after a failed turn, the next turn's request body must not
    still carry the previous (un-answered) user message — history must stay
    role-alternating for the server."""
    from click.testing import CliRunner

    chat_bodies = []

    def fake_urlopen(req, timeout=None, **kwargs):
        if isinstance(req, str):
            return _models_probe_response()
        body = json.loads(req.data.decode())
        chat_bodies.append(body)
        if len(chat_bodies) == 1:
            raise RuntimeError("connection reset by peer")
        return _FakeStreamResponse([_sse_bytes("ok")])

    runner = CliRunner()
    with (
        patch.object(turbo_cli, "_server_is_running", return_value=True),
        patch.object(turbo_cli.urllib.request, "urlopen", side_effect=fake_urlopen),
    ):
        result = runner.invoke(turbo_cli.cli, ["chat"], input="hello\nworld\n")

    assert result.exit_code == 0, result.output
    assert "Error:" in result.output
    assert len(chat_bodies) == 2
    # The second request must only see "world" — "hello" (whose turn failed)
    # must have been popped, not left dangling ahead of it.
    assert chat_bodies[1]["messages"] == [{"role": "user", "content": "world"}]


def test_chat_handles_keyboard_interrupt_mid_stream_without_traceback():
    from click.testing import CliRunner

    def fake_urlopen(req, timeout=None, **kwargs):
        if isinstance(req, str):
            return _models_probe_response()
        raise KeyboardInterrupt()

    runner = CliRunner()
    with (
        patch.object(turbo_cli, "_server_is_running", return_value=True),
        patch.object(turbo_cli.urllib.request, "urlopen", side_effect=fake_urlopen),
    ):
        result = runner.invoke(turbo_cli.cli, ["chat"], input="hello\n")

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "Interrupted" in result.output
