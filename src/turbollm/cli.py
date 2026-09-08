import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
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

from turbollm.multipart import build_multipart
from turbollm.picker import pick as picker_pick
from turbollm.providers import get_provider
from turbollm.registry import (
    PI_THINKING_LEVELS,
    effective_context,
    get_defaults,
    load_registry,
    resolve_model,
    supported_thinking_levels,
)

console = Console()


def _get_provider_for(m: dict):
    backend = m.get("backend", get_defaults().get("backend", "vllm-mlx"))
    return get_provider(backend)


def _get_model_id(m: dict) -> str:
    """Get the model ID that the server will report (matches --served-model-name / -a).

    Delegates to the provider so each backend can override (mlx-vlm reports a
    local filesystem path, others report hf_repo)."""
    return _get_provider_for(m).get_model_id(m)


def _next_free_port(start: int) -> int:
    """Return the first loopback TCP port at or above ``start`` that can bind."""
    for port in range(start, 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise click.UsageError(f"No free TCP port available at or above {start}.")


# Backends grouped by modality. Chat/text harnesses (pi, claude, codex, …) serve
# text-generation models; mlx-audio is a speech-to-text (ASR) backend and must
# not appear in their model pickers. The `transcribe` command targets it directly.
_ALL_BACKENDS = ("vllm-mlx", "gguf", "omlx", "mlx-vlm", "mlx-audio")
_AUDIO_BACKENDS = ("mlx-audio",)
_CHAT_BACKENDS = [b for b in _ALL_BACKENDS if b not in _AUDIO_BACKENDS]


def _harness_requires_backend(harness_config: dict) -> list[str]:
    """Effective backend allow-list for a harness's model picker.

    Honors an explicit ``[harnesses.<name>].requires_backend`` when set;
    otherwise defaults to chat/text-gen backends so ASR (mlx-audio) models are
    excluded from chat-harness pickers.
    """
    return list(harness_config.get("requires_backend") or _CHAT_BACKENDS)


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
    alias, new_model = result
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
    backends = list(_ALL_BACKENDS)
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
    from turbollm.model_storage import (
        delete_removal_targets,
        gather_removal_targets,
    )

    registry = load_registry()
    models = registry.get("models", {})
    current_alias = next(
        (alias for alias, configured in models.items() if configured == m),
        None,
    )
    other_models = [
        configured
        for alias, configured in models.items()
        if alias != current_alias
    ]
    targets = gather_removal_targets(m, other_models)
    if not targets:
        console.print(
            f"[yellow]No safely removable artifacts found:[/yellow] {model}"
        )
        return

    total = sum(target.size_bytes for target in targets)
    console.print(f"\n  [bold]{m['name']}[/bold]")
    for target in targets:
        console.print(f"  {target.label:>9}: {target.path}")
    if not yes:
        click.confirm(
            f"Remove {len(targets)} artifact(s) ({total / 1e9:.1f}GB)?",
            abort=True,
        )

    deleted, freed = delete_removal_targets(targets)
    console.print(
        f"[green]Removed[/green] {deleted} artifact(s), {freed / 1e9:.1f}GB"
    )


@cli.command()
@click.argument("model", required=False)
@click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)")
@click.option("--backend", "-b", default=None, type=click.Choice(_ALL_BACKENDS),
              help="Override backend (default: from model config)")
@click.option("--context-window", "context_window", default=None, type=int,
              help="Override the model's context_default for this server (tokens). "
                   "Use to start the server at e.g. 192K without editing models.toml.")
