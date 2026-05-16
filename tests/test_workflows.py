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


def test_run_workflow_refuses_unresolved_acquired_param(monkeypatch):
    monkeypatch.setattr(workflows, "find_acquirer_bin", lambda: None)
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


# ---------- Review fix: HUD UserDefaults override + screenshot default ----------


def test_acquirer_argv_uses_hud_scope_override(monkeypatch):
    """When the HUD has stored a scope sticky in UserDefaults, _acquirer_argv
    must prefer it over the workflow's static `scope` field."""

    captured = []

    def fake_run(argv, *_, **__):
        captured.append(argv)

        class _R:
            returncode = 0
            stdout = "mic-only\n"

        return _R()

    monkeypatch.setattr(workflows._subprocess, "run", fake_run)

    param = {
        "name": "audio",
        "type": "audio-recording",
        "scope": "system+mic",  # workflow default
    }
    argv = workflows._acquirer_argv(
        "/usr/local/bin/turbo-acquirer",
        param,
        workflow_name="record-to-obsidian",
    )

    # The HUD override "mic-only" must win over the static "system+mic".
    assert "--scope" in argv
    assert argv[argv.index("--scope") + 1] == "mic-only"
    # And the read should have hit the HUD's suite + key convention.
    keys_read = [a[3] for a in captured if a[:3] == ["/usr/bin/defaults", "read", "com.turbollm.hud"]]
    assert "record-to-obsidian.audio.scope" in keys_read


def test_acquirer_argv_uses_hud_device_uid_override(monkeypatch):
    """HUD-stored input device UID must override the static device_uid."""

    def fake_run(argv, *_, **__):
        class _R:
            returncode = 0
            stdout = ""

        if argv[:3] == ["/usr/bin/defaults", "read", "com.turbollm.hud"]:
            if argv[3].endswith(".input_device"):
                _R.stdout = "BuiltInMicrophoneDevice\n"
            elif argv[3].endswith(".scope"):
                _R.returncode = 1  # not set
        return _R()

    monkeypatch.setattr(workflows._subprocess, "run", fake_run)

    param = {"name": "audio", "type": "audio-recording"}
    argv = workflows._acquirer_argv(
        "/usr/local/bin/turbo-acquirer",
        param,
        workflow_name="rec",
    )

    assert "--device-uid" in argv
    assert argv[argv.index("--device-uid") + 1] == "BuiltInMicrophoneDevice"


def test_acquirer_argv_falls_back_to_workflow_scope_when_no_hud_override(monkeypatch):
    """No HUD sticky → workflow's static scope wins."""

    def fake_run(*_, **__):
        class _R:
            returncode = 1  # nothing in UserDefaults
            stdout = ""

        return _R()

    monkeypatch.setattr(workflows._subprocess, "run", fake_run)

    param = {"name": "audio", "type": "audio-recording", "scope": "system+mic"}
    argv = workflows._acquirer_argv(
        "/usr/local/bin/turbo-acquirer",
        param,
        workflow_name="rec",
    )
    assert argv[argv.index("--scope") + 1] == "system+mic"


def test_acquirer_argv_no_hud_lookup_when_workflow_name_absent(monkeypatch):
    """Calling without workflow_name (e.g. unit tests) must not shell out to defaults."""

    def fail_run(*_, **__):
        raise AssertionError("defaults read should not be invoked without workflow_name")

    monkeypatch.setattr(workflows._subprocess, "run", fail_run)

    param = {"name": "audio", "type": "audio-recording", "scope": "mic-only"}
    argv = workflows._acquirer_argv("/x/turbo-acquirer", param)
    assert argv[argv.index("--scope") + 1] == "mic-only"


def test_acquirer_argv_screenshot_default_output_dir():
    """screenshot-manual without param.output_dir gets a default under ~/.turbollm."""
    from pathlib import Path

    param = {"name": "shot", "type": "screenshot-manual"}
    argv = workflows._acquirer_argv("/x/turbo-acquirer", param)
    assert "--output-dir" in argv
    output_dir = argv[argv.index("--output-dir") + 1]
    assert output_dir == str(Path.home() / ".turbollm" / "screenshots")


