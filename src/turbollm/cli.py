import json
import os
import subprocess
import sys
import tempfile
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

from turbollm.picker import pick as picker_pick
from turbollm.providers import get_provider
from turbollm.registry import get_defaults, load_registry, resolve_model

console = Console()


def _get_provider_for(m: dict):
    backend = m.get("backend", get_defaults().get("backend", "vllm-mlx"))
    return get_provider(backend)


def _get_model_id(m: dict) -> str:
    """Get the model ID that the server will report (matches --served-model-name / -a).

    Delegates to the provider so each backend can override (mlx-vlm reports a
    local filesystem path, others report hf_repo)."""
    return _get_provider_for(m).get_model_id(m)


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

    Uses the arrow-key picker (turbollm.picker) which lets the user pick model
    AND adjust per-model context size + reasoning on the same screen. Falls
    back to a numbered prompt when stdin/stdout isn't a TTY. The returned
    model dict has any user overrides already merged into its [server] block.
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

    # Split into compatible / incompatible (incompatible-backend models are
    # listed dim and unselectable below the picker).
    compatible: list[tuple[str, dict, str]] = []
    incompatible: list[tuple[str, dict, str]] = []
    for alias, m in downloaded:
        backend = m.get("backend", get_defaults().get("backend", "vllm-mlx"))
        if requires_backend and backend not in requires_backend:
            incompatible.append((alias, m, backend))
        else:
            compatible.append((alias, m, backend))

    if not compatible:
        console.print("[yellow]No compatible models downloaded.[/yellow]")
        if requires_backend:
            console.print(f"  Requires backend: [bold]{', '.join(requires_backend)}[/bold]")
        raise SystemExit(1)

    if len(compatible) == 1 and not incompatible:
        alias, m, _ = compatible[0]
        console.print(f"Using [bold]{m['name']}[/bold]")
        return alias, m

    result = picker_pick(compatible)
    if result is None:
        console.print("[yellow]Cancelled.[/yellow]")
        raise SystemExit(1)
    alias, new_model, overrides = result
    if incompatible:
        console.print()
        for ialias, im, ibackend in incompatible:
            console.print(
                f"  [dim](skipped) {im.get('name', ialias)} ({ialias}) "
                f"[{ibackend}] — incompatible backend[/dim]"
            )
    return alias, new_model


# ---------------------------------------------------------------------------
# Dynamic CLI group — discovers harness commands from models.toml
# ---------------------------------------------------------------------------

class TurboGroup(click.Group):
    """Click group that auto-discovers harness and script commands from TOML."""

    def get_command(self, ctx, cmd_name):
        # Built-in commands always take priority, then harnesses, then scripts.
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        reg = load_registry()
        if cmd_name in reg.get("harnesses", {}):
            return _make_harness_command(cmd_name)
        if cmd_name in reg.get("scripts", {}):
            return _make_script_command(cmd_name, reg["scripts"][cmd_name])
        return None

    def list_commands(self, ctx):
        builtin = set(click.Group.list_commands(self, ctx))
        reg = load_registry()
        harnesses = set(reg.get("harnesses", {}).keys())
        scripts = set(reg.get("scripts", {}).keys())
        return sorted(builtin | harnesses | scripts)


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

    # Ensure draft model is also pulled (no-op for providers without draft support).
    provider.pull_draft(m)


@cli.command(name="ls")
@click.option("--available", "-a", is_flag=True, help="Show all models in registry")
def ls_cmd(available):
    """List models, backends, and harnesses."""
    reg = load_registry()
    models = reg.get("models", {})

    table = Table(show_header=True, title="Models", title_justify="left")
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
    else:
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

    _print_backends_table()
    _print_harnesses_table(reg)
    _print_scripts_table(reg)


def _print_backends_table() -> None:
    from turbollm.providers import get_provider

    backends = ["vllm-mlx", "gguf", "omlx", "mlx-vlm", "mlx-audio"]
    table = Table(show_header=True, title="\nBackends", title_justify="left")
    table.add_column("Backend", style="bold")
    table.add_column("Binary")
    table.add_column("Status")
    table.add_column("Install", style="dim")

    for key in backends:
        provider = get_provider(key)
        available = provider.is_available()
        status = "[green]available[/green]" if available else "[dim]missing[/dim]"
        install = "" if available else provider.install_hint
        table.add_row(key, provider.name, status, install)
    console.print(table)


