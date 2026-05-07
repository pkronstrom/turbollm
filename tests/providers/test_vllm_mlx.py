from pathlib import Path

from turbollm.providers.vllm_mlx import VllmMlxProvider


def test_is_downloaded_checks_configured_local_path(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model-00001-of-00001.safetensors").touch()
    provider = VllmMlxProvider()
    model = {
        "hf_repo": "unsloth/gemma-4-31b-it-UD-MLX-4bit",
        "local_path": str(model_dir),
    }

    assert provider.is_downloaded(model) is True

    (model_dir / "model-00001-of-00001.safetensors").unlink()

    assert provider.is_downloaded(model) is False


def test_build_serve_cmd_uses_configured_local_path(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model-00001-of-00001.safetensors").touch()
    provider = VllmMlxProvider()
    model = {
        "hf_repo": "unsloth/gemma-4-31b-it-UD-MLX-4bit",
        "local_path": str(model_dir),
        "server": {"timeout": 600},
    }

    cmd = provider.build_serve_cmd(model, 8899)

    assert cmd[:3] == ["vllm-mlx", "serve", str(model_dir)]


def test_build_serve_cmd_passes_gemma_defaults(tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model-00001-of-00001.safetensors").touch()
    provider = VllmMlxProvider()
    model = {
        "hf_repo": "unsloth/gemma-4-31b-it-UD-MLX-4bit",
        "local_path": str(model_dir),
        "tool_use": True,
        "tool_call_parser": "gemma4",
        "reasoning_parser": "gemma4",
        "server": {
            "default_temperature": 1.0,
            "default_top_p": 0.95,
            "default_chat_template_kwargs": {"enable_thinking": False},
        },
    }

    cmd = provider.build_serve_cmd(model, 8899)

    assert cmd[cmd.index("--tool-call-parser") + 1] == "gemma4"
    assert cmd[cmd.index("--reasoning-parser") + 1] == "gemma4"
    assert cmd[cmd.index("--default-temperature") + 1] == "1.0"
    assert cmd[cmd.index("--default-top-p") + 1] == "0.95"
    assert (
        cmd[cmd.index("--default-chat-template-kwargs") + 1]
        == '{"enable_thinking":false}'
    )
