import inspect
import io

import pytest
import json
import socket
import struct
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


def test_parse_bare_gemma_tool_call_rejects_missing_closing_brace():
    assert parse_bare_gemma_tool_call("call:ls{path:.") is None


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


@pytest.mark.parametrize("content", [
    "call:f{x:[1,2}}", "call:f{x:{a:1]}", "call:f{x:}", "call:f{garbage}",
    "call:f{x:1,,y:2}", "call:f{x:1,}", 'call:f{x:"unterminated}',
    "call:f{x:1,x:2}", "call:f{x:[1,,2]}",
])
def test_malformed_arguments_never_become_executable(content):
    assert parse_bare_gemma_tool_call(content) is None


@pytest.mark.parametrize("reason", ["length", "content_filter", None])
def test_nonstream_requires_clean_finish(reason):
    payload = _chat_response()
    payload["choices"][0]["finish_reason"] = reason
    assert rewrite_chat_response(payload) == payload


def _event(delta=None, reason=None, **extra):
    return {"id": "test", "choices": [{"index": 0, "delta": delta or {}, "finish_reason": reason}], **extra}


def _sse(payload):
    return ("data: " + (payload if isinstance(payload, str) else json.dumps(payload)) + "\n\n").encode()


def _rewritten(events):
    raw = b"".join(proxy_mod.rewrite_sse_stream(io.BytesIO(b"".join(_sse(e) for e in events))))
    return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b"data: ") and line != b"data: [DONE]"]


def _content(events):
    return "".join(c.get("delta", {}).get("content", "") or "" for e in events for c in e.get("choices", []))


def _calls(events):
    return [t for e in events for c in e.get("choices", []) for t in c.get("delta", {}).get("tool_calls", [])]


def test_split_prefix_converts_only_at_clean_finish_and_keeps_reasoning_usage():
    events = [_event({"role": "assistant", "reasoning_content": "thinking"}),
              *[_event({"content": part}) for part in ["  c", "al", "l:", "ls{pa", "th:.", "}"]],
              _event(reason="stop"), {"choices": [], "usage": {"total_tokens": 9}}, "[DONE]"]
    output = _rewritten(events)
    assert output[0]["choices"][0]["delta"]["reasoning_content"] == "thinking"
    assert _content(output) == ""
    assert _calls(output)[0]["function"]["arguments"] == '{"path": "."}'
    assert any(c["finish_reason"] == "tool_calls" for e in output for c in e.get("choices", []))
    assert output[-1]["usage"]["total_tokens"] == 9


@pytest.mark.parametrize("parts,reason,done", [
    (["call:ls{path:.", "} trailing prose"], "stop", True),
    (["call:ls{path:.}"], "length", True),
    (["call:ls{path:."], "stop", True),
    (["call:ls{path:.}"], None, True),
    (["call:ls{path:."], None, False),
    (["call:f{x:[1,2}}"], "stop", True),
])
def test_stream_unsafe_calls_remain_text(parts, reason, done):
    events = [_event({"content": p}) for p in parts]
    if reason:
        events.append(_event(reason=reason))
    if done:
        events.append("[DONE]")
    output = _rewritten(events)
    assert not _calls(output)
    assert _content(output) == "".join(parts)
    if reason:
        assert output[-1]["choices"][0]["finish_reason"] == reason


def test_native_tool_deltas_and_stream_errors_are_preserved():
    native = {"index": 0, "id": "native", "function": {"name": "ls", "arguments": "{"}}
    events = [_event({"content": "call:ls{path:.}"}), _event({"tool_calls": [native]}),
              _event({"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]}),
              _event(reason="tool_calls"), {"error": {"message": "broken"}}, "[DONE]"]
    output = _rewritten(events)
    assert _calls(output) == [native, {"index": 0, "function": {"arguments": "}"}}]
    assert _content(output) == "call:ls{path:.}"
    assert output[-1] == {"error": {"message": "broken"}}


def test_proxy_forwards_first_prose_before_upstream_finishes():
    release = threading.Event()
    finished = threading.Event()
    received = []

    class DelayedHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(_sse(_event({"content": "Hello"})))
            self.wfile.flush()
            release.wait(3)
            self.wfile.write(_sse(_event({"content": " world"})))
            self.wfile.write(_sse(_event(reason="stop")) + _sse("[DONE]"))
            finished.set()

        def log_message(self, *args):
            pass

    with _running_server(DelayedHandler) as upstream_port, _running_proxy(upstream_port) as proxy_port:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                                         data=b'{"stream": true}', headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as response:
                first = response.readline()
                assert b"Hello" in first
                assert not finished.is_set()
                release.set()
                assert b"world" in response.read()
            assert received[0]["stream"] is True
        finally:
            release.set()


def test_truncated_chunked_stream_flushes_candidate_without_handler_error():
    candidate = "call:ls{path:.}"

    class TruncatedHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            frame = _sse(_event({"content": candidate}))
            self.wfile.write(f"{len(frame):x}\r\n".encode() + frame + b"\r\n")
            # Declare a chunk, then close before its body or terminating chunk.
            self.wfile.write(b"100\r\ndata: broken")
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *args):
            pass

    with patch.object(ThreadingHTTPServer, "handle_error") as handle_error:
        with _running_server(TruncatedHandler) as upstream_port, _running_proxy(upstream_port) as proxy_port:
            req = urllib.request.Request(f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                                         data=b'{"stream": true}', headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as response:
                output = response.read()
        assert candidate.encode() in output
        assert b'"tool_calls"' not in output
        handle_error.assert_not_called()


def test_main_cleans_up_upstream_if_binding_fails():
    proc = MagicMock()
    with patch.object(proxy_mod.subprocess, "Popen", return_value=proc), \
         patch.object(proxy_mod, "ThreadingHTTPServer", side_effect=OSError("busy")), \
         patch.object(proxy_mod.signal, "signal"):
        with pytest.raises(OSError, match="busy"):
            proxy_mod.main(["--listen-port", "1", "--upstream-port", "2", "--", "true"])
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=5)


@pytest.mark.parametrize("reset", [False, True])
def test_disconnect_closes_silent_upstream_promptly(reset):
    disconnected = threading.Event()

    class SilentHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(_sse(_event({"content": "Hello"})))
            self.wfile.flush()
            self.connection.settimeout(3)
            try:
                if self.connection.recv(1) == b"":
                    disconnected.set()
            except TimeoutError:
                pass

        def log_message(self, *args):
            pass

    with _running_server(SilentHandler) as upstream_port, _running_proxy(upstream_port) as proxy_port:
        client = socket.create_connection(("127.0.0.1", proxy_port), timeout=2)
        client.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nContent-Length: 16\r\n"
                       b"Content-Type: application/json\r\n\r\n{\"stream\": true}")
        data = b""
        while b"Hello" not in data:
            data += client.recv(4096)
        if reset:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        else:
            client.shutdown(socket.SHUT_RDWR)
        client.close()
        assert disconnected.wait(1), "proxy kept the silent generation alive after the client disconnected"