def _print_scripts_table(reg: dict) -> None:
    scripts = reg.get("scripts", {})
    if not scripts:
        return
    table = Table(show_header=True, title="\nScripts", title_justify="left")
    table.add_column("Name", style="bold")
    table.add_column("Usage")
    table.add_column("Description")
    for name, cfg in scripts.items():
        table.add_row(name, cfg.get("usage", ""), cfg.get("description", ""))
    console.print(table)


def _print_harnesses_table(reg: dict) -> None:
    from turbollm.harnesses import get_harness

    harnesses = reg.get("harnesses", {})
    table = Table(show_header=True, title="\nHarnesses", title_justify="left")
    table.add_column("Name", style="bold")
    table.add_column("Binary")
    table.add_column("Status")
    table.add_column("Install", style="dim")

    if not harnesses:
        console.print("[dim]No harnesses configured.[/dim]")
        return

    for name, config in harnesses.items():
        harness = get_harness(name, config)
        binary = config.get("binary", name)
        available = harness.is_available()
        status = "[green]available[/green]" if available else "[dim]missing[/dim]"
        install = "" if available else config.get("install", "")
        table.add_row(name, binary, status, install)
    console.print(table)


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
@click.option("--backend", "-b", default=None, type=click.Choice(["vllm-mlx", "omlx", "gguf", "mlx-vlm", "mlx-audio"]),
              help="Override backend (default: from model config)")
def serve(model, port, backend):
    """Start model server (auto-detects backend)."""
    alias: str | None = None
    if model:
        m = resolve_model(model)
        # If the user typed an alias (vs. a raw hf_repo), keep it for the port stamp
        # so harnesses launched in other shells can pick the exact registry entry.
        reg = load_registry()
        if model in reg.get("models", {}):
            alias = model
    else:
        alias, m = pick_model()
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
    _write_port_stamp(port, alias)
    try:
        subprocess.run(cmd)
    finally:
        _clear_port_stamp(port)


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
# Audio: transcribe via mlx-audio backend
# ---------------------------------------------------------------------------

def _build_multipart(fields: dict, file_field: str, filename: str,
                     content_type: str, file_bytes: bytes) -> tuple[bytes, str]:
    """Hand-rolled multipart/form-data so transcribe stays stdlib-only."""
    import uuid
    boundary = "----turbollm" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


_AUDIO_MIME = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
    ".mp4": "audio/mp4",
}


@cli.command()
@click.argument("audio_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--model", "-m", default=None,
              help="Model alias or HF repo. Defaults to the running server's model, else parakeet-v3.")
