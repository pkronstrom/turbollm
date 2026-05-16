from unittest.mock import patch

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


def test_run_with_server_prints_backend_log_when_startup_fails(capsys):
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
    ):
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