def test_nested_arguments_and_quoted_escapes_are_preserved():
    call = parse_bare_gemma_tool_call(r'call:f{options:{enabled:true},items:[1,"two"],text:"a\\nb"}')
    assert json.loads(call["function"]["arguments"]) == {
        "options": {"enabled": True}, "items": [1, "two"], "text": "a\\nb",
    }


def test_reasoning_is_forwarded_while_call_content_is_still_buffered():
    sent = []

    def upstream():
        yield _sse(_event({"content": "call:f{"}))
        yield b"\n"
        yield _sse(_event({"reasoning_content": "working"}))
        yield b"\n"
        assert sent, "reasoning was buffered behind a possible tool call"
        yield _sse(_event({"content": "x:1}"}, reason="stop"))
        yield b"\n"

    for event in proxy_mod.rewrite_sse_stream(upstream()):
        if b"working" in event:
            sent.append(event)
    assert sent


def test_nonstream_native_tool_is_never_overwritten():
    payload = _chat_response()
    native = {"id": "native", "function": {"name": "real", "arguments": "{}"}}
    payload["choices"][0]["message"]["tool_calls"] = [native]
    assert rewrite_chat_response(payload) == payload


def test_json_stream_fallback_retains_reasoning_usage_and_length_finish():
    payload = _chat_response("ordinary prose")
    payload["choices"][0]["message"]["reasoning_content"] = "thinking"
    payload["choices"][0]["finish_reason"] = "length"
    payload["usage"] = {"total_tokens": 12}
    events = [json.loads(line[6:]) for line in _stream_from_chat_response(payload).splitlines()
              if line.startswith(b"data: ") and line != b"data: [DONE]"]
    assert events[0]["choices"][0]["delta"]["reasoning_content"] == "thinking"
    assert events[1]["choices"][0]["finish_reason"] == "length"
    assert events[-1]["usage"] == {"total_tokens": 12}


def test_done_ends_stream_without_waiting_for_upstream_eof():
    def lines():
        yield b"data: [DONE]\n"
        yield b"\n"
        raise AssertionError("read beyond DONE")

    assert b"".join(proxy_mod.rewrite_sse_stream(lines())) == b"data: [DONE]\n\n"


def test_error_disables_later_tool_conversion():
    events = [{"error": "upstream failure"}, _event({"content": "call:ls{path:.}"}),
              _event(reason="stop"), "[DONE]"]
    output = _rewritten(events)
    assert not _calls(output)
    assert _content(output) == "call:ls{path:.}"


def test_malformed_stream_choice_is_passed_through_and_disables_conversion():
    output = _rewritten([{"choices": ["bad"]}, _event({"content": "call:ls{path:.}"}),
                         _event(reason="stop"), "[DONE]"])
    assert output[0] == {"choices": ["bad"]}
    assert not _calls(output[1:])


def test_json_stream_fallback_preserves_upstream_error():
    error = {"error": {"message": "generation failed", "type": "server_error"}}
    output = _stream_from_chat_response(error)
    assert json.loads(output.splitlines()[0][6:]) == error


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
@pytest.mark.parametrize("buffered", [False, True])
def test_unterminated_final_sse_event_never_synthesizes_tool_call(ending, buffered):
    text = "call:f{x:1}"
    prefix = _sse(_event({"content": text})) if buffered else b""
    final = _sse(_event({} if buffered else {"content": text}, reason="stop")).rstrip(b"\n") + ending
    raw = b"".join(proxy_mod.rewrite_sse_stream(io.BytesIO(prefix + final)))
    events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b"data: ")]
    assert not _calls(events)
    assert _content(events) == text
    # Never manufacture an event delimiter or a clean completion at transport EOF.
    assert raw.endswith(final)


def test_incomplete_json_at_eof_flushes_buffered_content_before_raw_fragment():
    prefix = _sse(_event({"content": "call:f{x:1}"}))
    fragment = b'data: {"choices": [{"finish_reason": "st'
    raw = b"".join(proxy_mod.rewrite_sse_stream(io.BytesIO(prefix + fragment)))
    assert raw.endswith(fragment)
    assert b'"content":"call:f{x:1}"' in raw
    assert b'"tool_calls"' not in raw
