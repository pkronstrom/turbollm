# oMLX Provider + Pi.dev Harness Integration

**Date**: 2026-04-21
**Status**: Design complete, ready for implementation

## Context

TurboLLM currently has two providers (vllm-mlx, gguf) and six harnesses (opencode, hermes, goose, codex, aichat, qwen-code). This plan adds:

1. **oMLX** — inference server that evolved from vllm-mlx, adding SSD KV caching, multi-model serving, and auto-detected tool calling
2. **Pi.dev** — lightweight terminal coding agent by Mario Zechner (badlogic)

## Design Decisions

### oMLX as a separate provider (not extending vllm-mlx)

oMLX has its own flag surface and a fundamentally different serving model (multi-model directory vs single-model process). Keeping it separate avoids muddying the vllm-mlx provider and lets both coexist during transition.

### oMLX as a long-running multi-model service (not single-model-per-process)

oMLX auto-discovers all models in `--model-dir` and serves them with LRU eviction. TurboLLM adapts by:
- Checking if oMLX is already running on the port (existing `_server_is_running` logic)
- If not, starting oMLX pointed at the model directory
- Selecting the specific model via `model_id` in the API request, not at server startup

<!-- Option B: If multi-model serving becomes annoying (memory pressure from
auto-loading unwanted models), you can force single-model by pointing --model-dir
at a temp dir containing only one symlink to the target model's HF snapshot.
Swap model_dir in build_serve_cmd for a tempdir with just the target model's symlink. -->

### Symlink farm for model discovery

oMLX expects `--model-dir` to contain `org/model-name/` subdirectories. Models live in the HF cache. The oMLX provider manages symlinks:
- `~/.omlx/models/mlx-community/Qwen3.6-35B-A3B-4bit` → `~/.cache/huggingface/hub/models--mlx-community--Qwen3.6-35B-A3B-4bit/snapshots/<hash>/`
- Created during `turbo pull`, removed during `turbo rm`
- Models stay in HF cache (shared with vllm-mlx provider)

### Pi.dev as a generic TOML-driven harness

Pi picks up `OPENAI_BASE_URL` from env — no custom harness class needed. Backend-agnostic: works with vllm-mlx, gguf, or omlx.

## File Changes

### 1. Create `src/turbollm/providers/omlx.py`

New `OmlxProvider` class implementing the `Provider` protocol.

```python
class OmlxProvider:
    name = "omlx"
    install_hint = "brew tap jundot/omlx && brew install omlx"
```

**`build_serve_cmd(model, port)`**:
```
omlx serve
  --model-dir ~/.omlx/models
  --port <port>
  --host 127.0.0.1
  --paged-ssd-cache-dir ~/.omlx/cache
  --paged-ssd-cache-max-size 100GB
  --hot-cache-max-size 20%
  --max-concurrent-requests 8
```

Flags sourced from `[defaults.omlx]` in models.toml, with per-model `[models.*.server]` overrides possible.

No `--served-model-name`, `--tool-call-parser`, or `--reasoning-parser` flags — oMLX auto-detects these from the model config.

**`pull(model)`**: Same HF download as vllm-mlx, then creates symlink:
```
~/.omlx/models/<org>/<model-name> → <hf-snapshot-path>
```

**`is_downloaded(model)`**: Checks HF cache has safetensors AND symlink exists.

**`is_available()`**: `shutil.which("omlx") is not None`

**Symlink helpers**:
- `_symlink_path(model)` → `~/.omlx/models/<org>/<model>`
- `_ensure_symlink(model)` — creates parent dirs + symlink
- `_remove_symlink(model)` — removes symlink, cleans empty parent dirs

### 2. Edit `src/turbollm/providers/__init__.py`

Add omlx to `get_provider()`:

```python
if backend == "omlx":
    from turbollm.providers.omlx import OmlxProvider
    return OmlxProvider()
```

Update the error message to include omlx in available backends.

### 3. Edit `models.toml`

Add defaults:
```toml
[defaults.omlx]
model_dir = "~/.omlx/models"
paged_ssd_cache_dir = "~/.omlx/cache"
paged_ssd_cache_max_size = "100GB"
hot_cache_max_size = "20%"
max_concurrent_requests = 8
```

Add Pi harness:
```toml
[harnesses.pi]
binary = "pi"
install = "npm install -g @mariozechner/pi-coding-agent"
cmd = ["pi"]
env = { OPENAI_BASE_URL = "http://127.0.0.1:{port}/v1" }
```

Add oMLX model entry:
```toml
[models.qwen36-35b-omlx-4bit]
name = "Qwen3.6 35B-A3B oMLX 4bit"
backend = "omlx"
hf_repo = "mlx-community/Qwen3.6-35B-A3B-4bit"
size_gb = 20
min_memory_gb = 24
tool_use = true
can_reason = true
tags = ["coding", "moe"]
```

## What's NOT changing

- `cli.py` — `_run_with_server`, `pick_model`, harness discovery all work as-is
- `registry.py` — model resolution, HF cache helpers unchanged
- `harnesses/__init__.py` — GenericHarness handles Pi, no custom class
- No new dependencies
