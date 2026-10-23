import json
from pathlib import Path

import pytest

from turbollm.providers.kev import KevProvider

ADAPTER_SHA = "a" * 40


def write_checkpoint(path, base="Qwen/base", revision="base-sha"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": base}))
    (path / "training_config.json").write_text(json.dumps({"base_revision": revision}))
    (path / "head.pt").write_bytes(b"head")
    (path / "adapter_model.safetensors").write_bytes(b"adapter")
    metadata = path / ".cache/huggingface/download"
    metadata.mkdir(parents=True, exist_ok=True)
    for name in ("adapter_config.json", "head.pt", "adapter_model.safetensors"):
        (metadata / (name + ".metadata")).write_text(ADAPTER_SHA + "\netag\n0\n")
    head = (path / "head.pt").stat()
    (path / ".turbollm-kev-head.json").write_text(json.dumps({
        "head_signature": [head.st_dev, head.st_ino, head.st_size, head.st_mtime_ns, head.st_ctime_ns],
        "base": base, "base_revision": revision,
    }))


def write_base(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text("{}")
    (path / "model.safetensors").write_bytes(b"weights")
    (path / "tokenizer.json").write_text("{}")


def model(tmp_path):
    refs = tmp_path / "hf/hub/models--jaredpalmer--kev-4b/refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "v1.0").write_text(ADAPTER_SHA)
    return {"backend": "kev", "hf_repo": "jaredpalmer/kev-4b", "revision": "v1.0",
            "local_path": str(tmp_path / "adapter"), "server": {"hf_home": str(tmp_path / "hf")}}


def test_command_uses_isolated_runtime_loopback_and_mlx(tmp_path):
    provider = KevProvider()
    m = model(tmp_path)
    m["server"]["python"] = "/custom/python"
    command = provider.build_serve_cmd(m, 8901)
    python_index = command.index("/custom/python")
    assert command[:python_index] == ["env", f"HF_HOME={tmp_path / 'hf'}", f"HF_HUB_CACHE={tmp_path / 'hf/hub'}", "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "KEV_BACKEND=mlx"]
    assert command[python_index + 1:] == ["-m", "kev.serve", "--run", "jaredpalmer/kev-4b@v1.0", "--host", "127.0.0.1", "--port", "8901"]
    assert provider.get_model_id(m) == "kev-latest"


def test_command_uses_existing_adapter_path(tmp_path):
    m = model(tmp_path)
    write_checkpoint(Path(m["local_path"]))
    command = KevProvider().build_serve_cmd(m, 8901)
    assert command[command.index("--run") + 1] == m["local_path"]


def test_missing_base_is_not_downloaded(tmp_path):
    m = model(tmp_path)
    write_checkpoint(Path(m["local_path"]))
    assert not KevProvider().is_downloaded(m)


def test_adapter_and_exact_pinned_base_are_downloaded(tmp_path):
    m = model(tmp_path)
    write_checkpoint(Path(m["local_path"]))
    base = tmp_path / "hf/hub/models--Qwen--base/snapshots/base-sha"
    write_base(base)
    assert KevProvider().is_downloaded(m)
    (base / "model.safetensors").unlink()
    assert not KevProvider().is_downloaded(m)


def test_incomplete_weight_shards_are_not_downloaded(tmp_path):
    m = model(tmp_path)
    write_checkpoint(Path(m["local_path"]))
    base = tmp_path / "hf/hub/models--Qwen--base/snapshots/base-sha"
    write_base(base)
    (base / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"x": "model-1.safetensors", "y": "model-2.safetensors"}}))
    (base / "model-1.safetensors").write_bytes(b"weights")
    assert not KevProvider().is_downloaded(m)


def test_cached_adapter_resolves_revision_ref_without_network(tmp_path):
    m = model(tmp_path)
    adapter = tmp_path / f"hf/hub/models--jaredpalmer--kev-4b/snapshots/{ADAPTER_SHA}"
    write_checkpoint(adapter)
    refs = adapter.parent.parent / "refs"
    (refs / "v1.0").write_text(ADAPTER_SHA)
    write_base(tmp_path / "hf/hub/models--Qwen--base/snapshots/base-sha")
    assert KevProvider().is_downloaded(m)
    assert KevProvider().get_run(m) == str(adapter)


def test_local_base_dependency_is_checked(tmp_path):
    m = model(tmp_path)
    base = tmp_path / "local-base"
    write_checkpoint(Path(m["local_path"]), base=str(base), revision=None)
    write_base(base)
    assert KevProvider().is_downloaded(m)


