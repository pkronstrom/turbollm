import pytest

from turbollm import workflows


def test_load_workflows_returns_dict_keyed_by_name():
    reg = {
        "workflows": {
            "transcribe-file": {
                "description": "Transcribe a file",
                "command": "turbo transcribe \"{{file}}\"",
                "params": [{"name": "file", "type": "file"}],
            }
        }
    }
    loaded = workflows.load_workflows(reg)
    assert "transcribe-file" in loaded
    assert loaded["transcribe-file"]["description"] == "Transcribe a file"
    assert loaded["transcribe-file"]["params"][0]["name"] == "file"


def test_load_workflows_empty_when_no_section():
    assert workflows.load_workflows({}) == {}


def test_validate_workflow_rejects_two_primary_acquired_params():
    wf = {
        "command": "echo",
        "params": [
            {"name": "a", "type": "audio-recording", "mode": "primary"},
            {"name": "b", "type": "command", "mode": "primary", "acquire": "echo x"},
        ],
    }
    with pytest.raises(workflows.WorkflowError, match="exactly one primary"):
        workflows.validate_workflow("bad", wf)


def test_validate_workflow_rejects_command_acquirer_without_acquire_field():
    wf = {
        "command": "echo",
        "params": [{"name": "x", "type": "command", "mode": "primary"}],
    }
    with pytest.raises(workflows.WorkflowError, match="`acquire` or `acquire_script`"):
        workflows.validate_workflow("bad", wf)


def test_validate_workflow_accepts_minimal_no_acquired():
    wf = {"command": "echo hello", "params": [{"name": "x", "type": "string"}]}
    workflows.validate_workflow("ok", wf)  # no exception


def test_validate_workflow_requires_command_or_script():
    wf = {"params": []}
    with pytest.raises(workflows.WorkflowError, match="`command` or `script`"):
        workflows.validate_workflow("bad", wf)


import datetime as _dt


def test_expand_template_substitutes_param():
    out = workflows.expand_template("hello {{name}}", params={"name": "world"})
    assert out == "hello world"


def test_expand_template_substitutes_env(monkeypatch):
    monkeypatch.setenv("MY_VAR", "value-from-env")
    out = workflows.expand_template("x={{env:MY_VAR}}", params={})
    assert out == "x=value-from-env"


def test_expand_template_missing_env_is_empty(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    out = workflows.expand_template("[{{env:MISSING_VAR}}]", params={})
    assert out == "[]"


def test_expand_template_date_uses_strftime():
    out = workflows.expand_template("{{date:%Y}}", params={})
    assert out == _dt.datetime.now().strftime("%Y")


def test_expand_template_missing_param_raises():
    with pytest.raises(workflows.WorkflowError, match="undefined param"):
        workflows.expand_template("{{missing}}", params={"other": "x"})


def test_expand_template_handles_multiple_tokens():
    out = workflows.expand_template(
        "audio={{audio}} title={{title}}",
        params={"audio": "/tmp/a.wav", "title": "Meeting"},
    )
    assert out == "audio=/tmp/a.wav title=Meeting"


def test_resolve_params_uses_default_when_no_override():
    params = [{"name": "title", "type": "string", "default": "untitled"}]
    out = workflows.resolve_params(params, overrides={})
    assert out == {"title": "untitled"}


def test_resolve_params_override_beats_default():
    params = [{"name": "title", "type": "string", "default": "untitled"}]
    out = workflows.resolve_params(params, overrides={"title": "From CLI"})
    assert out == {"title": "From CLI"}


def test_resolve_params_default_env(monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT", "/Users/me/Vault")
    params = [{"name": "vault", "type": "directory", "default_env": "OBSIDIAN_VAULT"}]
    out = workflows.resolve_params(params, overrides={})
    assert out == {"vault": "/Users/me/Vault"}


def test_resolve_params_auto_template():
    params = [{"name": "title", "type": "string", "auto": "{{date:%Y}}"}]
    out = workflows.resolve_params(params, overrides={})
    assert out["title"] == _dt.datetime.now().strftime("%Y")


def test_resolve_params_override_beats_auto():
    params = [{"name": "title", "type": "string", "auto": "{{date:%Y}}"}]
    out = workflows.resolve_params(params, overrides={"title": "Manual"})
    assert out == {"title": "Manual"}


def test_resolve_params_acquired_without_override_raises():
    params = [{"name": "audio", "type": "audio-recording", "mode": "primary"}]
    with pytest.raises(workflows.WorkflowError, match="acquired param 'audio'"):
        workflows.resolve_params(params, overrides={})


def test_resolve_params_acquired_accepts_override():
    params = [{"name": "audio", "type": "audio-recording", "mode": "primary"}]
    out = workflows.resolve_params(params, overrides={"audio": "/tmp/file.wav"})
    assert out == {"audio": "/tmp/file.wav"}


def test_resolve_params_empty_string_when_no_default():
    params = [{"name": "title", "type": "string"}]
    out = workflows.resolve_params(params, overrides={})
    assert out == {"title": ""}
