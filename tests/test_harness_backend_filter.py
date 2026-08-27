"""Text/chat harnesses must not offer ASR (mlx-audio) models in their picker.

The mlx-audio backend is a different modality (speech-to-text). Harnesses like
pi/claude/codex are chat harnesses and should exclude it by default, while a
harness may still opt into any backend via [harnesses.<name>].requires_backend.
"""
from turbollm import cli as turbo_cli

CHAT_BACKENDS = ["vllm-mlx", "gguf", "omlx", "mlx-vlm"]


def test_audio_backend_excluded_by_default():
    # A harness with no explicit requires_backend should reject mlx-audio.
    assert turbo_cli._is_backend_compatible({}, "mlx-audio") is False


def test_chat_backends_allowed_by_default():
    for backend in CHAT_BACKENDS:
        assert turbo_cli._is_backend_compatible({}, backend) is True


def test_explicit_requires_backend_overrides_default():
    # A harness may opt into mlx-audio explicitly.
    cfg = {"requires_backend": ["mlx-audio"]}
    assert turbo_cli._is_backend_compatible(cfg, "mlx-audio") is True
    # And an explicit allow-list still excludes backends not in it.
    assert turbo_cli._is_backend_compatible({"requires_backend": ["vllm-mlx"]}, "gguf") is False


def test_default_requires_backend_lists_chat_backends_only():
    eff = turbo_cli._harness_requires_backend({})
    assert "mlx-audio" not in eff
    for backend in CHAT_BACKENDS:
        assert backend in eff


def test_lean_harness_can_launch_a_new_server_when_port_is_occupied(monkeypatch):
    running = {"name": "Running Qwen", "backend": "gguf"}
    selected = {"name": "Picked Qwen", "backend": "gguf"}
    launched = []

    monkeypatch.setattr(
        turbo_cli,
        "load_registry",
        lambda: {"harnesses": {"omp-lean": {"offer_running_server_choice": True}}},
    )
    monkeypatch.setattr(turbo_cli, "_server_is_running", lambda port: port == 8899)
    monkeypatch.setattr(turbo_cli, "_get_running_model", lambda port: running)
    monkeypatch.setattr(turbo_cli.click, "prompt", lambda *args, **kwargs: "launch-new-server")
    monkeypatch.setattr(turbo_cli, "_next_free_port", lambda port: 8901)
    monkeypatch.setattr(turbo_cli, "pick_model", lambda requires_backend: ("picked", selected))
    monkeypatch.setattr(
        turbo_cli,
        "_run_harness",
        lambda harness_name, model, port, prompt=None: launched.append(
            (harness_name, model, port, prompt)
        ),
    )

    turbo_cli._dispatch_harness("omp-lean", None, None, None, None)

    assert launched == [("omp-lean", selected, 8901, None)]


def test_lean_harness_attaches_to_running_server_when_selected(monkeypatch):
    running = {"name": "Running Qwen", "backend": "gguf"}
    launched = []

    monkeypatch.setattr(
        turbo_cli,
        "load_registry",
        lambda: {"harnesses": {"omp-lean": {"offer_running_server_choice": True}}},
    )
    monkeypatch.setattr(turbo_cli, "_server_is_running", lambda port: port == 8899)
    monkeypatch.setattr(turbo_cli, "_get_running_model", lambda port: running)
    monkeypatch.setattr(turbo_cli.click, "prompt", lambda *args, **kwargs: "use-running-server")
    monkeypatch.setattr(
        turbo_cli,
        "_run_harness",
        lambda harness_name, model, port, prompt=None: launched.append(
            (harness_name, model, port, prompt)
        ),
    )

    turbo_cli._dispatch_harness("omp-lean", None, None, None, None)

    assert launched == [("omp-lean", running, 8899, None)]
