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
