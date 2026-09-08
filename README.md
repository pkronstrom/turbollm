# turbollm

> ⚠️ **Early-stage project.** This is actively under development and rough around the edges — APIs, defaults, and command surface may shift between releases. Tested on the author's machine; mileage may vary. Issues and PRs welcome.

Ollama-like CLI for local LLM serving on Apple Silicon. Supports multiple backends (vllm-mlx, llama-server) and multiple agent harnesses (opencode, hermes, pi, omp, codex, aichat, qwen-code) with a unified interface.

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

Goose support has been retired. Existing user registries are preserved on
upgrade; remove their `[harnesses.goose]` section to retire the command there too.

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
uv tool install --with jinja2 mlx-vlm       # mlx-vlm — vision-language MLX models
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
turbo pull qwen38-27b-oq6e-mtp

# List downloaded models
turbo ls

# Serve a model (auto-detects backend)
turbo serve qwen38-27b-oq6e-mtp

# Remove a model
turbo rm qwen38-27b-oq6e-mtp
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
turbo codex [model]        # OpenAI Codex CLI
turbo aichat [model]       # AIChat
turbo qwen-code [model]    # Qwen Code

# Or use the generic run command
turbo run [model] -H pi
```

Install commands for the harnesses above (same hints `turbo ls -a` prints):

```bash
npm install -g @anthropic-ai/claude-code      # claude
brew install opencode                         # opencode
npm install -g @earendil-works/pi-coding-agent # pi
brew install --cask codex                     # codex
brew install aichat                           # aichat
brew install qwen-code                        # qwen-code
# hermes — see `turbo ls -a` Install column for the installer URL
```

### Recommended Qwen3.8 + Pi setup

The local coding profile uses the 6-bit MLX Qwen3.8 checkpoint and oMLX's
native MTP:

```bash
turbo pull qwen38-27b-oq6e-mtp
turbo pi qwen38-27b-oq6e-mtp
turbo pi qwen38-27b-oq6e-mtp --thinking xhigh
turbo omp qwen38-27b-oq6e-mtp
```

Pi defaults to `medium`; Qwen3.8 supports exactly `low`, `medium`, and
`xhigh`. Turbo sends Qwen's official thinking sampler and preserves thinking
across turns. Native MTP remains configured in oMLX, which verifies every
draft token before it is committed.
`turbo omp` also participates in the normal Turbo model picker. It writes only
the selected Turbo provider into OMP's native `models.yml`, preserving other
configured providers, then launches the model at its configured effort.

### Lean local OMP profile

`turbo omp-lean qwen38-27b-oq6e-mtp` starts an isolated OMP profile for local Qwen coding. It exposes file tools, Bash, LSP, ask, todo, and only the `vault-mcp` / `vault-skills` catalogue. It never changes the normal `turbo omp` profile, sessions, extensions, or configuration.

With no model argument, `turbo omp-lean` offers to attach to an already-running compatible Turbo server or start a new server on the next free local port and open the model picker. It never stops the running server.

The lean route is experimental. A [local smoke comparison](docs/stabilization-2026-09-08.md) measured 8,587 input tokens versus 23,917 for full OMP. File reading, LSP definition lookup, cross-file rename, and compile/run probes passed; broader interactive coding acceptance remains open. Use `turbo omp` when the full OMP tool surface is needed.

### Qwen3.8 oMLX Q6 MTP

`turbo pull qwen38-27b-oq6e-mtp` installs the `Jundot/Qwen3.8-27B-oQ6e-mtp` 6-bit MLX checkpoint under `~/.models`. Turbo configures oMLX native MTP for this model; use oMLX 0.6.4 or newer.


To make OMP lazily start a selected Turbo model on its first request, install
the bundled extension globally for OMP:

```bash
mkdir -p ~/.omp/agent/extensions
cp integrations/omp/turbo-autoserve.ts ~/.omp/agent/extensions/
```

The extension never replaces or kills a server. If port 8899 already serves a
different model, it asks you to switch it in the Turbo panel; otherwise it
starts `turbo serve` in the background and waits until the endpoint is ready.

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

### Screen-recording scope

For `screen-recording` workflow params, the HUD's scope submenu offers three modes:

| Scope | What it captures |
|-------|------------------|
| `full-display` | The whole primary display. |
| `region` | A rectangle you drag with the built-in picker (re-pick via "Re-pick region…"). |
| `window` | A specific window you choose via the native macOS picker (`SCContentSharingPicker`) — follows that window. |

Just before capture starts, the acquirer briefly flashes a green outline of the
exact area it's about to record (~1.5s) so you can confirm the target.

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
