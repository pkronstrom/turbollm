import shlex as _shlex

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


# ---------- BUG-6: expand_template(quote=True) shell-quotes substituted values ----------


def test_expand_template_quote_false_is_unchanged_by_default():
    """Default behavior (quote=False) is byte-for-byte unchanged — used by
    script-reference workflows' positional args and env values, which are
    never spliced into a shell string so quoting would be wrong there."""
    out = workflows.expand_template("hi {{name}}", params={"name": "world"})
    assert out == "hi world"


def test_expand_template_quote_true_leaves_safe_values_unquoted():
    """shlex.quote is a no-op for values that need no quoting — so existing
    inline-command templates with simple values are unaffected."""
    out = workflows.expand_template("echo {{name}}", params={"name": "hello-world"}, quote=True)
    assert out == "echo hello-world"


def test_expand_template_quote_true_quotes_value_with_spaces():
    out = workflows.expand_template(
        "turbo transcribe {{file}}", params={"file": "/tmp/my recording.wav"}, quote=True
    )
    assert out == "turbo transcribe '/tmp/my recording.wav'"


def test_expand_template_quote_true_neutralizes_shell_injection():
    """A `--param` value containing shell metacharacters must not execute as
    a second command when the expanded template is handed to `sh -c`."""
    malicious = "x; touch /tmp/should-not-exist-from-workflow-test"
    out = workflows.expand_template("echo {{val}}", params={"val": malicious}, quote=True)
    # The whole malicious string must be a single shell-quoted token, not
    # unquoted text that a shell would split on `;`.
    assert out == f"echo {_shlex.quote(malicious)}"
    assert ";" not in out.split("'", 1)[0]  # semicolon only appears inside the quotes


def test_run_workflow_inline_command_quotes_param_with_space(tmp_path):
    """End-to-end: a recorded path containing a space must survive intact
    through an inline `command` workflow instead of being word-split.

    Note the template below does *not* manually wrap `{{audio}}` in quotes —
    `expand_template(quote=True)` (used for all inline `command` workflows,
    see run_workflow) already shell-quotes the substituted value. Manually
    quoting on top of that (`"{{audio}}"`) would nest quoting and corrupt the
    value; workflow authors should stop hand-quoting params in `command`
    strings now that this is automatic.
    """
    out_file = tmp_path / "out.txt"
    audio_dir = tmp_path / "my recordings"
    audio_dir.mkdir()
    audio_path = audio_dir / "clip.wav"
    audio_path.write_bytes(b"RIFF")

    wf = {
        "command": f'echo {{{{audio}}}} > "{out_file}"',
        "params": [{"name": "audio", "type": "audio-recording", "mode": "primary"}],
    }
    rc = workflows.run_workflow("x", wf, overrides={"audio": str(audio_path)}, registry={})
    assert rc == 0
    assert out_file.read_text().strip() == str(audio_path)


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


# ---------- auto templates referencing background-acquired params ----------


def test_auto_template_referencing_background_param_raises_clear_error(monkeypatch, tmp_path):
    """An `auto` template referencing a background-mode acquired param must
    fail with a dedicated, actionable error — not the generic 'undefined
    param' message expand_template raises for genuine typos."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_acquirer(bin_dir, "turbo-acquirer", """\
case "$1" in
  record-screen) sleep 5; printf 'unused' ;;
  *) exit 1 ;;
