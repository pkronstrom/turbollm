import tomllib
from pathlib import Path
from urllib.parse import urlparse

import click

CONFIG_DIR = Path.home() / ".turbollm"
BUNDLED_TOML = Path(__file__).parent.parent.parent / "models.toml"
USER_TOML = CONFIG_DIR / "models.toml"
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"
LEGACY_DIR = CONFIG_DIR / "models"

PI_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


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
    return raw


def resolve_model(raw: str) -> dict:
    """Resolve a model input to a model dict. Accepts alias, repo, or URL."""
    repo_or_alias = parse_model_input(raw)
    reg = load_registry()
    models = reg.get("models", {})

    if repo_or_alias in models:
        return models[repo_or_alias]

    for m in models.values():
        # .get(), not [] — a user-added model missing hf_repo shouldn't
        # break resolution of every other entry.
        if m.get("hf_repo") == repo_or_alias:
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


def supported_thinking_levels(model: dict) -> tuple[str, ...]:
    """Pi reasoning levels this model actually supports, in Pi's UI order."""
    configured = model.get("pi", {}).get("thinking_levels")
    if configured is None:
        return PI_THINKING_LEVELS
    return tuple(level for level in PI_THINKING_LEVELS if level in configured)


def pi_thinking_level_map(model: dict) -> dict[str, str | None]:
    """Pi model-schema mapping, using null for levels that must stay hidden."""
    supported = set(supported_thinking_levels(model))
    return {
        level: level if level in supported else None
        for level in PI_THINKING_LEVELS
    }


# --- Unified config helpers (sampling presets, KV quant, context) --------------
# These let provider code and the picker read one source of truth instead of
# probing many overlapping keys across [server] / [opencode] / [pi] blocks.

def get_sampling_preset(name: str) -> dict:
    """Resolve a named sampling preset (e.g. "qwen3-thinking-coding") to its
    fields. Returns {} if the preset is missing — providers should treat that
    as "use binary defaults"."""
    return load_registry().get("sampling", {}).get(name, {})


def effective_sampling(model: dict) -> dict:
    """Resolve the sampler fields that should be applied for this model.

    Order of precedence (lowest to highest):
      1. [defaults.sampling]
      2. [sampling.<preset>] if model has `sampling = "<preset>"`
      3. Any per-model overrides in [models.X.server] with `default_<field>` keys
         (legacy shape — preserved so old configs keep working).
    """
    out: dict = {}
    out.update(load_registry().get("defaults", {}).get("sampling", {}))
    preset_name = model.get("sampling")
    if preset_name:
        out.update(get_sampling_preset(preset_name))
    srv = model.get("server", {})
    for k in ("temperature", "top_p", "top_k", "min_p",
              "presence_penalty", "repeat_penalty"):
        v = srv.get(f"default_{k}")
        if v is not None:
            out[k] = v
    return out


def effective_kv_quant(model: dict) -> str:
    """Resolve the model's `kv_quant` field to a concrete value.

    `auto` resolves per backend:
      - gguf  → "q8"  (Unsloth's broadly-safe baseline for Qwen3-family)
      - other → "off" (no validated case yet for MLX backends)

    Returns one of: off / q8 / q4. Unknown values are treated as "off".
    """
    raw = (model.get("kv_quant") or "off").strip().lower()
    if raw == "auto":
        backend = model.get("backend", "vllm-mlx")
        return "q8" if backend == "gguf" else "off"
    if raw in ("off", "q8", "q4"):
        return raw
    return "off"


def effective_context(model: dict) -> int:
    """The context window the server will actually serve, in tokens.

    Mirrors the providers' runtime precedence: a [server] override wins
    (`max_tokens` for the MLX backends, `context` for gguf — the picker
    writes these at runtime), then the registry's `context_default`.
    This is the single source of truth for the serve command, the port
    stamp, and what harnesses (pi/opencode) report as their context
    window — divergence between those was bug-class "harness compacts at
    the wrong size / server rejects mid-session".
    """
    srv = model.get("server", {})
    v = srv.get("max_tokens") or srv.get("context")
    if v:
        return int(v)
    return context_default_tokens(model)


def context_default_tokens(model: dict) -> int:
    """Picker's default landing context, in tokens. Falls back to legacy
    [server].max_tokens / [server].context for old-shape configs."""
    v = model.get("context_default")
    if v:
        return int(v)
    srv = model.get("server", {})
    return int(srv.get("max_tokens") or srv.get("context") or 32768)


def context_max_tokens(model: dict) -> int:
    """Picker ceiling, in tokens. Falls back to context_default if not set
    so the picker degenerates to a single choice rather than misbehaving."""
    v = model.get("context_max")
    if v:
        return int(v)
    return context_default_tokens(model)


# --- HF cache helpers (used by providers) ---

def _hf_cache_path(hf_repo: str) -> Path:
    return HF_CACHE / f"models--{hf_repo.replace('/', '--')}"


def _hf_snapshot_path(hf_repo: str) -> Path | None:
    cache_dir = _hf_cache_path(hf_repo)
    snapshots = cache_dir / "snapshots"
    if not snapshots.exists():
        return None
    dirs = sorted(
        (d for d in snapshots.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return dirs[0] if dirs else None


def _legacy_path(hf_repo: str) -> Path:
    return LEGACY_DIR / hf_repo.replace("/", "--")
