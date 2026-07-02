"""Shared helpers for the mac plugin: tool paths, console, symlinks.

Kept dependency-light and free of any ``turbollm.cli`` imports so the plugin
modules never import back into the core CLI (which imports the plugin loader).
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

console = Console()


def tools_dir() -> Path:
    """Locate the repo's ``tools/`` directory (next to the turbollm package).

    Anchored on the installed ``turbollm`` package: ``src/turbollm/__init__.py``
    → ``src/turbollm`` → ``src`` → ``<repo root>/tools``. On a non-editable wheel
    install this won't exist, which is exactly when the mac plugin reports
    unsupported.
    """
    import turbollm

    return Path(turbollm.__file__).resolve().parents[2] / "tools"


def hud_dir() -> Path:
    return tools_dir() / "turbo-hud"


def acquirer_dir() -> Path:
    return tools_dir() / "turbo-acquirer"


def raycast_extension_dir() -> Path:
    return tools_dir() / "raycast-turbo"


def local_bin() -> Path:
    """Return ``~/.local/bin/``, creating it if necessary."""
    p = Path.home() / ".local" / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return p


def swift_build_product_path(package_dir: Path, product: str) -> Path:
    """Return the expected debug binary path for a Swift package product."""
    return package_dir / ".build" / "debug" / product


def refresh_symlink(link_path: Path, target_path: Path) -> None:
    """Create or refresh a symlink at ``link_path`` → ``target_path``.

    Also removes a plain regular file occupying ``link_path`` (e.g. left over
    from a build layout that predates symlinking, or a stray touch) so the
    symlink can be created — only ``symlink_to`` would otherwise raise
    ``FileExistsError``. A real *directory* at ``link_path`` is left alone;
    deleting a directory here is never the right silent fallback.
    """
    if link_path.is_symlink() or (link_path.exists() and link_path.is_file()):
        link_path.unlink()
    link_path.symlink_to(target_path)