def test_acquirer_argv_screenshot_explicit_output_dir_wins():
    """An explicit param.output_dir is honoured over the default."""
    param = {"name": "shot", "type": "screenshot-manual", "output_dir": "/tmp/custom"}
    argv = workflows._acquirer_argv("/x/turbo-acquirer", param)
    assert argv[argv.index("--output-dir") + 1] == "/tmp/custom"


# ---------- T-7: TURBO_T0_NS time-origin alignment ----------


import time as _time_mod


def test_run_workflow_sets_turbo_t0_ns_env(monkeypatch, tmp_path):
    """run_workflow exports TURBO_T0_NS to acquirer subprocesses."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Fake acquirer: dumps $TURBO_T0_NS to stdout so we can verify it was set.
    _make_fake_acquirer(bin_dir, "turbo-acquirer", """\
case "$1" in
  record-audio) printf '%s' "${TURBO_T0_NS:-MISSING}" ;;
  *) exit 1 ;;
esac
""")

    out_file = tmp_path / "marker.txt"
    wf = {
        "command": f'echo "{{{{audio}}}}" > "{out_file}"',
        "params": [{"name": "audio", "type": "audio-recording", "mode": "primary"}],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    t_before = _time_mod.monotonic_ns()
    rc = workflows.run_workflow("t0-test", wf, overrides={}, registry={})
    t_after = _time_mod.monotonic_ns()

    assert rc == 0
    t0_value_str = out_file.read_text().strip()
    assert t0_value_str != "MISSING", "TURBO_T0_NS was not set on the acquirer subprocess"
    t0_value = int(t0_value_str)
    assert t_before <= t0_value <= t_after, (
        f"TURBO_T0_NS={t0_value} should be between t_before={t_before} and t_after={t_after}"
    )


def test_run_workflow_both_acquirers_see_same_t0_ns(monkeypatch, tmp_path):
    """Both acquirer calls in a multi-acquirer workflow share the same TURBO_T0_NS."""
    captured_t0s: list[int] = []

    def fake_spawn(binary, param, *, workflow_id, workflow_name=None, t0_ns=None):
        if t0_ns is not None:
            captured_t0s.append(t0_ns)
        return "/fake/path"

    monkeypatch.setattr(workflows, "_spawn_acquirer", fake_spawn)
    monkeypatch.setenv("TURBO_ACQUIRER_BIN", "/fake/acquirer")

    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    out_file = tmp_path / "out.txt"
    wf = {
        "command": f'echo done > "{out_file}"',
        "params": [
            {"name": "audio", "type": "audio-recording", "mode": "primary"},
            # screenshot-manual defaults to "trigger" mode — not a second primary
            {"name": "screen", "type": "screenshot-manual"},
        ],
    }

    rc = workflows.run_workflow("multi-acq-test", wf, overrides={}, registry={})
    assert rc == 0

    assert len(captured_t0s) == 2, f"Expected 2 acquirer calls, got {len(captured_t0s)}"
    assert captured_t0s[0] == captured_t0s[1], (
        f"Both acquirers must see the same TURBO_T0_NS; got {captured_t0s}"
    )
    assert captured_t0s[0] > 0, "TURBO_T0_NS must be a positive monotonic timestamp"


# ---------------------------------------------------------------------------
# T-15: record-meeting-with-screen workflow definition
# ---------------------------------------------------------------------------

def test_record_meeting_with_screen_is_in_models_toml():
    """record-meeting-with-screen must be loadable from models.toml."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    from pathlib import Path

    toml_path = Path(__file__).resolve().parent.parent / "models.toml"
    if not toml_path.exists():
        pytest.skip("models.toml not found")
    with open(toml_path, "rb") as f:
        registry = tomllib.load(f)
    wfs = workflows.load_workflows(registry)
    assert "record-meeting-with-screen" in wfs, (
        f"record-meeting-with-screen not found in workflows: {list(wfs)}"
    )
    wf = wfs["record-meeting-with-screen"]
    # validate_workflow must not raise
    workflows.validate_workflow("record-meeting-with-screen", wf)
    # Should have all three params
    param_names = {p["name"] for p in wf.get("params", [])}
    assert param_names == {"audio", "screen", "vault"}, (
        f"Unexpected params: {param_names}"
    )
    # audio = primary audio-recording
    audio_p = next(p for p in wf["params"] if p["name"] == "audio")
    assert audio_p["type"] == "audio-recording"
    assert audio_p.get("mode") == "primary"
    # screen = background screen-recording
    screen_p = next(p for p in wf["params"] if p["name"] == "screen")
    assert screen_p["type"] == "screen-recording"
    assert screen_p.get("mode") == "background"


