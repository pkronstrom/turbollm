import io
import json
from urllib.error import HTTPError, URLError

import pytest
from click.testing import CliRunner

from turbollm.decisions import decide_cmd


@pytest.fixture
def environment(monkeypatch):
    model = {"name": "Kev4B", "backend": "kev", "hf_repo": "jaredpalmer/kev-4b"}
    calls = []
    monkeypatch.setattr("turbollm.decisions.resolve_model", lambda alias: model)
    def run(m, port, launch):
        calls.append((m, port))
        return launch(m, port)
    monkeypatch.setattr("turbollm.cli._run_with_server", run)
    return model, calls


def payload():
    return {"state": "All work complete", "model": "hosted/other", "questions": {"done": {"type": "noul", "instructions": "Is the work complete?"}}}


def test_decide_stdin_returns_raw_json_and_selects_local_model(environment, monkeypatch):
    seen = []
    response = {"model": "kev-latest", "answers": {"done": {"type": "noul", "noul": 0.98}}}
    def open_request(request, timeout):
        seen.append((request, timeout))
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr("turbollm.decisions.urlopen", open_request)
    result = CliRunner().invoke(decide_cmd, ["-", "--timeout", "2.5"], input=json.dumps(payload()))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response
    assert json.loads(seen[0][0].data)["model"] == "kev-latest"
    assert seen[0][0].full_url == "http://127.0.0.1:8901/v1/systemone"
    assert seen[0][1] == 2.5
    assert len(environment[1]) == 1


@pytest.mark.parametrize("body", ["not json", "[]", "{}", '{"state":"x","questions":{}}', '{"state":"x","questions":{"q":{"type":"chat"}}}', '{"state":"x","questions":{"q":{"type":"choice","criteria":{}}}}', '{"state":NaN,"questions":{"q":{"type":"noul"}}}'])
def test_bad_request_fails_before_starting_server(environment, body):
    result = CliRunner().invoke(decide_cmd, ["-"], input=body)
    assert result.exit_code != 0
    assert environment[1] == []


def test_non_decision_backend_fails_before_starting(environment):
    environment[0]["backend"] = "omlx"
    result = CliRunner().invoke(decide_cmd, ["-"], input=json.dumps(payload()))
    assert result.exit_code != 0
    assert "kev" in result.output.lower()
    assert environment[1] == []


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_invalid_timeout_fails_before_starting(environment, timeout):
    result = CliRunner().invoke(decide_cmd, ["-", "--timeout", timeout], input=json.dumps(payload()))
    assert result.exit_code != 0
    assert environment[1] == []


@pytest.mark.parametrize("failure", [URLError("connection refused"), TimeoutError("timed out"), HTTPError("local", 422, "invalid question", {}, io.BytesIO(b'{"detail":"bad criteria"}'))])
def test_local_failures_are_errors_on_stderr(environment, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr("turbollm.decisions.urlopen", fail)
    result = CliRunner().invoke(decide_cmd, ["-"], input=json.dumps(payload()))
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Error" in result.stderr


def test_file_input_and_custom_port(environment, monkeypatch, tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload()))
    seen = []
    def open_request(request, timeout):
        seen.append(request.full_url)
        return io.BytesIO(b'{"answers":{}}')
    monkeypatch.setattr("turbollm.decisions.urlopen", open_request)
    result = CliRunner().invoke(decide_cmd, [str(path), "--port", "18984"])
    assert result.exit_code == 0, result.output
    assert seen == ["http://127.0.0.1:18984/v1/systemone"]


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"noul":NaN}'])
def test_invalid_response_is_an_error_on_stderr(environment, monkeypatch, body):
    monkeypatch.setattr("turbollm.decisions.urlopen", lambda *args, **kwargs: io.BytesIO(body))
    result = CliRunner().invoke(decide_cmd, ["-"], input=json.dumps(payload()))
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Error" in result.stderr


def test_overflowing_number_fails_before_starting_server(environment):
    body = '{"state":1e999,"questions":{"q":{"type":"noul"}}}'
    result = CliRunner().invoke(decide_cmd, ["-"], input=body)
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Error" in result.stderr
    assert environment[1] == []


def test_overflowing_response_number_is_clean_error(environment, monkeypatch):
    monkeypatch.setattr("turbollm.decisions.urlopen", lambda *args, **kwargs: io.BytesIO(b'{"score":1e999}'))
    result = CliRunner().invoke(decide_cmd, ["-"], input=json.dumps(payload()))
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Error" in result.stderr
