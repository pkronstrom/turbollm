#!/usr/bin/env python3
"""Measure an existing local chat server: identical cold/warm prompt, no lifecycle changes."""
import argparse
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--model", default="Qwen3.8-27B-oQ6e-mtp")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    context = "\n".join(f"# reference entry {i}: square({i}) = {i*i}" for i in range(256))
    payload = {
        "model": args.model,
        "messages": [{"role": "system", "content": "You are a concise coding assistant."},
                     {"role": "user", "content": context + "\n\ndef square(n):\n    return n*n\n\nWhat does square(17) return? Reply only with the integer."}],
        "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 256,
        "temperature": 1.0, "top_p": .95, "top_k": 20, "min_p": 0,
        "presence_penalty": 0,
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True, "reasoning_effort": "low"},
    }
    results = []
    for index in range(3):
        start = time.perf_counter()
        first_delta = first_content = None
        content, reasoning, usage = "", "", None
        request = Request(f"http://127.0.0.1:{args.port}/v1/chat/completions", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=300) as response:
            for raw in response:
                if not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    break
                event = json.loads(data)
                usage = event.get("usage") or usage
                for choice in event.get("choices", []):
                    delta = choice.get("delta", {})
                    text = delta.get("content") or ""
                    thought = delta.get("reasoning_content") or ""
                    elapsed = time.perf_counter() - start
                    if first_delta is None and (text or thought):
                        first_delta = elapsed
                    if first_content is None and text:
                        first_content = elapsed
                    content += text
                    reasoning += thought
        row = {"run": index + 1, "first_delta_s": first_delta, "first_content_s": first_content,
               "total_s": time.perf_counter() - start, "content": content,
               "correct": content.strip() == "289", "reasoning_chars": len(reasoning), "usage": usage}
        results.append(row)
        print(json.dumps(row), flush=True)
    args.out.write_text(json.dumps({"description": "Identical repeated prompt, not a quality or general cache benchmark",
                                    "request": payload, "runs": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
