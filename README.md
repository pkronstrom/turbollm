# turbollm

Ollama-like CLI for local LLM serving on Apple Silicon. Supports multiple backends (vllm-mlx, llama-server) and multiple agent harnesses (opencode, hermes, goose, codex, aichat, qwen-code) with a unified interface.

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

Turbo bootstraps user config into `~/.turbollm/` on first run:

- `~/.turbollm/models.toml` is the canonical model and harness registry
- `~/.turbollm/.env` is loaded for user-specific environment variables
- bundled `models.toml` is only used to seed `~/.turbollm/models.toml` if it does not exist yet

### Backends

Install at least one backend:

```bash
# MLX models (recommended for Apple Silicon)
uv tool install git+https://github.com/waybarrios/vllm-mlx.git

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

# Remove a model
turbo rm qwen36-35b-mlx-4bit
```

## Harnesses (agent CLIs)

Harnesses are agentic CLI tools that connect to the turbo server. If a server is already running, harnesses attach to it directly. Otherwise, a model picker is shown.

```bash
# Launch by name — attaches to running server or starts one
turbo claude [model]       # Claude Code (Anthropic Messages API)
turbo opencode [model]     # OpenCode IDE
turbo hermes [model]       # Hermes Agent
turbo goose [model]        # Goose
turbo codex [model]        # OpenAI Codex CLI
turbo aichat [model]       # AIChat
turbo qwen-code [model]    # Qwen Code

# Or use the generic run command
turbo run [model] -H goose
```

### Adding a harness

Add to `models.toml`:

```toml
[harnesses.my-tool]
binary = "my-tool"                          # binary to look for in PATH
install = "pip install my-tool"             # install instructions
cmd = ["my-tool", "--model", "{model_id}"]  # command template
env = { MY_VAR = "{port}" }                 # extra env vars (optional)
```

Template variables: `{model_id}` (model name from server), `{port}` (server port).

For harnesses needing custom logic beyond env + cmd (like opencode's JSON config generation), add a Python class in `src/turbollm/harnesses/` implementing the `Harness` protocol and register with `@register("name")`.

## Adding models

Edit `~/.turbollm/models.toml`:

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

## TurboHUD (macOS menu-bar companion)

`turbo sidecar` builds and launches **TurboHUD** — a menu-bar app that shows
active workflow and server status. Phase 1 introduced a two-binary architecture:

| Binary | Role |
|--------|------|
| `TurboHUD` | Menu-bar app. Renders workflow list, shows activity, triggers runs. |
| `turbo-acquirer` | Acquires media: records audio, takes screenshots, runs shell commands. Holds macOS TCC permissions (Microphone, Screen Recording). |

`turbo sidecar` builds both binaries and symlinks them to `~/.local/bin/`.

### Running workflows

All workflows are available via a unified CLI entrypoint:

```bash
turbo workflows run record-to-obsidian     # headless or with HUD running
turbo workflows run transcribe-file --param file=/path/to/audio.wav
```

The HUD menu Run button shells out to the same `turbo workflows run` command,
so HUD and CLI produce identical results.

### Configuring sticky workflow params

Use `turbo workflows config` to pre-set params that the HUD or CLI would
otherwise prompt for:

```bash
# Set the Obsidian vault path for record-to-obsidian:
turbo workflows config record-to-obsidian vault=/Users/you/Documents/Obsidian

# List all configured stickies for a workflow:
turbo workflows config record-to-obsidian

# Clear a sticky:
turbo workflows config record-to-obsidian vault=
```

### TCC permissions after first install

On first recording after deploying Phase 1, macOS will prompt you to grant
**Microphone** (and **Screen Recording** if you use `system+mic` scope) to
`turbo-acquirer`. This is expected and one-time — the binary identity changed
from the HUD to the dedicated acquirer binary. You will see a notification
from TurboHUD explaining this on first launch.

## Requirements

- Apple Silicon Mac (M1+)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
