"""macOS sidecar + Raycast plugin.

Adds the ``turbo sidecar`` command and the ``turbo raycast`` group when running
on a Mac with the Swift toolchain and the in-tree ``tools/`` sources present.
Lightweight by design: no top-level platform imports, heavy work deferred into
the hook functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click


def is_supported() -> bool:
    """True only when the sidecar/raycast commands can actually run.

    Requires macOS, a ``swift`` toolchain, **and** the ``tools/turbo-hud``
    source tree (what the commands build and symlink from). The ``tools/`` check
    matters: an installed wheel on a Mac with Swift but without the source tree
    must report unsupported, otherwise the commands would appear and then fail.
    """
    import shutil
    import sys

    if sys.platform != "darwin":
        return False
    if shutil.which("swift") is None:
        return False

    from turbollm.plugins.mac._common import hud_dir

    return hud_dir().exists()


def register_root_commands(cli: "click.Group") -> None:
    """Attach the mac plugin's top-level commands onto the core CLI group."""
    from turbollm.plugins.mac.raycast import raycast_grp
    from turbollm.plugins.mac.sidecar import sidecar_cmd

    cli.add_command(sidecar_cmd)
    cli.add_command(raycast_grp)
