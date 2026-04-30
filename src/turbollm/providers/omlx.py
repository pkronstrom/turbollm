import logging
import os
import shutil
from pathlib import Path

from rich.console import Console

from turbollm.registry import _hf_snapshot_path, get_defaults

console = Console()

OMLX_MODELS_DIR = Path.home() / ".omlx" / "models"


class OmlxProvider:
    name = "omlx"
    install_hint = "brew tap jundot/omlx && brew install omlx"

    def is_available(self) -> bool:
        return shutil.which("omlx") is not None

    def _symlink_path(self, model: dict) -> Path:
        """Path where oMLX expects to find this model: ~/.omlx/models/<org>/<model>"""
        org, name = model["hf_repo"].split("/", 1)
        return OMLX_MODELS_DIR / org / name

    def _ensure_symlink(self, model: dict, snapshot_path: Path) -> None:
        """Create or update symlink from oMLX model dir to HF snapshot."""
        link = self._symlink_path(model)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(snapshot_path)

    def _remove_symlink(self, model: dict) -> None:
        """Remove oMLX symlink for a model."""
        link = self._symlink_path(model)
        if link.is_symlink() or link.exists():
            link.unlink()

    def build_serve_cmd(self, model: dict, port: int) -> list[str]:
        defaults = get_defaults()
        omlx_defaults = defaults.get("omlx", {})
        srv = model.get("server", {})

        model_dir = srv.get("model_dir") or omlx_defaults.get("model_dir") or str(OMLX_MODELS_DIR)
        model_dir = os.path.expanduser(model_dir)

        cmd = ["omlx", "serve",
               "--model-dir", model_dir,
               "--port", str(port),
               "--host", "127.0.0.1"]

        # SSD KV cache
        cache_dir = srv.get("paged_ssd_cache_dir") or omlx_defaults.get("paged_ssd_cache_dir")
        if cache_dir:
            cmd += ["--paged-ssd-cache-dir", os.path.expanduser(cache_dir)]

        cache_max = srv.get("paged_ssd_cache_max_size") or omlx_defaults.get("paged_ssd_cache_max_size")
        if cache_max:
            cmd += ["--paged-ssd-cache-max-size", str(cache_max)]

        hot_cache = srv.get("hot_cache_max_size") or omlx_defaults.get("hot_cache_max_size")
        if hot_cache:
            cmd += ["--hot-cache-max-size", str(hot_cache)]

        # Concurrency
        max_conc = srv.get("max_concurrent_requests") or omlx_defaults.get("max_concurrent_requests")
        if max_conc:
            cmd += ["--max-concurrent-requests", str(max_conc)]

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

        for f in other_files:
            hf_hub_download(repo_id=repo, filename=f)

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

        local = snapshot_download(repo_id=repo)
        snapshot_path = Path(local)
        total_size = sum(f.stat().st_size for f in snapshot_path.rglob("*") if f.is_file()) / 1e9
        console.print(f"\n  [green]Done![/green] {total_size:.1f}GB total")

        # Create oMLX symlink
        self._ensure_symlink(model, snapshot_path)
        console.print(f"  [dim]Symlinked → {self._symlink_path(model)}[/dim]")

    def is_downloaded(self, model: dict) -> bool:
        snap = _hf_snapshot_path(model["hf_repo"])
        if not snap or not any(snap.glob("*.safetensors")):
            return False
        link = self._symlink_path(model)
        return link.is_symlink()
