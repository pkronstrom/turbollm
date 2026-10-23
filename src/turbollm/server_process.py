"""Owned foreground/background servers, independent of a harness's lifetime."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

import click

from turbollm.registry import effective_context


def tcp_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


@contextlib.contextmanager
def port_lock(state_dir: Path, port: int):
    state_dir.mkdir(parents=True, exist_ok=True)
    # Never unlink lock files: another starter may already have the inode open.
    with (state_dir / f"port-{port}.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise click.ClickException(f"Port {port} is already owned or starting.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextlib.contextmanager
def startup_lock(state_dir: Path, port: int, timeout: float):
    """Serialize automatic warm starts without holding the server's lifetime lock."""
    state_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with (state_dir / f"port-{port}.startup.lock").open("a+") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise click.ClickException(f"Timed out waiting for startup on port {port}.") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def process_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
                                capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _read_stamp(state_dir: Path, port: int) -> dict | None:
    try:
        data = json.loads((state_dir / f"port-{port}.json").read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_status(state_dir: Path, port: int) -> dict | None:
    data = _read_stamp(state_dir, port)
    if not data or not data.get("identity"):
        return None
    try:
        identity = process_identity(int(data["pid"]))
    except (KeyError, TypeError, ValueError):
        return None
    return data if identity and identity == data["identity"] else None


def _atomic_stamp(state_dir: Path, port: int, data: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=f".port-{port}-", dir=state_dir)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
        os.replace(name, state_dir / f"port-{port}.json")
    finally:
        Path(name).unlink(missing_ok=True)


def _owns_listener(pid: int, port: int) -> bool:
    """Do not mistake another process winning the bind race for our server."""
    binary = shutil.which("lsof") or "/usr/sbin/lsof"
    try:
        result = subprocess.run([binary, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                capture_output=True, text=True, timeout=2)
        listeners = [int(value) for value in result.stdout.split()]
        for current in listeners:
            for _ in range(8):
                if current == pid:
                    return True
                parent = subprocess.run(["ps", "-p", str(current), "-o", "ppid="],
                                        capture_output=True, text=True, timeout=2)
                current = int(parent.stdout.strip() or "0")
                if current <= 1:
                    break
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return False


def _ready(port: int, model_id: str, backend: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=1) as response:
            data = json.load(response)
        if backend in ("mlx-audio", "kev"):
            return isinstance(data, dict) and ("data" in data or "models" in data)
        return any(item.get("id") == model_id for item in data.get("data", []))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _terminate_child(child: subprocess.Popen) -> None:
    # Workers can outlive the backend leader. The session/group is ours even
    # when the direct child has exited; clean it before relinquishing ownership.
    deadline = time.monotonic() + 8
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        child.wait()
        return
    if child.poll() is None:
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)
    while time.monotonic() < deadline:
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGKILL)


def run_server(command: list[str] | None, model: dict, port: int, state_dir: Path, *,
               model_id: str, alias: str | None = None, token: str | None = None,
               timeout: float = 120) -> int:
    """Supervise a backend and publish its identity only once it owns the port."""
    token = token or uuid.uuid4().hex
    managed = sys.argv[0].endswith("server_process.py")
    with port_lock(state_dir, port):
        if tcp_listening(port):
            raise click.ClickException(f"Port {port} is occupied by a listening process.")
        if command is None:
            from turbollm.providers import get_provider
            command = get_provider(model["backend"]).build_serve_cmd(model, port)
        child = None
        previous = {}
        ready = False
        clean_exit = False

        def stop(_signum, _frame):
            raise KeyboardInterrupt

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous[sig] = signal.signal(sig, stop)
            child = subprocess.Popen(command, start_new_session=True)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise click.ClickException(f"Backend exited during startup (status {child.returncode}).")
                if _ready(port, model_id, model.get("backend", "")) and _owns_listener(child.pid, port):
                    break
                time.sleep(0.1)
            else:
                raise click.ClickException(f"Backend did not become ready within {timeout:g}s.")
            identity = process_identity(os.getpid())
            if not identity:
                raise click.ClickException("Cannot verify server process identity.")
            stamp = {"pid": os.getpid(), "identity": identity, "token": token,
                     "managed": managed, "started_at": time.time(), "alias": alias,
                     "hf_repo": model.get("hf_repo"), "backend": model.get("backend"),
                     "revision": model.get("revision"), "backend_pid": child.pid,
                     "model_id": model_id, "max_tokens": effective_context(model)}
            _atomic_stamp(state_dir, port, stamp)
            ready = True
            result = child.wait()
            clean_exit = result == 0
            return result
        except KeyboardInterrupt:
            clean_exit = ready
            return 0
        finally:
            # Further signals must not interrupt child cleanup.
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            try:
                if child is not None:
                    _terminate_child(child)
                if clean_exit:
                    with contextlib.suppress(OSError):
                        (state_dir / f"server-{port}-{token}.log").unlink(missing_ok=True)
                current = _read_stamp(state_dir, port)
                if current and current.get("token") == token:
                    (state_dir / f"port-{port}.json").unlink(missing_ok=True)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)


def start_background(command: list[str] | None, model: dict, port: int, state_dir: Path, *,
                     model_id: str, alias: str | None = None, timeout: float = 120) -> dict:
    if tcp_listening(port):
        raise click.ClickException(f"Port {port} is occupied by a listening process.")
    state_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    config = {"command": command, "model": model, "port": port,
              "state_dir": str(state_dir), "model_id": model_id, "alias": alias,
              "token": token, "timeout": timeout}
    fd, name = tempfile.mkstemp(prefix="server-", suffix=".json", dir=state_dir)
    with os.fdopen(fd, "w") as handle:
        json.dump(config, handle)
    log_path = state_dir / f"server-{port}-{token}.log"
    process = None
    try:
        with log_path.open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "turbollm.server_process", name, token],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        deadline = time.monotonic() + timeout + 5
        while time.monotonic() < deadline and process.poll() is None:
            data = _read_stamp(state_dir, port)
            if data and data.get("token") == token and data.get("pid") == process.pid:
                return {**data, "log": str(log_path)}
            time.sleep(0.1)
        try:
            tail = log_path.read_text(errors="replace")[-6000:]
        except FileNotFoundError:
            tail = "Server exited cleanly before the launcher observed readiness."
        raise click.ClickException(f"Server failed to start. Log: {log_path}\n{tail}")
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                # The supervisor handles its own child group; do not kill unrelated PIDs.
                process.kill()
                process.wait()
        raise
    finally:
        Path(name).unlink(missing_ok=True)


def stop_background(state_dir: Path, port: int) -> None:
    data = read_status(state_dir, port)
    if not data or not data.get("managed") or not data.get("token"):
        raise click.ClickException(f"No owned background server on port {port}; stale identity or unmanaged process.")
    # The random token is also in the worker argv, guarding against PID reuse.
    if data["token"] not in data["identity"]:
        raise click.ClickException("Server launch identity does not match; refusing to signal it.")
    os.kill(int(data["pid"]), signal.SIGTERM)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if not _read_stamp(state_dir, port) or not read_status(state_dir, port):
            return
        time.sleep(0.1)
    raise click.ClickException("Server shutdown timed out; inspect its log before stopping it manually.")


def main() -> int:
    path = Path(sys.argv[1])
    config = json.loads(path.read_text())
    path.unlink()
    if config["token"] != sys.argv[2]:
        raise click.ClickException("Invalid launch identity.")
    config["state_dir"] = Path(config["state_dir"])
    return run_server(**config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except click.ClickException as error:
        error.show()
        raise SystemExit(error.exit_code)
