"""Locate bundled assets in installed wheels and editable source checkouts."""

from importlib.resources import files
from pathlib import Path


def asset_path(name: str, source_path: str) -> Path:
    """Return a filesystem asset (wheels are unpacked by the installer)."""
    packaged = Path(str(files("turbollm").joinpath("data", name)))
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / source_path
