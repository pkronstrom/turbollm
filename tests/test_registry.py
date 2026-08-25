import tomllib
from pathlib import Path

from turbollm import registry


def test_load_registry_prefers_user_config(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled-models.toml"
    user_dir = tmp_path / ".turbollm"
    user_toml = user_dir / "models.toml"

    bundled.write_text("[models.bundled]\nname = 'Bundled'\nhf_repo = 'org/bundled'\n")
    user_dir.mkdir()
    user_toml.write_text("[models.user]\nname = 'User'\nhf_repo = 'org/user'\n")

    monkeypatch.setattr(registry, "BUNDLED_TOML", bundled)
    monkeypatch.setattr(registry, "CONFIG_DIR", user_dir)
    monkeypatch.setattr(registry, "USER_TOML", user_toml)

    loaded = registry.load_registry()

    assert "user" in loaded["models"]
    assert "bundled" not in loaded["models"]


def test_load_registry_bootstraps_user_config_from_bundled(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled-models.toml"
    user_dir = tmp_path / ".turbollm"
    user_toml = user_dir / "models.toml"
    bundled_text = "[defaults]\nport = 9000\n\n[models.seed]\nname = 'Seed'\nhf_repo = 'org/seed'\n"

    bundled.write_text(bundled_text)

    monkeypatch.setattr(registry, "BUNDLED_TOML", bundled)
    monkeypatch.setattr(registry, "CONFIG_DIR", user_dir)
    monkeypatch.setattr(registry, "USER_TOML", user_toml)

    loaded = registry.load_registry()

    assert loaded["defaults"]["port"] == 9000
    assert user_toml.exists()
    assert tomllib.loads(user_toml.read_text()) == loaded


def test_load_registry_returns_empty_when_no_config_exists(monkeypatch, tmp_path):
    bundled = tmp_path / "missing-bundled.toml"
    user_dir = tmp_path / ".turbollm"
    user_toml = user_dir / "models.toml"

    monkeypatch.setattr(registry, "BUNDLED_TOML", bundled)
    monkeypatch.setattr(registry, "CONFIG_DIR", user_dir)
    monkeypatch.setattr(registry, "USER_TOML", user_toml)

    assert registry.load_registry() == {"defaults": {}, "models": {}}
    assert not user_toml.exists()


def test_hf_snapshot_path_ignores_stray_files(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "HF_CACHE", tmp_path)
    snapshots = tmp_path / "models--org--repo" / "snapshots"
    snapshots.mkdir(parents=True)
    # A stray file (e.g. .DS_Store) must never be returned as "the snapshot".
    (snapshots / ".DS_Store").touch()
    real_snapshot = snapshots / "abcdef0123456789"
    real_snapshot.mkdir()

    result = registry._hf_snapshot_path("org/repo")

    assert result == real_snapshot


def test_hf_snapshot_path_returns_none_when_only_stray_files(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "HF_CACHE", tmp_path)
    snapshots = tmp_path / "models--org--repo" / "snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / ".DS_Store").touch()

    assert registry._hf_snapshot_path("org/repo") is None


def test_parse_model_input_passes_through_bare_repo():
    assert registry.parse_model_input("org/repo") == "org/repo"


def test_parse_model_input_passes_through_alias():
    assert registry.parse_model_input("my-alias") == "my-alias"


def test_resolve_model_skips_entries_missing_hf_repo(monkeypatch):
    # A user-added model missing hf_repo shouldn't break resolution of an
    # unrelated model by hf_repo lookup.
    reg = {
        "models": {
            "broken": {"name": "Broken entry, no hf_repo"},
            "good": {"name": "Good", "hf_repo": "org/good-repo"},
        }
    }
    monkeypatch.setattr(registry, "load_registry", lambda: reg)

    resolved = registry.resolve_model("org/good-repo")

    assert resolved is reg["models"]["good"]


def test_bundled_qwen38_q8_mtp_contract():
    data = registry._load_toml(registry.BUNDLED_TOML)
    model = data["models"]["qwen38-27b-q8-mtp"]

    assert "qwen36-27b-6bit" not in data["models"]
    assert model["backend"] == "gguf"
    assert model["hf_repo"] == "ggml-org/Qwen3.8-27B-GGUF"
    assert model["hf_file"] == "Qwen3.8-27B-Q8_0.gguf"
    assert model["draft_hf_file"] == "mtp-Qwen3.8-27B-Q8_0.gguf"
    assert model["mmproj_hf_file"] == "mmproj-Qwen3.8-27B-Q8_0.gguf"
    assert model["context_default"] == 262144
    assert model["kv_quant"] == "off"
    assert model["pi"]["thinking"] == "medium"
    assert model["pi"]["thinking_levels"] == ["low", "medium", "xhigh"]
    assert data["sampling"]["qwen38-thinking"]["temperature"] == 1.0
