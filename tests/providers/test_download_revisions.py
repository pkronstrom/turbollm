from pathlib import Path
from unittest.mock import patch

import pytest

from turbollm.hf_download import download_repo_with_progress
from turbollm.providers.gguf import GgufProvider
from turbollm.providers.mlx_audio import MlxAudioProvider
from turbollm.providers.mlx_vlm import MlxVlmProvider
from turbollm.providers.omlx import OmlxProvider
from turbollm.providers.vllm_mlx import VllmMlxProvider


def test_snapshot_download_is_one_concurrent_revision_pinned_operation(tmp_path):
    with (
        patch("huggingface_hub.snapshot_download", return_value=str(tmp_path)) as download,
        patch("huggingface_hub.hf_hub_download") as individual,
        patch("huggingface_hub.list_repo_files") as listing,
    ):
        assert download_repo_with_progress("org/model", tmp_path, revision="commit") == tmp_path
    download.assert_called_once_with(
        repo_id="org/model", local_dir=str(tmp_path), revision="commit", max_workers=8
    )
    individual.assert_not_called()
    listing.assert_not_called()


@pytest.mark.parametrize("provider", [MlxAudioProvider(), MlxVlmProvider(), GgufProvider()])
def test_direct_download_providers_forward_target_revision(provider, tmp_path):
    artifact = tmp_path / "model.gguf"
    artifact.touch()
    model = {"hf_repo": "org/model", "revision": "commit", "hf_file": artifact.name}
    with (
        patch("huggingface_hub.snapshot_download", return_value=str(tmp_path)) as snapshot,
        patch("huggingface_hub.hf_hub_download", return_value=str(artifact)) as single,
    ):
        provider.pull(model)
    calls = snapshot.call_args_list + single.call_args_list
    assert len(calls) == 1
    assert calls[0].kwargs["revision"] == "commit"


@pytest.mark.parametrize("provider,module", [(OmlxProvider(), "omlx"), (VllmMlxProvider(), "vllm_mlx")])
def test_shared_download_providers_forward_target_revision(provider, module, tmp_path):
    model = {"hf_repo": "org/model", "revision": "commit"}
    with patch(f"turbollm.providers.{module}.download_repo_with_progress", return_value=tmp_path) as download:
        if isinstance(provider, OmlxProvider):
            with patch.object(provider, "_ensure_symlink"):
                provider.pull(model)
        else:
            provider.pull(model)
    download.assert_called_once_with("org/model", None, revision="commit")


@pytest.mark.parametrize("provider,module", [
    (OmlxProvider(), "omlx"), (VllmMlxProvider(), "vllm_mlx"),
    (MlxVlmProvider(), "mlx_vlm"), (MlxAudioProvider(), "mlx_audio"),
    (GgufProvider(), "gguf"),
])
def test_downloaded_check_forwards_configured_revision(provider, module):
    model = {"hf_repo": "org/model", "revision": "commit", "hf_file": "model.gguf"}
    with patch(f"turbollm.providers.{module}._hf_snapshot_path", return_value=None) as snapshot:
        assert provider.is_downloaded(model) is False
    snapshot.assert_called_once_with("org/model", revision="commit")


def test_cached_draft_is_not_downloaded_again_when_target_updated(tmp_path):
    provider = VllmMlxProvider()
    model = {"hf_repo": "org/model", "revision": "commit", "draft_hf_repo": "org/draft"}
    with (
        patch("turbollm.providers.vllm_mlx.download_repo_with_progress", return_value=tmp_path),
        patch.object(provider, "_draft_model_path", return_value=tmp_path),
        patch("huggingface_hub.snapshot_download") as draft,
    ):
        provider.pull(model)
    draft.assert_not_called()


def test_audio_pinned_model_id_uses_exact_snapshot(tmp_path):
    with patch("turbollm.providers.mlx_audio._hf_snapshot_path", return_value=tmp_path) as snapshot:
        assert MlxAudioProvider().get_model_id({"hf_repo": "org/audio", "revision": "abc"}) == str(tmp_path)
    snapshot.assert_called_once_with("org/audio", revision="abc")


def local_metadata(root, filename, commit):
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    metadata = root / ".cache" / "huggingface" / "download" / (filename + ".metadata")
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(commit + "\netag\n123.0\n")


