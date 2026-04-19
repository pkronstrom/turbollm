# turbollm

Ollama-like CLI for MLX TurboQuant models on Apple Silicon.

## Install

```bash
uv tool install git+https://github.com/pkronstrom/turbollm.git[serve]
```

Or from local checkout:

```bash
git clone https://github.com/pkronstrom/turbollm.git
cd turbollm
uv tool install -e ".[serve]"
```

## Usage

```bash
# List available models
turbo ls -a

# Pull a model (alias, owner/repo, or HuggingFace URL)
turbo pull qwen36-35b-tq4
turbo pull majentik/Qwen3.6-35B-A3B-TurboQuant-MLX-4bit
turbo pull https://huggingface.co/majentik/Qwen3.6-35B-A3B-TurboQuant-MLX-4bit

# List downloaded models
turbo ls

# Serve a model (OpenAI-compatible API on port 8899)
turbo serve qwen36-35b-tq4

# Serve + launch opencode
turbo opencode qwen36-35b-tq4

# Remove a model
turbo rm qwen36-35b-tq4
```

## Adding models

Edit `models.toml` to add new models:

```toml
[models.my-model]
name = "My Model"
hf_repo = "owner/repo-name"
size_gb = 20
tool_use = true
can_reason = true
```

Or pull any HF repo directly without adding it to the registry:

```bash
turbo pull owner/repo-name
```

## Requirements

- Apple Silicon Mac (M1+)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
