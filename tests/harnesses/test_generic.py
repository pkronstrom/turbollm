from unittest.mock import patch

import click
import pytest

from turbollm.harnesses import GenericHarness
from turbollm.harnesses.claude_code import ClaudeCodeHarness
from turbollm.harnesses.hermes import HermesHarness
from turbollm.harnesses.opencode import OpenCodeHarness


def test_launch_formats_model_id_and_port_into_cmd_and_env():
    harness = GenericHarness("goose", {
        "binary": "goose",
        "cmd": ["goose"],
        "env": {"GOOSE_MODEL": "{model_id}", "OPENAI_HOST": "http://127.0.0.1:{port}"},
    })

    captured = {}

    def fake_run(cmd, env=None):
        captured["cmd"] = cmd
        captured["env"] = env

    with patch("subprocess.run", side_effect=fake_run):
        harness.launch("org/model", 8899, {})

    assert captured["cmd"] == ["goose"]
    assert captured["env"]["GOOSE_MODEL"] == "org/model"
    assert captured["env"]["OPENAI_HOST"] == "http://127.0.0.1:8899"


def test_launch_raises_clear_error_on_literal_brace_in_cmd_template():
    # A TOML-supplied cmd value with a literal `{` (not a {model_id}/{port}
    # placeholder) previously raised a raw, unhelpful traceback from
    # str.format(). It must now fail with a clear message naming the value.
    harness = GenericHarness("broken", {
        "binary": "broken",
        "cmd": ["broken", '{"not": "a template"}'],
    })

    with patch("subprocess.run"):
        with pytest.raises(click.UsageError, match=r"not.*a template"):
            harness.launch("org/model", 8899, {})


def test_launch_raises_clear_error_on_unknown_placeholder_in_env_template():
    harness = GenericHarness("broken", {
        "binary": "broken",
        "env": {"SOME_VAR": "{unknown_placeholder}"},
    })

    with patch("subprocess.run"):
        with pytest.raises(click.UsageError, match="env.SOME_VAR"):
            harness.launch("org/model", 8899, {})

@pytest.mark.parametrize(
    "harness_class",
    [ClaudeCodeHarness, OpenCodeHarness, HermesHarness],
)
def test_custom_harnesses_reject_headless_mode(harness_class):
    harness = harness_class({"binary": "unused"})

    with pytest.raises(NotImplementedError) as exc:
        harness.headless("org/model", 8899, {}, "hello")

    assert str(exc.value) == f"{harness.name} harness does not support headless mode"
