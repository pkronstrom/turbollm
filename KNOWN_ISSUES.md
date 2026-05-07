# Known Issues

Issues that affect turbollm model serving. Re-check these periodically as upstream projects update.

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