@click.option("--language", "-l", default=None, help="ISO language code (e.g. en, fi). Optional.")
@click.option("--format", "-f", "response_format",
              type=click.Choice(["text", "json", "srt", "vtt", "verbose_json", "segments"]),
              default="text", help="Response format (default: text). Use 'segments' for JSON array with timestamps.")
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899).")
def transcribe(audio_file, model, language, response_format, port):
    """Transcribe an audio file via mlx-audio. Auto-starts server if needed.

    Transcript goes to stdout; status to stderr. Pipe-friendly:
        turbo transcribe meeting.m4a | pi -p "summarize this"
    """
    err = Console(stderr=True)

    defaults = get_defaults()
    audio_defaults = defaults.get("mlx-audio", {})
    port = port or audio_defaults.get("port") or 8900

    # Resolve the model to use for the API call.
    if model:
        m = resolve_model(model)
    else:
        stamped = _get_running_model(port)
        m = stamped if stamped and stamped.get("backend") == "mlx-audio" else resolve_model("parakeet-v3")

    # Guard: if something is already running on this port but it's not an
    # mlx-audio server, refuse rather than POST to the wrong process.
    running = _get_running_model(port)
    if running and running.get("backend") not in ("mlx-audio", "unknown"):
        err.print(
            f"[red]Port {port} is serving {running.get('name')} ({running.get('backend')}).[/red] "
            f"Pick a free port with -p, or stop that server first."
        )
        raise SystemExit(1)

    if m.get("backend") != "mlx-audio":
        err.print(f"[red]{m.get('name','?')} is not an mlx-audio model.[/red] Pick an ASR alias with -m.")
        raise SystemExit(1)

    model_id = _get_provider_for(m).get_model_id(m)
    path = Path(audio_file)

    # mlx-audio's server crashes writing an intermediate tmp file when the
    # input extension isn't a format its audio writer supports (e.g. .m4a).
    # Pre-convert anything non-wav to wav via ffmpeg so the server's
    # extension-based output selection succeeds.
    if path.suffix.lower() != ".wav":
        import shutil as _sh
        if not _sh.which("ffmpeg"):
            err.print("[red]ffmpeg required to transcribe non-wav files.[/red] brew install ffmpeg")
            raise SystemExit(1)
        err.print(f"[dim]converting {path.suffix} → wav via ffmpeg…[/dim]")
        wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav_tmp.close()
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                 "-ac", "1", "-ar", "16000", wav_tmp.name],
                check=True,
            )
            file_bytes = Path(wav_tmp.name).read_bytes()
            send_name = path.with_suffix(".wav").name
            mime = "audio/wav"
        finally:
            os.unlink(wav_tmp.name)
    else:
        file_bytes = path.read_bytes()
        send_name = path.name
        mime = "audio/wav"

    def _do(_m, p):
        # Always request verbose_json internally so we have both text and
        # segments available regardless of the user-facing --format flag.
        fields = {"model": model_id, "response_format": "verbose_json"}
        if language:
            fields["language"] = language
        body, ctype = _build_multipart(fields, "file", send_name, mime, file_bytes)
        req = urllib.request.Request(
            f"http://127.0.0.1:{p}/v1/audio/transcriptions",
            data=body,
            headers={"Content-Type": ctype},
        )
        err.print(f"[dim]POST /v1/audio/transcriptions  model={model_id}  size={len(file_bytes)/1e6:.1f}MB[/dim]")
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            err.print(f"[red]HTTP {e.code}:[/red] {e.read().decode('utf-8', 'replace')}")
            raise SystemExit(1) from None
        # Parse the JSON response (mlx-audio always returns JSON).
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"text": raw}

        if response_format == "text":
            # Default behaviour: emit concatenated text, pipe-friendly.
            sys.stdout.write(data.get("text", raw).rstrip() + "\n")
        elif response_format == "segments":
            # Emit the segments array as pretty-printed JSON.
            segments = data.get("segments")
            if segments is None:
                # Older mlx-audio versions may not return verbose_json segments.
                sys.stderr.write(
                    "warning: mlx-audio did not return verbose_json segments\n"
                )
                segments = [{"start": 0, "end": None, "text": data.get("text", "")}]
            sys.stdout.write(json.dumps(segments, indent=2, ensure_ascii=False) + "\n")
        else:
            # Legacy passthroughs: json, srt, vtt, verbose_json — emit raw response.
            sys.stdout.write(raw)
        sys.stdout.flush()
        return 0

    _run_with_server(m, port, _do)


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

def _server_is_running(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2)
        return True
    except Exception:
        return False


TURBOLLM_STATE_DIR = Path.home() / ".turbollm" / "state"


def _port_stamp_path(port: int) -> Path:
    return TURBOLLM_STATE_DIR / f"port-{port}.json"


def _write_port_stamp(port: int, alias: str | None) -> None:
    """Record the alias served on this port so `turbo <harness>` in another shell
    can resolve back to the full registry entry (including pi/opencode/server
    config) without depending on the server's advertised model id, which may not
    match any hf_repo (e.g. backends that rename the served model)."""
    TURBOLLM_STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"alias": alias, "pid": os.getpid(), "started_at": time.time()}
    _port_stamp_path(port).write_text(json.dumps(payload) + "\n")


def _read_port_stamp(port: int) -> dict | None:
    p = _port_stamp_path(port)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    pid = data.get("pid")
    if pid:
        try:
            os.kill(int(pid), 0)
        except (OSError, ProcessLookupError):
            return None
    return data


def _clear_port_stamp(port: int) -> None:
    p = _port_stamp_path(port)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def _get_running_model(port: int) -> dict | None:
    """If a server is running, return the matching model dict from registry (or None).

    Prefers the port-stamp written by `turbo serve` (carries the exact alias the user
    asked for, so two aliases sharing the same hf_repo resolve correctly). Falls back
    to matching the server-advertised model id against hf_repo for servers started
    outside of turbo."""
    stamp = _read_port_stamp(port)
    if stamp and stamp.get("alias"):
        try:
            return resolve_model(stamp["alias"])
        except Exception:
            pass

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


def _read_log_tail(path: str, max_lines: int = 80) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return ""
    return "".join(lines[-max_lines:]).strip()


