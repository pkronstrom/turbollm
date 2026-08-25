from turbollm.model_storage import gather_removal_targets


def test_gather_removal_targets_lists_exact_local_sidecars(tmp_path):
    model = {
        "hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "hf_file": "target.gguf",
        "local_path": str(tmp_path),
        "draft_hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "draft_hf_file": "mtp.gguf",
        "draft_local_path": str(tmp_path),
        "mmproj_hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "mmproj_hf_file": "mmproj.gguf",
        "mmproj_local_path": str(tmp_path),
    }
    for filename in ("target.gguf", "mtp.gguf", "mmproj.gguf"):
        (tmp_path / filename).write_bytes(b"x")

    targets = gather_removal_targets(model, other_models=[])

    assert {target.path.name for target in targets} == {
        "target.gguf",
        "mtp.gguf",
        "mmproj.gguf",
    }
    assert all(not target.is_dir for target in targets)


def test_gather_removal_targets_includes_old_mlx_target_and_draft_caches(
    tmp_path,
    monkeypatch,
):
    from turbollm import registry

    monkeypatch.setattr(registry, "HF_CACHE", tmp_path / "hub")
    monkeypatch.setattr(registry, "LEGACY_DIR", tmp_path / "legacy")
    model = {
        "hf_repo": "unsloth/Qwen3.6-27B-UD-MLX-6bit",
        "draft_hf_repo": "mlx-community/Qwen3.6-27B-MTP-5bit",
    }
    for repo in (model["hf_repo"], model["draft_hf_repo"]):
        path = registry._hf_cache_path(repo)
        path.mkdir(parents=True)
        (path / "weights.bin").write_bytes(b"abc")

    targets = gather_removal_targets(model, other_models=[])

    assert {target.path for target in targets} == {
        registry._hf_cache_path(model["hf_repo"]),
        registry._hf_cache_path(model["draft_hf_repo"]),
    }


def test_gather_removal_targets_protects_repo_used_by_another_model(
    tmp_path,
    monkeypatch,
):
    from turbollm import registry

    monkeypatch.setattr(registry, "HF_CACHE", tmp_path / "hub")
    repo = "org/shared"
    cache = registry._hf_cache_path(repo)
    cache.mkdir(parents=True)
    (cache / "weights.bin").write_bytes(b"abc")
    current = {"hf_repo": repo}
    other = {"hf_repo": repo, "name": "another quant"}

    assert gather_removal_targets(current, other_models=[other]) == []


def test_gather_removal_targets_protects_files_inside_shared_local_directory(tmp_path):
    target = tmp_path / "target.gguf"
    target.write_bytes(b"abc")
    current = {
        "hf_repo": "org/current",
        "hf_file": target.name,
        "local_path": str(tmp_path),
    }
    other = {
        "hf_repo": "org/other",
        "local_path": str(tmp_path),
    }

    assert gather_removal_targets(current, other_models=[other]) == []
