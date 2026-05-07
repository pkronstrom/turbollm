from pathlib import Path
from unittest.mock import patch

import pytest

from turbollm.providers.omlx import OMLX_MODELS_DIR, OmlxProvider


@pytest.fixture
def provider():
    return OmlxProvider()


@pytest.fixture
def fake_model():
    return {
        "hf_repo": "mlx-community/Qwen3.6-35B-A3B-4bit",
        "name": "Qwen3.6 35B-A3B oMLX 4bit",
        "size_gb": 20,
    }


class TestSymlinkPath:
    def test_returns_correct_path(self, provider, fake_model):
        result = provider._symlink_path(fake_model)
        expected = OMLX_MODELS_DIR / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
        assert result == expected

    def test_handles_nested_org(self, provider):
        model = {"hf_repo": "some-org/some-model-name"}
        result = provider._symlink_path(model)
        assert result == OMLX_MODELS_DIR / "some-org" / "some-model-name"


class TestIsAvailable:
    def test_available_when_binary_found(self, provider):
        with patch("shutil.which", return_value="/opt/homebrew/bin/omlx"):
            assert provider.is_available() is True

    def test_unavailable_when_binary_missing(self, provider):
        with patch("shutil.which", return_value=None):
            assert provider.is_available() is False


class TestEnsureSymlink:
    def test_creates_symlink(self, provider, fake_model, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"

        with patch.object(provider, "_symlink_path", return_value=target):
            provider._ensure_symlink(fake_model, snapshot)

        assert target.is_symlink()
        assert target.resolve() == snapshot.resolve()

    def test_replaces_stale_symlink(self, provider, fake_model, tmp_path):
        old_snapshot = tmp_path / "old"
        old_snapshot.mkdir()
        new_snapshot = tmp_path / "new"
        new_snapshot.mkdir()
        (new_snapshot / "model.safetensors").touch()
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
        target.parent.mkdir(parents=True)
        target.symlink_to(old_snapshot)

        with patch.object(provider, "_symlink_path", return_value=target):
            provider._ensure_symlink(fake_model, new_snapshot)

        assert target.resolve() == new_snapshot.resolve()


class TestRemoveSymlink:
    def test_removes_symlink(self, provider, fake_model, tmp_path):
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
        target.parent.mkdir(parents=True)
        target.symlink_to(tmp_path)

        with patch.object(provider, "_symlink_path", return_value=target):
            provider._remove_symlink(fake_model)

        assert not target.exists()

    def test_noop_if_no_symlink(self, provider, fake_model, tmp_path):
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"

        with patch.object(provider, "_symlink_path", return_value=target):
            provider._remove_symlink(fake_model)  # should not raise


class TestBuildServeCmd:
    def test_minimal_cmd(self, provider):
        model = {"hf_repo": "mlx-community/test-model", "server": {}}
        with patch("turbollm.providers.omlx.get_defaults", return_value={}):
            cmd = provider.build_serve_cmd(model, 8899)

        assert cmd[:2] == ["omlx", "serve"]
        assert "--port" in cmd
        assert "8899" in cmd
        assert "--host" in cmd
        assert "127.0.0.1" in cmd
        assert "--model-dir" in cmd

    def test_ssd_cache_flags(self, provider):
        model = {"hf_repo": "mlx-community/test-model", "server": {}}
        defaults = {
            "omlx": {
                "paged_ssd_cache_dir": "/tmp/cache",
                "paged_ssd_cache_max_size": "50GB",
                "hot_cache_max_size": "8GB",
            }
        }
        with patch("turbollm.providers.omlx.get_defaults", return_value=defaults):
            cmd = provider.build_serve_cmd(model, 8899)

        assert "--paged-ssd-cache-dir" in cmd
        idx = cmd.index("--paged-ssd-cache-dir")
        assert cmd[idx + 1] == "/tmp/cache"
        assert "--paged-ssd-cache-max-size" in cmd
        assert "--hot-cache-max-size" in cmd

    def test_per_model_server_overrides(self, provider):
        model = {
            "hf_repo": "mlx-community/test-model",
            "server": {"max_concurrent_requests": 4},
        }
        with patch("turbollm.providers.omlx.get_defaults", return_value={}):
            cmd = provider.build_serve_cmd(model, 8899)

        assert "--max-concurrent-requests" in cmd
        idx = cmd.index("--max-concurrent-requests")
        assert cmd[idx + 1] == "4"


class TestIsDownloaded:
    def test_true_when_hf_snapshot_and_symlink_exist(self, provider, fake_model, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        link = tmp_path / "link"
        link.symlink_to(snapshot)

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=snapshot), \
             patch.object(provider, "_symlink_path", return_value=link):
            assert provider.is_downloaded(fake_model) is True

    def test_false_when_no_snapshot(self, provider, fake_model, tmp_path):
        link = tmp_path / "link"

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=None), \
             patch.object(provider, "_symlink_path", return_value=link):
            assert provider.is_downloaded(fake_model) is False

    def test_false_when_no_symlink(self, provider, fake_model, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        link = tmp_path / "nonexistent_link"

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=snapshot), \
             patch.object(provider, "_symlink_path", return_value=link):
            assert provider.is_downloaded(fake_model) is False
