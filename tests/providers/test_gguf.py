from pathlib import Path
from unittest.mock import patch

import click
import pytest

from turbollm.providers.gguf import GgufProvider


def _qwen38_model(tmp_path):
    return {
        "hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "hf_file": "Qwen3.8-27B-Q8_0.gguf",
        "local_path": str(tmp_path),
        "draft_hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "draft_hf_file": "mtp-Qwen3.8-27B-Q8_0.gguf",
        "draft_local_path": str(tmp_path),
        "mmproj_hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "mmproj_hf_file": "mmproj-Qwen3.8-27B-Q8_0.gguf",
        "mmproj_local_path": str(tmp_path),
        "strict_artifacts": True,
    }


def _touch_qwen38_artifacts(tmp_path, model):
    for key in ("hf_file", "draft_hf_file", "mmproj_hf_file"):
        (tmp_path / model[key]).touch()


def test_builds_mtp_spec_flags_for_grafted_gguf(tmp_path):
    model_file = tmp_path / "Qwen3.6-27B-MTP-UD-Q6_K_XL.gguf"
    model_file.touch()
    provider = GgufProvider()
    model = {
        "hf_repo": "havenoammo/Qwen3.6-27B-MTP-UD-GGUF",
        "hf_file": model_file.name,
        "local_path": str(tmp_path),
        "server": {
            "context": 65536,
            "spec_type": "mtp",
            "spec_draft_n_max": 3,
        },
    }

    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"):
        cmd = provider.build_serve_cmd(model, 8899)

    assert cmd[:3] == ["/tmp/llama-server", "-m", str(model_file)]
    assert cmd[cmd.index("--spec-type") + 1] == "mtp"
    assert cmd[cmd.index("--spec-draft-n-max") + 1] == "3"


def test_is_downloaded_checks_local_path(tmp_path):
    model_file = tmp_path / "Qwen3.6-27B-MTP-UD-Q6_K_XL.gguf"
    model_file.touch()
    provider = GgufProvider()
    model = {
        "hf_repo": "havenoammo/Qwen3.6-27B-MTP-UD-GGUF",
        "hf_file": model_file.name,
        "local_path": str(tmp_path),
    }

    assert provider.is_downloaded(model) is True

    model_file.unlink()

    assert provider.is_downloaded(model) is False


def test_draft_gguf_file_prefers_configured_draft_local_path(tmp_path):
    # pull() honors draft_local_path — _draft_gguf_file must check the same
    # location, or a draft downloaded there is never found at serve time.
    model_file = tmp_path / "model.gguf"
    model_file.touch()
    draft_dir = tmp_path / "draft"
    draft_dir.mkdir()
    draft_file = draft_dir / "draft.gguf"
    draft_file.touch()

    provider = GgufProvider()
    model = {
        "hf_repo": "havenoammo/Qwen3.6-27B-MTP-UD-GGUF",
        "hf_file": model_file.name,
        "local_path": str(tmp_path),
        "draft_hf_repo": "org/draft-repo",
        "draft_hf_file": "draft.gguf",
        "draft_local_path": str(draft_dir),
    }

    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"):
        cmd = provider.build_serve_cmd(model, 8899)

    assert cmd[cmd.index("--spec-draft-model") + 1] == str(draft_file)


def test_missing_draft_warns_instead_of_silently_omitting(tmp_path, capsys):
    model_file = tmp_path / "model.gguf"
    model_file.touch()

    provider = GgufProvider()
    model = {
        "hf_repo": "havenoammo/Qwen3.6-27B-MTP-UD-GGUF",
        "hf_file": model_file.name,
        "local_path": str(tmp_path),
        "draft_hf_repo": "org/draft-repo",
        "draft_hf_file": "draft.gguf",
    }

    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"), \
         patch("turbollm.providers.gguf._hf_snapshot_path", return_value=None):
        cmd = provider.build_serve_cmd(model, 8899)

    assert "--spec-draft-model" not in cmd
    out = capsys.readouterr().out
    assert "org/draft-repo" in out


