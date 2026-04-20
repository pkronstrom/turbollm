import tomllib
from pathlib import Path
from urllib.parse import urlparse

BUNDLED_TOML = Path(__file__).parent.parent.parent / "models.toml"
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"
LEGACY_DIR = Path.home() / ".turbollm" / "models"


def load_registry() -> dict:
    for path in [BUNDLED_TOML, Path.home() / ".turbollm" / "models.toml"]:
        if path.exists():
            return tomllib.loads(path.read_text())
    return {"defaults": {}, "models": {}}


def parse_model_input(raw: str) -> str:
    """Accept alias, hf_repo, or full HF URL. Returns hf_repo string."""
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and "huggingface.co" in parsed.netloc:
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    if "/" in raw:
        return raw
    return raw


def resolve_model(raw: str) -> dict:
    """Resolve a model input to a model dict. Accepts alias, repo, or URL."""
    repo_or_alias = parse_model_input(raw)
    reg = load_registry()
    models = reg.get("models", {})

    if repo_or_alias in models:
        return models[repo_or_alias]

    for m in models.values():
        if m["hf_repo"] == repo_or_alias:
            return m

    return {
        "name": repo_or_alias.split("/")[-1],
        "hf_repo": repo_or_alias,
        "size_gb": None,
        "tool_use": True,
        "can_reason": True,
    }


def get_defaults() -> dict:
    return load_registry().get("defaults", {})


def _hf_cache_path(hf_repo: str) -> Path:
    """Return the HF cache dir for a repo: ~/.cache/huggingface/hub/models--owner--repo"""
    return HF_CACHE / f"models--{hf_repo.replace('/', '--')}"


def _hf_snapshot_path(hf_repo: str) -> Path | None:
    """Return the latest snapshot path in HF cache, or None."""
    cache_dir = _hf_cache_path(hf_repo)
    snapshots = cache_dir / "snapshots"
    if not snapshots.exists():
        return None
    dirs = sorted(snapshots.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
    return dirs[0] if dirs else None


def _legacy_path(hf_repo: str) -> Path:
    """Old ~/.turbollm/models/ location."""
    return LEGACY_DIR / hf_repo.replace("/", "--")


def model_path(hf_repo: str) -> Path | None:
    """Return the local path to a downloaded model. Checks HF cache then legacy dir."""
    snap = _hf_snapshot_path(hf_repo)
    if snap:
        # Check for safetensors (MLX) or gguf files
        if any(snap.glob("*.safetensors")) or any(snap.glob("*.gguf")):
            return snap

    legacy = _legacy_path(hf_repo)
    if legacy.exists():
        if any(legacy.glob("*.safetensors")) or any(legacy.glob("*.gguf")):
            return legacy

    return None


def is_downloaded(hf_repo: str, hf_file: str | None = None) -> bool:
    """Check if a model is downloaded. For GGUF, checks specific file."""
    if hf_file:
        # GGUF single-file: check if that specific file exists in HF cache
        snap = _hf_snapshot_path(hf_repo)
        if snap and (snap / hf_file).exists():
            return True
        # Also check blobs (HF may symlink)
        cache = _hf_cache_path(hf_repo)
        if cache.exists():
            for f in cache.rglob(hf_file):
                return True
        return False
    return model_path(hf_repo) is not None


def list_downloaded() -> list[tuple[str, dict]]:
    """Return list of (alias_or_repo, model_dict) for all downloaded models."""
    reg = load_registry()
    models = reg.get("models", {})
    result = []

    for alias, m in models.items():
        if is_downloaded(m["hf_repo"], m.get("hf_file")):
            result.append((alias, m))

    return result
