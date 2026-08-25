from unittest.mock import patch

import yaml

from turbollm.harnesses.omp import OmpHarness


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
            "compat": {
                "thinkingFormat": "chat-template",
                "chatTemplateKwargs": {
                    "enable_thinking": {"$var": "thinking.enabled"},
                    "reasoning_effort": {"$var": "thinking.effort"},
                },
            },
        },
    }


def test_omp_harness_preserves_existing_providers_and_writes_qwen38(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    models_path = tmp_path / "models.yml"
    models_path.write_text(
        "providers:\n"
        "  existing:\n"
        "    baseUrl: https://example.invalid/v1\n"
        "    api: openai-completions\n"
        "    auth: none\n"
        "    models:\n"
        "      - id: keep-me\n"
    )
    harness = OmpHarness({"binary": "omp"})
    model = qwen38_model()

    with patch("subprocess.run") as run:
        harness.launch(model["hf_repo"], 8899, model)

    data = yaml.safe_load(models_path.read_text())
    assert data["providers"]["existing"]["models"][0]["id"] == "keep-me"
    provider = data["providers"]["turbo"]
    entry = provider["models"][0]
    assert provider["baseUrl"] == "http://127.0.0.1:8899/v1"
    assert provider["api"] == "openai-completions"
    assert provider["auth"] == "none"
    assert entry["id"] == model["hf_repo"]
    assert entry["input"] == ["text", "image"]
    assert entry["contextWindow"] == 262144
    assert entry["maxTokens"] == 32768
    assert entry["thinking"] == {
        "mode": "effort",
        "efforts": ["low", "medium", "xhigh"],
    }
    assert entry["compat"] == {
        "supportsDeveloperRole": False,
        "thinkingFormat": "qwen-chat-template",
        "qwenTemplateReasoningEffort": True,
        "reasoningContentField": "reasoning_content",
    }
    run.assert_called_once_with(
        ["omp", "--model", f"turbo/{model['hf_repo']}:medium"]
    )


def test_omp_harness_uses_supported_agent_dir_and_supports_headless(tmp_path, monkeypatch):
    omp_dir = tmp_path / "omp"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(omp_dir))
    harness = OmpHarness({"binary": "omp"})
    model = qwen38_model()

    with patch("subprocess.run") as run:
        run.return_value.returncode = 7
        result = harness.headless(model["hf_repo"], 8899, model, "hello")

    assert result == 7
    assert (omp_dir / "models.yml").exists()
    run.assert_called_once_with([
        "omp",
        "-p",
        "--model",
        f"turbo/{model['hf_repo']}:medium",
        "hello",
    ])


def test_omp_harness_backs_up_invalid_yaml(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    models_path = tmp_path / "models.yml"
    models_path.write_text("providers: [not: valid")
    harness = OmpHarness({"binary": "omp"})
    model = qwen38_model()

    with patch("subprocess.run"):
        harness.launch(model["hf_repo"], 8899, model)

    assert (tmp_path / "models.yml.bak").read_text() == "providers: [not: valid"
    assert "corrupt" in capsys.readouterr().err
    assert "turbo" in yaml.safe_load(models_path.read_text())["providers"]