def _run_with_server(m: dict, port: int, launch_fn):
    # Route every status/error line through stderr. The function's only data
    # path is launch_fn's return value (and whatever launch_fn writes to
    # stdout). Server-startup messages on stdout would otherwise leak into
    # pipelines like `turbo transcribe file.wav | pi -p '…'`, where the LLM
    # mistakes them for transcript content.
    console = Console(stderr=True)
    """Start turbo server (or reuse running one) and run launch_fn(m, port).

    Handles server lifecycle: starts if needed, waits for ready,
    stops on exit.  launch_fn should be a blocking call (e.g. subprocess.run).
    Returns whatever launch_fn returns (used for headless exit codes).
    """
    # If a server is already running, just attach — no provider needed
    if _server_is_running(port):
        console.print(f"[green]Server already running on port {port}.[/green]")
        return launch_fn(m, port)

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
    log_file = tempfile.NamedTemporaryFile(
        mode="w+",
        encoding="utf-8",
        prefix="turbollm-server-",
        suffix=".log",
        delete=False,
    )
    log_path = log_file.name
    server = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)

    # Startup timeout in seconds. Per-model override beats global default;
    # falls back to 120s. Larger models (e.g. Qwen3.6 35B) reliably need more
    # than the default on cold disk caches.
    startup_timeout = int(
        m.get("server", {}).get("startup_timeout")
        or get_defaults().get("server_startup_timeout")
        or 120
    )
    try:
        for _ in range(startup_timeout):
            if _server_is_running(port):
                break
            if server.poll() is not None:
                break
            time.sleep(1)

        if not _server_is_running(port):
            console.print("[red]Server failed to start.[/red]")
            log_file.flush()
            tail = _read_log_tail(log_path)
            if tail:
                console.print("[yellow]Backend log:[/yellow]")
                console.print(tail)
            if server.poll() is None:
                server.terminate()
            raise SystemExit(1)

        console.print("[green]Server ready.[/green]")
        try:
            return launch_fn(m, port)
        except KeyboardInterrupt:
            return None
    finally:
        if server.poll() is None:
            server.terminate()
        server.wait()
        log_file.close()
        try:
            os.unlink(log_path)
        except OSError:
            pass
        console.print("[dim]Server stopped.[/dim]")


# ---------------------------------------------------------------------------
# Harness integration
# ---------------------------------------------------------------------------

def _run_harness(harness_name: str, m: dict, port: int, prompt: str | None = None):
    """Load a harness by name, check availability, and run with server.

    If ``prompt`` is provided, runs the harness in headless (print) mode and
    exits with the harness's return code. Otherwise launches interactively.
    """
    from turbollm.harnesses import get_harness

    reg = load_registry()
    config = reg.get("harnesses", {}).get(harness_name, {})
    harness = get_harness(harness_name, config)

    if not harness.is_available():
        console.print(f"[red]{harness_name} not found.[/red] Install: [bold]{harness.install_hint}[/bold]")
        raise SystemExit(1)

    model_id = _get_model_id(m)
    if prompt is not None:
        console.print(f"  Running {harness_name} (headless) → [dim]{model_id}[/dim]")
        try:
            rc = _run_with_server(m, port, lambda _m, p: harness.headless(model_id, p, m, prompt))
        except NotImplementedError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(2)
        if rc:
            raise SystemExit(rc)
        return
    console.print(f"  Launching {harness_name} → [dim]{model_id}[/dim]")
    _run_with_server(m, port, lambda _m, p: harness.launch(model_id, p, m))


def _is_backend_compatible(harness_config: dict, backend: str) -> bool:
    """Check if a backend is compatible with a harness's requires_backend."""
    requires = harness_config.get("requires_backend")
    if not requires:
        return True
    return backend in requires


