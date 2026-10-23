"""OpenAI-compatible proxy shims for mlx-vlm edge cases.

DiffusionGemma can emit Gemma-style tool calls as plain text, e.g.
``call:ls{path:.}``, while mlx-vlm's OpenAI server only parses Gemma calls when
they are wrapped in ``<|tool_call>...<tool_call|>``. This proxy sits in front of
mlx_vlm.server for that model family and incrementally forwards chat streams,
converting only complete, cleanly finished calls into OpenAI ``tool_calls``.
"""

from __future__ import annotations

import argparse
import ast
import http.client
import math
import re
import select
import socket
import threading
import json
import signal
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _split_top_level(text: str) -> list[str]:
    """Split arguments without accepting empty fields or mismatched nesting."""
    if not text.strip():
        return []
    parts = []
    stack = []
    quote = None
    escape = False
    start = 0
    for i, ch in enumerate(text):
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack or stack.pop() != {"}": "{", "]": "["}[ch]:
                raise ValueError("mismatched argument delimiters")
        elif ch == "," and not stack:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    if stack or quote or any(not part for part in parts):
        raise ValueError("incomplete argument")
    return parts


def _parse_arguments(text: str) -> dict[str, Any]:
    args = {}
    for part in _split_top_level(text):
        match = re.fullmatch(r'''\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[\w.-]+)\s*:(.*)''', part, re.DOTALL)
        if not match:
            raise ValueError("invalid argument field")
        key, value = match.groups()
        if key.startswith(('"', "'")):
            key = _parse_value(key)
        if not key or key in args:
            raise ValueError("empty or duplicate argument name")
        args[key] = _parse_value(value)
    return args


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if not value:
        raise ValueError("missing argument value")
    if value[0] == '"':
        return json.loads(value)
    if value[0] == "'":
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise ValueError("invalid quoted value")
        return parsed
    if value[0] in "{[":
        close = _find_brace_close(value, 0)
        if close != len(value):
            raise ValueError("incomplete container")
        if value[0] == "{":
            return _parse_arguments(value[1:-1])
        return [_parse_value(part) for part in _split_top_level(value[1:-1])]
    if any(ch in value for ch in "{}[]\"'\\"):
        raise ValueError("invalid bare value")
    if value in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[value]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        number = float(value)
    except ValueError:
        return value
    if not math.isfinite(number):
        raise ValueError("nonfinite argument")
    return number


def _find_brace_close(text: str, open_brace: int) -> int | None:
    """Find a structurally matching close; reject mismatched braces/quotes."""
    stack = []
    quote = None
    escape = False
    for i in range(open_brace, len(text)):
        ch = text[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack or stack.pop() != {"}": "{", "]": "["}[ch]:
                raise ValueError("mismatched argument delimiters")
            if not stack:
                return i + 1
    return None


def parse_bare_gemma_tool_call(text: str) -> dict[str, Any] | None:
    """Convert only an entire, complete, structurally valid bare Gemma call."""
    stripped = text.strip()
    match = re.match(r"call:([\w-]+)\s*\{", stripped)
    if not match:
        return None
    open_brace = match.end() - 1
    try:
        close = _find_brace_close(stripped, open_brace)
        if close != len(stripped):
            return None
        args = _parse_arguments(stripped[open_brace + 1 : close - 1])
    except (ValueError, SyntaxError, RecursionError):
        return None
    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {"name": match[1], "arguments": json.dumps(args, separators=(",", ": "))},
    }


