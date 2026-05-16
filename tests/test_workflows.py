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


def test_validate_workflow_rejects_param_without_name():
    wf = {"command": "echo", "params": [{"type": "string"}]}
    with pytest.raises(workflows.WorkflowError, match="missing the required `name` field"):
        workflows.validate_workflow("bad", wf)


def test_validate_workflow_rejects_param_without_type():
    wf = {"command": "echo", "params": [{"name": "x"}]}
    with pytest.raises(workflows.WorkflowError, match="missing the required `type` field"):
        workflows.validate_workflow("bad", wf)


def test_validate_workflow_rejects_unknown_type():
    wf = {"command": "echo", "params": [{"name": "x", "type": "not-a-real-type"}]}
    with pytest.raises(workflows.WorkflowError, match="unknown type 'not-a-real-type'"):
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


def test_run_workflow_executes_inline_command(monkeypatch, tmp_path):
    out_file = tmp_path / "marker.txt"
    wf = {
        "command": f'echo hello > "{out_file}"',
        "params": [],
    }
    rc = workflows.run_workflow("simple", wf, overrides={}, registry={})
    assert rc == 0
    assert out_file.read_text() == "hello\n"


def test_run_workflow_resolves_script_reference(monkeypatch, tmp_path):
    out_file = tmp_path / "from-script.txt"
    wf = {
        "script": "the-script",
        "args": ["{{name}}"],
        "params": [{"name": "name", "type": "string", "default": "world"}],
    }
    registry = {
        "scripts": {
            "the-script": {"command": f'echo "hello $1" > "{out_file}"'},
        },
        "workflows": {"refscript": wf},
    }
    rc = workflows.run_workflow("refscript", wf, overrides={}, registry=registry)
    assert rc == 0
    assert out_file.read_text() == "hello world\n"


def test_run_workflow_writes_activity_file(monkeypatch, tmp_path):
    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")
    wf = {"command": "true", "params": []}
    captured_ids: list[str] = []

    real_start = activity.start_activity

    def _spy_start(**kw):
        aid = real_start(**kw)
        captured_ids.append(aid)
        return aid

    monkeypatch.setattr(activity, "start_activity", _spy_start)

    rc = workflows.run_workflow("named", wf, overrides={}, registry={})
    assert rc == 0
    assert len(captured_ids) == 1
    # Activity is cleared after run
    assert not list((tmp_path / "state").glob("activity-*.json"))


def test_run_workflow_refuses_unresolved_acquired_param():
    wf = {
        "command": "echo {{audio}}",
        "params": [{"name": "audio", "type": "audio-recording", "mode": "primary"}],
    }
    with pytest.raises(workflows.WorkflowError, match="acquired param 'audio'"):
        workflows.run_workflow("x", wf, overrides={}, registry={})


def test_run_workflow_accepts_acquired_param_override(tmp_path):
    out_file = tmp_path / "marker.txt"
    wf = {
        "command": f'echo "got={{{{audio}}}}" > "{out_file}"',
        "params": [{"name": "audio", "type": "audio-recording", "mode": "primary"}],
    }
    rc = workflows.run_workflow(
        "x", wf, overrides={"audio": "/path/to/file.wav"}, registry={}
    )
    assert rc == 0
    assert out_file.read_text() == "got=/path/to/file.wav\n"


# ---------------------------------------------------------------------------
# T-13: run_workflow spawns turbo-acquirer for acquired params
# ---------------------------------------------------------------------------

import os as _os
import stat as _stat
import textwrap as _textwrap


def _make_fake_acquirer(bin_dir, name: str, body: str) -> None:
    """Write an executable fake turbo-acquirer script to bin_dir."""
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + _textwrap.dedent(body))
    p.chmod(p.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)


def test_run_workflow_spawns_acquirer_for_audio_param(monkeypatch, tmp_path):
    """run_workflow spawns the fake acquirer, uses its stdout as the resolved value."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Fake acquirer: `record-audio` emits a fixed WAV path.
    fake_wav = tmp_path / "captured.wav"
    fake_wav.write_bytes(b"RIFF")
    _make_fake_acquirer(bin_dir, "turbo-acquirer", f"""\
case "$1" in
  record-audio) printf '%s' '{fake_wav}' ;;
  *) printf 'unexpected subcommand: %s' "$1" >&2; exit 1 ;;
