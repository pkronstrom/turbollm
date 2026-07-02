from pathlib import Path
from unittest.mock import patch

from turbollm.providers.gguf import GgufProvider


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

    assert cmd[cmd.index("--model-draft") + 1] == str(draft_file)


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

    assert "--model-draft" not in cmd
    out = capsys.readouterr().out
    assert "org/draft-repo" in out
