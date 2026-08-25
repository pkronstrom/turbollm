import os
import shutil
from pathlib import Path

import click
from rich.console import Console

from turbollm.hf_download import configured_path, download_repo_with_progress, translate_hf_errors
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
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            # A real (non-symlink) directory/file at the link path — e.g. a
            # manually placed model. `.unlink()` raises on a directory, and
            # blowing it away automatically risks deleting something the
            # user put there on purpose after a multi-GB download.
            raise click.ClickException(
                f"{link} exists and isn't a symlink turbo manages. "
                f"Move or remove it, then re-run `turbo pull`."
            )
        link.symlink_to(snapshot_path)

    def _remove_symlink(self, model: dict) -> None:
        """Remove oMLX symlink for a model."""
        link = self._symlink_path(model)
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise click.ClickException(
                f"{link} exists and isn't a symlink turbo manages. Remove it manually."
            )

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

    @translate_hf_errors
    def pull(self, model: dict) -> None:
        repo = model["hf_repo"]
        local_path = configured_path(model, "local_path")
        snapshot_path = download_repo_with_progress(repo, local_path)

        # Create oMLX symlink
        self._ensure_symlink(model, snapshot_path)
        console.print(f"  [dim]Symlinked → {self._symlink_path(model)}[/dim]")

    def _model_path(self, model: dict) -> Path | None:
        local_path = configured_path(model, "local_path")
        if local_path and any(local_path.glob("*.safetensors")):
            return local_path

        snapshot_path = _hf_snapshot_path(model["hf_repo"])
        if snapshot_path and any(snapshot_path.glob("*.safetensors")):
            return snapshot_path

        return None

    def is_downloaded(self, model: dict) -> bool:
        model_path = self._model_path(model)
        if model_path is None:
            return False

        link = self._symlink_path(model)
        return link.is_symlink() and link.resolve() == model_path.resolve()

    def get_model_id(self, model: dict) -> str:
        return model["hf_repo"]

    def pull_draft(self, model: dict) -> None:
        """omlx doesn't support speculative decoding yet — no draft model."""
        return