def rewrite_chat_response(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    if not choices:
        return payload
    message = choices[0].get("message") or {}
    content = message.get("content")
    if choices[0].get("finish_reason") != "stop" or message.get("tool_calls"):
        return payload
    if not isinstance(content, str):
        return payload

    tool_call = parse_bare_gemma_tool_call(content)
    if tool_call is None:
        return payload

    rewritten_message = dict(message)
    rewritten_message["content"] = None
    rewritten_message["tool_calls"] = [tool_call]

    first_choice = dict(choices[0])
    first_choice["message"] = rewritten_message
    first_choice["finish_reason"] = "tool_calls"

    rewritten_choices = list(choices)
    rewritten_choices[0] = first_choice
    rewritten = dict(payload)
    rewritten["choices"] = rewritten_choices
    return rewritten


def _stream_from_chat_response(payload: dict[str, Any]) -> bytes:
    if "error" in payload:
        return _encode_sse(payload) + b"data: [DONE]\n\n"
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    delta: dict[str, Any] = {"role": "assistant", **message}
    finish_reason = choice.get("finish_reason")
    if message.get("tool_calls"):
        delta["tool_calls"] = [
            {"index": index, **tool_call}
            for index, tool_call in enumerate(message["tool_calls"])
        ]
    else:
        delta["content"] = message.get("content") or ""

    base_chunk = {
        "id": payload.get("id", f"chatcmpl-{uuid.uuid4()}"),
        "object": "chat.completion.chunk",
        "created": payload.get("created", int(time.time())),
        "model": payload.get("model", "model"),
    }
    index = choice.get("index", 0)

    def chunk(chunk_delta: dict[str, Any], reason: str | None) -> str:
        body = {
            **base_chunk,
            "choices": [{"index": index, "delta": chunk_delta, "finish_reason": reason}],
        }
        return f"data: {json.dumps(body, separators=(',', ':'))}\n\n"

    stream = chunk(delta, None) + chunk({}, finish_reason)
    if "usage" in payload:
        stream += "data: " + json.dumps({**base_chunk, "choices": [], "usage": payload["usage"]}) + "\n\n"
    return (stream + "data: [DONE]\n\n").encode()


def _possible_bare_call(text: str) -> bool:
    text = text.lstrip()
    if "call:".startswith(text):
        return True
    if not text.startswith("call:"):
        return False
    rest = text[5:]
    if "{" not in rest:
        return bool(re.fullmatch(r"[\w-]*\s*", rest))
    name, _ = rest.split("{", 1)
    if not re.fullmatch(r"[\w-]+\s*", name):
        return False
    try:
        close = _find_brace_close(text, text.index("{"))
    except ValueError:
        return False
    return close is None or not text[close:].strip()


def _encode_sse(payload: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps(payload, separators=(",", ":")) + "\n\n").encode()


def rewrite_sse_stream(lines):
    """Forward SSE promptly, withholding only content that could be a bare call.

    A clean `stop` is required to execute buffered content. EOF, [DONE] without
    stop, errors, native tool calls and truncation all flush it as ordinary text.
    The 1 MiB bound also makes oversized candidates ordinary text.
    """
    states = {}
    disabled = False
    ended = False

    def flush(index, state):
        content = state["content"]
        state["content"] = ""
        state["passthrough"] = True
        if content:
            return _encode_sse({**state["base"], "choices": [
                {"index": index, "delta": {"content": content}, "finish_reason": None}
            ]})
        return b""

    def flush_all():
        return b"".join(flush(index, state) for index, state in states.items())

    def rewrite(frame):
        nonlocal disabled, ended
        data = b"\n".join(line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:"))
        if not data:
            return [frame]
        if data == b"[DONE]":
            ended = True
            return [flush_all(), frame]
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeError):
            disabled = True
            return [flush_all(), frame]
        if not isinstance(payload, dict) or "error" in payload:
            disabled = True
            return [flush_all(), frame]
        choices = payload.get("choices")
        if not choices:
            return [frame]
        if not isinstance(choices, list) or any(
            not isinstance(choice, dict)
            or not isinstance(choice.get("delta") or {}, dict)
            or not isinstance(choice.get("index", 0), int)
            for choice in choices
        ):
            disabled = True
            return [flush_all(), frame]
        output = []
        rewritten_choices = []
        for choice in choices:
            index = choice.get("index", 0)
            state = states.setdefault(index, {"content": "", "passthrough": disabled,
                                              "base": {k: v for k, v in payload.items() if k not in {"choices", "usage"}}})
            delta = dict(choice.get("delta") or {})
            if delta.get("tool_calls") or delta.get("function_call"):
                output.append(flush(index, state))
            content = delta.get("content")
            if not state["passthrough"] and isinstance(content, str):
                state["content"] += content
                delta.pop("content")
                if len(state["content"]) > 1024 * 1024 or not _possible_bare_call(state["content"]):
                    delta["content"] = state["content"]
                    state["content"] = ""
                    state["passthrough"] = True
            reason = choice.get("finish_reason")
            if reason is not None and not state["passthrough"]:
                call = parse_bare_gemma_tool_call(state["content"]) if reason == "stop" else None
                if call:
                    delta["tool_calls"] = [{"index": 0, **call}]
                    reason = "tool_calls"
                    state["content"] = ""
                    state["passthrough"] = True
                else:
                    output.append(flush(index, state))
            if delta or reason is not None or "content" not in (choice.get("delta") or {}):
                rewritten_choices.append({**choice, "delta": delta, "finish_reason": reason})
        if rewritten_choices or payload.get("usage") is not None:
            output.append(_encode_sse({**payload, "choices": rewritten_choices}))
        return output

    frame = []
    try:
        for line in lines:
            frame.append(line)
            if not line.strip(b"\r\n"):
                yield from (part for part in rewrite(b"".join(frame)) if part)
                frame = []
                if ended:
                    return
    except (OSError, http.client.HTTPException):
        # Interrupted HTTP chunks are an unclean EOF, never a clean tool stop.
        pass
    # EOF is not an SSE event delimiter. Never interpret an unterminated
    # final event as a clean stop, even when its JSON happens to be complete.
    # Flush prior candidate text first, then preserve the unfinished bytes
    # without manufacturing a delimiter or executable tool call.
    pending = flush_all()
    if pending:
        yield pending
    if frame:
        yield b"".join(frame)


# Generous upstream timeout: mlx-vlm generations on long prompts/outputs can
# legitimately take minutes; this only guards against a truly wedged upstream.
_UPSTREAM_TIMEOUT_S = 600


class ProxyHandler(BaseHTTPRequestHandler):
    upstream_host = "127.0.0.1"
    upstream_port = 0

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, body: bytes | None = None) -> None:
        upstream = f"http://{self.upstream_host}:{self.upstream_port}{self.path}"
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "accept-encoding"}
        }

        original_stream = False
        outgoing = body
        if self.command == "POST" and self.path == "/v1/chat/completions" and body:
            try:
                request_payload = json.loads(body)
                original_stream = bool(request_payload.get("stream"))
            except json.JSONDecodeError:
                pass

        req = urllib.request.Request(
            upstream,
            data=outgoing,
            headers=headers,
            method=self.command,
        )
        try:
            with urllib.request.urlopen(req, timeout=_UPSTREAM_TIMEOUT_S) as resp:
                if original_stream and resp.status == 200 and "text/event-stream" in resp.headers.get("Content-Type", ""):
                    self._forward_stream(resp)
                    return
                data = resp.read()
                status = resp.status
                content_type = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data = e.read()
            status = e.code
            content_type = e.headers.get("Content-Type", "application/json")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            # Upstream unreachable/down/timed out — respond with a real 502
            # instead of letting the handler thread crash (which the client
            # would just see as a reset connection).
            self._send_json_error(502, f"upstream unavailable: {e}")
            return

        # Only rewrite/convert on a successful upstream response. A non-200
        # body is the real error the client needs to see — rewriting it (or
        # forcing it through the streamed-chunk shape) would replace an
        # informative error with an empty/garbled one.
        if status == 200 and self.path == "/v1/chat/completions" and data:
            try:
                payload = rewrite_chat_response(json.loads(data))
                if original_stream:
                    data = _stream_from_chat_response(payload)
                    content_type = "text/event-stream"
                else:
                    data = json.dumps(payload).encode()
                    content_type = "application/json"
            except Exception:  # noqa: BLE001 — best-effort rewrite
                # Anything goes wrong parsing/rewriting: fall back to passing
                # the upstream bytes through untouched rather than dropping
                # the response or crashing the handler.
                pass

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _forward_stream(self, response) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        finished = threading.Event()
        # urllib exposes the HTTPResponse transport through this buffered reader.
        # Shutdown (before close) wakes a thread blocked waiting for the next token.
        upstream_socket = response.fp.raw._sock

        def watch_disconnect():
            while not finished.wait(0.1):
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    disconnected = readable and not self.connection.recv(1, socket.MSG_PEEK)
                except OSError:
                    disconnected = True
                if disconnected:
                    try:
                        upstream_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return

        watcher = threading.Thread(target=watch_disconnect, daemon=True)
        watcher.start()
        try:
            for event in rewrite_sse_stream(response):
                self.wfile.write(event)
                self.wfile.flush()
        except (OSError, ValueError, http.client.HTTPException):
            # Headers are already sent: never replace a partial stream with JSON.
            pass
        finally:
            finished.set()
            response.close()
            watcher.join(timeout=0.2)

    def do_GET(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self._proxy(self.rfile.read(length) if length else b"")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("upstream_cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    upstream_cmd = list(args.upstream_cmd)
    if upstream_cmd and upstream_cmd[0] == "--":
        upstream_cmd = upstream_cmd[1:]
    if not upstream_cmd:
        parser.error("missing upstream command after --")

    proc = subprocess.Popen(upstream_cmd)

    server = None
    try:
        def _stop(_signum: int, _frame: Any) -> None:
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        ProxyHandler.upstream_host = args.upstream_host
        ProxyHandler.upstream_port = args.upstream_port
        server = ThreadingHTTPServer((args.listen_host, args.listen_port), ProxyHandler)
        server.serve_forever()
    finally:
        if server is not None:
            server.server_close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
