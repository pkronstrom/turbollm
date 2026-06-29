# turbollm

> ⚠️ **Early-stage project.** This is actively under development and rough around the edges — APIs, defaults, and command surface may shift between releases. Tested on the author's machine; mileage may vary. Issues and PRs welcome.

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

Backends are the actual model servers. Install at least one; turbo only uses
what's on your PATH and tells you what's missing when you try to use it.

```bash
# vllm-mlx — MLX models, recommended default for Apple Silicon
uv tool install git+https://github.com/waybarrios/vllm-mlx.git

# llama.cpp — GGUF models
brew install llama.cpp

# Optional extras:
brew tap jundot/omlx && brew install omlx   # omlx — alternative MLX server
uv tool install mlx-vlm                     # mlx-vlm — vision-language MLX models
uv tool install mlx-audio                   # mlx-audio — Parakeet/ASR (used by `turbo transcribe`)
```

Run `turbo ls -a` at any time to see which backends are installed, which are
missing, and the install command for each. If you `turbo serve` a model whose
backend isn't installed, turbo prints the install hint and exits.

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

Harnesses are agentic CLI tools that connect to the turbo server. If a server
is already running, harnesses attach to it directly. Otherwise, a model picker
is shown.

Harnesses are **not** installed automatically — install the ones you actually
want to use. Like backends, missing harnesses produce a clear install hint
when invoked, and `turbo ls -a` lists status + install command for every
registered harness.

```bash
# Launch by name — attaches to running server or starts one
turbo claude [model]       # Claude Code (Anthropic Messages API)
turbo opencode [model]     # OpenCode IDE
turbo pi [model]           # pi-coding-agent (default for headless reasoning)
turbo hermes [model]       # Hermes Agent
turbo goose [model]        # Goose
turbo codex [model]        # OpenAI Codex CLI
turbo aichat [model]       # AIChat
turbo qwen-code [model]    # Qwen Code

# Or use the generic run command
turbo run [model] -H goose
```

Install commands for the harnesses above (same hints `turbo ls -a` prints):

```bash
npm install -g @anthropic-ai/claude-code      # claude
brew install opencode                         # opencode
npm install -g @mariozechner/pi-coding-agent  # pi
brew install --cask codex                     # codex
brew install aichat                           # aichat
brew install qwen-code                        # qwen-code
# goose, hermes — see `turbo ls -a` Install column for the latest URL
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

## Workflows

Workflows are shell snippets declared in `models.toml` and run via a unified
CLI entrypoint:

```bash
turbo workflows list
turbo workflows run transcribe-file --param file=/path/to/audio.wav
```

### Configuring sticky workflow params

Use `turbo workflows config` to pre-set params that would otherwise be
prompted for:

```bash
# Set the Obsidian vault path for record-to-obsidian:
turbo workflows config record-to-obsidian vault=/Users/you/Documents/Obsidian

# List all configured stickies for a workflow:
turbo workflows config record-to-obsidian

# Clear a sticky:
turbo workflows config record-to-obsidian vault=
```

## macOS sidecar + Raycast (optional plugin)

The repo ships three macOS-only companions under `tools/`, surfaced through an
optional in-tree plugin (`turbollm.plugins.mac`):

| Tool | Role |
|------|------|
| `turbo-acquirer` (Swift CLI) | Acquires media: records audio, takes screenshots, runs shell commands. Holds macOS TCC permissions (Microphone, Screen Recording). |
| `TurboHUD` (Swift menu-bar app) | Shows active workflows and server status; triggers runs from a menu. |
| `raycast-turbo` (Raycast extension) | Exposes every workflow as a searchable Raycast command with in-flight activities and a one-click Stop. |

These are **not code-signed**, not bundled as a `.app`, and require manual TCC
permission grants on first run — so the core `turbo` CLI carries none of this
surface. The `turbo sidecar` and `turbo raycast` commands **auto-register only**
when you're on macOS with the Swift toolchain and the in-tree `tools/` sources
present; on Linux or a core-only install they simply don't appear. (The
`turbollm[mac]` install extra documents the opt-in; activation itself is
capability-based.)

```bash
turbo sidecar         # build turbo-acquirer + TurboHUD, symlink, launch HUD
turbo raycast sync    # regenerate Raycast commands from models.toml
```

On first media-capturing run, macOS will prompt you to grant **Microphone**
(and **Screen Recording** for the `system+mic` scope) to `turbo-acquirer`.
This is a one-time grant per binary.

Set `TURBO_PLUGIN_DEBUG=1` to print a traceback if a plugin fails to register
(otherwise such failures are swallowed so they can never break core `turbo`).

See [`tools/raycast-turbo/README.md`](tools/raycast-turbo/README.md) for
Raycast-specific install steps.

## Requirements

- Apple Silicon Mac (M1+)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- (Sidecars only) Xcode command-line tools for `swift build`

## License

MIT — see [`LICENSE`](LICENSE).
