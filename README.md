# turbollm

Ollama-like CLI for local LLM serving on Apple Silicon. Supports multiple backends (vllm-mlx, llama-server) with a unified interface.

## Install

```bash
uv tool install git+https://github.com/pkronstrom/turbollm.git
```

Or from local checkout:

```bash
git clone https://github.com/pkronstrom/turbollm.git
cd turbollm
uv tool install -e .
```

### Backends

Install at least one backend:

```bash
# MLX models (recommended for Apple Silicon)
pip install git+https://github.com/waybarrios/vllm-mlx.git

# GGUF models
brew install llama.cpp
```

## Usage

```bash
# List available models
turbo ls -a

# Pull a model (alias, owner/repo, or HuggingFace URL)
turbo pull qwen36-35b-mlx-4bit
turbo pull https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-4bit

# List downloaded models
turbo ls

# Serve a model (auto-detects backend)
turbo serve qwen36-35b-mlx-4bit

# Serve + launch opencode
turbo opencode qwen36-35b-mlx-4bit

# Serve + launch hermes-agent
turbo hermes qwen36-35b-mlx-4bit

# Serve + launch goose
turbo goose qwen36-35b-mlx-4bit

# Remove a model
turbo rm qwen36-35b-mlx-4bit
```

### Agent CLIs

Install any supported agent CLI:

```bash
# hermes-agent (pip/uv)
pip install hermes-agent

# goose (brew or binary)
brew install goose
```

Then launch with `turbo hermes` or `turbo goose` — turbollm starts the server and configures the agent to use it automatically via OpenAI-compatible API environment variables.

## Adding models

Edit `models.toml`:

```toml
[models.my-model]
name = "My Model"
backend = "vllm-mlx"           # or "gguf"
hf_repo = "owner/repo-name"
size_gb = 20
tool_use = true
can_reason = true

# GGUF models need a specific file
[models.my-gguf-model]
backend = "gguf"
hf_repo = "owner/repo-name"
hf_file = "model-Q4_K_M.gguf"

# Per-model server settings (GGUF only)
[models.my-gguf-model.server]
ngl = 999
context = 131072
flash_attention = true
```

## Adding a new backend

Create `src/turbollm/providers/my_backend.py` implementing the `Provider` protocol, register it in `providers/__init__.py`.

## Requirements

- Apple Silicon Mac (M1+)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
