# TurboLLM CLI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A minimal Ollama-like CLI (`turbo`) for managing and serving MLX TurboQuant models on Apple Silicon, installable via `uv tool install`.

**Architecture:** Single Python package with a Click CLI. Models registry in `models.toml`. Downloads go to `~/.turbollm/models/` (shared across projects). Server wraps `mlx_lm.server`. OpenCode integration generates a temp config and launches `opencode`.

**Tech Stack:** Python 3.11+, Click, mlx-lm, huggingface-hub, tomllib (stdlib)

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/turbollm/__init__.py`
- Create: `src/turbollm/cli.py`

**Step 1: Init git repo**

```bash
cd <repo>
git init
```

**Step 2: Create pyproject.toml**

```toml
[project]
name = "turbollm"
version = "0.1.0"
description = "Ollama-like CLI for MLX TurboQuant models on Apple Silicon"
requires-python = ">=3.11"
dependencies = [
    "click>=8.0",
    "huggingface-hub>=0.20",
    "rich>=13.0",
]

[project.optional-dependencies]
serve = ["mlx-lm>=0.20"]

[project.scripts]
turbo = "turbollm.cli:cli"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Note: `mlx-lm` is an optional dep under `[serve]` so the CLI can be installed on non-Mac for model management, but serving requires Apple Silicon. When running `turbo serve` or `turbo opencode`, we check and prompt to install the extra.

**Step 3: Create .gitignore**

```
__pycache__/
*.egg-info/
dist/
.venv/
models/
```

**Step 4: Create empty package**

```python
# src/turbollm/__init__.py
__version__ = "0.1.0"
```

**Step 5: Create CLI entry point stub**

```python
# src/turbollm/cli.py
import click

@click.group()
def cli():
    """Ollama-like CLI for MLX TurboQuant models."""
    pass

if __name__ == "__main__":
    cli()
```

**Step 6: Verify it installs and runs**

```bash
cd <repo>
uv venv && uv pip install -e ".[serve]"
uv run turbo --help
```

Expected: Shows help text with no commands yet.

**Step 7: Commit**

```bash
git add pyproject.toml .gitignore src/
git commit -m "feat: project scaffold with CLI entry point"
```

---

### Task 2: Models registry (models.toml)

**Files:**
- Create: `models.toml`
- Create: `src/turbollm/registry.py`

**Step 1: Create models.toml**

```toml
[defaults]
port = 8899
max_kv_size = 4096

[defaults.opencode]
context_length = 32768
output_length = 8192

[models.qwen36-35b-tq4]
name = "Qwen3.6 35B-A3B TurboQuant 4bit"
hf_repo = "majentik/Qwen3.6-35B-A3B-TurboQuant-MLX-4bit"
size_gb = 18
min_memory_gb = 24
tool_use = true
can_reason = true
tags = ["coding", "moe", "turboquant"]

[models.qwen36-35b-4bit]
name = "Qwen3.6 35B-A3B MLX 4bit"
hf_repo = "mlx-community/Qwen3.6-35B-A3B-4bit"
size_gb = 20
min_memory_gb = 24
tool_use = true
can_reason = true
tags = ["coding", "moe"]
```

**Step 2: Create registry.py**

```python
# src/turbollm/registry.py
import tomllib
from pathlib import Path

MODELS_TOML = Path(__file__).parent.parent.parent / "models.toml"
MODELS_DIR = Path.home() / ".turbollm" / "models"

def load_registry() -> dict:
    # Look for bundled models.toml, fall back to user config
    for path in [MODELS_TOML, Path.home() / ".turbollm" / "models.toml"]:
        if path.exists():
            return tomllib.loads(path.read_text())
    return {"defaults": {}, "models": {}}

def get_model(alias: str) -> dict:
    reg = load_registry()
    models = reg.get("models", {})
    if alias in models:
        return models[alias]
    # Try matching by hf_repo
    for m in models.values():
        if m["hf_repo"] == alias:
            return m
    raise click.exceptions.UsageError(
        f"Unknown model '{alias}'. Run 'turbo ls --available' to see options."
    )

def get_defaults() -> dict:
    return load_registry().get("defaults", {})

def model_path(hf_repo: str) -> Path:
    return MODELS_DIR / hf_repo.replace("/", "--")

def is_downloaded(hf_repo: str) -> bool:
    p = model_path(hf_repo)
    return p.exists() and any(p.glob("*.safetensors"))
```

**Step 3: Commit**

```bash
git add models.toml src/turbollm/registry.py
git commit -m "feat: models registry with TOML config"
```

---

### Task 3: `turbo pull` command

**Files:**
- Modify: `src/turbollm/cli.py`

**Step 1: Add pull command**

```python
# Add to cli.py
from turbollm.registry import get_model, model_path, is_downloaded, load_registry, MODELS_DIR

@cli.command()
@click.argument("model")
def pull(model):
    """Download a model from HuggingFace."""
    m = get_model(model)
    repo = m["hf_repo"]
    dest = model_path(repo)

    if is_downloaded(repo):
        click.echo(f"Already downloaded: {repo}")
        return

    click.echo(f"Pulling {m['name']} ({m['size_gb']}GB) from {repo}...")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id=repo,
        local_dir=str(dest),
        local_dir_use_symlinks=False,
    )
    click.echo(f"Done: {dest}")
```

**Step 2: Verify**

```bash
uv run turbo pull qwen36-35b-tq4
```

Expected: Downloads ~18GB to `~/.turbollm/models/majentik--Qwen3.6-35B-A3B-TurboQuant-MLX-4bit/`

