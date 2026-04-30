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
