import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import click


def _load_env():
    """Load .env from package root or ~/.turbollm/.env."""
    for p in [Path(__file__).parent.parent.parent / ".env", Path.home() / ".turbollm" / ".env"]:
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            return


_load_env()
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from turbollm.registry import (
    get_defaults,
    is_downloaded,
    list_downloaded,
    load_registry,
    model_path,
    resolve_model,
)

console = Console()


def pick_model() -> tuple[str, dict]:
    """Interactive picker for downloaded models."""
    downloaded = list_downloaded()
    if not downloaded:
        console.print("[yellow]No models downloaded.[/yellow] Run [bold]turbo pull <model>[/bold] first.")
        raise SystemExit(1)
    if len(downloaded) == 1:
        alias, m = downloaded[0]
        console.print(f"Using [bold]{m['name']}[/bold]")
        return alias, m

    console.print("\n  [bold]Select a model:[/bold]\n")
    for i, (alias, m) in enumerate(downloaded, 1):
        console.print(f"  [bold cyan]{i}[/bold cyan]) {m['name']}  [dim]({alias})[/dim]")
    console.print()

    choice = click.prompt("  Choice", type=click.IntRange(1, len(downloaded)))
    return downloaded[choice - 1]


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
    console.print(f"\n  [bold]{m['name']}[/bold]{size}")
    console.print(f"  [dim]{repo}[/dim]\n")

    import logging

    from huggingface_hub import snapshot_download

    # Suppress noisy HTTP logs from huggingface_hub / httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

    # Download to HF's default cache — mlx-lm uses this natively
    console.print("  Downloading...")
    local = snapshot_download(repo_id=repo)
    total_size = sum(f.stat().st_size for f in Path(local).rglob("*") if f.is_file()) / 1e9
    console.print(f"\n  [green]Done![/green] {total_size:.1f}GB cached at {local}\n")


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

    found = False
    for alias, m in models.items():
        if is_downloaded(m["hf_repo"]):
            dest = model_path(m["hf_repo"])
            if dest:
                size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e9
                table.add_row(alias, f"{size:.1f}GB", "[green]downloaded[/green]", m["hf_repo"])
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

    if not is_downloaded(repo):
        console.print(f"[yellow]Not downloaded:[/yellow] {model}")
        return

    from turbollm.registry import _hf_cache_path

    cache_dir = _hf_cache_path(repo)
    size = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file()) / 1e9
    if not yes:
        click.confirm(f"Remove {m['name']} ({size:.1f}GB)?", abort=True)

    shutil.rmtree(cache_dir)
    console.print(f"[green]Removed[/green] {model}")


@cli.command()
@click.argument("model", required=False)
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
def serve(model, port):
    """Start MLX model server."""
    if model:
        m = resolve_model(model)
    else:
        _, m = pick_model()
    repo = m["hf_repo"]
    defaults = get_defaults()

    if not is_downloaded(repo):
        console.print(f"[yellow]Model not downloaded.[/yellow] Run: [bold]turbo pull {model}[/bold]")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)
    local = model_path(repo)

    console.print(f"Serving [bold]{m['name']}[/bold] on port {port}...")
    console.print(f"  [dim]{local}[/dim]\n")
    subprocess.run(
        [sys.executable, "-m", "mlx_lm", "server", "--model", str(local), "--port", str(port)],
    )


def _build_opencode_config(m: dict, port: int) -> dict:
    oc = get_defaults().get("opencode", {})
    # Use "default_model" — mlx-lm maps this to whatever --model was passed
    return {
        "provider": {
            "turbo": {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"TurboLLM ({m['name']})",
                "options": {"baseURL": f"http://127.0.0.1:{port}/v1"},
                "models": {
                    "default_model": {
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


def _server_is_running(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2)
        return True
    except Exception:
        return False


def _launch_opencode(m: dict, port: int):
    turbo_config = _build_opencode_config(m, port)

    # Read existing opencode config and merge turbo provider into it
    oc_path = Path.home() / ".config" / "opencode" / "opencode.json"
    if oc_path.exists():
        existing = json.loads(oc_path.read_text())
        existing.setdefault("provider", {}).update(turbo_config["provider"])
        config = existing
    else:
        config = turbo_config

    env = os.environ.copy()
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    subprocess.run(["opencode"], env=env)


@cli.command()
@click.argument("model", required=False)
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
def opencode(model, port):
    """Start model server + launch opencode."""
    if model:
        m = resolve_model(model)
    else:
        _, m = pick_model()
    repo = m["hf_repo"]
    defaults = get_defaults()

    if not is_downloaded(repo):
        console.print(f"[yellow]Model not downloaded.[/yellow] Run: [bold]turbo pull {model}[/bold]")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)

    if _server_is_running(port):
        console.print(f"[green]Server already running on port {port}.[/green] Launching opencode...")
        _launch_opencode(m, port)
        return

    local = model_path(repo)
    console.print(f"Starting [bold]{m['name']}[/bold] on port {port}...")
    server = subprocess.Popen(
        [sys.executable, "-m", "mlx_lm", "server", "--model", str(local), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    for _ in range(60):
        if _server_is_running(port):
            break
        time.sleep(1)
    else:
        console.print("[red]Server failed to start.[/red]")
        server.terminate()
        raise SystemExit(1)

    console.print("[green]Server ready.[/green] Launching opencode...")

    try:
        _launch_opencode(m, port)
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()
        server.wait()
        console.print("[dim]Server stopped.[/dim]")


if __name__ == "__main__":
    cli()
