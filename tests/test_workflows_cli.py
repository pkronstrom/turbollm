import json
from unittest.mock import patch

from click.testing import CliRunner

from turbollm import cli as turbo_cli


def test_workflows_list_json_emits_array():
    fake_registry = {
        "workflows": {
            "transcribe-file": {
                "description": "Transcribe a file",
                "command": 'turbo transcribe "{{file}}"',
                "params": [{"name": "file", "type": "file"}],
            },
            "another": {
                "description": "Another wf",
                "script": "summarize",
                "args": ["{{x}}"],
                "params": [{"name": "x", "type": "string", "default": "hi"}],
            },
        }
    }
    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value=fake_registry):
        result = runner.invoke(turbo_cli.cli, ["workflows", "list", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    names = {wf["name"] for wf in data}
    assert names == {"transcribe-file", "another"}
    transcribe = next(wf for wf in data if wf["name"] == "transcribe-file")
    assert transcribe["params"][0]["name"] == "file"


def test_workflows_list_json_empty_when_no_workflows():
    runner = CliRunner()
    with patch("turbollm.cli.load_registry", return_value={}):
        result = runner.invoke(turbo_cli.cli, ["workflows", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_activities_list_json_returns_current_entries(monkeypatch, tmp_path):
    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")
    activity.start_activity(kind="workflow", label="A")
    activity.start_activity(kind="server", label="B")

    runner = CliRunner()
    result = runner.invoke(turbo_cli.cli, ["activities", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    labels = {it["label"] for it in data}
    assert labels == {"A", "B"}


def test_activities_list_json_empty_when_no_state(monkeypatch, tmp_path):
    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")
    runner = CliRunner()
    result = runner.invoke(turbo_cli.cli, ["activities", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_hud_status_set_writes_activity_and_prints_id(monkeypatch, tmp_path):
    from turbollm import activity

    state_dir = tmp_path / "state"
    monkeypatch.setattr(activity, "STATE_DIR", state_dir)

    runner = CliRunner()
    result = runner.invoke(
        turbo_cli.cli,
        ["hud", "status", "set", "--label", "Building", "--icon", "hammer", "--color", "blue"],
    )
    assert result.exit_code == 0, result.output
    aid = result.output.strip()
    assert aid

    files = list(state_dir.glob("activity-*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["id"] == aid
    assert data["kind"] == "external"
    assert data["label"] == "Building"
    assert data["icon"] == "hammer"
    assert data["color"] == "blue"


def test_hud_status_update_merges_fields(monkeypatch, tmp_path):
    from turbollm import activity

    monkeypatch.setattr(activity, "STATE_DIR", tmp_path / "state")
    runner = CliRunner()

    result = runner.invoke(turbo_cli.cli, ["hud", "status", "set", "--label", "Phase 1"])
    aid = result.output.strip()

    upd = runner.invoke(
        turbo_cli.cli, ["hud", "status", "update", aid, "--label", "Phase 2", "--phase", "step-2"]
    )
    assert upd.exit_code == 0

    data = json.loads(activity.activity_path(aid).read_text())
    assert data["label"] == "Phase 2"
    assert data["phase"] == "step-2"


def test_hud_status_clear_removes_file(monkeypatch, tmp_path):
    from turbollm import activity

    state_dir = tmp_path / "state"
    monkeypatch.setattr(activity, "STATE_DIR", state_dir)
    runner = CliRunner()

    result = runner.invoke(turbo_cli.cli, ["hud", "status", "set", "--label", "x"])
    aid = result.output.strip()
    assert state_dir.glob("activity-*.json")

    clr = runner.invoke(turbo_cli.cli, ["hud", "status", "clear", aid])
    assert clr.exit_code == 0
    assert list(state_dir.glob("activity-*.json")) == []
