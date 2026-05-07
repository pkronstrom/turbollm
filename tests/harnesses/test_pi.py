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
    model_id = "/Users/pkronstrom/.models/mlx-community/gemma-4-26b-a4b-it-6bit"

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
