# Dual Backend (MLX + GGUF) Design

**Goal:** Add llama-server as a second backend alongside mlx-lm, with sane per-model settings in models.toml.

**Architecture:** `backend` field in models.toml determines which server turbo invokes. GGUF models get a `[server]` sub-table with llama-server flags. Auto-detect llama-server in PATH, prompt to install if missing.

## models.toml structure

- `backend = "mlx"` or `backend = "gguf"`
- GGUF entries have `hf_file` for single-file download
- GGUF entries have `[server]` sub-table with llama-server flags
- Per-model `[opencode]` overrides for context/output length
- Sampling defaults from Qwen's official recommendations

## Serve behavior

- MLX: same as today, `mlx_lm server`
- GGUF: invoke `llama-server` with flags from `[server]` table
- Both serve OpenAI-compatible API on same port
- `turbo opencode` works identically regardless of backend

## Pull behavior

- MLX: `snapshot_download` (full repo)
- GGUF: `hf_hub_download` single file via `hf_file` field

## Settings (from Qwen model card + r/localllama)

- temperature=0.6, top_p=0.95, top_k=20 for coding
- enable_thinking=false for tool use
- KV cache q8_0, flash attention, swa_full
- 131072 context for 35B models on 128GB machine
