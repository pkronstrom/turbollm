import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from turbollm import cli, registry


def model(backend="vllm-mlx"):
    return {"name": "Test", "hf_repo": "org/test", "backend": backend,
            "context_default": 65536, "server": {"max_tokens": 65536},
            "pi": {"context_window": 65536}, "omp": {"context_window": 65536},
            "opencode": {"context_length": 65536}}


def test_registry_cache_invalidates_and_does_not_leak_mutations(tmp_path, monkeypatch):
    path = tmp_path / "models.toml"
    path.write_text('[models.a]\nname="First"\nhf_repo="org/a"\n')
    monkeypatch.setattr(registry, "USER_TOML", path)
    original = registry._load_toml
    parse = Mock(side_effect=original)
    monkeypatch.setattr(registry, "_load_toml", parse)
    first = registry.load_registry()
    first["models"]["a"]["name"] = "mutated"
    assert registry.load_registry()["models"]["a"]["name"] == "First"
    assert parse.call_count == 1
    replacement = tmp_path / "new.toml"
    replacement.write_text('[models.a]\nname="Second"\nhf_repo="org/a"\n')
    replacement.replace(path)
    assert registry.load_registry()["models"]["a"]["name"] == "Second"
    assert parse.call_count == 2


def setup_dispatch(monkeypatch, selected):
    monkeypatch.setattr(cli, "get_defaults", lambda: {})
    monkeypatch.setattr(cli, "load_registry", lambda: {"models": {"test": selected}, "harnesses": {"pi": {}}})
    monkeypatch.setattr(cli, "resolve_model", lambda _: selected)
    launch = Mock()
    monkeypatch.setattr(cli, "_run_harness", launch)
    return launch


def test_context_flag_overrides_all_existing_limits(monkeypatch):
    selected = model()
    launch = setup_dispatch(monkeypatch, selected)
    monkeypatch.setattr(cli, "_server_is_running", lambda _: False)
    cli._dispatch_harness("pi", "test", 8899, None, None, context_window=16384)
    actual = launch.call_args.args[1]
    assert registry.effective_context(actual) == 16384
    assert actual["pi"]["context_window"] == 16384
    assert actual["omp"]["context_window"] == 16384
    assert actual["opencode"]["context_length"] == 16384
    assert selected["server"]["max_tokens"] == 65536


@pytest.mark.parametrize("explicit", [None, "test"])
def test_attachment_uses_served_context_without_flag(monkeypatch, explicit):
    selected = model()
    launch = setup_dispatch(monkeypatch, selected)
    monkeypatch.setattr(cli, "_server_is_running", lambda _: True)
    monkeypatch.setattr(cli, "_get_running_model", lambda _: selected)
    monkeypatch.setattr(cli, "_read_port_stamp", lambda _: {"max_tokens": 16384, "alias": "test"})
    cli._dispatch_harness("pi", explicit, 8899, None, None)
    assert registry.effective_context(launch.call_args.args[1]) == 16384


def test_explicit_model_refuses_different_running_model(monkeypatch):
    selected = model()
    launch = setup_dispatch(monkeypatch, selected)
    monkeypatch.setattr(cli, "_server_is_running", lambda _: True)
    monkeypatch.setattr(cli, "_get_running_model", lambda _: {**selected, "hf_repo": "org/other"})
    with pytest.raises(Exception, match="(?i)(different|serving|mismatch)"):
        cli._dispatch_harness("pi", "test", 8899, None, None)
    launch.assert_not_called()


def test_running_model_matches_omlx_basename(monkeypatch):
    selected = {**model("omlx"), "server": {}}
    monkeypatch.setattr(cli, "_read_port_stamp", lambda _: None)
    monkeypatch.setattr(cli, "load_registry", lambda: {"models": {"test": selected}})
    response = Mock()
    response.read.return_value = json.dumps({"data": [{"id": "test"}]}).encode()
    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda *a, **k: response)
    assert cli._get_running_model(8899)["backend"] == "omlx"


def test_pull_update_refreshes_downloaded_model(monkeypatch):
    provider = Mock()
    provider.is_downloaded.return_value = True
    monkeypatch.setattr(cli, "resolve_model", lambda _: model())
    monkeypatch.setattr(cli, "_get_provider_for", lambda _: provider)
    result = CliRunner().invoke(cli.cli, ["pull", "test", "--update", "--revision", "abc123"])
    assert result.exit_code == 0, result.output
    provider.pull.assert_called_once()
    assert provider.pull.call_args.args[0]["revision"] == "abc123"


def test_second_attach_preserves_smaller_client_context(monkeypatch):
    selected = registry.with_context_window(model(), 16384)
    monkeypatch.setattr(cli, "_server_is_running", lambda _: True)
    monkeypatch.setattr(cli, "_get_running_model", lambda _: model())
    monkeypatch.setattr(cli, "_read_port_stamp", lambda _: {"max_tokens": 65536})
    result = cli._run_with_server(selected, 8899, lambda current, _: registry.effective_context(current))
    assert result == 16384


def test_stamp_preserves_backend_and_id_after_registry_edit(monkeypatch):
    changed = model("omlx")
    monkeypatch.setattr(cli, "resolve_model", lambda _: changed)
    monkeypatch.setattr(cli, "_read_port_stamp", lambda _: {"alias": "test", "hf_repo": "org/test", "backend": "gguf", "model_id": "org/test", "max_tokens": 16384})
    running = cli._get_running_model(8899)
    assert running["backend"] == "gguf"
    assert cli._get_model_id(running) == "org/test"


def test_unpinned_stamp_does_not_inherit_later_registry_pin(monkeypatch):
    monkeypatch.setattr(cli, "resolve_model", lambda _: {**model(), "revision": "new-pin"})
    monkeypatch.setattr(cli, "_read_port_stamp", lambda _: {"alias": "test", "hf_repo": "org/test", "backend": "vllm-mlx", "revision": None})
    assert cli._get_running_model(8899)["revision"] is None


def test_explicit_pin_refuses_unknown_running_revision(monkeypatch):
    monkeypatch.setattr(cli, "_get_running_model", lambda _: model())
    with pytest.raises(Exception, match="different model revision"):
        cli._attach_model({**model(), "revision": "new-pin"}, 8899)


def test_explicit_local_path_refuses_other_served_provider_id(monkeypatch):
    running = {**model("mlx-vlm"), "_served_model_id": "/models/old"}
    monkeypatch.setattr(cli, "_get_running_model", lambda _: running)
    provider = Mock()
    provider.get_model_id.return_value = "/models/new"
    monkeypatch.setattr(cli, "_get_provider_for", lambda _: provider)
    with pytest.raises(Exception, match="different provider model ID"):
        cli._attach_model(model("mlx-vlm"), 8899)
