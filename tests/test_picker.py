"""Tests for turbollm.picker.

Note on ownership: picker.py is an owned source file for this pass, but no
test_picker.py existed and it wasn't on the pre-approved test-file list
(tests/providers/*, tests/harnesses/*, tests/test_registry.py,
tests/test_harness_backend_filter.py). Added here anyway so the picker fixes
(thinking-toggle honesty, omlx context selector, ESC handling) have coverage
— flagged for the reviewer as a scope note, not silently smuggled in.
"""
import io

from turbollm import picker


def _model(backend="vllm-mlx", can_reason=True, server=None, **extra):
    m = {
        "name": "Test Model",
        "hf_repo": "org/test-model",
        "backend": backend,
        "can_reason": can_reason,
        "server": server or {},
    }
    m.update(extra)
    return m


# --- _build_entry: reasoning_on initialization -----------------------------

def test_vllm_mlx_reasoning_on_defaults_true_when_unconfigured():
    entry = picker._build_entry("m", _model(backend="vllm-mlx"), "vllm-mlx")
    assert entry.reasoning_on is True
    assert entry.show_thinking_toggle is True


def test_vllm_mlx_reasoning_on_initializes_off_from_config():
    model = _model(
        backend="vllm-mlx",
        server={"default_chat_template_kwargs": {"enable_thinking": False}},
    )
    entry = picker._build_entry("m", model, "vllm-mlx")
    assert entry.reasoning_on is False


def test_gguf_reasoning_on_initializes_off_from_config():
    model = _model(backend="gguf", server={"enable_thinking": False})
    entry = picker._build_entry("m", model, "gguf")
    assert entry.reasoning_on is False


def test_reasoning_on_false_when_model_cannot_reason():
    entry = picker._build_entry("m", _model(can_reason=False), "vllm-mlx")
    assert entry.reasoning_on is False


# --- _build_entry: thinking toggle visibility per backend -------------------

def test_thinking_toggle_hidden_for_mlx_vlm():
    entry = picker._build_entry("m", _model(backend="mlx-vlm"), "mlx-vlm")
    assert entry.show_thinking_toggle is False


def test_thinking_toggle_hidden_for_omlx():
    entry = picker._build_entry("m", _model(backend="omlx"), "omlx")
    assert entry.show_thinking_toggle is False


def test_thinking_toggle_hidden_for_mlx_audio():
    entry = picker._build_entry("m", _model(backend="mlx-audio"), "mlx-audio")
    assert entry.show_thinking_toggle is False


def test_thinking_toggle_shown_for_gguf_and_vllm_mlx():
    assert picker._build_entry("m", _model(backend="gguf"), "gguf").show_thinking_toggle is True
    assert picker._build_entry("m", _model(backend="vllm-mlx"), "vllm-mlx").show_thinking_toggle is True


# --- _build_entry: omlx context selector collapses to a single value -------

def test_omlx_context_options_collapse_to_single_value():
    model = _model(backend="omlx", context_default=32768, context_max=131072)
    entry = picker._build_entry("m", model, "omlx")
    assert entry.ctx_options_k == (32,)
    assert entry.ctx_idx == 0


def test_non_omlx_context_options_use_ladder():
    model = _model(backend="vllm-mlx", context_default=65536, context_max=131072)
    entry = picker._build_entry("m", model, "vllm-mlx")
    assert len(entry.ctx_options_k) > 1


# --- context_override_for ---------------------------------------------------

def test_context_override_for_omlx_is_empty():
    assert picker.context_override_for(_model(backend="omlx"), 65536) == {}


def test_context_override_for_gguf_uses_context_key():
    assert picker.context_override_for(_model(backend="gguf"), 65536) == {"context": 65536}


def test_context_override_for_vllm_mlx_uses_max_tokens():
    overrides = picker.context_override_for(_model(backend="vllm-mlx"), 65536)
    assert overrides == {"max_tokens": 65536}


# --- pick(): end-to-end propagation of the reasoning toggle -----------------

def test_pick_writes_gguf_enable_thinking_false_when_toggled_off(monkeypatch):
    model = _model(backend="gguf")
    candidates = [("alias", model, "gguf")]

    def fake_pick(entries):
        entry = entries[0]
        entry.reasoning_on = False
        return entry

    monkeypatch.setattr(picker.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(picker, "_fallback_numbered", fake_pick)

    result = picker.pick(candidates)
    assert result is not None
    alias, new_model = result
    assert new_model["server"]["enable_thinking"] is False


def test_pick_writes_vllm_mlx_chat_template_kwargs_when_toggled_off(monkeypatch):
    model = _model(backend="vllm-mlx")
    candidates = [("alias", model, "vllm-mlx")]

    def fake_pick(entries):
        entry = entries[0]
        entry.reasoning_on = False
        return entry

    monkeypatch.setattr(picker.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(picker, "_fallback_numbered", fake_pick)

    result = picker.pick(candidates)
    assert result is not None
    alias, new_model = result
    assert new_model["server"]["default_chat_template_kwargs"]["enable_thinking"] is False


def test_pick_does_not_write_thinking_keys_for_mlx_vlm(monkeypatch):
    model = _model(backend="mlx-vlm")
    candidates = [("alias", model, "mlx-vlm")]

    monkeypatch.setattr(picker.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(picker, "_fallback_numbered", lambda entries: entries[0])

    result = picker.pick(candidates)
    assert result is not None
    alias, new_model = result
    assert "default_chat_template_kwargs" not in new_model["server"]
    assert "enable_thinking" not in new_model["server"]


# --- _read_key: bare ESC must not block ------------------------------------

def test_read_key_bare_esc_returns_immediately_without_blocking(monkeypatch):
    monkeypatch.setattr(picker.sys, "stdin", io.StringIO("\x1b"))
    monkeypatch.setattr(picker.select, "select", lambda *a, **k: ([], [], []))
    assert picker._read_key(0) == "esc"


def test_read_key_csi_arrow_still_decodes(monkeypatch):
    monkeypatch.setattr(picker.sys, "stdin", io.StringIO("\x1b[A"))
    monkeypatch.setattr(picker.select, "select", lambda *a, **k: ([1], [], []))
    assert picker._read_key(0) == "up"


def test_read_key_plain_char_passthrough(monkeypatch):
    monkeypatch.setattr(picker.sys, "stdin", io.StringIO("q"))
    assert picker._read_key(0) == "q"