def _dispatch_harness(
    harness_name: str,
    model: str | None,
    port: int | None,
    backend: str | None,
    prompt: str | None,
    *,
    show_incompat_warning: bool,
) -> None:
    """Shared dispatch for `turbo <harness>` and `turbo run -H <harness>`.

    Resolution order:
      1. Explicit ``model`` argument wins.
      2. Else, if a server is already running on ``port``:
         - compatible backend → attach to it
         - incompatible backend → fall through to picker (optionally warn)
      3. Else, run the picker filtered by the harness's ``requires_backend``.

    The ``show_incompat_warning`` flag exists because the original
    ``_make_harness_command`` path prints a yellow notice when it falls back
    from an incompatible running server to the picker, but ``turbo run`` did
    not. Kept as-is to preserve existing UX.
    """
    port = port or get_defaults().get("port", 8899)
    reg = load_registry()
    harness_config = reg.get("harnesses", {}).get(harness_name, {})
    requires_backend = harness_config.get("requires_backend")

    if model:
        m = resolve_model(model)
    elif _server_is_running(port):
        running = _get_running_model(port)
        running_backend = running.get("backend", "unknown") if running else "unknown"
        if running and _is_backend_compatible(harness_config, running_backend):
            _run_harness(harness_name, running, port, prompt=prompt)
            return
        if show_incompat_warning and running:
            console.print(
                f"[yellow]Server on port {port} ({running.get('name', '?')}) "
                f"uses incompatible backend [{running_backend}].[/yellow]"
            )
        _, m = pick_model(requires_backend=requires_backend)
    else:
        _, m = pick_model(requires_backend=requires_backend)

    if backend:
        m = {**m, "backend": backend}
    _run_harness(harness_name, m, port, prompt=prompt)


def _make_script_command(name: str, config: dict):
    """Create a click command for a TOML-defined [scripts.*] entry.

    The `command` field is run via /bin/sh -c with positional args. Inside the
    command string, $0 = script name, $1..$N = forwarded CLI args. Always quote
    positional refs in scripts to handle paths with spaces.
    """
    description = config.get("description", f"Run script: {name}")
    usage = config.get("usage", "[ARGS]...")
    command = config.get("command", "")

    @click.command(
        name=name,
        help=f"{description}\n\nUsage: turbo {name} {usage}",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    @click.argument("args", nargs=-1, type=click.UNPROCESSED)
    def cmd(args):
        if not command:
            console.print(f"[red]Script '{name}' has no `command` configured.[/red]")
            raise SystemExit(2)
        rc = subprocess.run(["/bin/sh", "-c", command, name, *args]).returncode
        raise SystemExit(rc)

    return cmd


def _make_harness_command(harness_name: str):
    """Create a click command for a TOML-defined harness."""
    @click.command(name=harness_name, help=f"Start model server + launch {harness_name}.")
    @click.argument("model", required=False)
    @click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
    @click.option("--backend", "-b", default=None, type=click.Choice(["vllm-mlx", "omlx", "gguf", "mlx-vlm"]),
                  help="Override backend (default: from model config)")
    @click.option("--prompt", default=None,
                  help="Run harness headlessly with this prompt and exit (no TTY).")
    def cmd(model, port, backend, prompt):
        _dispatch_harness(
            harness_name, model, port, backend, prompt,
            show_incompat_warning=True,
        )
    return cmd


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

@cli.group(name="workflows")
def workflows_grp():
    """Manage and run [workflows.*] from models.toml."""


@workflows_grp.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for machine consumption.")
def workflows_list(as_json):
    """List configured workflows."""
    from turbollm import workflows as _wf

    reg = load_registry()
    items = []
    for name, wf in _wf.load_workflows(reg).items():
        try:
            _wf.validate_workflow(name, wf)
        except _wf.WorkflowError as exc:
            click.echo(f"warning: skipping workflow '{name}': {exc}", err=True)
            continue
        items.append({"name": name, **wf})

    if as_json:
        click.echo(json.dumps(items, indent=2))
        return

    if not items:
        console.print("[dim]No workflows configured.[/dim]")
        return
    table = Table(show_header=True, title="Workflows", title_justify="left")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    for wf in items:
        table.add_row(wf["name"], wf.get("description", ""))
    console.print(table)


@workflows_grp.command(name="run")
@click.argument("name")
@click.option(
    "--param", "params", multiple=True, metavar="KEY=VALUE",
    help="Override a workflow param (repeatable). E.g. --param file=/tmp/audio.wav",
)
def workflows_run(name, params):
    """Run a workflow. Most acquired params (audio-recording, screenshot-manual)
    cannot be acquired from the CLI — pass a pre-resolved value via --param."""
    from turbollm import workflows as _wf

    reg = load_registry()
    all_wfs = _wf.load_workflows(reg)
    if name not in all_wfs:
        console.print(f"[red]No such workflow:[/red] {name}")
        raise SystemExit(1)

    overrides: dict[str, str] = {}
    for spec in params:
        if "=" not in spec:
            console.print(f"[red]Invalid --param '{spec}'.[/red] Expected KEY=VALUE.")
            raise SystemExit(2)
        k, v = spec.split("=", 1)
        overrides[k] = v

    try:
        rc = _wf.run_workflow(name, all_wfs[name], overrides=overrides, registry=reg)
    except _wf.WorkflowError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2)
    raise SystemExit(rc)


