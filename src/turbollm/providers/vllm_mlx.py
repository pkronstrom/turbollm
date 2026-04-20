import logging
import os
import shutil
from pathlib import Path

from rich.console import Console

from turbollm.registry import _hf_cache_path, _hf_snapshot_path, _legacy_path

console = Console()


class VllmMlxProvider:
    name = "vllm-mlx"
    install_hint = "uv tool install git+https://github.com/waybarrios/vllm-mlx.git"

    def is_available(self) -> bool:
        return shutil.which("vllm-mlx") is not None

    def build_serve_cmd(self, model: dict, port: int) -> list[str]:
        local = self._model_path(model)
        cmd = ["vllm-mlx", "serve", str(local), "--port", str(port)]

        # Enable tool calling and reasoning for agentic use
        if model.get("tool_use", False):
            cmd += ["--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder"]
        if model.get("can_reason", False):
            cmd += ["--reasoning-parser", "qwen3"]

        # Speculative decoding with draft model
        draft_repo = model.get("draft_hf_repo")
        if draft_repo:
            draft_path = self._draft_model_path(model)
            if draft_path:
                cmd += ["--speculative-model", str(draft_path)]

        return cmd

    def pull(self, model: dict) -> None:
        from huggingface_hub import hf_hub_download, list_repo_files, snapshot_download
        from rich.progress import (
            BarColumn, DownloadColumn, Progress, SpinnerColumn,
            TextColumn, TimeRemainingColumn, TransferSpeedColumn,
        )

        repo = model["hf_repo"]

        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

        files = list_repo_files(repo_id=repo)
        model_files = [f for f in files if not f.startswith(".")]
        safetensor_files = [f for f in model_files if f.endswith(".safetensors")]
        other_files = [f for f in model_files if not f.endswith(".safetensors")]

        # Download small files first (configs, tokenizer) quietly
        for f in other_files:
            hf_hub_download(repo_id=repo, filename=f)

        # Download safetensors with per-file progress
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.fields[filename]}"),
            BarColumn(bar_width=30), DownloadColumn(),
            TransferSpeedColumn(), TimeRemainingColumn(),
            console=console, transient=True,
        ) as progress:
            for f in safetensor_files:
                fname = f.split("/")[-1]
                task = progress.add_task("dl", filename=fname, total=None, start=True)
                local_file = hf_hub_download(repo_id=repo, filename=f)
                fsize = Path(local_file).stat().st_size
                progress.update(task, completed=fsize, total=fsize)
                progress.remove_task(task)
                console.print(f"  [green]done[/green] {fname} ({fsize / 1e9:.1f}GB)")

        # Ensure snapshot is resolved
        local = snapshot_download(repo_id=repo)
        total_size = sum(f.stat().st_size for f in Path(local).rglob("*") if f.is_file()) / 1e9
        console.print(f"\n  [green]Done![/green] {total_size:.1f}GB total")

        # Pull draft model if configured
        draft_repo = model.get("draft_hf_repo")
        if draft_repo:
            console.print(f"\n  Pulling draft model: [dim]{draft_repo}[/dim]")
            snapshot_download(repo_id=draft_repo)
            console.print(f"  [green]Done![/green] Draft model cached\n")

    def is_downloaded(self, model: dict) -> bool:
        p = self._model_path(model)
        return p is not None

    def _model_path(self, model: dict) -> Path | None:
        repo = model["hf_repo"]
        snap = _hf_snapshot_path(repo)
        if snap and any(snap.glob("*.safetensors")):
            return snap
        legacy = _legacy_path(repo)
        if legacy.exists() and any(legacy.glob("*.safetensors")):
            return legacy
        return None

    def _draft_model_path(self, model: dict) -> Path | None:
        draft_repo = model.get("draft_hf_repo")
        if not draft_repo:
            return None
        snap = _hf_snapshot_path(draft_repo)
        if snap and any(snap.glob("*.safetensors")):
            return snap
        return None
