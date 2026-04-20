# Handoff: TurboLLM — Local LLM Serving CLI

**Date**: 2026-04-20
**Context**: Built from scratch a CLI tool (`turbo`) for managing and serving local LLMs on Apple Silicon M4 Max 128GB, with opencode/goose/hermes integration.

## Goal

Create an Ollama-like CLI that serves MLX and GGUF models locally with optimized settings, modular backend architecture, and agent harness integration (opencode, goose, hermes, codex, etc.)

## State

- **Done**: Full CLI with pull/ls/rm/serve/chat/opencode + agent harnesses, modular provider architecture (vllm-mlx + gguf), models.toml with Qwen3.6 35B-A3B configs, binary-searched working vllm-mlx flags
- **Active**: Nothing in progress
- **Next**: Test GGUF backend with llama-server (`brew install llama.cpp`), try Qwen3-Coder model, file vllm-mlx issue for Qwen3.6 batching bugs
- **Blocked**: Speculative decoding / specprefill blocked by Qwen3.6 linear attention arch (vllm#36872)

## Key Discoveries

### Qwen3.6 35B-A3B is HOSTILE to optimizations

The mixed attention architecture (full + SWA + linear attention) breaks nearly every vllm-mlx optimization:

- `--continuous-batching` → produces `!!!!!!` garbage
- `--kv-cache-quantization` → produces `!!!!!!` garbage
- `--chunked-prefill-tokens` → produces `!!!!!!` garbage
- `--specprefill` with draft model → produces `!!!!!!` garbage
- `--reasoning-parser qwen3` → strips newlines from content chunks in streaming mode

All traced to the linear attention layers not handling modified KV cache or batched states.

**Working flags**: `--use-paged-cache`, `--cache-memory-percent 0.40`, `--max-tokens 65536`, `--timeout 600`, `--enable-auto-tool-choice --tool-call-parser hermes`

### vllm-mlx model ID must match exactly

When opencode sends `model: "X"` in API requests, vllm-mlx tries to load model X. If it doesn't match the `--model` path, it downloads a new model. Solution: use `--served-model-name` to set a friendly name, or use the exact path in opencode config.

### mlx-lm `default_model` mapping doesn't exist in vllm-mlx

mlx-lm.server maps `--model` path to `"default_model"` internally. vllm-mlx does NOT do this — it uses the actual path as the model ID. Different behavior caused hours of debugging.

### HF download creates phantom cache entries

`hf_hub_download` with `local_dir=` puts files in custom dir BUT also creates entries in `~/.cache/huggingface/hub/` with just metadata (no safetensors). mlx-lm then finds those entries and tries to re-download. Solution: always download to HF's default cache.

### TurboQuant is broken for Qwen3.6

`majentik/Qwen3.6-35B-A3B-TurboQuant-MLX-4bit` produces garbage thinking (`!!!!!!`). The standard `mlx-community/Qwen3.6-35B-A3B-4bit` works fine.

### mlx-lm 0.31.2 has batch cache bug

Causes shape mismatch crashes. Excluded in deps. See ml-explore/mlx-lm#1139.

### Tool call parser must match model variant

`--tool-call-parser qwen3_coder` is for Qwen3-Coder models only. Base Qwen3.6 uses `hermes` parser. Wrong parser → garbage output.

### opencode `OPENCODE_CONFIG_CONTENT` replaces entire config

Must merge turbo provider into existing config, not replace. Read `~/.config/opencode/opencode.json`, merge, set env var.

## Problems Solved

### HF xet download backend shows no progress
- **Symptom**: `turbo pull` shows 0% forever
- **Root cause**: HF's new xet backend ignores `HF_HUB_DISABLE_PROGRESS_BARS` and tqdm
- **Solution**: Show per-file completion status instead of per-byte progress

### Server re-downloads model on every request
- **Symptom**: `GET /v1/models` triggers 20GB download
- **Root cause**: Incomplete HF cache entries (metadata without safetensors) + model ID mismatch
- **Solution**: Delete phantom cache, use `--served-model-name` or exact path as model ID

### Opencode model not auto-selected
- **Symptom**: User has to manually pick turbo model in opencode
- **Solution**: Set `model.chat` in `OPENCODE_CONFIG_CONTENT` to auto-select

## Critical Files

- `models.toml` — All model configs, server flags, harness definitions. **THE source of truth**
- `src/turbollm/providers/vllm_mlx.py` — vllm-mlx command builder. Where broken flags are documented
- `src/turbollm/providers/__init__.py` — Provider protocol definition
- `src/turbollm/cli.py` — CLI commands, server lifecycle, chat client, harness launcher
- `src/turbollm/registry.py` — Model resolution, HF cache path helpers

## Decisions Made

| Decision | Why | Don't retry |
|----------|-----|-------------|
| vllm-mlx over mlx-lm | MCP tool calling, Anthropic API, continuous batching (when it works) | mlx-lm is abandoned upstream |
| No bundled ML deps | Backends change fast, better as external binaries | `[serve]` optional dep was heavyweight and version-fragile |
| Provider protocol (not ABC) | Structural typing, no inheritance needed | ABC adds ceremony for no benefit |
| Thinking mode ON | Improves code quality significantly | Don't disable with `enable_thinking: false` |
| No `--reasoning-parser` | Strips newlines from streaming content | Thinking comes through as `<think>` tags instead |
| 65k context (not 131k) | TTFT was 16-27s at 131k, ~8s at 65k | 131k works but is painfully slow |
| Hermes tool parser | Base Qwen3.6 uses hermes format | `qwen3_coder` parser is for Coder variant only |

## Useful Commands

```bash
# Serve with all working optimizations
turbo serve qwen36-35b-mlx-4bit

# Quick chat test (good for flag debugging)
turbo chat

# Check what model ID server reports
curl -s http://localhost:8899/v1/models | python3 -m json.tool

# Test streaming content for newlines
curl -s http://localhost:8899/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/Qwen3.6-35B-A3B-4bit","messages":[{"role":"user","content":"Write a 3-line poem"}],"stream":true}' | \
  python3 -c "import sys,json; [print(f'CONTENT: {repr(c)}') for l in sys.stdin if l.strip().startswith('data: ') and l.strip() != 'data: [DONE]' for c in [json.loads(l.strip()[6:])['choices'][0]['delta'].get('content')] if c]"

# Binary search flags: add one flag at a time to models.toml [server], restart, test with turbo chat
```

---

## Resume Prompt

Copy this to start a new session:

```
I'm working on turbollm at ~/Projects/own/turbollm — an Ollama-like CLI for local LLM serving on Apple Silicon.

Read the handoff at .claude/handoffs/2026-04-20-1200-turbollm-local-llm-cli.md for full context.

Key things to know:
- Modular provider architecture: src/turbollm/providers/ (vllm-mlx + gguf)
- Qwen3.6 35B-A3B linear attention breaks most vllm-mlx optimizations (batching, KV quant, chunked prefill, specprefill all produce garbage)
- Working vllm-mlx flags: paged_cache, cache_memory_percent 0.40, max_tokens 65536
- Thinking mode stays ON (no --reasoning-parser, no enable_thinking:false)
- models.toml is the source of truth for all configs

Current state: CLI is functional with pull/ls/rm/serve/chat/opencode + agent harnesses. GGUF backend untested (needs `brew install llama.cpp`).

[Your task here]
```
