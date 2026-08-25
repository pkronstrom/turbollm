from pathlib import Path
from unittest.mock import patch

import click
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


class TestEnsureSymlinkRealDirectory:
    def test_raises_clear_error_on_real_directory(self, provider, fake_model, tmp_path):
        # A real (non-symlink) directory at the link path — e.g. a manually
        # placed model — must not be blown away or crash with a raw
        # traceback from Path.unlink() on a directory.
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
        target.mkdir(parents=True)
        (target / "manually-placed.txt").touch()

        with patch.object(provider, "_symlink_path", return_value=target):
            with pytest.raises(click.ClickException, match="isn't a symlink"):
                provider._ensure_symlink(fake_model, snapshot)

        # The real directory (and its contents) must survive.
        assert target.is_dir()
        assert not target.is_symlink()
        assert (target / "manually-placed.txt").exists()


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

    def test_raises_clear_error_on_real_directory(self, provider, fake_model, tmp_path):
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
        target.mkdir(parents=True)

        with patch.object(provider, "_symlink_path", return_value=target):
            with pytest.raises(click.ClickException, match="isn't a symlink"):
                provider._remove_symlink(fake_model)

        assert target.is_dir()


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

    def test_true_when_configured_local_path_and_symlink_exist(
        self, provider, fake_model, tmp_path
    ):
        local_dir = tmp_path / "custom-model"
        local_dir.mkdir()
        (local_dir / "model.safetensors").touch()
        fake_model = {**fake_model, "local_path": str(local_dir)}
        link = tmp_path / "link"
        link.symlink_to(local_dir)

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=None), \
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


    def test_false_when_symlink_is_cyclic(self, provider, fake_model, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        link = tmp_path / "link"
        link.symlink_to(link.name)

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=snapshot), \
             patch.object(provider, "_symlink_path", return_value=link):
            assert provider.is_downloaded(fake_model) is False

    def test_false_when_symlink_targets_different_directory(
        self, provider, fake_model, tmp_path
    ):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        other_directory = tmp_path / "other"
        other_directory.mkdir()
        link = tmp_path / "link"
        link.symlink_to(other_directory)

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=snapshot), \
             patch.object(provider, "_symlink_path", return_value=link):
            assert provider.is_downloaded(fake_model) is False

    def test_false_when_symlink_resolution_is_unreadable(
        self, provider, fake_model, tmp_path
    ):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        link = tmp_path / "link"
        link.symlink_to(snapshot)

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=snapshot), \
             patch.object(provider, "_symlink_path", return_value=link), \
             patch.object(Path, "resolve", side_effect=PermissionError):
            assert provider.is_downloaded(fake_model) is False

class TestPull:
    def test_honors_configured_local_path(self, provider, fake_model, tmp_path):
        # omlx.pull previously called snapshot_download() with no local_dir,
        # silently dropping local_path support that gguf/vllm_mlx/mlx_vlm all
        # honor. The shared download_repo_with_progress() helper must be
        # called with the model's configured local_path.
        local_dir = tmp_path / "custom"
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        fake_model = {**fake_model, "local_path": str(local_dir)}

        with patch(
            "turbollm.providers.omlx.download_repo_with_progress", return_value=snapshot
        ) as download, patch.object(provider, "_ensure_symlink") as ensure_symlink:
            provider.pull(fake_model)

        download.assert_called_once_with(fake_model["hf_repo"], local_dir)
        ensure_symlink.assert_called_once_with(fake_model, snapshot)
