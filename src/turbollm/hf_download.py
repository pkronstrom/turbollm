"""Shared HuggingFace Hub download helpers used by providers.

Extracted three near-identical blocks that had drifted across
providers/*.py (audit DRY findings):
  - `quiet_hf()`      — the httpx/huggingface_hub logging + telemetry-env
                        boilerplate that was copy-pasted in all five providers.
  - `configured_path` — the `_configured_path` helper triplicated (with two
                        different expansion idioms) in gguf/vllm_mlx/mlx_vlm.
  - `download_repo_with_progress` — the ~40-line file-listing/progress/summary
                        dance duplicated between vllm_mlx.pull and omlx.pull;
                        the omlx copy had already drifted and silently lost
                        `local_dir` support, which this restores.
  - `translate_hf_errors` — a decorator so provider `pull()`s report a clean
                        one-line error for network/auth failures instead of a
                        raw huggingface_hub traceback.
"""

import functools
import logging
import os
from pathlib import Path

import click
from rich.console import Console

console = Console()


def quiet_hf() -> None:
    """Silence noisy httpx/huggingface_hub loggers and disable HF telemetry.

    Call once at the top of any `pull()` that talks to huggingface_hub.
    """
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def configured_path(model: dict, key: str) -> Path | None:
    """Expand a model-configured filesystem path field (e.g. `local_path`,
    `draft_local_path`) to an absolute Path, or None if unset.

    Single canonical version of the `_configured_path` method that was
    duplicated (with two different expansion idioms) across providers.
    """
    value = model.get(key)
    if not value:
        return None
    return Path(os.path.expanduser(str(value)))


def translate_hf_errors(fn):
    """Decorator: turn huggingface_hub network/auth errors into a one-line
    click error instead of a raw traceback surfacing from `pull()`."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from huggingface_hub.utils import GatedRepoError, HfHubHTTPError

        try:
            return fn(*args, **kwargs)
        except GatedRepoError as e:
            raise click.ClickException(
                f"Model is gated on HuggingFace — request access on the model "
                f"page, then run `huggingface-cli login`. ({e})"
            )
        except HfHubHTTPError as e:
            raise click.ClickException(f"HuggingFace download failed: {e}")

    return wrapper


def download_repo_with_progress(repo: str, local_dir: Path | None = None) -> Path:
    """Download a full HF repo snapshot: small files quietly, safetensors
    with a per-file progress bar. Returns the resolved snapshot directory
    (`local_dir` if given, else the HF cache snapshot path).
    """
    from huggingface_hub import hf_hub_download, list_repo_files, snapshot_download
    from rich.progress import (
        BarColumn, DownloadColumn, Progress, SpinnerColumn,
        TextColumn, TimeRemainingColumn, TransferSpeedColumn,
    )

    quiet_hf()
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

    files = list_repo_files(repo_id=repo)
    model_files = [f for f in files if not f.startswith(".")]
    safetensor_files = [f for f in model_files if f.endswith(".safetensors")]
    other_files = [f for f in model_files if not f.endswith(".safetensors")]

    download_kwargs = {"repo_id": repo}
    if local_dir is not None:
        local_dir.mkdir(parents=True, exist_ok=True)
        download_kwargs["local_dir"] = str(local_dir)

    # Download small files first (configs, tokenizer) quietly.
    for f in other_files:
        hf_hub_download(filename=f, **download_kwargs)

    # Download safetensors with per-file progress.
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
            local_file = hf_hub_download(filename=f, **download_kwargs)
            fsize = Path(local_file).stat().st_size
            progress.update(task, completed=fsize, total=fsize)
            progress.remove_task(task)
            console.print(f"  [green]done[/green] {fname} ({fsize / 1e9:.1f}GB)")

    # Ensure snapshot is fully resolved (also covers the metadata-only repos).
    local = snapshot_download(**download_kwargs)
    total_size = sum(f.stat().st_size for f in Path(local).rglob("*") if f.is_file()) / 1e9
    console.print(f"\n  [green]Done![/green] {total_size:.1f}GB total")
    return Path(local)
