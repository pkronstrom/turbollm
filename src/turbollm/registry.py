import tomllib
from pathlib import Path
from urllib.parse import urlparse

BUNDLED_TOML = Path(__file__).parent.parent.parent / "models.toml"
MODELS_DIR = Path.home() / ".turbollm" / "models"


def load_registry() -> dict:
    for path in [BUNDLED_TOML, Path.home() / ".turbollm" / "models.toml"]:
        if path.exists():
            return tomllib.loads(path.read_text())
    return {"defaults": {}, "models": {}}


def parse_model_input(raw: str) -> str:
    """Accept alias, hf_repo, or full HF URL. Returns hf_repo string."""
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and "huggingface.co" in parsed.netloc:
        # https://huggingface.co/owner/repo -> owner/repo
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    if "/" in raw:
        return raw  # already owner/repo
    return raw  # alias


def resolve_model(raw: str) -> dict:
    """Resolve a model input to a model dict. Accepts alias, repo, or URL."""
    repo_or_alias = parse_model_input(raw)
    reg = load_registry()
    models = reg.get("models", {})

    # Try alias match
    if repo_or_alias in models:
        return models[repo_or_alias]

    # Try hf_repo match
    for m in models.values():
        if m["hf_repo"] == repo_or_alias:
            return m

    # Unknown model — create an ad-hoc entry
    return {
        "name": repo_or_alias.split("/")[-1],
        "hf_repo": repo_or_alias,
        "size_gb": None,
        "tool_use": True,
        "can_reason": True,
    }


def get_defaults() -> dict:
    return load_registry().get("defaults", {})


def model_path(hf_repo: str) -> Path:
    return MODELS_DIR / hf_repo.replace("/", "--")


def is_downloaded(hf_repo: str) -> bool:
    p = model_path(hf_repo)
    return p.exists() and any(p.glob("*.safetensors"))


def list_downloaded() -> list[tuple[str, dict]]:
    """Return list of (alias_or_repo, model_dict) for all downloaded models."""
    reg = load_registry()
    models = reg.get("models", {})
    result = []

    for alias, m in models.items():
        if is_downloaded(m["hf_repo"]):
            result.append((alias, m))

    # Ad-hoc downloads not in registry
    if MODELS_DIR.exists():
        known_repos = {m["hf_repo"] for m in models.values()}
        for d in MODELS_DIR.iterdir():
            if not d.is_dir():
                continue
            repo = d.name.replace("--", "/")
            if repo not in known_repos and any(d.glob("*.safetensors")):
                result.append((repo, {
                    "name": repo.split("/")[-1],
                    "hf_repo": repo,
                    "size_gb": None,
                    "tool_use": True,
                    "can_reason": True,
                }))

    return result