esac
""")

    out_file = tmp_path / "marker.txt"
    wf = {
        "command": f'echo "audio={{{{audio}}}}" > "{out_file}"',
        "params": [{"name": "audio", "type": "audio-recording", "mode": "primary"}],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    rc = workflows.run_workflow("x", wf, overrides={}, registry={})
    assert rc == 0
    assert out_file.read_text().strip() == f"audio={fake_wav}"


def test_run_workflow_acquirer_receives_workflow_id_env(monkeypatch, tmp_path):
    """The TURBO_WORKFLOW_ID env var is passed to the acquirer subprocess."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Fake acquirer: dumps $TURBO_WORKFLOW_ID to stdout.
    _make_fake_acquirer(bin_dir, "turbo-acquirer", """\
case "$1" in
  record-audio) printf '%s' "${TURBO_WORKFLOW_ID:-MISSING}" ;;
  *) exit 1 ;;
esac
""")

    captured_values: list[str] = []
    from turbollm import activity

    real_start = activity.start_activity

    def _spy_start(**kw):
        aid = real_start(**kw)
        captured_values.append(aid)
        return aid

    monkeypatch.setattr(activity, "start_activity", _spy_start)
    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    out_file = tmp_path / "marker.txt"
    wf = {
        "command": f'echo "{{{{audio}}}}" > "{out_file}"',
        "params": [{"name": "audio", "type": "audio-recording", "mode": "primary"}],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    rc = workflows.run_workflow("y", wf, overrides={}, registry={})
    assert rc == 0
    # The activity id must have been passed as TURBO_WORKFLOW_ID.
    assert len(captured_values) == 1
    workflow_id = captured_values[0]
    assert out_file.read_text().strip() == workflow_id


def test_run_workflow_acquirer_argv_screenshot(monkeypatch, tmp_path):
    """screenshot-manual param maps to `turbo-acquirer screenshot`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    fake_png = tmp_path / "screen.png"
    fake_png.write_bytes(b"PNG")
    _make_fake_acquirer(bin_dir, "turbo-acquirer", f"""\
case "$1" in
  screenshot) printf '%s' '{fake_png}' ;;
  *) exit 1 ;;
esac
""")

    out_file = tmp_path / "marker.txt"
    wf = {
        "command": f'echo "shot={{{{shot}}}}" > "{out_file}"',
        "params": [{"name": "shot", "type": "screenshot-manual"}],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    rc = workflows.run_workflow("z", wf, overrides={}, registry={})
    assert rc == 0
    assert out_file.read_text().strip() == f"shot={fake_png}"


def test_run_workflow_acquirer_argv_command_type(monkeypatch, tmp_path):
    """command-type param maps to `turbo-acquirer command --shell <acquire>`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Fake acquirer: prints back the value of --shell argument.
    _make_fake_acquirer(bin_dir, "turbo-acquirer", """\
case "$1" in
  command)
    # argv is: command --shell <cmd>
    shift; shift  # consume 'command' and '--shell'
    result="$(eval "$@")"
    printf '%s' "$result"
    ;;
  *) exit 1 ;;
esac
""")

    out_file = tmp_path / "marker.txt"
    wf = {
        "command": f'echo "val={{{{val}}}}" > "{out_file}"',
        "params": [
            {
                "name": "val",
                "type": "command",
                "mode": "primary",
                "acquire": "echo hello-from-cmd",
            }
        ],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    rc = workflows.run_workflow("cmd-wf", wf, overrides={}, registry={})
    assert rc == 0
    assert out_file.read_text().strip() == "val=hello-from-cmd"


def test_run_workflow_acquirer_nonzero_raises_workflow_error(monkeypatch, tmp_path):
    """Non-zero acquirer exit raises WorkflowError."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _make_fake_acquirer(bin_dir, "turbo-acquirer", """\
printf 'permissionDenied(Screen Recording)' >&2
exit 1
""")

    wf = {
        "command": "echo {{audio}}",
        "params": [{"name": "audio", "type": "audio-recording", "mode": "primary"}],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    with pytest.raises(workflows.WorkflowError, match="turbo-acquirer exited"):
        workflows.run_workflow("fail-wf", wf, overrides={}, registry={})


def test_find_acquirer_bin_uses_env_override(monkeypatch):
    """TURBO_ACQUIRER_BIN overrides shutil.which resolution."""
    monkeypatch.setenv("TURBO_ACQUIRER_BIN", "/custom/turbo-acquirer")
    assert workflows.find_acquirer_bin() == "/custom/turbo-acquirer"


def test_find_acquirer_bin_returns_none_when_not_found(monkeypatch):
    """find_acquirer_bin returns None when binary is absent from PATH."""
    monkeypatch.delenv("TURBO_ACQUIRER_BIN", raising=False)
    import shutil
    real_which = shutil.which

    def _which_no_acquirer(name, **kw):
        if name == "turbo-acquirer":
            return None
        return real_which(name, **kw)

    monkeypatch.setattr(shutil, "which", _which_no_acquirer)
    # Also patch the shutil reference inside workflows module.
    import turbollm.workflows as _wf_mod
    monkeypatch.setattr(_wf_mod._shutil, "which", _which_no_acquirer)
    assert workflows.find_acquirer_bin() is None
