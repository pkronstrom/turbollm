# Code Review: turbollm

## Critical Issues

### 1. `__version__` mismatch
`pyproject.toml:2` declares `0.2.0` but `__init__.py:1` has `0.1.0`.

### 2. `_model_path()` can return `None`, crashing `build_serve_cmd()`
`vllm_mlx.py:21` calls `self._model_path(model)` which returns `None` when no safetensors are found. This becomes `str(None)` = `"None"` passed as the model path to `vllm-mlx serve`. Should raise a clear error instead.

### 3. `pull()` doesn't validate model has `hf_repo`
`cli.py:76` calls `resolve_model()` which returns a fallback dict with no `hf_repo` if the model isn't in the registry. Then `provider.pull(m)` crashes on `model["hf_repo"]` with a KeyError. User gets a confusing error instead of "model not found".

### 4. `opencode` command swallows stderr
`cli.py:274`: `stderr=subprocess.PIPE` but never read. If the server buffers stderr, the process could block and hang indefinitely. Should use `subprocess.DEVNULL` like stdout.

## Moderate Issues

### 5. `_launch_opencode()` clobbers existing config
`cli.py:234`: `existing.setdefault("provider", {}).update(turbo_config["provider"])` blindly overwrites any existing `turbo` provider entry. If the user has other providers configured, their config gets destroyed.

### 6. `_build_opencode_config()` inconsistency
`cli.py:193-196`: Checks `hasattr(provider, '_model_path')` to decide between a path string and `hf_repo`. This is fragile — depends on an internal method name. The fallback uses a repo string as a model ID, which is semantically different from an actual filesystem path.

### 7. `_hf_snapshot_path()` unreliable
`registry.py:66`: Uses `st_mtime` to pick the "latest" snapshot. This doesn't distinguish between a fully downloaded snapshot and a partial/corrupted one. A snapshot with just a config file could be picked over an incomplete download.

### 8. GGUF `_gguf_file()` fallback is fragile
`gguf.py:124-126`: If no `hf_file` is specified, it globs `*.gguf` and picks the first match. Repos often contain multiple quantizations — no guarantee of getting the right one.

## Low Priority / Code Quality

### 9. `pull()` progress bar is fake
`vllm_mlx.py:96-102`: `total=None` and the task is marked complete immediately after `hf_hub_download()` returns. The progress bar never shows actual progress — just a spinner per file.

### 10. `_load_env()` doesn't handle quoted values
`cli.py:18`: Simple `line.split("=", 1)` won't handle `KEY="value with spaces"` or `KEY='value'` formats common in `.env` files.

### 11. No schema validation
Model configs are dicts with no validation. Typos in keys (e.g., `tool_use` vs `tool-usage`) silently fail.

### 12. No error handling on TOML parsing
`registry.py:13`: If the TOML file has syntax errors, the entire CLI crashes.

### 13. `rm` command could report wrong size
`cli.py:144`: `sum(f.stat().st_size ...)` could fail with permission errors or symlinks pointing to nothing.

## Suggestions

- Add a `pydantic` or manual schema validation for model configs
- Return proper exceptions instead of `SystemExit(1)` from provider methods
- Add integration tests for the registry and provider resolution
- Consider using a proper `.env` library (e.g., `python-dotenv`) for edge cases