def test_qwen38_is_downloaded_requires_target_draft_and_projector(tmp_path):
    model = _qwen38_model(tmp_path)
    _touch_qwen38_artifacts(tmp_path, model)
    provider = GgufProvider()

    assert provider.is_downloaded(model) is True

    (tmp_path / model["mmproj_hf_file"]).unlink()
    assert provider.is_downloaded(model) is False


def test_qwen38_command_uses_exact_projector(tmp_path):
    model = _qwen38_model(tmp_path)
    _touch_qwen38_artifacts(tmp_path, model)
    provider = GgufProvider()

    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"):
        cmd = provider.build_serve_cmd(model, 8899)

    assert cmd[cmd.index("--mmproj") + 1] == str(tmp_path / model["mmproj_hf_file"])


def test_strict_qwen38_refuses_missing_projector(tmp_path):
    model = _qwen38_model(tmp_path)
    (tmp_path / model["hf_file"]).touch()
    (tmp_path / model["draft_hf_file"]).touch()
    provider = GgufProvider()

    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"), \
         pytest.raises(click.ClickException, match="mmproj-Qwen3.8-27B-Q8_0.gguf"):
        provider.build_serve_cmd(model, 8899)


def test_qwen38_builds_quality_native_mtp_command(tmp_path):
    model = _qwen38_model(tmp_path)
    _touch_qwen38_artifacts(tmp_path, model)
    model["sampling"] = "qwen38-thinking"
    model["kv_quant"] = "off"
    model["context_default"] = 262144
    model["server"] = {
        "ngl": "all",
        "draft_ngl": "all",
        "batch": 512,
        "ubatch": 512,
        "parallel": 1,
        "flash_attention": "on",
        "no_context_shift": True,
        "cache_prompt": True,
        "reasoning_preserve": True,
        "jinja": True,
        "spec_type": "draft-mtp",
        "spec_draft_n_max": 3,
        "draft_cache_type_k": "f16",
        "draft_cache_type_v": "f16",
    }
    sampling = {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repeat_penalty": 1.0,
    }

    provider = GgufProvider()
    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"), \
         patch("turbollm.providers.gguf.effective_sampling", return_value=sampling):
        cmd = provider.build_serve_cmd(model, 8899)

    expected_pairs = {
        "-c": "262144",
        "--parallel": "1",
        "-ngl": "all",
        "--spec-draft-ngl": "all",
        "--cache-type-k": "f16",
        "--cache-type-v": "f16",
        "--spec-draft-type-k": "f16",
        "--spec-draft-type-v": "f16",
        "--spec-type": "draft-mtp",
        "--spec-draft-n-max": "3",
    }
    for flag, value in expected_pairs.items():
        assert cmd[cmd.index(flag) + 1] == value
    for flag in (
        "--cache-prompt",
        "--no-context-shift",
        "--reasoning-preserve",
        "--jinja",
    ):
        assert flag in cmd
    assert cmd[cmd.index("-fa") + 1] == "on"
    assert cmd[cmd.index("--spec-draft-model") + 1].endswith(
        "mtp-Qwen3.8-27B-Q8_0.gguf"
    )


def test_required_llama_flags_fail_before_launch(tmp_path):
    model = _qwen38_model(tmp_path)
    _touch_qwen38_artifacts(tmp_path, model)
    model["server"] = {
        "required_flags": ["--spec-type", "--reasoning-preserve"],
    }
    completed = type(
        "Result",
        (),
        {"stdout": "--spec-type", "stderr": "", "returncode": 0},
    )()

    provider = GgufProvider()
    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"), \
         patch("subprocess.run", return_value=completed), \
         pytest.raises(click.ClickException, match="--reasoning-preserve"):
        provider.build_serve_cmd(model, 8899)
