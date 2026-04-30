import tomllib
from pathlib import Path
from urllib.parse import urlparse

import click

CONFIG_DIR = Path.home() / ".turbollm"
BUNDLED_TOML = Path(__file__).parent.parent.parent / "models.toml"
USER_TOML = CONFIG_DIR / "models.toml"
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"
LEGACY_DIR = CONFIG_DIR / "models"


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise click.UsageError(f"Invalid TOML in {path}: {e}")


def _bootstrap_user_registry() -> dict | None:
    if USER_TOML.exists():
        return _load_toml(USER_TOML)
    if not BUNDLED_TOML.exists():
        return None

    data = _load_toml(BUNDLED_TOML)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_TOML.write_text(BUNDLED_TOML.read_text())
    return data


def load_registry() -> dict:
    data = _bootstrap_user_registry()
    if data is not None:
        return data
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

    if "/" not in repo_or_alias:
        available = ", ".join(models.keys())
        raise click.UsageError(
            f"Unknown model '{repo_or_alias}'. Available: {available}"
        )

    return {
        "name": repo_or_alias.split("/")[-1],
        "hf_repo": repo_or_alias,
        "size_gb": None,
        "tool_use": True,
        "can_reason": True,
    }


def get_defaults() -> dict:
    return load_registry().get("defaults", {})


# --- HF cache helpers (used by providers) ---

def _hf_cache_path(hf_repo: str) -> Path:
    return HF_CACHE / f"models--{hf_repo.replace('/', '--')}"


def _hf_snapshot_path(hf_repo: str) -> Path | None:
    cache_dir = _hf_cache_path(hf_repo)
    snapshots = cache_dir / "snapshots"
    if not snapshots.exists():
        return None
    dirs = sorted(snapshots.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
    return dirs[0] if dirs else None


def _legacy_path(hf_repo: str) -> Path:
    return LEGACY_DIR / hf_repo.replace("/", "--")