def serve(model, port, backend, context_window):
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
    if context_window:
        # Flows through both the provider (server --max-tokens) and the port
        # stamp's max_tokens field used by attaching harnesses to validate
        # their own --context-window against the server's actual capacity.
        m = {**m, "context_default": int(context_window)}

    provider = _get_provider_for(m)
    defaults = get_defaults()

    if not provider.is_downloaded(m):
        # `model` is the raw CLI arg — None when the model came from the
        # picker. Fall back to the resolved alias, then the hf_repo, so the
        # hint is never a literal "turbo pull None".
        hint = model or alias or m.get("hf_repo", "")
        console.print(f"[yellow]Model not downloaded.[/yellow] Run: [bold]turbo pull {hint}[/bold]")
        raise SystemExit(1)

    if not provider.is_available():
        console.print(f"[red]{provider.name} not found.[/red] Install: [bold]{provider.install_hint}[/bold]")
        raise SystemExit(1)

    port = port or defaults.get("port", 8899)

    if _server_is_running(port):
        console.print(
            f"[red]Port {port} is already serving a model.[/red] "
            "Use --port to pick a different port, or stop that server first."
        )
        raise SystemExit(1)

    cmd = provider.build_serve_cmd(m, port)

    console.print(f"Serving [bold]{m['name']}[/bold] on port {port}")
    console.print(f"  [dim]backend: {provider.name}[/dim]")
    console.print(f"  [dim]{' '.join(cmd)}[/dim]\n")
    # effective_context (not context_default_tokens): the provider serves
    # server-block-first precedence (a [server].max_tokens/context override
    # beats context_default), so the stamp must record what will actually be
    # served or the --context-window guard on attaching harnesses validates
    # against the wrong number.
    _write_port_stamp(port, alias, max_tokens=effective_context(m))
    try:
        subprocess.run(cmd)
    finally:
        # Only clear the stamp if it's still ours: a losing `turbo serve` on
        # a port that turned out to be busy (bind failed) must not delete the
        # winning process's stamp out from under it.
        _clear_port_stamp_if_owned(port)


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
    try:
        resp = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=5).read()
        )
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        console.print(f"[red]Could not reach server on port {port}:[/red] {e}")
        raise SystemExit(1) from None
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
        except KeyboardInterrupt:
            # Exit just this turn cleanly (no traceback) rather than the whole
            # REPL. The un-answered user message is popped below so `messages`
            # stays symmetric (user/assistant alternating) for the next turn.
            print()
            console.print("[dim]Interrupted.[/dim]")
            messages.pop()
            continue
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")
            messages.pop()
            continue

        print()
        messages.append({"role": "assistant", "content": "".join(assistant_msg)})


# ---------------------------------------------------------------------------
# Audio: transcribe via mlx-audio backend
# ---------------------------------------------------------------------------

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
@click.option("--split/--no-split", "split_flag", default=None,
              help="Force silence-aware pre-chunking on/off. Default: auto-split when audio > 30 min.")
@click.option("--split-threshold", default=1800.0, type=float, show_default=True,
              help="Auto-split when audio duration (seconds) exceeds this.")
@click.option("--split-target", default=300.0, type=float, show_default=True,
              help="Target chunk size (seconds) for the silence-aware splitter.")
@click.option("--split-max", default=600.0, type=float, show_default=True,
              help="Hard cap on chunk size (seconds); used when no silence lies within the target window.")
@click.option("--split-silence-db", default=-30.0, type=float, show_default=True,
              help="ffmpeg silencedetect noise threshold (dB).")
@click.option("--split-silence-min", default=0.8, type=float, show_default=True,
              help="ffmpeg silencedetect minimum silence duration (seconds).")
