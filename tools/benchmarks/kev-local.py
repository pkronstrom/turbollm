#!/usr/bin/env python3
"""Run the committed typed-decision smoke fixture against an isolated Kev child.

Uses only Python's standard library. Weights must already be downloaded. The
child runs offline and is stopped in a finally block; existing servers are never
attached to or stopped. This is a smoke benchmark, not a calibration study.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import resource
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urlopen(req, timeout=60) as response:
        body = json.load(response)
    return body, (time.perf_counter() - started) * 1000


def summarize(rows):
    times = sorted(row["wall_ms"] for row in rows)
    # Nearest-rank percentile, with no interpolation between observations.
    p95 = times[max(0, (95 * len(times) + 99) // 100 - 1)]
    return {"requests": len(rows), "correct": sum(row["correct"] for row in rows),
            "p50_wall_ms": statistics.median(times), "p95_wall_ms": p95,
            "mean_wall_ms": statistics.mean(times)}


def evaluate(url, case):
    response, elapsed = request(url + "/v1/systemone", case["request"])
    answer = response["answers"]["decision"]
    kind = answer["type"]
    if kind == "choice":
        predicted = answer["choice"]
    elif kind == "noul":
        predicted = answer["noul"] >= 0.5
    else:
        predicted = max(answer["probabilities"], key=answer["probabilities"].get)
    return {"id": case["id"], "type": kind, "expected": case.get("expected"),
            "predicted": predicted, "correct": predicted == case.get("expected"),
            "wall_ms": elapsed, "response": response}


def runtime_environment(hf_home):
    env = os.environ.copy()
    env.update(HF_HOME=str(hf_home), HF_HUB_CACHE=str(hf_home / "hub"),
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    for key in list(env):
        if key.startswith("KEV_"):
            del env[key]
    env["KEV_BACKEND"] = "mlx"
    return env


def runtime_metadata(runtime):
    script = ("import sys,json,importlib.metadata as m; "
              "print(json.dumps({'runtime_python':sys.version,'runtime_packages':"
              "{n:m.version(n) for n in ['torch','transformers','peft','mlx','mlx-lm','huggingface-hub']}}))")
    result = subprocess.run([str(runtime / ".venv/bin/python"), "-c", script],
                            check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def stop_child(child):
    if child.poll() is None:
        child.send_signal(signal.SIGINT)
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Official Kev source checkout containing .venv")
    parser.add_argument("--run", default="jaredpalmer/kev-4b@v1.0")
    parser.add_argument("--hf-home", type=Path, default=Path.home() / ".models/.hf-cache")
    parser.add_argument("--fixture", type=Path, default=Path(__file__).with_name("kev-fixtures.json"))
    parser.add_argument("--port", type=int, default=18984)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    fixture = json.loads(args.fixture.read_text())
    url = f"http://127.0.0.1:{args.port}"
    # Refuse to collect results from a server whose lifecycle we do not own.
    import socket
    with socket.socket() as check:
        check.bind(("127.0.0.1", args.port))
    env = runtime_environment(args.hf_home)
    metadata = runtime_metadata(args.runtime)
    command = [str(args.runtime / ".venv/bin/python"), "-u", "-m", "kev.serve",
               "--run", args.run, "--host", "127.0.0.1", "--port", str(args.port)]
    started = time.perf_counter()
    with tempfile.TemporaryFile(mode="w+") as log:
        child = subprocess.Popen(command, cwd=args.runtime, env=env, stdout=log, stderr=log)
        try:
            while True:
                if child.poll() is not None:
                    raise RuntimeError(f"Kev exited before readiness: {child.returncode}")
                try:
                    initial_models, _ = request(url + "/v1/models")
                    break
                except (URLError, TimeoutError, ConnectionError):
                    if time.perf_counter() - started > 180:
                        raise TimeoutError("Kev readiness exceeded180seconds")
                    time.sleep(0.1)
            cold_ready = time.perf_counter() - started
            first = evaluate(url, fixture["cases"][0])
            for case in fixture["cases"][1:6]:
                evaluate(url, case)
            rows = [evaluate(url, case) for _ in range(args.rounds) for case in fixture["cases"]]
            before_prefix, _ = request(url + "/v1/models")
            repeated = [evaluate(url, case) for case in fixture["repeated_state"]]
            after_prefix, _ = request(url + "/v1/models")
            order = []
            for case in fixture["cases"]:
                question = case["request"]["questions"]["decision"]
                if question["type"] != "choice":
                    continue
                original = evaluate(url, case)
                reverse = json.loads(json.dumps(case))
                reverse["request"]["questions"]["decision"]["criteria"] = dict(reversed(list(question["criteria"].items())))
                reversed_result = evaluate(url, reverse)
                order.append({"id": case["id"], "stable": original["predicted"] == reversed_result["predicted"],
                              "original": original, "reversed": reversed_result})
            unscored = [evaluate(url, case) for case in fixture["unscored"]]
            validation = []
            for payload in [
                {"state": "x", "questions": {}},
                {"state": "x", "questions": {"q": {"type": "choice", "criteria": {}}}},
                {"state": "x", "questions": {"q": {"type": "score", "criteria": []}}},
            ]:
                try:
                    body, _ = request(url + "/v1/systemone", payload)
                    validation.append({"request": payload, "status": 200, "body": body})
                except HTTPError as exc:
                    validation.append({"request": payload, "status": exc.code, "body": json.load(exc)})
            final_models, _ = request(url + "/v1/models")
            result = {"fixture": fixture["name"], "checkpoint": args.run,
                      "offline": True, "platform": platform.platform(), "driver_python": sys.version,
                      **metadata,
                      "cold_ready_seconds_cached_files": cold_ready, "first_inference": first,
                      "initial_models": initial_models, "short_summary": summarize(rows),
                      "short_by_type": {kind: summarize([row for row in rows if row["type"] == kind]) for kind in ["choice", "noul", "score"]},
                      "short_rows": rows, "repeated_first": repeated[0],
                      "repeated_warm_summary": summarize(repeated[1:]), "repeated_rows": repeated,
                      "prefix_cache_before": before_prefix["models"][0]["prefix_cache"],
                      "prefix_cache_after": after_prefix["models"][0]["prefix_cache"],
                      "option_order": order, "unscored": unscored,
                      "validation": validation, "final_models": final_models}
        except BaseException:
            log.seek(0)
            print(log.read(), file=sys.stderr)
            raise
        finally:
            stop_child(child)
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        # macOS reports bytes; Linux reports KiB. This is process RSS, not an MLX
        # allocator measurement, and includes model loading and all requests.
        result["child_peak_rss_bytes"] = usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        result["child_exit_code"] = child.returncode
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({key: result[key] for key in ["cold_ready_seconds_cached_files", "child_peak_rss_bytes", "short_summary", "short_by_type", "repeated_warm_summary"]}, indent=2))


if __name__ == "__main__":
    main()
