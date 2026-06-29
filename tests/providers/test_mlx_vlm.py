from pathlib import Path
import sys
from unittest.mock import patch

from turbollm.providers import get_provider
from turbollm.providers.mlx_vlm import MlxVlmProvider
from turbollm.cli import _get_model_id


def test_registers_mlx_vlm_provider():
    assert isinstance(get_provider("mlx-vlm"), MlxVlmProvider)


def test_available_when_server_binary_found():
    provider = MlxVlmProvider()

    with patch("shutil.which", return_value="/opt/homebrew/bin/mlx_vlm.server"):
        assert provider.is_available() is True


def test_builds_gemma_mtp_server_command():
    provider = MlxVlmProvider()
    model = {
        "hf_repo": "mlx-community/gemma-4-26b-a4b-it-6bit",
        "local_path": "~/.models/mlx-community/gemma-4-26b-a4b-it-6bit",
        "draft_hf_repo": "mlx-community/gemma-4-26B-A4B-it-assistant-bf16",
        "draft_local_path": "~/.models/mlx-community/gemma-4-26B-A4B-it-assistant-bf16",
        "server": {
            "draft_kind": "mtp",
            "draft_block_size": 6,
            "max_tokens": 4096,
            "prefill_step_size": 1024,
            "trust_remote_code": True,
        },
    }

    base_path = Path("~/.models/mlx-community/gemma-4-26b-a4b-it-6bit").expanduser()
    draft_path = Path(
        "~/.models/mlx-community/gemma-4-26B-A4B-it-assistant-bf16"
    ).expanduser()

    with patch.object(provider, "_model_path", return_value=base_path), patch.object(
        provider, "_draft_model_path", return_value=draft_path
    ):
        cmd = provider.build_serve_cmd(model, 8899)

    assert cmd[:2] == ["mlx_vlm.server", "--model"]
    assert str(base_path) in cmd
    assert "--draft-model" in cmd
    draft_idx = cmd.index("--draft-model")
    assert cmd[draft_idx + 1] == str(draft_path)
    assert cmd[cmd.index("--draft-kind") + 1] == "mtp"
    assert cmd[cmd.index("--draft-block-size") + 1] == "6"
    assert "--trust-remote-code" in cmd


def test_builds_tool_call_shim_server_command():
    provider = MlxVlmProvider()
    model = {
        "hf_repo": "mlx-community/diffusiongemma-26B-A4B-it-6bit",
        "local_path": "~/.models/mlx-community/diffusiongemma-26B-A4B-it-6bit",
        "server": {
            "tool_call_shim": "gemma4_bare",
            "trust_remote_code": True,
        },
    }

    base_path = Path(
        "~/.models/mlx-community/diffusiongemma-26B-A4B-it-6bit"
    ).expanduser()

    with patch.object(provider, "_model_path", return_value=base_path), patch.object(
        provider, "_draft_model_path", return_value=None
    ):
        cmd = provider.build_serve_cmd(model, 8899)

    assert cmd[:5] == [
        sys.executable,
        "-m",
        "turbollm.mlx_vlm_tool_proxy",
        "--listen-port",
        "8899",
    ]
    assert "--upstream-port" in cmd
    upstream_port = cmd[cmd.index("--upstream-port") + 1]
    assert upstream_port != "8899"
    upstream_cmd = cmd[cmd.index("--") + 1 :]
    assert upstream_cmd[:2] == ["mlx_vlm.server", "--model"]
    assert upstream_cmd[upstream_cmd.index("--port") + 1] == upstream_port
    assert "--trust-remote-code" in upstream_cmd


def test_downloaded_requires_base_and_draft_local_paths(tmp_path):
    provider = MlxVlmProvider()
    base = tmp_path / "base"
    draft = tmp_path / "draft"
    base.mkdir()
    draft.mkdir()
    (base / "model-00001-of-00001.safetensors").touch()
    (draft / "model.safetensors").touch()

    model = {
        "hf_repo": "mlx-community/gemma-4-26b-a4b-it-6bit",
        "local_path": str(base),
        "draft_hf_repo": "mlx-community/gemma-4-26B-A4B-it-assistant-bf16",
        "draft_local_path": str(draft),
    }

    assert provider.is_downloaded(model) is True

    (draft / "model.safetensors").unlink()

    assert provider.is_downloaded(model) is False


def test_model_id_uses_local_path_for_mlx_vlm():
    model = {
        "backend": "mlx-vlm",
        "hf_repo": "mlx-community/gemma-4-26b-a4b-it-6bit",
        "local_path": "~/.models/mlx-community/gemma-4-26b-a4b-it-6bit",
    }

    assert _get_model_id(model) == str(
        Path("~/.models/mlx-community/gemma-4-26b-a4b-it-6bit").expanduser()
    )
