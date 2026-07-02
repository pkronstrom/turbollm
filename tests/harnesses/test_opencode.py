import json
import pathlib
from unittest.mock import patch

from turbollm.harnesses.opencode import OpenCodeHarness


def _patch_home(monkeypatch, home):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))


def _launch_and_capture_config(harness, model_id, port, model):
    captured_env = {}

    def fake_run(cmd, env=None):
        captured_env.update(env or {})

    with patch("subprocess.run", side_effect=fake_run):
        harness.launch(model_id, port, model)

    return json.loads(captured_env["OPENCODE_CONFIG_CONTENT"])


def test_launch_derives_context_from_effective_context(tmp_path, monkeypatch):
    # No opencode.context_length configured — must derive from
    # registry.effective_context() (server.max_tokens / context_default),
    # not a fixed 32768 that ignores what the picker/model actually chose.
    _patch_home(monkeypatch, tmp_path)
    harness = OpenCodeHarness({"binary": "opencode"})
    model = {
        "name": "Test Model",
        "hf_repo": "org/test-model",
        "tool_use": True,
        "can_reason": False,
        "context_default": 98304,
    }

    config = _launch_and_capture_config(harness, "org/test-model", 8899, model)

    limit = config["provider"]["turbo"]["models"]["org/test-model"]["limit"]
    assert limit["context"] == 98304


def test_launch_selects_turbo_model_when_existing_config_present(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    oc_dir = tmp_path / ".config" / "opencode"
    oc_dir.mkdir(parents=True)
    (oc_dir / "opencode.json").write_text(json.dumps({
        "provider": {"other": {"npm": "@ai-sdk/other"}},
        "model": {"chat": "other/some-remote-model"},
    }))

    harness = OpenCodeHarness({"binary": "opencode"})
    model = {"name": "Test Model", "hf_repo": "org/test-model", "can_reason": False}

    config = _launch_and_capture_config(harness, "org/test-model", 8899, model)

    # The turbo-served model must actually be selected, not the user's
    # previous (likely remote) default that was silently kept before.
    assert config["model"]["chat"] == "turbo/org/test-model"
    # The user's other providers must still be preserved.
    assert "other" in config["provider"]


def test_launch_falls_back_to_turbo_only_config_on_corrupt_json(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    oc_dir = tmp_path / ".config" / "opencode"
    oc_dir.mkdir(parents=True)
    (oc_dir / "opencode.json").write_text("{not valid json")

    harness = OpenCodeHarness({"binary": "opencode"})
    model = {"name": "Test Model", "hf_repo": "org/test-model", "can_reason": False}

    config = _launch_and_capture_config(harness, "org/test-model", 8899, model)  # must not raise

    assert config["model"]["chat"] == "turbo/org/test-model"
