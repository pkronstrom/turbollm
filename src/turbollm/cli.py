import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import click


def _load_env():
    """Load .env from ~/.turbollm first, then fall back to package root."""
    for p in [Path.home() / ".turbollm" / ".env", Path(__file__).parent.parent.parent / ".env"]:
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


def _get_model_id(m: dict) -> str:
    """Get the model ID that the server will report (matches --served-model-name / -a)."""
    return m["hf_repo"]


def _picker_stats(m: dict) -> tuple[str, str, str, str]:
    backend = m.get("backend", get_defaults().get("backend", "vllm-mlx"))
    model_oc = m.get("opencode", {})
    srv = m.get("server", {})
    defaults_oc = get_defaults().get("opencode", {})
    ctx = model_oc.get("context_length", srv.get("max_tokens", defaults_oc.get("context_length", 32768)))
    out = model_oc.get("output_length", defaults_oc.get("output_length", 8192))
    size_gb = m.get("size_gb")
    size = f"{size_gb:g}GB" if isinstance(size_gb, int | float) else "?"
    return backend, f"{int(ctx / 1024)}k", f"{int(out / 1024)}k", size


def pick_model(requires_backend: list[str] | None = None) -> tuple[str, dict]:
    """Interactive picker for downloaded models.

    If requires_backend is set, incompatible models are shown greyed out
    and cannot be selected.
    """
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

    # Split into compatible / incompatible
    compatible = []
    incompatible = []
    for alias, m in downloaded:
        backend = m.get("backend", get_defaults().get("backend", "vllm-mlx"))
        if requires_backend and backend not in requires_backend:
            incompatible.append((alias, m))
        else:
            compatible.append((alias, m))

    if not compatible:
        console.print("[yellow]No compatible models downloaded.[/yellow]")
        if requires_backend:
            console.print(f"  Requires backend: [bold]{', '.join(requires_backend)}[/bold]")
        raise SystemExit(1)

    if len(compatible) == 1 and not incompatible:
        alias, m = compatible[0]
        console.print(f"Using [bold]{m['name']}[/bold]")
        return alias, m

    console.print("\n  [bold]Select a model:[/bold]\n")
    selectable = []
    idx = 0
    for alias, m in compatible:
        idx += 1
        backend, ctx, out, size = _picker_stats(m)
        console.print(
            f"  [bold cyan]{idx}[/bold cyan]) {m['name']}  "
            f"[dim]({alias}) [{backend}] ctx {ctx} out {out} size {size}[/dim]"
        )
        selectable.append((alias, m))
    for alias, m in incompatible:
        backend, ctx, out, size = _picker_stats(m)
        console.print(
            f"  [dim]  ) {m['name']}  ({alias}) [{backend}] "
            f"ctx {ctx} out {out} size {size} — incompatible backend[/dim]"
        )
    console.print()

    choice = click.prompt("  Choice", type=click.IntRange(1, len(selectable)))
    return selectable[choice - 1]


# ---------------------------------------------------------------------------
# Dynamic CLI group — discovers harness commands from models.toml
# ---------------------------------------------------------------------------

class TurboGroup(click.Group):
    """Click group that auto-discovers harness commands from [harnesses.*] in TOML."""

    def get_command(self, ctx, cmd_name):
        # Built-in commands always take priority
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        reg = load_registry()
        if cmd_name in reg.get("harnesses", {}):
            return _make_harness_command(cmd_name)
        return None

    def list_commands(self, ctx):
        builtin = set(click.Group.list_commands(self, ctx))
        reg = load_registry()
        harnesses = set(reg.get("harnesses", {}).keys())
        return sorted(builtin | harnesses)


@click.group(cls=TurboGroup)
def cli():
    """Ollama-like CLI for MLX and GGUF models on Apple Silicon."""
    pass