def transcribe(audio_file, model, language, response_format, port,
               split_flag, split_threshold, split_target, split_max,
               split_silence_db, split_silence_min):
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
    #
    # cleanup_wav is assigned (and the whole conversion+run wrapped in
    # try/finally) before ffmpeg actually runs, so a failed conversion still
    # gets its temp file cleaned up instead of leaking it.
    cleanup_wav: Path | None = None
    try:
        if path.suffix.lower() != ".wav":
            import shutil as _sh
            if not _sh.which("ffmpeg"):
                err.print("[red]ffmpeg required to transcribe non-wav files.[/red] brew install ffmpeg")
                raise SystemExit(1)
            err.print(f"[dim]converting {path.suffix} → wav via ffmpeg…[/dim]")
            wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            wav_tmp.close()
            wav_path = Path(wav_tmp.name)
            cleanup_wav = wav_path
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                     "-ac", "1", "-ar", "16000", str(wav_path)],
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                err.print(f"[red]ffmpeg failed to convert {path.name}:[/red] {e}")
                raise SystemExit(1) from None
            send_name = path.with_suffix(".wav").name
        else:
            wav_path = path
            send_name = path.name

        def _decide_split() -> bool:
            """Pick the path: explicit --split / --no-split overrides auto-detect."""
            if split_flag is not None:
                return split_flag
            try:
                import shutil as _sh
                if not _sh.which("ffprobe"):
                    return False
                from turbollm.transcribe_split import probe_duration_seconds
                duration = probe_duration_seconds(wav_path)
            except Exception:
                return False
            return duration > split_threshold

        def _do(_m, p):
            if _decide_split():
                if response_format in ("srt", "vtt"):
                    # Split mode merges chunk results into a single JSON blob
                    # (text/segments) — it never produces actual SRT/VTT text,
                    # so silently falling through to the raw-passthrough branch
                    # below would emit JSON while claiming to be srt/vtt.
                    raise click.UsageError(
                        f"--format {response_format} is not supported with split mode "
                        "(chunked transcription only produces text/segments/json/"
                        "verbose_json). Use --no-split, or pick a different --format."
                    )
                from turbollm.transcribe_split import SplitOptions, transcribe_split
                err.print(f"[dim]split mode: pre-chunking on silences for {wav_path.name}[/dim]")
                data = transcribe_split(
                    wav_path,
                    model_id=model_id,
                    port=p,
                    options=SplitOptions(
                        target_s=split_target,
                        max_s=split_max,
                        silence_db=split_silence_db,
                        silence_min_s=split_silence_min,
                    ),
                    language=language,
                    progress=lambda s: err.print(f"[dim]{s}[/dim]"),
                )
                raw = json.dumps(data, ensure_ascii=False)
            else:
                # Text and structured formats need transcript metadata; subtitle
                # formats must retain the provider's raw subtitle representation.
                server_format = response_format if response_format in ("srt", "vtt") else "verbose_json"
                file_bytes = wav_path.read_bytes()
                fields = {"model": model_id, "response_format": server_format}
                if language:
                    fields["language"] = language
                body, ctype = build_multipart(fields, "file", send_name, "audio/wav", file_bytes)
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
                if response_format not in ("srt", "vtt"):
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {"text": raw}

            if response_format in ("srt", "vtt"):
                sys.stdout.write(raw)
            elif response_format == "text":
                # Default behaviour: emit concatenated text, pipe-friendly.
                sys.stdout.write(data.get("text", raw).rstrip() + "\n")
            elif response_format == "segments":
                # mlx-audio's parakeet backend returns `sentences[]` (with per-sentence
                # start/end from token alignment); whisper-shaped backends return
                # `segments[]`. Normalise either shape to {start, end, text}.
                segments = data.get("segments")
                if not segments:
                    sentences = data.get("sentences") or []
                    if sentences:
                        segments = [
                            {"start": s.get("start", 0.0),
                             "end": s.get("end"),
                             "text": (s.get("text") or "").strip()}
                            for s in sentences
                        ]
                    else:
                        keys = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
                        sys.stderr.write(
                            f"warning: mlx-audio returned neither segments nor sentences (response keys: {keys})\n"
                        )
                        segments = [{"start": 0, "end": None, "text": data.get("text", "")}]
                sys.stdout.write(json.dumps(segments, indent=2, ensure_ascii=False) + "\n")
            else:
                # Legacy passthroughs: json, verbose_json — emit raw response.
                sys.stdout.write(raw)
            sys.stdout.flush()
            return 0

        _run_with_server(m, port, _do)
    finally:
        if cleanup_wav is not None and cleanup_wav.exists():
            try:
                os.unlink(cleanup_wav)
            except OSError:
                pass


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


def _write_port_stamp(port: int, alias: str | None, max_tokens: int | None = None) -> None:
    """Record the alias served on this port so `turbo <harness>` in another shell
    can resolve back to the full registry entry (including pi/opencode/server
    config) without depending on the server's advertised model id, which may not
    match any hf_repo (e.g. backends that rename the served model).

    ``max_tokens`` records the context size the server was actually started with,
    so attaching harnesses can refuse oversized --context-window overrides loudly
    instead of letting them silently get rejected by the server mid-request."""
    TURBOLLM_STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict = {"alias": alias, "pid": os.getpid(), "started_at": time.time()}
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
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