esac
""")
    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))

    params = [
        {"name": "screen", "type": "screen-recording", "mode": "background"},
        {"name": "title", "type": "string", "auto": "{{screen}}-title"},
    ]
    with pytest.raises(workflows.WorkflowError, match="acquired in the background"):
        workflows.resolve_params(
            params, overrides={}, acquirer_bin=workflows.find_acquirer_bin()
        )


def test_auto_template_referencing_resolved_param_still_works():
    """Sanity: `auto` referencing an earlier *non*-background param is unaffected."""
    params = [
        {"name": "who", "type": "string", "default": "world"},
        {"name": "greeting", "type": "string", "auto": "hi {{who}}"},
    ]
    out = workflows.resolve_params(params, overrides={})
    assert out["greeting"] == "hi world"


def test_auto_template_referencing_overridden_background_param_is_fine():
    """If the background param was supplied via --param (so it's already in
    `resolved`), the auto template can reference it without error."""
    params = [
        {"name": "screen", "type": "screen-recording", "mode": "background"},
        {"name": "title", "type": "string", "auto": "{{screen}}-title"},
    ]
    out = workflows.resolve_params(params, overrides={"screen": "/tmp/manifest.json"})
    assert out["title"] == "/tmp/manifest.json-title"


# ---------- `turbo workflows config` stickies consulted by resolve_params ----------


def test_resolve_params_reads_workflow_config_sticky(monkeypatch):
    def fake_sticky(workflow_name, param_name):
        assert workflow_name == "record-to-obsidian"
        assert param_name == "vault"
        return "/sticky/vault"

    monkeypatch.setattr(workflows, "_read_workflow_config_sticky", fake_sticky)
    params = [{"name": "vault", "type": "directory", "default": "/default/vault"}]
    out = workflows.resolve_params(params, overrides={}, workflow_name="record-to-obsidian")
    assert out == {"vault": "/sticky/vault"}


def test_resolve_params_override_beats_sticky(monkeypatch):
    monkeypatch.setattr(workflows, "_read_workflow_config_sticky", lambda *a: "/sticky/vault")
    params = [{"name": "vault", "type": "directory", "default": "/default/vault"}]
    out = workflows.resolve_params(
        params, overrides={"vault": "/cli/vault"}, workflow_name="wf"
    )
    assert out == {"vault": "/cli/vault"}


def test_resolve_params_sticky_beats_default():
    params = [{"name": "vault", "type": "directory", "default": "/default/vault"}]

    def sticky(workflow_name, param_name):
        return "/sticky/vault"

    import unittest.mock as _mock

    with _mock.patch.object(workflows, "_read_workflow_config_sticky", side_effect=sticky):
        out = workflows.resolve_params(params, overrides={}, workflow_name="wf")
    assert out == {"vault": "/sticky/vault"}


def test_resolve_params_no_sticky_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(workflows, "_read_workflow_config_sticky", lambda *a: None)
    params = [{"name": "vault", "type": "directory", "default": "/default/vault"}]
    out = workflows.resolve_params(params, overrides={}, workflow_name="wf")
    assert out == {"vault": "/default/vault"}


def test_resolve_params_no_sticky_lookup_without_workflow_name(monkeypatch):
    """No workflow_name → sticky lookup must not be attempted at all (this is
    also what keeps every other resolve_params test in this file, which don't
    pass workflow_name, from shelling out to /usr/bin/defaults)."""
    def fail(*a, **kw):
        raise AssertionError("_read_workflow_config_sticky must not be called")

    monkeypatch.setattr(workflows, "_read_workflow_config_sticky", fail)
    params = [{"name": "vault", "type": "directory", "default": "/default/vault"}]
    out = workflows.resolve_params(params, overrides={})
    assert out == {"vault": "/default/vault"}


def test_read_workflow_config_sticky_returns_none_when_defaults_missing(monkeypatch):
    """Non-macOS / no /usr/bin/defaults degrades to 'no sticky', not a crash."""
    def fail_run(*a, **kw):
        raise FileNotFoundError()

    monkeypatch.setattr(workflows._subprocess, "run", fail_run)
    assert workflows._read_workflow_config_sticky("wf", "param") is None


def test_read_workflow_config_sticky_uses_matching_key_convention(monkeypatch):
    """Key format must match cli.py's `_defaults_key` convention exactly, so
    a value set via `turbo workflows config <wf> <param>=<value>` is found."""
    captured = []

    def fake_run(argv, **kw):
        captured.append(argv)

        class _R:
            returncode = 0
            stdout = "hello\n"

        return _R()

    monkeypatch.setattr(workflows._subprocess, "run", fake_run)
    value = workflows._read_workflow_config_sticky("record-to-obsidian", "vault")
    assert value == "hello"
    assert captured == [
        ["/usr/bin/defaults", "read", "com.turbollm.hud", "workflow.record-to-obsidian.param.vault"]
    ]


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


# ---------- T-fix-2: screen-recording argv reads HUD scope/region stickies ----------


def test_acquirer_argv_screen_recording_uses_hud_scope_override(monkeypatch):
    """HUD scope sticky wins over the static TOML scope for screen-recording."""

    captured = []

    def fake_run(argv, *_, **__):
        captured.append(argv)

        class _R:
            returncode = 0
            stdout = "display\n"

        return _R()

    monkeypatch.setattr(workflows._subprocess, "run", fake_run)

    param = {
        "name": "screen",
        "type": "screen-recording",
        "scope": "window",  # workflow default, should be overridden
    }
    argv = workflows._acquirer_argv(
        "/usr/local/bin/turbo-acquirer",
        param,
        workflow_name="record-meeting-with-screen",
    )

    assert "--scope" in argv
    assert argv[argv.index("--scope") + 1] == "display"
    # The HUD suite+key convention must have been queried.
    keys_read = [
        a[3]
        for a in captured
        if a[:3] == ["/usr/bin/defaults", "read", "com.turbollm.hud"]
    ]
    assert "record-meeting-with-screen.screen.scope" in keys_read


def test_acquirer_argv_screen_recording_region_scope_includes_region(monkeypatch):
    """When scope==region, the HUD region sticky must be passed as --region."""

    def fake_run(argv, *_, **__):
        class _R:
            returncode = 0
            stdout = ""

        if argv[:3] == ["/usr/bin/defaults", "read", "com.turbollm.hud"]:
            if argv[3].endswith(".scope"):
                _R.stdout = "region\n"
            elif argv[3].endswith(".region"):
                _R.stdout = "100,200,800,600\n"
        return _R()

    monkeypatch.setattr(workflows._subprocess, "run", fake_run)

    param = {"name": "screen", "type": "screen-recording"}
    argv = workflows._acquirer_argv(
        "/usr/local/bin/turbo-acquirer",
        param,
        workflow_name="record-meeting-with-screen",
    )

    assert "--scope" in argv
    assert argv[argv.index("--scope") + 1] == "region"
    assert "--region" in argv
    assert argv[argv.index("--region") + 1] == "100,200,800,600"


def test_acquirer_argv_screen_recording_no_hud_lookup_without_workflow_name(monkeypatch):
    """Calling without workflow_name must not shell out to defaults (screen-recording)."""

    def fail_run(*_, **__):
        raise AssertionError("defaults read must not be called without workflow_name")

    monkeypatch.setattr(workflows._subprocess, "run", fail_run)

    param = {"name": "screen", "type": "screen-recording", "scope": "display"}
    argv = workflows._acquirer_argv("/x/turbo-acquirer", param)
    assert "--scope" in argv
    assert argv[argv.index("--scope") + 1] == "display"
    # No --region without workflow_name even if scope were region.


def test_acquirer_argv_screen_recording_falls_back_to_workflow_scope(monkeypatch):
    """No HUD scope sticky → static workflow scope is used for screen-recording."""

    def fake_run(*_, **__):
        class _R:
            returncode = 1
            stdout = ""

        return _R()

    monkeypatch.setattr(workflows._subprocess, "run", fake_run)

    param = {"name": "screen", "type": "screen-recording", "scope": "window"}
    argv = workflows._acquirer_argv(
        "/x/turbo-acquirer",
        param,
        workflow_name="some-workflow",
    )
    assert "--scope" in argv
    assert argv[argv.index("--scope") + 1] == "window"


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


def test_primary_failure_terminates_background_acquirer(monkeypatch, tmp_path):
    """When the primary acquirer fails, background acquirers are still terminated.

    Regression test for T-fix-1: a WorkflowError from the primary must not
    leak background child processes.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    sigterm_file = tmp_path / "sigterm_received.txt"

    # Fake acquirer:
    #   record-audio  → exits non-zero so resolve_params raises WorkflowError
    #   record-screen → loops until SIGTERM; writes a marker file on SIGTERM
    _make_fake_acquirer(
        bin_dir,
        "turbo-acquirer",
        f"""\
case "$1" in
  record-audio)
    exit 2
    ;;
  record-screen)
    trap 'touch "{sigterm_file}"; exit 0' TERM INT
    while true; do sleep 0.05; done
    ;;
  *) exit 1 ;;
esac
""",
    )

    wf = {
        "command": "echo done",
        "params": [
            {"name": "audio", "type": "audio-recording", "mode": "primary", "scope": "system+mic"},
            {"name": "screen", "type": "screen-recording", "mode": "background"},
        ],
    }

    monkeypatch.setenv("TURBO_ACQUIRER_BIN", str(bin_dir / "turbo-acquirer"))
    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    with pytest.raises(workflows.WorkflowError):
        workflows.run_workflow("primary-fail-test", wf, overrides={}, registry={})

    # Give the background process a moment to handle SIGTERM.
    import time
    time.sleep(0.2)

    assert sigterm_file.exists(), (
        "Background acquirer was not terminated after primary failure; "
        "leaked child process detected"
    )


