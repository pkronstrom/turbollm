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