def _clear_port_stamp_if_owned(port: int) -> None:
    """Clear the port stamp only if it belongs to this process.

    A `turbo serve`/`_run_with_server` invocation that loses a bind race
    (another server won the port between our own liveness check and the
    actual bind) must not delete the *winning* process's stamp in its
    ``finally`` cleanup — that would erase a live server's stamp and quietly
    reintroduce the "harness validates against the wrong context size" bug.
    """
    p = _port_stamp_path(port)
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if data.get("pid") == os.getpid():
        _clear_port_stamp(port)


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
        except Exception as e:
            Console(stderr=True).print(
                f"[dim]stale port stamp: alias '{stamp['alias']}' no longer resolves "
                f"({e}); falling back to server probe[/dim]"
            )

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
    """Start turbo server (or reuse running one) and run launch_fn(m, port).

    Handles server lifecycle: starts if needed, waits for ready,
    stops on exit.  launch_fn should be a blocking call (e.g. subprocess.run).
    Returns whatever launch_fn returns (used for headless exit codes).
    """
    # Route every status/error line through stderr. The function's only data
    # path is launch_fn's return value (and whatever launch_fn writes to
    # stdout). Server-startup messages on stdout would otherwise leak into
    # pipelines like `turbo transcribe file.wav | pi -p '…'`, where the LLM
    # mistakes them for transcript content.
    console = Console(stderr=True)

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

    # Stamp the port so harnesses/transcribe attaching from another shell (and
    # the --context-window guard in _dispatch_harness) can see what this
    # auto-started server actually launched with. Previously only `turbo
    # serve` wrote a stamp, so servers auto-started here (harness/transcribe)
    # were invisible to that guard and it silently skipped validation.
    _write_port_stamp(port, None, max_tokens=effective_context(m))

    # Startup timeout in seconds. Per-model override beats global default;
    # falls back to 120s. Larger models (e.g. Qwen3.6 35B) reliably need more
    # than the default on cold disk caches. Uses a monotonic deadline (not an
    # iteration count) so the configured seconds roughly match wall-clock
    # time regardless of how long each _server_is_running probe takes.
    startup_timeout = int(
        m.get("server", {}).get("startup_timeout")
        or get_defaults().get("server_startup_timeout")
        or 120
    )
    try:
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
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
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        log_file.close()
        try:
            os.unlink(log_path)
        except OSError:
            pass
        _clear_port_stamp_if_owned(port)
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
    """Check if a backend is compatible with a harness's effective allow-list.

    Defaults to chat/text-gen backends (excludes ASR mlx-audio) when the harness
    declares no explicit requires_backend. See _harness_requires_backend.
    """
    return backend in _harness_requires_backend(harness_config)


_VALID_THINKING_LEVELS = PI_THINKING_LEVELS


