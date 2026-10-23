"""Shared Hugging Face downloads, revision options, paths, and error handling."""

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


def local_revision_matches(
    model: dict, directory: Path, *, prefix: str = "", filenames: list[str] | None = None
) -> bool:
    """Validate local-dir artifacts against a pin using only local Hub metadata.

    An exact commit can be checked directly. Branch/tag pins require a cached
    Hub ref; without one we conservatively refuse to claim the files match.
    No metadata or mismatched shards/config means the local model is unverified.
    """
    revision = model.get(prefix + "revision")
    if not revision:
        return True
    revision_path = Path(str(revision))
    if revision_path.is_absolute() or ".." in revision_path.parts:
        return False
    expected = str(revision)
    repo = model.get(prefix + "hf_repo")
    if repo:
        from turbollm.registry import _hf_cache_path
        try:
            cached = (_hf_cache_path(repo) / "refs" / revision_path).read_text().strip()
        except OSError:
            cached = None
        if cached:
            if Path(cached).name != cached or cached in (".", ".."):
                return False
            expected = cached
    if filenames is None:
        filenames = [str(path.relative_to(directory)) for path in directory.rglob("*")
                     if path.is_file() and ".cache" not in path.relative_to(directory).parts
                     and (path.suffix in (".safetensors", ".bin", ".gguf")
                          or path.name == "config.json" or path.name.endswith(".index.json"))]
    if not filenames:
        return False
    for name in filenames:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not (directory / relative).is_file():
            return False
        metadata = directory / ".cache" / "huggingface" / "download" / (name + ".metadata")
        try:
            with metadata.open() as handle:
                commit = handle.readline().strip()
        except (OSError, UnicodeError):
            return False
        if commit != expected:
            return False
    return True


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


def revision_kwargs(model: dict, key: str = "revision") -> dict:
    """Only pass configured revisions, retaining Hub defaults otherwise."""
    revision = model.get(key)
    return {"revision": revision} if revision else {}


def download_repo_with_progress(
    repo: str, local_dir: Path | None = None, *, revision: str | None = None
) -> Path:
    """Download one coherent snapshot with the Hub's concurrent progress UI.

    snapshot_download resolves the revision to a commit once and fetches its
    files concurrently. Avoid individual unpinned downloads followed by a
    second snapshot pass (which could also mix files across moving revisions).
    """
    from huggingface_hub import snapshot_download

    quiet_hf()
    download_kwargs = {"repo_id": repo, "max_workers": 8}
    if revision:
        download_kwargs["revision"] = revision
    if local_dir is not None:
        local_dir.mkdir(parents=True, exist_ok=True)
        download_kwargs["local_dir"] = str(local_dir)
    local = Path(snapshot_download(**download_kwargs))
    total_size = sum(f.stat().st_size for f in local.rglob("*") if f.is_file()) / 1e9
    console.print(f"\n  [green]Done![/green] {total_size:.1f}GB total")
    return local
