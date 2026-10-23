import json
import os
import socket
import sys
import time

import click
import pytest


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fixture_command(port):
    source = '''
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  body=json.dumps({"data":[{"id":"org/test"}]}).encode()
  self.send_response(200); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
 def log_message(self,*args): pass
HTTPServer(("127.0.0.1",PORT),Handler).serve_forever()
'''.replace("PORT", str(port))
    return [sys.executable, "-c", source]


def test_background_server_survives_launcher_and_stops_owned_child(tmp_path):
    from turbollm.server_process import start_background, stop_background, tcp_listening, read_status
    port = free_port()
    model = {"name": "Test", "hf_repo": "org/test", "backend": "gguf", "context_default": 16384}
    try:
        info = start_background(fixture_command(port), model, port, tmp_path, alias="test", model_id="org/test", timeout=10)
        assert info["pid"] != os.getpid()
        assert info["max_tokens"] == 16384
        assert info["alias"] == "test"
        assert tcp_listening(port)
        assert read_status(tmp_path, port)["token"] == info["token"]
        assert os.path.exists(info["log"])
    finally:
        stop_background(tmp_path, port)
    assert not tcp_listening(port)
    assert read_status(tmp_path, port) is None
    assert not os.path.exists(info["log"])


def test_background_refuses_busy_non_api_port(tmp_path):
    from turbollm.server_process import start_background
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); sock.listen()
        port = sock.getsockname()[1]
        with pytest.raises(click.ClickException, match="(?i)(busy|listening|occupied)"):
            start_background(fixture_command(port), {}, port, tmp_path, model_id="org/test", timeout=1)
        assert not (tmp_path / f"port-{port}.json").exists()


def test_stop_refuses_reused_pid_and_unmanaged_process(tmp_path):
    from turbollm.server_process import stop_background
    path = tmp_path / "port-8899.json"
    path.write_text(json.dumps({"pid": os.getpid(), "managed": True, "identity": "not-this-process", "token": "stale"}))
    with pytest.raises(click.ClickException, match="(?i)(identity|owned|stale)"):
        stop_background(tmp_path, 8899)
    assert path.exists()


def test_port_lock_excludes_second_starter(tmp_path):
    from turbollm.server_process import port_lock
    with port_lock(tmp_path, 8899):
        with pytest.raises(click.ClickException, match="(?i)(starting|owned|locked)"):
            with port_lock(tmp_path, 8899):
                pytest.fail("both starters acquired the lock")


def test_concurrent_warm_clients_start_once_and_share_server(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from turbollm import cli, server_process

    port = free_port()
    model = {"name": "Test", "hf_repo": "org/test", "backend": "gguf",
             "context_default": 4096, "server": {"keep_alive": True, "startup_timeout": 10}}
    actual_start = server_process.start_background
    actual_probe = cli._server_is_running
    starts = []
    barrier = threading.Barrier(2)
    local = threading.local()

    class Provider:
        name = "fixture"
        install_hint = ""

        def is_downloaded(self, model):
            return True

        def is_available(self):
            return True

        def get_model_id(self, model):
            return "org/test"

    def probe(p):
        running = actual_probe(p)
        if not getattr(local, "checked", False):
            local.checked = True
            barrier.wait(timeout=5)
        return running

    def start(command, current, p, state_dir, **kwargs):
        starts.append(p)
        return actual_start(fixture_command(p), current, p, state_dir, **kwargs)

    monkeypatch.setattr(cli, "_server_is_running", probe)
    monkeypatch.setattr(cli, "_get_provider_for", lambda _m: Provider())
    monkeypatch.setattr(cli, "TURBOLLM_STATE_DIR", tmp_path)
    monkeypatch.setattr(server_process, "start_background", start)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            clients = [pool.submit(cli._run_with_server, model, port,
                                   lambda current, p: (current["hf_repo"], p)) for _ in range(2)]
            assert [client.result(timeout=15) for client in clients] == [("org/test", port)] * 2
        assert starts == [port]
    finally:
        if server_process.read_status(tmp_path, port):
            server_process.stop_background(tmp_path, port)


def test_warm_start_wait_is_bounded_and_does_not_release_owner_lock(tmp_path):
    from turbollm.server_process import startup_lock
    with startup_lock(tmp_path, 8899, timeout=0.1):
        for _ in range(2):
            with pytest.raises(click.ClickException, match="Timed out waiting for startup"):
                with startup_lock(tmp_path, 8899, timeout=0.1):
                    pytest.fail("waiting launcher acquired the owner's lock")
    with startup_lock(tmp_path, 8899, timeout=0.1):
        pass


def test_failed_background_start_exits_and_keeps_diagnostics(tmp_path):
    from turbollm.server_process import start_background, read_status
    port = free_port()
    command = [sys.executable, "-c", "import sys; print('fixture failed',file=sys.stderr); sys.exit(2)"]
    with pytest.raises(click.ClickException, match="fixture failed"):
        start_background(command, {}, port, tmp_path, model_id="org/test", timeout=5)
    assert read_status(tmp_path, port) is None
    assert any("fixture failed" in path.read_text() for path in tmp_path.glob("server-*.log"))


def test_exited_backend_leader_does_not_leave_listening_worker(tmp_path):
    from turbollm.server_process import start_background, tcp_listening, read_status
    port = free_port()
    worker = fixture_command(port)
    leader = [sys.executable, "-c", f"import subprocess,time; subprocess.Popen({worker!r}); time.sleep(1)"]
    start_background(leader, {"backend": "gguf"}, port, tmp_path, model_id="org/test", timeout=5)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and (tcp_listening(port) or read_status(tmp_path, port)):
        time.sleep(0.1)
    assert not tcp_listening(port)
    assert read_status(tmp_path, port) is None