def _dispatch_harness(
    harness_name: str,
    model: str | None,
    port: int | None,
    backend: str | None,
    prompt: str | None,
    *,
    context_window: int | None = None,
    thinking: str | None = None,
) -> None:
    """Shared dispatch for `turbo <harness>` and `turbo run -H <harness>`.

    Resolution order:
      1. Explicit ``model`` argument wins.
      2. Else, if a server is already running on ``port``:
         - compatible backend → attach to it
         - incompatible backend → refuse (see below)
      3. Else, run the picker filtered by the harness's ``requires_backend``.

    Refusing on an incompatible running server (rather than falling through to
    the picker) matters because the fallback used to pick a *different*,
    compatible model and then still hand it to `_run_with_server(m, port, …)`
    — which only checks "is something listening on `port`", not "is it
    compatible with `m`" — so it attached to the same incompatible server
    anyway (e.g. pi pointed at an mlx-audio ASR server). The user needs a
    clear signal to pick a free port or stop the other server, not a picker
    that silently doesn't fix anything.
    """
    explicit_port = port is not None
    port = port or get_defaults().get("port", 8899)
    reg = load_registry()
    harness_config = reg.get("harnesses", {}).get(harness_name, {})
    requires_backend = _harness_requires_backend(harness_config)

    def _apply_overrides(d: dict) -> dict:
        if backend:
            d = {**d, "backend": backend}
        if context_window:
            # Override the picker landing context for this invocation. Flows through
            # both the provider (server max-model-len) and the harness's models.json
            # via context_default_tokens().
            d = {**d, "context_default": int(context_window)}
        if thinking is not None:
            # Merge into pi config so PiHarness picks it up and appends `:level`
            # to the model arg. Harnesses that don't read pi config ignore it.
            supported = supported_thinking_levels(d)
            if thinking not in supported:
                raise click.UsageError(
                    f"{d.get('name', d.get('hf_repo', 'model'))} supports Pi "
                    f"thinking levels: {', '.join(supported)}"
                )
            pi_cfg = {**d.get("pi", {}), "thinking": thinking}
            d = {**d, "pi": pi_cfg}
        return d

    if model:
        m = resolve_model(model)
    elif _server_is_running(port):
        running = _get_running_model(port)
        running_backend = running.get("backend", "unknown") if running else "unknown"
        if running and _is_backend_compatible(harness_config, running_backend):
            # If the user asked for a larger context than the running server
            # actually started with, refuse loudly. Silently lying to the
            # harness here is what produced the autocompact-at-64K bug class:
            # pi happily plans for 192K, then the server rejects requests.
            if context_window:
                stamp = _read_port_stamp(port)
                served_max = stamp.get("max_tokens") if stamp else None
                if served_max and int(context_window) > int(served_max):
                    hint_alias = (stamp or {}).get("alias") or running.get("hf_repo", "<model>")
                    console.print(
                        f"[red]--context-window {context_window} exceeds the running "
                        f"server's max_tokens ({served_max}).[/red]\n"
                        f"  Stop that server and restart with the larger context:\n"
                        f"    [bold]turbo serve {hint_alias} --context-window {context_window}[/bold]"
                    )
                    raise SystemExit(1)
            if (
                not explicit_port
                and prompt is None
                and harness_config.get("offer_running_server_choice") is True
            ):
                choice = click.prompt(
                    f"Port {port} is serving {running.get('name', 'a Turbo model')}",
                    type=click.Choice(["use-running-server", "launch-new-server"]),
                    default="use-running-server",
                    show_choices=True,
                )
                if choice == "launch-new-server":
                    new_port = _next_free_port(port + 1)
                    _, selected = pick_model(requires_backend=requires_backend)
                    _run_harness(
                        harness_name,
                        _apply_overrides(selected),
                        new_port,
                    )
                    return
            running = _apply_overrides(running)
            _run_harness(harness_name, running, port, prompt=prompt)
            return
        if running:
            console.print(
                f"[red]Port {port} is busy with an incompatible server "
                f"({running.get('name', '?')} [{running_backend}]).[/red]\n"
                f"  {harness_name} requires backend: {', '.join(requires_backend)}\n"
                f"  Use --port to pick a different port, or stop that server first."
            )
            raise SystemExit(1)
        _, m = pick_model(requires_backend=requires_backend)
    else:
        _, m = pick_model(requires_backend=requires_backend)

    m = _apply_overrides(m)
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


def _harness_common_options(f):
    """Shared --port/--backend/--prompt/--context-window/--thinking options for
    harness-launching commands (`turbo <harness>` and `turbo run -H <harness>`).

    Previously duplicated verbatim between `_make_harness_command` and
    `run_cmd`; kept as one decorator so the two can't drift.
    """
    for option in reversed([
        click.option("--port", "-p", default=None, type=int, help="Port (default: 8899)"),
        click.option("--backend", "-b", default=None, type=click.Choice(_CHAT_BACKENDS),
                     help="Override backend (default: from model config)"),
        click.option("--prompt", default=None,
                     help="Run harness headlessly with this prompt and exit (no TTY)."),
        click.option("--context-window", "context_window", default=None, type=int,
                     help="Override the model's context_default for this invocation (tokens)."),
        click.option("--thinking", default=None, type=click.Choice(_VALID_THINKING_LEVELS),
                     help="Override the reasoning budget for this invocation (pi harness)."),
    ]):
        f = option(f)
    return f