# ---------------------------------------------------------------------------
# Activities (HUD state visibility)
# ---------------------------------------------------------------------------

@cli.group(name="activities")
def activities_grp():
    """Inspect running turbollm activities (cross-process via ~/.turbollm/state/)."""


@activities_grp.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for machine consumption.")
def activities_list(as_json):
    """List currently-running activities."""
    from turbollm import activity as _act

    items = _act.list_activities()
    if as_json:
        click.echo(json.dumps(items, indent=2))
        return

    if not items:
        console.print("[dim]No activities running.[/dim]")
        return
    table = Table(show_header=True, title="Activities", title_justify="left")
    table.add_column("Kind", style="bold")
    table.add_column("Label")
    table.add_column("PID")
    table.add_column("Started")
    for it in items:
        table.add_row(it["kind"], it["label"], str(it["owner_pid"]), it["started_at"])
    console.print(table)


# ---------------------------------------------------------------------------
# HUD status helpers (for scripts to announce activity)
# ---------------------------------------------------------------------------

@cli.group(name="hud")
def hud_grp():
    """HUD-related helpers (used by scripts to set/clear status entries)."""


@hud_grp.group(name="status")
def hud_status_grp():
    """Set / update / clear HUD activity entries."""


@hud_status_grp.command(name="set")
@click.option("--label", required=True, help="User-visible label.")
@click.option("--icon", default=None, help="SF Symbol name (HUD uses if recognized).")
@click.option("--color", default=None, help="Color hint, e.g. red, blue, green.")
@click.option("--phase", default=None, help="Optional phase string.")
def hud_status_set(label, icon, color, phase):
    """Announce a new activity. Prints the id to stdout."""
    from turbollm import activity as _act

    aid = _act.start_activity(
        kind="external", label=label, icon=icon, color=color, phase=phase,
    )
    click.echo(aid)


@hud_status_grp.command(name="update")
@click.argument("activity_id")
@click.option("--label", default=None)
@click.option("--icon", default=None)
@click.option("--color", default=None)
@click.option("--phase", default=None)
def hud_status_update(activity_id, label, icon, color, phase):
    """Merge new field values into an existing activity entry."""
    from turbollm import activity as _act

    _act.update_activity(activity_id, label=label, icon=icon, color=color, phase=phase)


@hud_status_grp.command(name="clear")
@click.argument("activity_id")
def hud_status_clear(activity_id):
    """Remove an activity entry."""
    from turbollm import activity as _act

    _act.clear_activity(activity_id)


@cli.command(name="run")
@click.argument("model", required=False)
@click.option("--harness", "-H", required=True, help="Harness to launch (e.g. goose, hermes)")
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
@click.option("--backend", "-b", default=None, type=click.Choice(["vllm-mlx", "omlx", "gguf", "mlx-vlm"]),
              help="Override backend (default: from model config)")
@click.option("--prompt", default=None,
              help="Run harness headlessly with this prompt and exit (no TTY).")
def run_cmd(model, harness, port, backend, prompt):
    """Start model server + launch a harness by name."""
    _dispatch_harness(
        harness, model, port, backend, prompt,
        show_incompat_warning=False,
    )


# ---------------------------------------------------------------------------
# Sidecar — launch the Swift HUD
# ---------------------------------------------------------------------------

def _hud_dir() -> Path:
    """Locate the tools/turbo-hud/ directory next to the turbollm source tree."""
    return Path(__file__).resolve().parent.parent.parent / "tools" / "turbo-hud"


@cli.command(name="sidecar")
@click.option("--build/--no-build", default=True,
              help="Run `swift build` before launching (default: yes).")
def sidecar_cmd(build):
    """Launch the Turbo HUD (Swift menu-bar app) via `swift run`."""
    hud_dir = _hud_dir()
    if not hud_dir.exists() or not (hud_dir / "Package.swift").exists():
        console.print(
            f"[red]Swift HUD package not found at {hud_dir}.[/red]\n"
            "Implement the package (Plan 2) before invoking turbo sidecar."
        )
        raise SystemExit(1)
    args = ["swift", "run"] if build else ["swift", "run", "--skip-build"]
    console.print(f"[dim]Launching HUD via `swift run` in {hud_dir}...[/dim]")
    subprocess.run(args, cwd=hud_dir)


if __name__ == "__main__":
    cli()
