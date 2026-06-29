"""Optional command plugins for turbollm.

A plugin is a package under ``turbollm.plugins`` that exposes two functions:

    is_supported() -> bool
    register_root_commands(cli: click.Group) -> None

``register_all(cli)`` registers every *supported* plugin's commands onto the
core CLI group. A plugin that is unsupported, or that raises during
registration, is skipped so it can never break core ``turbo`` — set
``TURBO_PLUGIN_DEBUG=1`` to print the traceback instead of swallowing it.

Plugins are kept in an explicit list rather than discovered by scanning the
filesystem: a single plugin doesn't warrant dynamic discovery, and an explicit
list is trivially extensible and easy to test.
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import TYPE_CHECKING

from turbollm.plugins import mac as _mac

if TYPE_CHECKING:
    import click

_PLUGINS = [_mac]


def _debug_enabled() -> bool:
    return os.environ.get("TURBO_PLUGIN_DEBUG", "").strip().lower() in ("1", "true", "yes")


def register_all(cli: "click.Group") -> None:
    """Attach every supported plugin's root commands onto ``cli``."""
    for plugin in _PLUGINS:
        try:
            if plugin.is_supported():
                plugin.register_root_commands(cli)
        except Exception:  # noqa: BLE001 — a plugin must never break core turbo
            if _debug_enabled():
                traceback.print_exc(file=sys.stderr)
