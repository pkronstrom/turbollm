# Known Issues

Issues that affect turbollm model serving. Re-check these periodically as upstream projects update.

## TurboHUD — Phase 1 architecture notes

### TCC re-grant required after Phase 1 upgrade

**Status:** Expected, one-time.

After upgrading to the two-binary architecture (Phase 1), macOS will prompt
you to grant **Microphone** access to `turbo-acquirer` on the first recording
run. If you use the `system+mic` scope, it also prompts for **Screen Recording**.
This happens because TCC grants are bound to binary identity, and the recording
code moved from `TurboHUD` to the new `turbo-acquirer` binary.

TurboHUD posts a notification explaining this on first launch after Phase 1.
The re-grant is a one-time event.

**Workaround:** Grant the permissions when prompted. Use `turbo-acquirer
permissions-state` to verify the current state.

### `turbo sidecar` must be run before first use

**Status:** By design.

`turbo sidecar` builds both `TurboHUD` and `turbo-acquirer` and writes symlinks
to `~/.local/bin/`. Without it, `turbo workflows run` cannot find `turbo-acquirer`
and will fall back to direct TCC calls from the HUD (which may not have mic
access). Always run `turbo sidecar` after cloning or updating the repo.



## Qwen3.6 35B-A3B on vllm-mlx

### Continuous batching produces garbage output

**Status:** Broken (as of April 2026)

Enabling `--continuous-batching` with Qwen3.6-35B-A3B on vllm-mlx produces corrupted/garbled output.
The root cause is likely that vllm-mlx hasn't implemented the hybrid KV cache manager needed for
Qwen3.5/3.6's mixed attention architecture (full attention + Gated DeltaNet linear attention).
GPU vLLM has this via Triton kernels from Flash Linear Attention, but the MLX backend appears to
lack equivalent support.

The same issue affects `--kv-cache-quantization` and `--chunked-prefill-tokens` — any optimization
that modifies KV cache or batch scheduling breaks the linear attention layers.

**Workaround:** Keep these flags disabled in models.toml. Requests are queued sequentially.

**Re-check:**
- [waybarrios/vllm-mlx](https://github.com/waybarrios/vllm-mlx) for hybrid attention / GatedDeltaNet support
- [vllm-mlx continuous batching docs](https://github.com/waybarrios/vllm-mlx/blob/main/docs/guides/continuous-batching.md) for Qwen3.5/3.6 being added to tested models
- [vLLM blog on Qwen3-Next](https://vllm.ai/blog/qwen3-next) — describes the hybrid KV cache manager that MLX needs to port

### Speculative prefill (specprefill) produces garbage output

**Status:** Broken (as of April 2026)

`--specprefill` with a draft model (e.g. Qwen3-0.6B) causes gibberish output. This is a known
upstream vLLM issue — the Multi-Token Predictor (MTP) had a bug where `spec_step_idx` wasn't
forwarded properly, causing all draft tokens to use MTP layer 0 instead of cycling through layers.

Fixed in GPU vLLM ([#36910](https://github.com/vllm-project/vllm/pull/36910)), but unclear if the
fix has landed in vllm-mlx.

**Re-check:**
- [vllm-project/vllm#36872](https://github.com/vllm-project/vllm/issues/36872) — original bug report
- [vllm-project/vllm#36910](https://github.com/vllm-project/vllm/pull/36910) — the fix
- Whether vllm-mlx has synced this fix

### Think tags leaking into OpenCode output

**Status:** Fixed (April 2026)

Without `reasoning_parser = "qwen3"` in models.toml, vllm-mlx passes raw `<think>...</think>` tags
in the API `content` field instead of extracting them into `reasoning_content`. OpenCode then renders
them as plain text.

**Fix:** Added `reasoning_parser = "qwen3"` to the model config. vllm-mlx passes `--reasoning-parser qwen3`
which extracts thinking into the `reasoning_content` field that OpenCode hides by default.

## mlx-audio (ASR backend)

### Repo names with dots crash model category lookup

**Status:** Patched locally (May 2026). Not reported upstream.

`mlx-community/parakeet-tdt-0.6b-v3` contains `0.6b`. `get_model_name_parts()` in
`mlx_audio/utils.py` derives candidates like `parakeet_tdt_0.6b`; `is_valid_module_name`
doesn't reject the `.`, so `importlib.util.find_spec("mlx_audio.tts.models.parakeet_tdt_0.6b")`
treats the dot as a package separator, fails on the non-existent `parakeet_tdt_0` parent, and
raises `ModuleNotFoundError` — aborting category resolution before the valid `parakeet`
candidate is ever tried. Server returns an empty chunked response; clients see
`http.client.IncompleteRead`.

**Local patch** applied to:
`~/.local/share/uv/tools/mlx-audio/lib/python3.12/site-packages/mlx_audio/utils.py`
in `is_valid_module_name()` — reject any name containing `.`. Patch is overwritten by
`uv tool upgrade mlx-audio`; reapply after upgrades.

**Re-check** after each `mlx-audio` upgrade — drop the patch if upstream filters dots in
`is_valid_module_name` or wraps `find_spec` in try/except.