def _make_harness_command(harness_name: str):
    """Create a click command for a TOML-defined harness."""
    @click.command(name=harness_name, help=f"Start model server + launch {harness_name}.")
    @click.argument("model", required=False)
    @_harness_common_options
    def cmd(model, port, backend, prompt, context_window, thinking):
        _dispatch_harness(
            harness_name, model, port, backend, prompt,
            context_window=context_window,
            thinking=thinking,
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


_WORKFLOWS_CONFIG_SUITE = "com.turbollm.hud"
_WORKFLOWS_CONFIG_KEY_PREFIX = "workflow"


def _defaults_key(workflow: str, param: str) -> str:
    return f"{_WORKFLOWS_CONFIG_KEY_PREFIX}.{workflow}.param.{param}"


def _defaults_read(suite: str, key: str) -> str | None:
    """Read a UserDefaults key from the given suite via /usr/bin/defaults.

    Workflow param stickies are a macOS/HUD convenience; off-Mac (no
    /usr/bin/defaults) the correct behavior is "no sticky", not a crash."""
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", suite, key],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _defaults_write(suite: str, key: str, value: str) -> None:
    """Write a UserDefaults string key to the given suite via /usr/bin/defaults."""
    try:
        subprocess.run(
            ["/usr/bin/defaults", "write", suite, key, "-string", value],
            check=True,
        )
    except FileNotFoundError:
        console.print("[dim]Sticky values need macOS (/usr/bin/defaults); skipping.[/dim]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to write sticky '{key}':[/red] {e}")


def _defaults_delete(suite: str, key: str) -> None:
    """Delete a UserDefaults key from the given suite via /usr/bin/defaults."""
    try:
        subprocess.run(
            ["/usr/bin/defaults", "delete", suite, key],
            capture_output=True,  # ignore errors if key doesn't exist
        )
    except FileNotFoundError:
        pass


def _defaults_read_all_for_workflow(suite: str, workflow: str) -> dict[str, str]:
    """Read all param sticky values for a workflow from UserDefaults."""
    prefix = f"{_WORKFLOWS_CONFIG_KEY_PREFIX}.{workflow}.param."
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", suite],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {}
    if result.returncode != 0:
        return {}
    items: dict[str, str] = {}
    # Parse plist-style output: each line is "    key = value ;" or similar.
    # Use a simple line-by-line scan; full plist parsing is overkill here.
    for line in result.stdout.splitlines():
        stripped = line.strip().rstrip(";").strip()
        if "=" not in stripped:
            continue
        k, _, v = stripped.partition(" = ")
        k = k.strip().strip('"')
        v = v.strip().strip('"')
        if k.startswith(prefix):
            param = k[len(prefix):]
            items[param] = v
    return items


@workflows_grp.command(name="config")
@click.argument("workflow_name")
@click.argument("assignments", nargs=-1, metavar="[PARAM=VALUE]...")
def workflows_config(workflow_name, assignments):
    """Read or write sticky param values for a workflow.

    \b
    Examples:
      turbo workflows config record-to-obsidian          # list all stickies
      turbo workflows config record-to-obsidian vault=/my/vault  # set one
      turbo workflows config record-to-obsidian vault=   # clear one
    """
    suite = _WORKFLOWS_CONFIG_SUITE

    if not assignments:
        # List all sticky values for this workflow.
        stickies = _defaults_read_all_for_workflow(suite, workflow_name)
        if not stickies:
            console.print(f"[dim]No sticky values for workflow '{workflow_name}'.[/dim]")
            return
        for param, value in sorted(stickies.items()):
            console.print(f"{param} = {value}")
        return

    for spec in assignments:
        if "=" not in spec:
            console.print(
                f"[red]Invalid assignment '{spec}'.[/red] Expected PARAM=VALUE or PARAM= to clear."
            )
            raise SystemExit(2)
        param, _, value = spec.partition("=")
        param = param.strip()
        key = _defaults_key(workflow_name, param)
        if value:
            _defaults_write(suite, key, value)
            console.print(f"Set {param} = {value}")
        else:
            _defaults_delete(suite, key)
            console.print(f"Cleared {param}")


@workflows_grp.command(name="status")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for machine consumption.")
def workflows_status(name, as_json):
    """Report whether a workflow is currently running.

    Probes the per-workflow flock without holding it. Exits 0 in both cases —
    "idle" is not an error condition. Use --json for Raycast / scripts.
    """
    from turbollm import workflows as _wf

    info = _wf.workflow_status(name)
    if as_json:
        click.echo(json.dumps(info))
        return
    if info["state"] == "running":
        pid = info["pid"]
        click.echo(f"running (pid {pid})" if pid else "running")
    else:
        click.echo("idle")


@workflows_grp.command(name="stop")
@click.argument("name")
def workflows_stop(name):
    """Send SIGTERM to a running workflow's launcher process."""
    from turbollm import workflows as _wf

    pid = _wf.stop_workflow(name)
    if pid is None:
        click.echo(f"workflow '{name}' is not running", err=True)
        raise SystemExit(1)
    click.echo(f"sent SIGTERM to pid {pid}")


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
@click.option("--harness", "-H", required=True, help="Harness to launch (e.g. pi, hermes)")
@_harness_common_options
def run_cmd(model, harness, port, backend, prompt, context_window, thinking):
    """Start model server + launch a harness by name."""
    _dispatch_harness(
        harness, model, port, backend, prompt,
        context_window=context_window,
        thinking=thinking,
    )


# ---------------------------------------------------------------------------
# Maintenance: turbo prune
# ---------------------------------------------------------------------------

_PRUNE_CATEGORY_LABELS = {
    "recordings": "Recordings (transcribed)",
    "activities": "Stale activity files",
    "locks": "Stale workflow locks",
    "session_dirs": "Orphaned screen-recording dirs",
}


def _format_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


@cli.command(name="prune")
@click.option("--dry-run", is_flag=True, help="Print what would be removed, do not delete.")
@click.option(
    "--vault", default=None, type=click.Path(),
    help="Obsidian vault path (default: $OBSIDIAN_VAULT or ~/Documents/Obsidian).",
)
@click.option(
    "--audio-dir", default=None, type=click.Path(),
    help="Audio files directory (default: $TURBO_AUDIO_INBOX or ~/Recordings/turbo).",
)
def prune_cmd(dry_run, vault, audio_dir):
    """Remove turbollm's intermediate artifacts (safe by default).

    \b
    Cleans up four categories:
      - recordings:   .wav files whose transcript is already in the vault
      - activities:   ~/.turbollm/state/*.json with no live owner_pid
      - locks:        ~/.turbollm/run/*.lock that are unheld + at least 1h old
      - session_dirs: orphaned screen-recording temp directories (>1h old)

    Never touches anything under the Obsidian vault or files held by a
    live process.

    \b
    Examples:
      turbo prune --dry-run     # preview what would be removed
      turbo prune               # delete
    """
    from turbollm import prune as _prune

    vault_path = Path(vault) if vault else _prune.default_vault()
    audio_path = Path(audio_dir) if audio_dir else _prune.default_recordings_dir()

    targets = _prune.gather_prune_targets(
        recordings_dir=audio_path,
        vault=vault_path,
    )

    total_count = sum(len(items) for items in targets.values())
    if total_count == 0:
        console.print("[dim]Nothing to prune.[/dim]")
        return

    table = Table(show_header=True, title="Prune candidates", title_justify="left")
    table.add_column("Category", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Size", justify="right")
    grand_total = 0
    for category in _prune.CATEGORY_ORDER:
        items = targets.get(category, [])
        if not items:
            continue
        size = sum(t.size_bytes for t in items)
        grand_total += size
        table.add_row(_PRUNE_CATEGORY_LABELS[category], str(len(items)), _format_bytes(size))
    console.print(table)
    console.print(f"[bold]Total:[/bold] {_format_bytes(grand_total)}")

    if dry_run:
        # Show individual paths so the user can audit.
        for category in _prune.CATEGORY_ORDER:
            items = targets.get(category, [])
            if not items:
                continue
            console.print(f"\n[bold]{_PRUNE_CATEGORY_LABELS[category]}[/bold]")
            for t in items:
                console.print(f"  {t.path}  [dim]({_format_bytes(t.size_bytes)})[/dim]")
        return

    all_targets = [t for items in targets.values() for t in items]
    deleted, freed = _prune.execute_prune(all_targets)
    console.print(f"[green]Deleted {deleted} item(s), freed {_format_bytes(freed)}[/green]")


# ---------------------------------------------------------------------------
# Optional plugins — attach supported plugin commands (e.g. the macOS sidecar +
# Raycast surface). Runs at import time, after every core command is defined, so
# it applies to both the installed `turbo` entry point and `python -m`. A
# missing/unsupported/broken plugin never breaks core turbo (see plugins loader).
# ---------------------------------------------------------------------------
from turbollm.plugins import register_all  # noqa: E402

register_all(cli)


if __name__ == "__main__":
    cli()
