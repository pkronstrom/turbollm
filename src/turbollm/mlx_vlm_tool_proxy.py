"""OpenAI-compatible proxy shims for mlx-vlm edge cases.

DiffusionGemma can emit Gemma-style tool calls as plain text, e.g.
``call:ls{path:.}``, while mlx-vlm's OpenAI server only parses Gemma calls when
they are wrapped in ``<|tool_call>...<tool_call|>``. This proxy sits in front of
mlx_vlm.server for that model family and rewrites complete chat responses into
structured OpenAI ``tool_calls`` for agent harnesses like pi.
"""

from __future__ import annotations

import argparse
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
    parts: list[str] = []
    start = 0
    depth = 0
    in_string = False
    quote = ""
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_string:
            if ch == quote:
                in_string = False
            continue
        if ch in ("'", '"'):
            in_string = True
            quote = ch
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if (value[0], value[-1]) in (('"', '"'), ("'", "'")):
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _find_brace_close(text: str, open_brace: int) -> int | None:
    """Return the index just past the ``}``/``]`` matching ``text[open_brace]``.

    Returns ``None`` if the braces never balance out before the string ends
    (an unterminated/truncated call — tolerated by the caller since there's
    nothing *after* it to worry about folding in).
    """
    depth = 0
    in_string = False
    quote = ""
    escape = False
    for i in range(open_brace, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_string:
            if ch == quote:
                in_string = False
            continue
        if ch in ("'", '"'):
            in_string = True
            quote = ch
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def parse_bare_gemma_tool_call(text: str) -> dict[str, Any] | None:
    """Parse ``call:name{k:v}`` into an OpenAI tool call dict.

    Gemma's native tool syntax uses bare identifiers and often unquoted scalar
    values, so JSON parsing is not enough here.

    Only matches when the *entire* (stripped) message is the call: it must
    start with ``call:`` at position 0, and once the ``{...}`` braces close,
    nothing but whitespace may follow. A substring match here would hijack
    ordinary prose that merely mentions the ``call:`` syntax (discarding the
    model's actual answer) and would fold any trailing text after a real call
    into the last parsed argument.
    """
    stripped = text.strip()
    if not stripped.startswith("call:"):
        return None
    open_brace = stripped.find("{")
    if open_brace < 0:
        return None
    name = stripped[len("call:") : open_brace].strip()
    if not name or not all(ch.isalnum() or ch in "_-" for ch in name):
        return None

    close = _find_brace_close(stripped, open_brace)
    if close is None:
        # Unterminated — tolerate it (nothing trails an unterminated call).
        args_text = stripped[open_brace + 1 :]
        if args_text.endswith("}"):
            args_text = args_text[:-1]
    else:
        if stripped[close:].strip():
            # Trailing prose after a complete call — not a bare exact call.
            return None
        args_text = stripped[open_brace + 1 : close - 1]

    args: dict[str, Any] = {}
    for part in _split_top_level(args_text):
        if ":" not in part:
            continue
        key, raw_value = part.split(":", 1)
        args[key.strip().strip("\"'")] = _parse_value(raw_value)

    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, separators=(",", ": ")),
        },
    }


def rewrite_chat_response(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    if not choices:
        return payload
    message = choices[0].get("message") or {}
    content = message.get("content")
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
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    delta: dict[str, Any] = {"role": "assistant"}
    if message.get("tool_calls"):
        delta["tool_calls"] = [
            {"index": index, **tool_call}
            for index, tool_call in enumerate(message["tool_calls"])
        ]
        finish_reason = "tool_calls"
    else:
        delta["content"] = message.get("content") or ""
        finish_reason = choice.get("finish_reason") or "stop"

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

    return (chunk(delta, None) + chunk({}, finish_reason) + "data: [DONE]\n\n").encode()


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
                if original_stream:
                    request_payload = dict(request_payload)
                    request_payload["stream"] = False
                    outgoing = json.dumps(request_payload).encode()
                    headers["Content-Type"] = "application/json"
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

    def _stop(_signum: int, _frame: Any) -> None:
        proc.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    ProxyHandler.upstream_host = args.upstream_host
    ProxyHandler.upstream_port = args.upstream_port
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), ProxyHandler)
    try:
        server.serve_forever()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