def test_pull_downloads_adapter_then_referenced_base_with_separate_revisions(tmp_path, monkeypatch):
    calls = []
    m = model(tmp_path)
    def download(**kwargs):
        calls.append(kwargs)
        if kwargs["repo_id"] == m["hf_repo"]:
            path = Path(kwargs["local_dir"])
            write_checkpoint(path)
        else:
            path = tmp_path / "hf/hub/models--Qwen--base/snapshots/base-sha"
            write_base(path)
        return str(path)
    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    monkeypatch.setattr(KevProvider, "_read_head_metadata", lambda *args: ("Qwen/base", "base-sha"))
    KevProvider().pull(m)
    assert [call["repo_id"] for call in calls] == [m["hf_repo"], "Qwen/base"]
    assert [call["revision"] for call in calls] == ["v1.0", "base-sha"]
    assert all(call["cache_dir"] == str(tmp_path / "hf/hub") for call in calls)
    assert KevProvider().is_downloaded(m)


def test_availability_respects_explicit_python(tmp_path):
    runtime = tmp_path / "python"
    runtime.write_text("python")
    runtime.chmod(0o755)
    assert KevProvider().is_available({"server": {"python": str(runtime)}})
    assert not KevProvider().is_available({"server": {"python": str(tmp_path / "absent")}})


def test_cached_authoritative_head_wins_over_stale_training_metadata(tmp_path):
    m = model(tmp_path)
    adapter = Path(m["local_path"])
    write_checkpoint(adapter)
    (adapter / "training_config.json").write_text('{"base_revision":"wrong-revision"}')
    write_base(tmp_path / "hf/hub/models--Qwen--base/snapshots/base-sha")
    assert KevProvider().is_downloaded(m)


def test_replaced_head_invalidates_verified_metadata(tmp_path):
    m = model(tmp_path)
    adapter = Path(m["local_path"])
    write_checkpoint(adapter)
    write_base(tmp_path / "hf/hub/models--Qwen--base/snapshots/base-sha")
    (adapter / "head.pt").write_bytes(b"changed head")
    assert not KevProvider().is_downloaded(m)


def test_unverified_head_requires_explicit_pull(tmp_path):
    m = model(tmp_path)
    adapter = Path(m["local_path"])
    write_checkpoint(adapter)
    write_base(tmp_path / "hf/hub/models--Qwen--base/snapshots/base-sha")
    (adapter / ".turbollm-kev-head.json").unlink()
    assert not KevProvider().is_downloaded(m)


def test_head_reader_uses_isolated_runtime_and_weights_only(tmp_path, monkeypatch):
    from types import SimpleNamespace
    seen = []
    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(stdout='{"base":"Qwen/head-base","base_revision":"head-sha"}')
    monkeypatch.setattr("turbollm.providers.kev.subprocess.run", run)
    m = model(tmp_path)
    m["server"]["python"] = "/isolated/python"
    result = KevProvider()._read_head_metadata(Path(m["local_path"]), m)
    assert result == ("Qwen/head-base", "head-sha")
    assert seen[0][0][0] == "/isolated/python"
    assert "weights_only=True" in seen[0][0][2]
    assert seen[0][1]["timeout"] <= 30


def test_new_pin_rejects_previous_local_adapter(tmp_path):
    m = model(tmp_path)
    write_checkpoint(Path(m["local_path"]))
    write_base(tmp_path / "hf/hub/models--Qwen--base/snapshots/base-sha")
    m["revision"] = "c" * 40
    provider = KevProvider()
    assert not provider.is_downloaded(m)
    assert provider.get_run(m) == m["hf_repo"] + "@" + m["revision"]


@pytest.mark.parametrize("artifact", ["adapter_config.json", "head.pt", "adapter_model.safetensors"])
def test_all_adapter_artifacts_must_match_requested_revision(tmp_path, artifact):
    m = model(tmp_path)
    write_checkpoint(Path(m["local_path"]))
    metadata = Path(m["local_path"]) / ".cache/huggingface/download" / (artifact + ".metadata")
    metadata.write_text("c" * 40 + "\netag\n0\n")
    assert KevProvider().get_run(m) != m["local_path"]


@pytest.mark.parametrize("revision", ["../outside", "/tmp/outside", "nested/../../outside"])
def test_snapshot_rejects_revision_traversal(tmp_path, revision):
    m = model(tmp_path)
    cache = tmp_path / "hf/hub/models--jaredpalmer--kev-4b"
    (cache / "outside").mkdir()
    (cache / "snapshots/nested").mkdir(parents=True)
    if revision.startswith("/"):
        revision = str(cache / "outside")
    assert KevProvider()._snapshot(m, m["hf_repo"], revision) is None


def test_snapshot_rejects_traversal_in_symbolic_ref(tmp_path):
    m = model(tmp_path)
    cache = tmp_path / "hf/hub/models--jaredpalmer--kev-4b"
    (cache / "outside").mkdir()
    (cache / "snapshots").mkdir()
    (cache / "refs/v1.0").write_text("../outside")
    assert KevProvider()._snapshot(m, m["hf_repo"], "v1.0") is None
