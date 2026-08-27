from unittest.mock import patch

import yaml

from turbollm.harnesses.omp_lean import (
    HEADLESS_TOOL_FLAGS,
    LEAN_PROMPT_PATH,
    LEAN_TOOL_FLAGS,
    OmpLeanHarness,
)


def qwen38_model() -> dict:
    return {
        "name": "Qwen3.8 27B Q8 + MTP",
        "hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "can_reason": True,
        "input": ["text", "image"],
        "context_default": 262144,
        "pi": {
            "max_tokens": 32768,
            "thinking": "medium",
            "thinking_levels": ["low", "medium", "xhigh"],
        },
    }


def test_lean_harness_writes_provider_only_to_its_isolated_agent_dir(tmp_path, monkeypatch):
    normal_dir = tmp_path / "normal"
    lean_dir = tmp_path / "lean"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(normal_dir))
    harness = OmpLeanHarness({"binary": "omp", "agent_dir": str(lean_dir)})
    model = qwen38_model()

    with patch("subprocess.run") as run:
        harness.launch(model["hf_repo"], 8899, model)

    assert (lean_dir / "models.yml").exists()
    assert not (normal_dir / "models.yml").exists()
    assert yaml.safe_load((lean_dir / "config.yml").read_text()) == {"setupVersion": 2}
    assert not (normal_dir / "config.yml").exists()
    entry = yaml.safe_load((lean_dir / "models.yml").read_text())["providers"]["turbo"]["models"][0]
    assert entry["compat"] == {
        "supportsDeveloperRole": False,
        "thinkingFormat": "qwen-chat-template",
        "qwenTemplateReasoningEffort": True,
        "reasoningContentField": "reasoning_content",
    }

    argv = run.call_args.args[0]
    env = run.call_args.kwargs["env"]
    assert env["PI_CODING_AGENT_DIR"] == str(lean_dir)
    assert argv == [
        "omp",
        "--no-extensions",
        "--no-rules",
        *LEAN_TOOL_FLAGS,
        "--skills=vault-mcp,vault-skills",
        "--system-prompt",
        str(LEAN_PROMPT_PATH),
        "--model",
        f"turbo/{model['hf_repo']}:medium",
    ]


def test_lean_harness_headless_keeps_the_lean_flags_and_prompt(tmp_path):
    lean_dir = tmp_path / "lean"
    harness = OmpLeanHarness({"binary": "omp", "agent_dir": str(lean_dir)})
    model = qwen38_model()

    with patch("subprocess.run") as run:
        run.return_value.returncode = 7
        result = harness.headless(model["hf_repo"], 8899, model, "hello")

    assert result == 7
    argv = run.call_args.args[0]
    assert argv == [
        "omp",
        "--no-extensions",
        "--no-rules",
        *HEADLESS_TOOL_FLAGS,
        "--skills=vault-mcp,vault-skills",
        "--system-prompt",
        str(LEAN_PROMPT_PATH),
        "-p",
        "--model",
        f"turbo/{model['hf_repo']}:medium",
        "hello",
    ]
    assert run.call_args.kwargs["env"]["PI_CODING_AGENT_DIR"] == str(lean_dir)