**Step 3: Commit**

```bash
git add src/turbollm/cli.py
git commit -m "feat: turbo pull command"
```

---

### Task 4: `turbo ls` and `turbo rm` commands

**Files:**
- Modify: `src/turbollm/cli.py`

**Step 1: Add ls command**

```python
@cli.command()
@click.option("--available", "-a", is_flag=True, help="Show all models in registry")
def ls(available):
    """List models."""
    reg = load_registry()
    models = reg.get("models", {})

    if available:
        for alias, m in models.items():
            status = "downloaded" if is_downloaded(m["hf_repo"]) else "not pulled"
            click.echo(f"  {alias:30s} {m['size_gb']:>4}GB  [{status}]")
        return

    # Show only downloaded
    if not MODELS_DIR.exists():
        click.echo("No models downloaded. Run 'turbo ls -a' to see available.")
        return

    found = False
    for alias, m in models.items():
        if is_downloaded(m["hf_repo"]):
            dest = model_path(m["hf_repo"])
            size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e9
            click.echo(f"  {alias:30s} {size:>5.1f}GB  {m['hf_repo']}")
            found = True

    if not found:
        click.echo("No models downloaded. Run 'turbo pull <model>' to get started.")
```

**Step 2: Add rm command**

```python
@cli.command()
@click.argument("model")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def rm(model, yes):
    """Remove a downloaded model."""
    m = get_model(model)
    repo = m["hf_repo"]
    dest = model_path(repo)

    if not dest.exists():
        click.echo(f"Model not downloaded: {model}")
        return

    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e9
    if not yes:
        click.confirm(f"Remove {m['name']} ({size:.1f}GB)?", abort=True)

    import shutil
    shutil.rmtree(dest)
    click.echo(f"Removed {model}")
```

**Step 3: Verify**

```bash
uv run turbo ls -a
uv run turbo ls
```

**Step 4: Commit**

```bash
git add src/turbollm/cli.py
git commit -m "feat: turbo ls and rm commands"
```

---

### Task 5: `turbo serve` command

**Files:**
- Modify: `src/turbollm/cli.py`

**Step 1: Add serve command**

```python
import subprocess
import sys

@cli.command()
@click.argument("model")
@click.option("--port", "-p", default=None, type=int, help="Port (default from models.toml)")
def serve(model, port):
    """Start MLX model server."""
    m = get_model(model)
    repo = m["hf_repo"]
    defaults = get_defaults()

    if not is_downloaded(repo):
        click.echo(f"Model not downloaded. Run: turbo pull {model}")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)
    local = model_path(repo)

    click.echo(f"Serving {m['name']} on port {port}...")
    cmd = [
        sys.executable, "-m", "mlx_lm.server",
        "--model", str(local),
        "--port", str(port),
    ]
    subprocess.run(cmd)
```

**Step 2: Verify**

```bash
uv run turbo serve qwen36-35b-tq4
# In another terminal: curl http://localhost:8899/v1/models
```

**Step 3: Commit**

```bash
git add src/turbollm/cli.py
git commit -m "feat: turbo serve command"
```

---

### Task 6: `turbo opencode` command

**Files:**
- Modify: `src/turbollm/cli.py`

**Step 1: Add opencode command**

```python
import json
import tempfile
import os
import signal
import time

@cli.command()
@click.argument("model")
@click.option("--port", "-p", default=None, type=int)
def opencode(model, port):
    """Start model server + launch opencode."""
    m = get_model(model)
    repo = m["hf_repo"]
    defaults = get_defaults()
    oc_defaults = defaults.get("opencode", {})

    if not is_downloaded(repo):
        click.echo(f"Model not downloaded. Run: turbo pull {model}")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)
    local = model_path(repo)

    # Build opencode config overlay
    config = {
        "provider": {
            "turbo": {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"TurboLLM ({m['name']})",
                "options": {"baseURL": f"http://127.0.0.1:{port}/v1"},
                "models": {
                    repo: {
                        "name": m["name"],
                        "tool_use": m.get("tool_use", False),
                        "can_reason": m.get("can_reason", False),
                        "limit": {
                            "context": oc_defaults.get("context_length", 32768),
                            "output": oc_defaults.get("output_length", 8192),
                        },
                    }
                },
            }
        }
    }

    # Start server in background
    click.echo(f"Starting {m['name']} on port {port}...")
    server = subprocess.Popen(
        [sys.executable, "-m", "mlx_lm.server",
         "--model", str(local), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready
    import urllib.request
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models")
            break
        except Exception:
            time.sleep(1)
    else:
        click.echo("Server failed to start. Check logs.")
        server.terminate()
        raise SystemExit(1)

    click.echo("Server ready. Launching opencode...")

    # Write temp config and set env
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        config_path = f.name

    try:
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        subprocess.run(["opencode"], env=env)
    finally:
        server.terminate()
        server.wait()
        os.unlink(config_path)
        click.echo("Server stopped.")
```

**Step 2: Verify**

```bash
uv run turbo opencode qwen36-35b-tq4
```

Expected: Server starts, opencode launches with the turbo provider available.

**Step 3: Commit**

```bash
git add src/turbollm/cli.py
git commit -m "feat: turbo opencode command"
```

---

### Task 7: Polish and uv tool install

**Step 1: Test full install as uv tool**

```bash
cd <repo>
uv tool install -e ".[serve]"
turbo --help
turbo ls -a
```

**Step 2: Final commit**

```bash
git add -A
git commit -m "chore: ready for uv tool install"
```
