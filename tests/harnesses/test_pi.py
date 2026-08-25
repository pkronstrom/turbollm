import json
from unittest.mock import patch

from turbollm.harnesses.pi import PiHarness


def test_pi_harness_launches_mlx_vlm_gemma_with_thinking_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    harness = PiHarness({"binary": "pi"})
    model = {
        "backend": "mlx-vlm",
        "name": "Gemma 4 26B-A4B IT 6bit + MTP",
        "hf_repo": "mlx-community/gemma-4-26b-a4b-it-6bit",
        "can_reason": True,
        "pi": {
            "thinking": "off",
            "thinking_format": "qwen",
        },
    }
    model_id = "/tmp/models/mlx-community/gemma-4-26b-a4b-it-6bit"

    with patch("subprocess.run") as run:
        harness.launch(model_id, 8899, model)

    models_json = json.loads((tmp_path / "models.json").read_text())
    entry = models_json["providers"]["turbo"]["models"][0]

    assert entry["id"] == model_id
    assert entry["compat"]["thinkingFormat"] == "qwen"
    run.assert_called_once_with(["pi", "--model", f"turbo/{model_id}:off"])


def test_pi_harness_applies_gemma_tool_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    harness = PiHarness({"binary": "pi"})
    model = {
        "backend": "vllm-mlx",
        "name": "Gemma 4 26B-A4B IT UD MLX 4bit",
        "hf_repo": "unsloth/gemma-4-26b-a4b-it-UD-MLX-4bit",
        "can_reason": True,
        "pi": {
            "reasoning": False,
            "thinking": "off",
            "compat": {
                "thinkingFormat": "qwen-chat-template",
                "requiresToolResultName": True,
                "requiresAssistantAfterToolResult": True,
            },
        },
    }

    with patch("subprocess.run") as run:
        harness.launch(model["hf_repo"], 8899, model)

    models_json = json.loads((tmp_path / "models.json").read_text())
    entry = models_json["providers"]["turbo"]["models"][0]

    assert entry["reasoning"] is False
    assert entry["compat"] == {
        "thinkingFormat": "qwen-chat-template",
        "requiresToolResultName": True,
        "requiresAssistantAfterToolResult": True,
    }
    run.assert_called_once_with(["pi", "--model", f"turbo/{model['hf_repo']}:off"])


def test_pi_harness_derives_context_from_effective_context_not_opencode(tmp_path, monkeypatch):
    # No pi.context_window configured, but a large opencode.context_length is
    # present — pi must NOT borrow it (cross-harness borrowing, item BUG-1/18).
    # It should derive purely from registry.effective_context() instead.
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    harness = PiHarness({"binary": "pi"})
    model = {
        "name": "Qwen3.6 27B UD 6bit",
        "hf_repo": "unsloth/Qwen3.6-27B-UD-MLX-6bit",
        "can_reason": True,
        "context_default": 65536,
        "opencode": {
            "context_length": 999999,
        },
    }

    with patch("subprocess.run"):
        harness.launch(model["hf_repo"], 8899, model)

    models_json = json.loads((tmp_path / "models.json").read_text())
    entry = models_json["providers"]["turbo"]["models"][0]

    assert entry["contextWindow"] == 65536


def test_pi_harness_recovers_from_corrupt_models_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    models_json_path = tmp_path / "models.json"
    models_json_path.write_text("{not valid json")

    harness = PiHarness({"binary": "pi"})
    model = {
        "name": "Qwen3.6 27B UD 6bit",
        "hf_repo": "unsloth/Qwen3.6-27B-UD-MLX-6bit",
        "can_reason": True,
    }

    with patch("subprocess.run"):
        harness.launch(model["hf_repo"], 8899, model)  # must not raise

    # Original corrupt content backed up, fresh config written.
    backup = tmp_path / "models.json.bak"
    assert backup.read_text() == "{not valid json"
    models_json = json.loads(models_json_path.read_text())
    assert "turbo" in models_json["providers"]
    assert "corrupt" in capsys.readouterr().err


def test_pi_harness_uses_pi_context_and_max_token_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    harness = PiHarness({"binary": "pi"})
    model = {
        "name": "Qwen3.6 27B UD 6bit",
        "hf_repo": "unsloth/Qwen3.6-27B-UD-MLX-6bit",
        "can_reason": True,
        "opencode": {
            "context_length": 65536,
            "output_length": 16384,
        },
        "pi": {
            "context_window": 65536,
            "max_tokens": 8192,
            "thinking": "low",
        },
    }

    with patch("subprocess.run") as run:
        harness.launch(model["hf_repo"], 8899, model)

    models_json = json.loads((tmp_path / "models.json").read_text())
    entry = models_json["providers"]["turbo"]["models"][0]

    assert entry["contextWindow"] == 65536
    assert entry["maxTokens"] == 8192
    run.assert_called_once_with(["pi", "--model", f"turbo/{model['hf_repo']}:low"])


def test_pi_qwen38_schema_preserves_quality_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    harness = PiHarness({"binary": "pi"})
    model = {
        "name": "Qwen3.8 27B Q8 + MTP",
        "hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "can_reason": True,
        "input": ["text", "image"],
        "context_default": 262144,
        "sampling": "qwen38-thinking",
        "pi": {
            "max_tokens": 32768,
            "thinking": "medium",
            "thinking_levels": ["low", "medium", "xhigh"],
            "compat": {
                "thinkingFormat": "chat-template",
                "chatTemplateKwargs": {
                    "enable_thinking": {"$var": "thinking.enabled"},
                    "preserve_thinking": True,
                    "reasoning_effort": {"$var": "thinking.effort"},
                },
            },
        },
    }
    sampling = {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repeat_penalty": 1.0,
    }

    with patch(
        "turbollm.harnesses.pi.effective_sampling",
        return_value=sampling,
        create=True,
    ), patch("subprocess.run") as run:
        harness.launch(model["hf_repo"], 8899, model)

    entry = json.loads(
        (tmp_path / "models.json").read_text()
    )["providers"]["turbo"]["models"][0]
    assert entry["input"] == ["text", "image"]
    assert entry["contextWindow"] == 262144
    assert entry["maxTokens"] == 32768
    assert entry["samplingParams"] == sampling
    assert entry["thinkingLevelMap"] == {
        "off": None,
        "minimal": None,
        "low": "low",
        "medium": "medium",
        "high": None,
        "xhigh": "xhigh",
        "max": None,
    }
    assert entry["compat"]["thinkingFormat"] == "chat-template"
    assert entry["compat"]["chatTemplateKwargs"]["preserve_thinking"] is True
    run.assert_called_once_with(
        ["pi", "--model", f"turbo/{model['hf_repo']}:medium"]
    )
