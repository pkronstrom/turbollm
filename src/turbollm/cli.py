import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import click
from rich.console import Console
from rich.table import Table

from turbollm.registry import (
    MODELS_DIR,
    get_defaults,
    is_downloaded,
    load_registry,
    model_path,
    resolve_model,
)

console = Console()


@click.group()
def cli():
    """Ollama-like CLI for MLX TurboQuant models on Apple Silicon."""
    pass


@cli.command()
@click.argument("model")
def pull(model):
    """Download a model. Accepts alias, owner/repo, or HuggingFace URL."""
    m = resolve_model(model)
    repo = m["hf_repo"]
    dest = model_path(repo)

    if is_downloaded(repo):
        console.print(f"[green]Already downloaded:[/green] {repo}")
        return

    size = f" ({m['size_gb']}GB)" if m.get("size_gb") else ""
    console.print(f"Pulling [bold]{m['name']}[/bold]{size} from {repo}...")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=repo,
        local_dir=str(dest),
        local_dir_use_symlinks=False,
    )
    console.print(f"[green]Done:[/green] {dest}")


@cli.command(name="ls")
@click.option("--available", "-a", is_flag=True, help="Show all models in registry")
def ls_cmd(available):
    """List models."""
    reg = load_registry()
    models = reg.get("models", {})

    table = Table(show_header=True)
    table.add_column("Alias", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("Status")
    table.add_column("HF Repo", style="dim")

    if available:
        for alias, m in models.items():
            status = "[green]downloaded[/green]" if is_downloaded(m["hf_repo"]) else "[dim]not pulled[/dim]"
            table.add_row(alias, f"{m['size_gb']}GB", status, m["hf_repo"])
        console.print(table)
        return

    if not MODELS_DIR.exists():
        console.print("No models downloaded. Run [bold]turbo ls -a[/bold] to see available.")
        return

    found = False
    for alias, m in models.items():
        if is_downloaded(m["hf_repo"]):
            dest = model_path(m["hf_repo"])
            size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e9
            table.add_row(alias, f"{size:.1f}GB", "[green]downloaded[/green]", m["hf_repo"])
            found = True

    # Check for ad-hoc downloads not in registry
    if MODELS_DIR.exists():
        for d in MODELS_DIR.iterdir():
            if not d.is_dir():
                continue
            repo = d.name.replace("--", "/")
            if not any(m["hf_repo"] == repo for m in models.values()):
                if any(d.glob("*.safetensors")):
                    size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e9
                    table.add_row("-", f"{size:.1f}GB", "[green]downloaded[/green]", repo)
                    found = True

    if found:
        console.print(table)
    else:
        console.print("No models downloaded. Run [bold]turbo pull <model>[/bold] to get started.")


@cli.command()
@click.argument("model")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def rm(model, yes):
    """Remove a downloaded model."""
    m = resolve_model(model)
    repo = m["hf_repo"]
    dest = model_path(repo)

    if not dest.exists():
        console.print(f"[yellow]Not downloaded:[/yellow] {model}")
        return

    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e9
    if not yes:
        click.confirm(f"Remove {m['name']} ({size:.1f}GB)?", abort=True)

    shutil.rmtree(dest)
    console.print(f"[green]Removed[/green] {model}")


@cli.command()
@click.argument("model")
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
def serve(model, port):
    """Start MLX model server."""
    m = resolve_model(model)
    repo = m["hf_repo"]
    defaults = get_defaults()

    if not is_downloaded(repo):
        console.print(f"[yellow]Model not downloaded.[/yellow] Run: [bold]turbo pull {model}[/bold]")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)
    local = model_path(repo)

    console.print(f"Serving [bold]{m['name']}[/bold] on port {port}...")
    subprocess.run(
        [sys.executable, "-m", "mlx_lm.server", "--model", str(local), "--port", str(port)],
    )


@cli.command()
@click.argument("model")
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
def opencode(model, port):
    """Start model server + launch opencode."""
    m = resolve_model(model)
    repo = m["hf_repo"]
    defaults = get_defaults()
    oc = defaults.get("opencode", {})

    if not is_downloaded(repo):
        console.print(f"[yellow]Model not downloaded.[/yellow] Run: [bold]turbo pull {model}[/bold]")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)
    local = model_path(repo)

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
                            "context": oc.get("context_length", 32768),
                            "output": oc.get("output_length", 8192),
                        },
                    }
                },
            }
        }
    }

    console.print(f"Starting [bold]{m['name']}[/bold] on port {port}...")
    server = subprocess.Popen(
        [sys.executable, "-m", "mlx_lm.server", "--model", str(local), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models")
            break
        except Exception:
            time.sleep(1)
    else:
        console.print("[red]Server failed to start.[/red]")
        server.terminate()
        raise SystemExit(1)

    console.print("[green]Server ready.[/green] Launching opencode...")

    try:
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        subprocess.run(["opencode"], env=env)
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()
        server.wait()
        console.print("[dim]Server stopped.[/dim]")


if __name__ == "__main__":
    cli()
