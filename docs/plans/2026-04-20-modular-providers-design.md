# Modular Provider Architecture

**Goal:** Clean provider interface so backends are swappable. Replace mlx-lm with vllm-mlx, keep llama-server for GGUF.

## Architecture

```
src/turbollm/
├── cli.py              # thin — delegates to providers
├── registry.py         # model config/alias resolution only
└── providers/
    ├── __init__.py     # Provider protocol + get_provider()
    ├── vllm_mlx.py     # MLX backend via vllm-mlx (default)
    └── gguf.py         # GGUF backend via llama-server
```

## Provider Protocol

```python
class Provider(Protocol):
    name: str
    install_hint: str

    def is_available(self) -> bool: ...
    def build_serve_cmd(self, model: dict, port: int) -> list[str]: ...
    def pull(self, model: dict) -> None: ...
    def is_downloaded(self, model: dict) -> bool: ...
```

## Key decisions

- **Drop mlx-lm** — vllm-mlx is a superset (same MLX models, adds MCP + batching)
- **No bundled ML deps** — both backends are external binaries, auto-detected via PATH
- **pyproject.toml** — only click, huggingface-hub, rich
- **is_downloaded() and pull() move to providers** — each backend knows its file layout
- **Registry stays** for model config resolution, providers handle files + serving
- **Lazy imports** in get_provider() — don't load gguf code if using vllm-mlx
- **models.toml** — `backend = "vllm-mlx"` (default) or `backend = "gguf"`
- **Existing MLX downloads work** — vllm-mlx reads same safetensors from HF cache

## Adding a new provider

1. Create `providers/new_backend.py` with class implementing Provider protocol
2. Register in `providers/__init__.py` `_providers` dict
3. Add models to `models.toml` with `backend = "new-backend"`
