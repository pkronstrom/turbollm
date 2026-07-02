import inspect
import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from turbollm import mlx_vlm_tool_proxy as proxy_mod
from turbollm.mlx_vlm_tool_proxy import (
    ProxyHandler,
    _stream_from_chat_response,
    parse_bare_gemma_tool_call,
    rewrite_chat_response,
)


def _chat_response(content="call:ls{path:.}", **overrides):
    response = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "diffusiongemma",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    response.update(overrides)
    return response


def test_parse_bare_gemma_tool_call_converts_unquoted_value_to_json_arguments():
    call = parse_bare_gemma_tool_call("call:ls{path:.}")

    assert call["type"] == "function"
    assert call["function"]["name"] == "ls"
    assert call["function"]["arguments"] == '{"path": "."}'


def test_parse_bare_gemma_tool_call_tolerates_missing_closing_brace():
    call = parse_bare_gemma_tool_call("call:ls{path:.")

    assert call["function"]["name"] == "ls"
    assert call["function"]["arguments"] == '{"path": "."}'


def test_parse_bare_gemma_tool_call_ignores_prose_mentioning_call_syntax():
    """A model *explaining* the call: syntax must not be hijacked — only a
    substring match would trigger on this and discard the real answer."""
    text = "The tool syntax looks like call:ls{path:.} if you want to try it."
    assert parse_bare_gemma_tool_call(text) is None


def test_parse_bare_gemma_tool_call_ignores_call_with_trailing_prose():
    """A real call followed by trailing text must not be rewritten — the old
    substring-match implementation folded the trailing text into the last
    parsed argument instead of passing the message through untouched."""
    text = "call:ls{path:.} Let me check that for you."
    assert parse_bare_gemma_tool_call(text) is None


def test_parse_bare_gemma_tool_call_rewrites_bare_exact_call():
    """A message that IS exactly the call (only surrounding whitespace) still
    rewrites normally."""
    call = parse_bare_gemma_tool_call("  call:ls{path:.}  ")
    assert call["function"]["name"] == "ls"
    assert call["function"]["arguments"] == '{"path": "."}'


def test_rewrite_chat_response_passes_through_prose_mentioning_call_syntax():
    """End-to-end: rewrite_chat_response must leave a prose explanation of
    the call: syntax untouched (no tool_calls injected, content preserved)."""
    text = "You can invoke it with call:ls{path:.} — try that."
    rewritten = rewrite_chat_response(_chat_response(content=text))
    message = rewritten["choices"][0]["message"]
    assert message["content"] == text
    assert "tool_calls" not in message


def test_rewrite_chat_response_moves_bare_call_text_to_tool_calls():
    rewritten = rewrite_chat_response(_chat_response())

    message = rewritten["choices"][0]["message"]
    assert rewritten["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] is None
    assert message["tool_calls"][0]["function"]["name"] == "ls"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"path": "."}'


def test_stream_from_chat_response_uses_indexed_tool_call_delta():
    response = rewrite_chat_response(_chat_response())

    events = [
        line.removeprefix("data: ")
        for line in _stream_from_chat_response(response).decode().splitlines()
        if line.startswith("data: ")
    ]

    first = json.loads(events[0])
    tool_call = first["choices"][0]["delta"]["tool_calls"][0]
    assert first["choices"][0]["finish_reason"] is None
    assert tool_call["index"] == 0
    assert tool_call["function"]["name"] == "ls"

    second = json.loads(events[1])
    assert second["choices"][0]["delta"] == {}
    assert second["choices"][0]["finish_reason"] == "tool_calls"
    assert events[2] == "[DONE]"


# ---------------------------------------------------------------------------
# ProxyHandler error-handling — real upstream + proxy HTTP servers.
# ---------------------------------------------------------------------------


def _make_upstream_handler(status: int, body: bytes, content_type: str = "application/json"):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # noqa: A002
            pass

    return Handler


@contextmanager
def _running_server(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def _running_proxy(upstream_port: int):
    ProxyHandler.upstream_host = "127.0.0.1"
    ProxyHandler.upstream_port = upstream_port
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


def _closed_port() -> int:
    """A port nothing is listening on (bind then immediately release)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_proxy_passes_through_non_200_status_untouched():
    """A non-200 upstream response must reach the client with its real
    status and body — not get rewritten into an empty/garbled SSE payload."""
    error_body = json.dumps({"error": "boom"}).encode()
    with _running_server(_make_upstream_handler(500, error_body)) as upstream_port:
        with _running_proxy(upstream_port) as proxy_port:
            req = urllib.request.Request(
                f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                data=json.dumps({"model": "m", "stream": False}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
                raise AssertionError("expected HTTPError")
            except urllib.error.HTTPError as e:
                assert e.code == 500
                assert json.loads(e.read()) == {"error": "boom"}


def test_proxy_returns_502_when_upstream_down():
    """An unreachable upstream must produce a 502 with a JSON error body
    instead of crashing the handler thread (which the client would just see
    as a reset connection)."""
    dead_port = _closed_port()
    with _running_proxy(dead_port) as proxy_port:
        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 502
            payload = json.loads(e.read())
            assert "error" in payload


def test_proxy_falls_back_to_raw_bytes_when_rewrite_raises():
    """If rewrite_chat_response throws for any reason, the proxy must fall
    back to passing the upstream bytes through untouched rather than
    crashing or dropping the response."""
    raw = json.dumps({
        "id": "x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": "hello"}}],
    }).encode()
    with _running_server(_make_upstream_handler(200, raw)) as upstream_port:
        with _running_proxy(upstream_port) as proxy_port:
            with patch.object(proxy_mod, "rewrite_chat_response", side_effect=RuntimeError("boom")):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                    data=json.dumps({"model": "m", "stream": False}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    assert resp.status == 200
                    assert json.loads(resp.read()) == json.loads(raw)


def test_proxy_upstream_request_uses_generous_timeout():
    """Guards against a hang: the upstream urlopen call must pass an
    explicit, generous timeout rather than blocking forever."""
    src = inspect.getsource(ProxyHandler._proxy)
    assert "timeout=_UPSTREAM_TIMEOUT_S" in src
    assert proxy_mod._UPSTREAM_TIMEOUT_S >= 300


# ---------------------------------------------------------------------------
# main() teardown
# ---------------------------------------------------------------------------


def test_main_kills_upstream_process_when_graceful_wait_times_out():
    """main()'s finally block must escalate to kill() when proc.wait(timeout=5)
    raises TimeoutExpired, rather than letting the exception propagate and
    leak the upstream process."""
    fake_proc = MagicMock()
    fake_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="x", timeout=5), None]

    fake_server = MagicMock()
    fake_server.serve_forever.return_value = None  # returns immediately → finally runs

    with (
        patch.object(proxy_mod.subprocess, "Popen", return_value=fake_proc),
        patch.object(proxy_mod, "ThreadingHTTPServer", return_value=fake_server),
        patch.object(proxy_mod.signal, "signal"),
    ):
        rc = proxy_mod.main([
            "--listen-port", "0", "--upstream-port", "1", "--", "true",
        ])

    assert rc == 0
    fake_proc.terminate.assert_called_once()
    fake_proc.kill.assert_called_once()
    assert fake_proc.wait.call_count == 2