# ── Per-workflow lock (concurrency guard) ─────────────────────────────────────


def test_workflow_lock_refuses_concurrent_run(tmp_path, monkeypatch):
    """A second concurrent call to run_workflow with the same name must raise WorkflowError."""
    import fcntl
    from turbollm import activity, workflows

    # Redirect HOME so the lockfile lands in tmp_path, not the user's real ~.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    # Externally hold the workflow's lock.
    lock_path = workflows._workflow_lock_path("locked-wf")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        wf = {"command": "true"}
        with pytest.raises(workflows.WorkflowError, match="already running"):
            workflows.run_workflow("locked-wf", wf, overrides={}, registry={})
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_workflow_lock_losing_attempt_does_not_erase_holder_stamp(tmp_path, monkeypatch):
    """Regression for BUG-4: a second `run_workflow` racing a live holder must
    not truncate the lock file before the flock attempt — that erased the
    running instance's PID stamp, leaving `workflow_status`/`stop_workflow`
    unable to find it even though it's still running."""
    import fcntl
    from turbollm import activity, workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    lock_path = workflows._workflow_lock_path("stamped-holder")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    holder.write("424242")
    holder.flush()
    try:
        wf = {"command": "true"}
        with pytest.raises(workflows.WorkflowError, match="already running"):
            workflows.run_workflow("stamped-holder", wf, overrides={}, registry={})

        # The holder's PID stamp must still be intact on disk.
        assert lock_path.read_text().strip() == "424242"
        info = workflows.workflow_status("stamped-holder")
        assert info == {"state": "running", "pid": 424242}
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_workflow_lock_released_after_success(tmp_path, monkeypatch):
    """After a normal run, the lock is releasable — sequential runs work."""
    import fcntl
    from turbollm import activity, workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    wf = {"command": "true"}
    assert workflows.run_workflow("seq-wf", wf, overrides={}, registry={}) == 0

    # Lock file persists (it's just a file) — what matters is the kernel
    # lock is released, so a fresh acquire on the same path succeeds.
    lock_path = workflows._workflow_lock_path("seq-wf")
    with open(lock_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def test_workflow_lock_path_sanitizes_slashes(tmp_path, monkeypatch):
    """A workflow name with a slash must not create a nested directory."""
    from turbollm import workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    path = workflows._workflow_lock_path("foo/bar")
    assert "/" not in path.name
    assert path.name == "foo_bar.lock"


# ── workflow_status / stop_workflow ───────────────────────────────────────────


def test_workflow_status_idle_when_no_lock(tmp_path, monkeypatch):
    """No lock file → idle, no pid."""
    from turbollm import workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    assert workflows.workflow_status("never-ran") == {"state": "idle", "pid": None}


def test_workflow_status_idle_when_lock_unheld(tmp_path, monkeypatch):
    """Stale lock file (kernel lock not held) → idle, no pid."""
    import os

    from turbollm import workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    path = workflows._workflow_lock_path("stale-wf")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write a PID stamp but don't hold the flock — simulates a crashed holder
    # whose file lingers on disk.
    path.write_text(str(os.getpid()))
    assert workflows.workflow_status("stale-wf") == {"state": "idle", "pid": None}


def test_workflow_status_running_reports_holder_pid(tmp_path, monkeypatch):
    """Externally held lock with a PID stamp → running, correct pid."""
    import fcntl
    import os

    from turbollm import workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    path = workflows._workflow_lock_path("held-wf")
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    holder.write(str(os.getpid()))
    holder.flush()
    try:
        info = workflows.workflow_status("held-wf")
        assert info == {"state": "running", "pid": os.getpid()}
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_stop_workflow_returns_none_when_idle(tmp_path, monkeypatch):
    """stop_workflow on an idle workflow is a no-op returning None."""
    from turbollm import workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    assert workflows.stop_workflow("never-ran") is None


def test_stop_workflow_signals_holder_directly(tmp_path, monkeypatch):
    """stop_workflow signals the holder process directly, tearing down a real child."""
    import os
    import signal
    import subprocess
    import sys
    import time

    from turbollm import workflows

    monkeypatch.setenv("HOME", str(tmp_path))

    # Spawn a real child that holds the lock + sleeps in its own session. We
    # use a python -c one-liner so the test is hermetic.
    lock_path = workflows._workflow_lock_path("real-holder")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "import fcntl, os, sys, time;"
        f"f=open({str(lock_path)!r},'w');"
        "fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB);"
        "f.write(str(os.getpid())); f.flush();"
        "time.sleep(30)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
    )
    try:
        # Wait until the holder has actually stamped its PID.
        for _ in range(50):
            if lock_path.exists() and lock_path.read_text().strip().isdigit():
                break
            time.sleep(0.05)
        info = workflows.workflow_status("real-holder")
        assert info["state"] == "running"
        assert info["pid"] == proc.pid

        signaled = workflows.stop_workflow("real-holder", sig=signal.SIGTERM)
        assert signaled == proc.pid

        # Confirm the holder actually exited.
        proc.wait(timeout=5)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)