# ---------------------------------------------------------------------------
# T-16: SIGTERM cascade — background acquirer receives SIGTERM when primary exits
# ---------------------------------------------------------------------------

def test_sigterm_cascade_background_receives_sigterm_when_primary_exits(
    monkeypatch, tmp_path
):
    """When the primary acquirer exits naturally, background acquirers get SIGTERM."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    sigterm_file = tmp_path / "sigterm_received.txt"
    screen_manifest = (
        '{"frames":[],"duration_ms":1000,"dropped_overcap":0,"start_offset_ms":0}'
    )
    manifest_file1 = tmp_path / "manifest1.json"
    manifest_file1.write_text(screen_manifest)

    # Fake acquirer:
    #   record-audio  → exits immediately, returns a fake path
    #   record-screen → loops until SIGTERM; on SIGTERM cats manifest file + writes marker
    _make_fake_acquirer(
        bin_dir,
        "turbo-acquirer",
        f"""\
case "$1" in
  record-audio)
    printf '%s' '/fake/audio.wav'
    ;;
  record-screen)
    trap 'cat "{manifest_file1}"; touch "{sigterm_file}"; exit 0' TERM INT
    while true; do sleep 0.05; done
    ;;
  *) exit 1 ;;
esac
""",
    )

    out_file = tmp_path / "out.txt"
    wf = {
        "command": f'echo done > "{out_file}"',
        "params": [
            {"name": "audio", "type": "audio-recording", "mode": "primary", "scope": "system+mic"},
            {"name": "screen", "type": "screen-recording", "mode": "background"},
        ],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    rc = workflows.run_workflow("cascade-test", wf, overrides={}, registry={})
    assert rc == 0, f"run_workflow returned {rc}"
    assert sigterm_file.exists(), (
        "Background acquirer did not receive SIGTERM; marker file missing"
    )
    assert out_file.read_text().strip() == "done", "Workflow command did not run after acquirers"


def test_sigterm_cascade_background_output_is_resolved(monkeypatch, tmp_path):
    """Background acquirer stdout is captured and available as a resolved param."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    screen_manifest = (
        '{"frames":[],"duration_ms":2000,"dropped_overcap":0,"start_offset_ms":100}'
    )
    manifest_file2 = tmp_path / "manifest2.json"
    manifest_file2.write_text(screen_manifest)
    captured_screen_file = tmp_path / "screen_value.txt"

    _make_fake_acquirer(
        bin_dir,
        "turbo-acquirer",
        f"""\
case "$1" in
  record-audio)
    printf '%s' '/fake/audio.wav'
    ;;
  record-screen)
    trap 'cat "{manifest_file2}"; exit 0' TERM INT
    while true; do sleep 0.05; done
    ;;
  *) exit 1 ;;
esac
""",
    )

    # Pass {{screen}} via an env var to avoid shell quoting issues with JSON content.
    wf = {
        "command": f'echo "$SCREEN_MANIFEST" > "{captured_screen_file}"',
        "env": {"SCREEN_MANIFEST": "{{screen}}"},
        "params": [
            {"name": "audio", "type": "audio-recording", "mode": "primary", "scope": "system+mic"},
            {"name": "screen", "type": "screen-recording", "mode": "background"},
        ],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    rc = workflows.run_workflow("cascade-output-test", wf, overrides={}, registry={})
    assert rc == 0, f"run_workflow returned {rc}"

    screen_value = captured_screen_file.read_text()
    assert '"duration_ms": 2000' in screen_value or '"duration_ms":2000' in screen_value, (
        f"Expected screen manifest in output, got: {screen_value!r}"
    )
