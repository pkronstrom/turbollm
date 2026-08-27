import json
import os
import shutil
from pathlib import Path

import click
from rich.console import Console

from turbollm.hf_download import configured_path, download_repo_with_progress, translate_hf_errors
from turbollm.registry import _hf_snapshot_path, get_defaults

console = Console()

OMLX_MODELS_DIR = Path.home() / ".omlx" / "models"
OMLX_MODEL_SETTINGS_PATH = Path.home() / ".omlx" / "model_settings.json"


_NATIVE_MTP_SETTING_KEYS = (
    "mtp_enabled",
    "turboquant_kv_enabled",
    "dflash_enabled",
    "vlm_mtp_enabled",
)


def _write_native_mtp_settings(model: dict) -> None:
    configured = {
        key: model.get("server", {})[key]
        for key in _NATIVE_MTP_SETTING_KEYS
        if key in model.get("server", {})
    }
    if not configured:
        return

    try:
        existing = json.loads(OMLX_MODEL_SETTINGS_PATH.read_text()) if OMLX_MODEL_SETTINGS_PATH.exists() else {}
    except (json.JSONDecodeError, OSError) as error:
        raise click.ClickException(
            f"Cannot update oMLX model settings at {OMLX_MODEL_SETTINGS_PATH}: {error}"
        ) from error
    if not isinstance(existing, dict):
        raise click.ClickException(
            f"Cannot update oMLX model settings at {OMLX_MODEL_SETTINGS_PATH}: root must be a mapping"
        )

    models = existing.setdefault("models", {})
    if not isinstance(models, dict):
        raise click.ClickException(
            f"Cannot update oMLX model settings at {OMLX_MODEL_SETTINGS_PATH}: models must be a mapping"
        )
    model_name = model["hf_repo"].split("/", 1)[1]
    current = models.get(model_name, {})
    if not isinstance(current, dict):
        raise click.ClickException(
            f"Cannot update oMLX model settings for {model_name}: settings must be a mapping"
        )
    models[model_name] = {**current, **configured}

    OMLX_MODEL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    OMLX_MODEL_SETTINGS_PATH.write_text(json.dumps(existing, indent=2) + "\n")

_HOMEBREW_OMLX_BIN_DIR = "/opt/homebrew/opt/omlx/bin"



class OmlxProvider:
    name = "omlx"
    install_hint = "brew tap jundot/omlx && brew install omlx"

    def _executable(self) -> str | None:
        return shutil.which("omlx") or shutil.which("omlx", path=_HOMEBREW_OMLX_BIN_DIR)

    def is_available(self) -> bool:
        return self._executable() is not None

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
        _write_native_mtp_settings(model)

        model_dir = srv.get("model_dir") or omlx_defaults.get("model_dir") or str(OMLX_MODELS_DIR)
        model_dir = os.path.expanduser(model_dir)

        cmd = [self._executable() or "omlx", "serve",
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
        if not link.is_symlink():
            return False

        try:
            return link.resolve() == model_path.resolve()
        except (OSError, RuntimeError):
            return False

    def get_model_id(self, model: dict) -> str:
        return model["hf_repo"].split("/", 1)[-1]

    def pull_draft(self, model: dict) -> None:
        """omlx doesn't support speculative decoding yet — no draft model."""
        return
