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
