import fcntl
import json
import os
import shutil
import tempfile
from pathlib import Path

import click
from rich.console import Console

from turbollm.hf_download import configured_path, local_revision_matches, revision_kwargs, download_repo_with_progress, translate_hf_errors
from turbollm.registry import _hf_snapshot_path, effective_context, effective_sampling, get_defaults

console = Console()

OMLX_MODELS_DIR = Path.home() / ".omlx" / "models"
OMLX_MODEL_SETTINGS_PATH = Path.home() / ".omlx" / "model_settings.json"


_NATIVE_SETTING_KEYS = (
    "mtp_enabled", "turboquant_kv_enabled", "dflash_enabled", "vlm_mtp_enabled",
    "is_pinned", "ttl_seconds", "enable_thinking", "preserve_thinking",
)
_SAMPLER_KEYS = (
    "temperature", "top_p", "top_k", "min_p", "presence_penalty", "repeat_penalty",
)


def _merge_settings(existing: dict, configured: dict) -> dict:
    result = dict(existing)
    for key, value in configured.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_settings(result[key], value)
        else:
            result[key] = value
    return result


def _write_native_mtp_settings(model: dict) -> None:
    """Merge configured defaults into native oMLX settings, without forcing requests.

    The sidecar lock serializes Turbo writers; atomic replacement also keeps
    readers from observing a partial JSON document.
    """
    srv = model.get("server", {})
    configured = {key: srv[key] for key in _NATIVE_SETTING_KEYS if key in srv}
    if "context_default" in model or "max_context_window" in srv:
        configured["max_context_window"] = effective_context({**model, "backend": "omlx"})
    if "max_tokens" in srv:
        configured["max_tokens"] = srv["max_tokens"]
    if model.get("sampling") or any(f"default_{key}" in srv for key in _SAMPLER_KEYS):
        for key, value in effective_sampling(model).items():
            if key in _SAMPLER_KEYS:
                configured["repetition_penalty" if key == "repeat_penalty" else key] = value
    if "default_chat_template_kwargs" in srv:
        configured["chat_template_kwargs"] = srv["default_chat_template_kwargs"]
    if not configured:
        return

    path = OMLX_MODEL_SETTINGS_PATH
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(existing, dict):
                raise ValueError("root must be a mapping")
            models = existing.setdefault("models", {})
            if not isinstance(models, dict):
                raise ValueError("models must be a mapping")
            model_name = model["hf_repo"].split("/", 1)[-1]
            current = models.get(model_name, {})
            if not isinstance(current, dict):
                raise ValueError(f"settings for {model_name} must be a mapping")
            models[model_name] = _merge_settings(current, configured)
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent,
                                             prefix=path.name + ".", delete=False) as output:
                temporary = Path(output.name)
                if path.exists():
                    os.fchmod(output.fileno(), path.stat().st_mode & 0o777)
                output.write(json.dumps(existing, indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            temporary = None
    except (OSError, ValueError) as error:
        raise click.ClickException(f"Cannot update oMLX model settings at {path}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

_HOMEBREW_OMLX_BIN_DIR = "/opt/homebrew/opt/omlx/bin"



class OmlxProvider:
    name = "omlx"
    install_hint = "brew tap jundot/omlx && brew install omlx"

    def _executable(self) -> str | None:
        return shutil.which("omlx") or shutil.which("omlx", path=_HOMEBREW_OMLX_BIN_DIR)

    def is_available(self) -> bool:
        return self._executable() is not None

    def _model_dir(self, model: dict) -> Path:
        configured = (model.get("server", {}).get("model_dir")
                      or get_defaults().get("omlx", {}).get("model_dir")
                      or OMLX_MODELS_DIR)
        return Path(configured).expanduser()

    def _symlink_path(self, model: dict) -> Path:
        """Path where oMLX expects to find this model: ~/.omlx/models/<org>/<model>"""
        org, name = model["hf_repo"].split("/", 1)
        return self._model_dir(model) / org / name

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

        model_dir = str(self._model_dir(model))

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
        snapshot_path = download_repo_with_progress(repo, local_path, **revision_kwargs(model))

        # Create oMLX symlink
        self._ensure_symlink(model, snapshot_path)
        console.print(f"  [dim]Symlinked → {self._symlink_path(model)}[/dim]")

    def _model_path(self, model: dict) -> Path | None:
        local_path = configured_path(model, "local_path")
        if local_path and any(local_path.glob("*.safetensors")) and local_revision_matches(model, local_path):
            return local_path

        snapshot_path = _hf_snapshot_path(model["hf_repo"], **revision_kwargs(model))
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