@pytest.mark.parametrize("provider,module", [
    (OmlxProvider(), "omlx"), (VllmMlxProvider(), "vllm_mlx"),
    (MlxVlmProvider(), "mlx_vlm"), (GgufProvider(), "gguf"),
])
@pytest.mark.parametrize("commit", ["expected", "old", None])
def test_local_weights_must_match_pinned_revision(provider, module, commit, tmp_path):
    filename = "model.gguf" if module == "gguf" else "model.safetensors"
    if commit:
        local_metadata(tmp_path, filename, commit)
    else:
        (tmp_path / filename).touch()
    model = {"hf_repo": "org/model", "revision": "expected", "local_path": str(tmp_path),
             "hf_file": filename}
    with patch(f"turbollm.providers.{module}._hf_snapshot_path", return_value=None):
        if module == "omlx":
            link = tmp_path / "link"
            link.symlink_to(tmp_path)
            with patch.object(provider, "_symlink_path", return_value=link):
                assert provider.is_downloaded(model) is (commit == "expected")
        else:
            assert provider.is_downloaded(model) is (commit == "expected")


def test_local_pinned_config_must_match_weights(tmp_path):
    local_metadata(tmp_path, "model.safetensors", "expected")
    local_metadata(tmp_path, "config.json", "old")
    model = {"hf_repo": "org/model", "revision": "expected", "local_path": str(tmp_path)}
    with patch("turbollm.providers.vllm_mlx._hf_snapshot_path", return_value=None):
        assert not VllmMlxProvider().is_downloaded(model)


def test_missing_audio_pin_cannot_fall_back_to_default_repo():
    import click
    with patch("turbollm.providers.mlx_audio._hf_snapshot_path", return_value=None):
        with pytest.raises(click.ClickException, match="revision"):
            MlxAudioProvider().get_model_id({"hf_repo": "org/audio", "revision": "missing"})


def test_local_revision_resolves_cached_ref_without_snapshot(tmp_path, monkeypatch):
    from turbollm import registry
    from turbollm.hf_download import local_revision_matches

    local = tmp_path / "local"
    local_metadata(local, "model.safetensors", "commit")
    monkeypatch.setattr(registry, "HF_CACHE", tmp_path / "hub")
    model = {"hf_repo": "org/model", "revision": "release/v1"}
    assert not local_revision_matches(model, local)
    ref = registry._hf_cache_path("org/model") / "refs" / "release" / "v1"
    ref.parent.mkdir(parents=True)
    ref.write_text("commit")
    assert local_revision_matches(model, local)
    ref.write_text("different")
    assert not local_revision_matches(model, local)


@pytest.mark.parametrize("kind", ["draft", "mmproj"])
def test_gguf_sidecar_local_revision_is_checked(tmp_path, kind):
    local_metadata(tmp_path, "sidecar.gguf", "old")
    model = {f"{kind}_hf_repo": "org/sidecar", f"{kind}_revision": "new",
             f"{kind}_hf_file": "sidecar.gguf", f"{kind}_local_path": str(tmp_path)}
    provider = GgufProvider()
    lookup = provider._draft_gguf_file if kind == "draft" else provider._mmproj_gguf_file
    with patch("turbollm.providers.gguf._hf_snapshot_path", return_value=None):
        assert lookup(model) is None
        local_metadata(tmp_path, "sidecar.gguf", "new")
        assert lookup(model) == tmp_path / "sidecar.gguf"


def test_mlx_vlm_draft_local_revision_is_checked(tmp_path):
    local_metadata(tmp_path, "model.safetensors", "old")
    model = {"draft_hf_repo": "org/draft", "draft_revision": "new", "draft_local_path": str(tmp_path)}
    with patch("turbollm.providers.mlx_vlm._hf_snapshot_path", return_value=None):
        assert MlxVlmProvider()._draft_model_path(model) is None
        local_metadata(tmp_path, "model.safetensors", "new")
        assert MlxVlmProvider()._draft_model_path(model) == tmp_path


def test_gguf_serve_cannot_bypass_local_revision_validation(tmp_path):
    local_metadata(tmp_path, "model.gguf", "old")
    model = {"hf_repo": "org/model", "revision": "new", "hf_file": "model.gguf",
             "local_path": str(tmp_path)}
    with patch("turbollm.providers.gguf._hf_snapshot_path", return_value=None):
        with pytest.raises(SystemExit):
            GgufProvider()._gguf_file(model)