# ---------------------------------------------------------------------------
# Core commands
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("model")
def pull(model):
    """Download a model. Accepts alias, owner/repo, or HuggingFace URL."""
    m = resolve_model(model)
    provider = _get_provider_for(m)

    if provider.is_downloaded(m):
        console.print(f"[green]Already downloaded:[/green] {m['hf_repo']}")
    else:
        size = f" ({m['size_gb']}GB)" if m.get("size_gb") else ""
        console.print(f"\n  [bold]{m['name']}[/bold]{size}")
        console.print(f"  [dim]{m['hf_repo']}[/dim]\n")
        provider.pull(m)

    # Ensure draft model is also pulled
    draft_repo = m.get("draft_hf_repo")
    if draft_repo and hasattr(provider, '_draft_model_path'):
        if provider._draft_model_path(m) is None:
            console.print(f"\n  Pulling draft model: [dim]{draft_repo}[/dim]")
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=draft_repo)
            console.print(f"  [green]Done![/green] Draft model cached")


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
@click.option("--backend", "-b", default=None, type=click.Choice(["vllm-mlx", "omlx", "gguf"]),
              help="Override backend (default: from model config)")
def serve(model, port, backend):
    """Start model server (auto-detects backend)."""
    if model:
        m = resolve_model(model)
    else:
        _, m = pick_model()
    if backend:
        m = {**m, "backend": backend}

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


@cli.command()
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
def chat(port):
    """Quick chat with a running turbo server."""
    defaults = get_defaults()
    port = port or defaults.get("port", 8899)

    if not _server_is_running(port):
        console.print(f"[yellow]No server on port {port}.[/yellow] Run [bold]turbo serve[/bold] first.")
        raise SystemExit(1)

    # Get model info from server
    resp = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models").read())
    model_id = resp["data"][0]["id"] if resp.get("data") else "default"
    console.print(f"[dim]Connected to {model_id} on port {port}[/dim]\n")

    import readline  # noqa: F401 — enables line editing in input()

    messages = []
    while True:
        try:
            user_input = input("> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            break

        if not user_input.strip():
            continue
        if user_input.strip() in ("/quit", "/exit", "/q"):
            break

        messages.append({"role": "user", "content": user_input})

        req = json.dumps({
            "model": model_id,
            "messages": messages,
            "stream": True,
        }).encode()

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=req,
            headers={"Content-Type": "application/json"},
        )

        assistant_msg = []
        try:
            with urllib.request.urlopen(request) as response:
                buf = ""
                for raw in response:
                    buf += raw.decode()
                    # SSE messages end with double newline
                    while "\n\n" in buf:
                        msg, buf = buf.split("\n\n", 1)
                        for line in msg.split("\n"):
                            line = line.strip()
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            # Show reasoning (thinking) in dim
                            reasoning = delta.get("reasoning_content") or ""
                            if reasoning:
                                print(f"\033[2m{reasoning}\033[0m", end="", flush=True)
                            content = delta.get("content") or ""
                            if content:
                                print(content, end="", flush=True)
                                assistant_msg.append(content)
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")
            continue

        print()
        messages.append({"role": "assistant", "content": "".join(assistant_msg)})


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

def _server_is_running(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2)
        return True
    except Exception:
        return False


def _get_running_model(port: int) -> dict | None:
    """If a server is running, return the matching model dict from registry (or None)."""
    try:
        resp = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2).read())
        model_id = resp["data"][0]["id"] if resp.get("data") else None
    except Exception:
        return None
    if not model_id:
        return None
    reg = load_registry()
    for m in reg.get("models", {}).values():
        if m.get("hf_repo") == model_id:
            return m
    # Unknown model on the server — return a synthetic dict
    return {"name": model_id, "hf_repo": model_id, "backend": "unknown"}


def _run_with_server(m: dict, port: int, launch_fn):
    """Start turbo server (or reuse running one) and run launch_fn(m, port).

    Handles server lifecycle: starts if needed, waits for ready,
    stops on exit.  launch_fn should be a blocking call (e.g. subprocess.run).
    """
    # If a server is already running, just attach — no provider needed
    if _server_is_running(port):
        console.print(f"[green]Server already running on port {port}.[/green]")
        launch_fn(m, port)
        return

    provider = _get_provider_for(m)

    if not provider.is_downloaded(m):
        size = f" ({m['size_gb']}GB)" if m.get("size_gb") else ""
        console.print(f"[yellow]Model not downloaded:[/yellow] {m.get('name', m['hf_repo'])}{size}")
        if click.confirm("  Pull now?"):
            provider.pull(m)
        else:
            raise SystemExit(1)
    if not provider.is_available():
        console.print(f"[red]{provider.name} not found.[/red] Install: [bold]{provider.install_hint}[/bold]")
        raise SystemExit(1)

    cmd = provider.build_serve_cmd(m, port)
    console.print(f"Starting [bold]{m['name']}[/bold] on port {port} [{provider.name}]...")
    server = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(120):
        if _server_is_running(port):
            break
        time.sleep(1)
    else:
        console.print("[red]Server failed to start.[/red]")
        server.terminate()
        raise SystemExit(1)

    console.print("[green]Server ready.[/green]")
    try:
        launch_fn(m, port)
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()
        server.wait()
        console.print("[dim]Server stopped.[/dim]")


