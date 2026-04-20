import json
import os
import subprocess
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
from rich.table import Table

from turbollm.providers import get_provider
from turbollm.registry import get_defaults, load_registry, resolve_model

console = Console()


def _get_provider_for(m: dict):
    backend = m.get("backend", get_defaults().get("backend", "vllm-mlx"))
    return get_provider(backend)


def pick_model() -> tuple[str, dict]:
    """Interactive picker for downloaded models."""
    reg = load_registry()
    models = reg.get("models", {})
    downloaded = []
    for alias, m in models.items():
        provider = _get_provider_for(m)
        if provider.is_downloaded(m):
            downloaded.append((alias, m))

    if not downloaded:
        console.print("[yellow]No models downloaded.[/yellow] Run [bold]turbo pull <model>[/bold] first.")
        raise SystemExit(1)
    if len(downloaded) == 1:
        alias, m = downloaded[0]
        console.print(f"Using [bold]{m['name']}[/bold]")
        return alias, m

    console.print("\n  [bold]Select a model:[/bold]\n")
    for i, (alias, m) in enumerate(downloaded, 1):
        backend = m.get("backend", "vllm-mlx")
        console.print(f"  [bold cyan]{i}[/bold cyan]) {m['name']}  [dim]({alias}) [{backend}][/dim]")
    console.print()

    choice = click.prompt("  Choice", type=click.IntRange(1, len(downloaded)))
    return downloaded[choice - 1]


@click.group()
def cli():
    """Ollama-like CLI for MLX and GGUF models on Apple Silicon."""
    pass


@cli.command()
@click.argument("model")
def pull(model):
    """Download a model. Accepts alias, owner/repo, or HuggingFace URL."""
    m = resolve_model(model)
    provider = _get_provider_for(m)

    if provider.is_downloaded(m):
        console.print(f"[green]Already downloaded:[/green] {m['hf_repo']}")
        return

    size = f" ({m['size_gb']}GB)" if m.get("size_gb") else ""
    console.print(f"\n  [bold]{m['name']}[/bold]{size}")
    console.print(f"  [dim]{m['hf_repo']}[/dim]\n")

    provider.pull(m)


@cli.command(name="ls")
@click.option("--available", "-a", is_flag=True, help="Show all models in registry")
def ls_cmd(available):
    """List models."""
    reg = load_registry()
    models = reg.get("models", {})

    table = Table(show_header=True)
    table.add_column("Alias", style="bold")
    table.add_column("Backend")
    table.add_column("Size", justify="right")
    table.add_column("Status")
    table.add_column("HF Repo", style="dim")

    if available:
        for alias, m in models.items():
            provider = _get_provider_for(m)
            backend = m.get("backend", "vllm-mlx")
            status = "[green]downloaded[/green]" if provider.is_downloaded(m) else "[dim]not pulled[/dim]"
            table.add_row(alias, backend, f"{m.get('size_gb', '?')}GB", status, m["hf_repo"])
        console.print(table)
        return

    found = False
    for alias, m in models.items():
        provider = _get_provider_for(m)
        if provider.is_downloaded(m):
            backend = m.get("backend", "vllm-mlx")
            table.add_row(alias, backend, f"{m.get('size_gb', '?')}GB", "[green]downloaded[/green]", m["hf_repo"])
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
    provider = _get_provider_for(m)

    if not provider.is_downloaded(m):
        console.print(f"[yellow]Not downloaded:[/yellow] {model}")
        return

    import shutil

    from turbollm.registry import _hf_cache_path

    cache_dir = _hf_cache_path(m["hf_repo"])
    size = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file()) / 1e9
    if not yes:
        click.confirm(f"Remove {m['name']} ({size:.1f}GB)?", abort=True)

    try:
        shutil.rmtree(cache_dir)
    except FileNotFoundError:
        pass
    console.print(f"[green]Removed[/green] {model}")


@cli.command()
@click.argument("model", required=False)
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
def serve(model, port):
    """Start model server (auto-detects backend)."""
    if model:
        m = resolve_model(model)
    else:
        _, m = pick_model()

    provider = _get_provider_for(m)
    defaults = get_defaults()

    if not provider.is_downloaded(m):
        console.print(f"[yellow]Model not downloaded.[/yellow] Run: [bold]turbo pull {model}[/bold]")
        raise SystemExit(1)

    if not provider.is_available():
        console.print(f"[red]{provider.name} not found.[/red] Install: [bold]{provider.install_hint}[/bold]")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)
    cmd = provider.build_serve_cmd(m, port)

    console.print(f"Serving [bold]{m['name']}[/bold] on port {port}")
    console.print(f"  [dim]backend: {provider.name}[/dim]")
    console.print(f"  [dim]{' '.join(cmd)}[/dim]\n")
    subprocess.run(cmd)


def _build_opencode_config(m: dict, port: int) -> dict:
    defaults_oc = get_defaults().get("opencode", {})
    model_oc = m.get("opencode", {})
    ctx = model_oc.get("context_length", defaults_oc.get("context_length", 32768))
    out = model_oc.get("output_length", defaults_oc.get("output_length", 8192))

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
                        "limit": {"context": ctx, "output": out},
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

    provider = _get_provider_for(m)
    defaults = get_defaults()

    if not provider.is_downloaded(m):
        console.print(f"[yellow]Model not downloaded.[/yellow] Run: [bold]turbo pull {model}[/bold]")
        raise SystemExit(1)

    if not provider.is_available():
        console.print(f"[red]{provider.name} not found.[/red] Install: [bold]{provider.install_hint}[/bold]")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)

    if _server_is_running(port):
        console.print(f"[green]Server already running on port {port}.[/green] Launching opencode...")
        _launch_opencode(m, port)
        return

    cmd = provider.build_serve_cmd(m, port)
    console.print(f"Starting [bold]{m['name']}[/bold] on port {port} [{provider.name}]...")
    server = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    for _ in range(120):
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