def test_stop_workflow_uses_kill_not_killpg(tmp_path, monkeypatch):
    """Regression for BUG-17: stop_workflow must signal the holder pid directly
    via os.kill, never the process group via killpg — killpg on a launcher
    that wasn't spawned in its own group can hit a shared HUD/Raycast host."""
    import fcntl
    import os
    import signal

    from turbollm import workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    lock_path = workflows._workflow_lock_path("kill-not-killpg")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    holder.write(str(os.getpid()))
    holder.flush()

    calls = []
    monkeypatch.setattr(workflows._os, "kill", lambda pid, sig: calls.append((pid, sig)))

    def _fail_killpg(*a, **kw):
        raise AssertionError("stop_workflow must not call killpg")

    monkeypatch.setattr(workflows._os, "killpg", _fail_killpg)
    try:
        signaled = workflows.stop_workflow("kill-not-killpg", sig=signal.SIGTERM)
        assert signaled == os.getpid()
        assert calls == [(os.getpid(), signal.SIGTERM)]
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_run_workflow_stamps_holder_pid(tmp_path, monkeypatch):
    """While run_workflow is executing, the lock file should contain its PID."""
    import os

    from turbollm import activity, workflows

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")

    # Use a small inline script that lets us peek at the lock file while the
    # workflow is "running". The command reads the lock file's contents and
    # compares to its own PPID (the python parent that holds the flock).
    wf = {
        "command": (
            f"cat {workflows._workflow_lock_path('stamp-wf')!s} > "
            f"{tmp_path / 'observed.txt'!s}"
        ),
    }
    rc = workflows.run_workflow("stamp-wf", wf, overrides={}, registry={})
    assert rc == 0
    observed = (tmp_path / "observed.txt").read_text().strip()
    assert observed.isdigit()
    assert int(observed) == os.getpid()