# ---------------------------------------------------------------------------
# Harness integration
# ---------------------------------------------------------------------------

def _run_harness(harness_name: str, m: dict, port: int):
    """Load a harness by name, check availability, and run with server."""
    from turbollm.harnesses import get_harness

    reg = load_registry()
    config = reg.get("harnesses", {}).get(harness_name, {})
    harness = get_harness(harness_name, config)

    if not harness.is_available():
        console.print(f"[red]{harness_name} not found.[/red] Install: [bold]{harness.install_hint}[/bold]")
        raise SystemExit(1)

    model_id = _get_model_id(m)
    console.print(f"  Launching {harness_name} → [dim]{model_id}[/dim]")
    _run_with_server(m, port, lambda _m, p: harness.launch(model_id, p, m))


def _is_backend_compatible(harness_config: dict, backend: str) -> bool:
    """Check if a backend is compatible with a harness's requires_backend."""
    requires = harness_config.get("requires_backend")
    if not requires:
        return True
    return backend in requires


def _make_harness_command(harness_name: str):
    """Create a click command for a TOML-defined harness."""
    @click.command(name=harness_name, help=f"Start model server + launch {harness_name}.")
    @click.argument("model", required=False)
    @click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
    @click.option("--backend", "-b", default=None, type=click.Choice(["vllm-mlx", "omlx", "gguf"]),
                  help="Override backend (default: from model config)")
    def cmd(model, port, backend):
        port = port or get_defaults().get("port", 8899)
        reg = load_registry()
        harness_config = reg.get("harnesses", {}).get(harness_name, {})
        requires_backend = harness_config.get("requires_backend")

        if model:
            m = resolve_model(model)
        elif _server_is_running(port):
            # Server already running — check compatibility
            running = _get_running_model(port)
            running_backend = running.get("backend", "unknown") if running else "unknown"
            if running and _is_backend_compatible(harness_config, running_backend):
                # Compatible server running — attach directly
                _run_harness(harness_name, running, port)
                return
            else:
                # Incompatible server — show picker
                if running:
                    console.print(
                        f"[yellow]Server on port {port} ({running.get('name', '?')}) "
                        f"uses incompatible backend [{running_backend}].[/yellow]"
                    )
                _, m = pick_model(requires_backend=requires_backend)
        else:
            _, m = pick_model(requires_backend=requires_backend)

        if backend:
            m = {**m, "backend": backend}
        _run_harness(harness_name, m, port)
    return cmd


@cli.command(name="run")
@click.argument("model", required=False)
@click.option("--harness", "-H", required=True, help="Harness to launch (e.g. goose, hermes)")
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
@click.option("--backend", "-b", default=None, type=click.Choice(["vllm-mlx", "omlx", "gguf"]),
              help="Override backend (default: from model config)")
def run_cmd(model, harness, port, backend):
    """Start model server + launch a harness by name."""
    port = port or get_defaults().get("port", 8899)
    reg = load_registry()
    harness_config = reg.get("harnesses", {}).get(harness, {})
    requires_backend = harness_config.get("requires_backend")

    if model:
        m = resolve_model(model)
    elif _server_is_running(port):
        running = _get_running_model(port)
        running_backend = running.get("backend", "unknown") if running else "unknown"
        if running and _is_backend_compatible(harness_config, running_backend):
            _run_harness(harness, running, port)
            return
        else:
            _, m = pick_model(requires_backend=requires_backend)
    else:
        _, m = pick_model(requires_backend=requires_backend)

    if backend:
        m = {**m, "backend": backend}
    _run_harness(harness, m, port)


if __name__ == "__main__":
    cli()
