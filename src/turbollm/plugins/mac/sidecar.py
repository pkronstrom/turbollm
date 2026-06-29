"""``turbo sidecar`` — build the Swift acquirer + HUD, symlink them, launch the HUD."""

from __future__ import annotations

import subprocess

import click

from turbollm.plugins.mac._common import (
    acquirer_dir,
    console,
    hud_dir,
    local_bin,
    raycast_extension_dir,
    refresh_symlink,
    swift_build_product_path,
)


def _sidecar_raycast_sync() -> None:
    """Auto-sync the Raycast extension on sidecar startup (T-26).

    Fails gracefully: if the extension directory is absent (e.g. the user has
    not installed the Raycast extension yet), a dim warning is printed and
    startup continues.
    """
    from turbollm.plugins.mac.raycast import _do_raycast_sync

    ext_dir = raycast_extension_dir()
    if not ext_dir.exists():
        console.print("[dim]Raycast extension not found — skipping sync.[/dim]")
        return
    try:
        _do_raycast_sync(ext_dir, quiet=True)
        console.print("[dim]Raycast commands synced.[/dim]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[dim]Warning: raycast sync failed: {exc}[/dim]")


@click.command(name="sidecar")
@click.option("--build/--no-build", default=True,
              help="Run `swift build` before launching (default: yes).")
def sidecar_cmd(build):
    """Build turbo-acquirer + TurboHUD, symlink both, then launch the HUD."""
    hud = hud_dir()
    if not hud.exists() or not (hud / "Package.swift").exists():
        console.print(
            f"[red]Swift HUD package not found at {hud}.[/red]\n"
            "Implement the package (Plan 2) before invoking turbo sidecar."
        )
        raise SystemExit(1)

    acquirer = acquirer_dir()
    has_acquirer = acquirer.exists() and (acquirer / "Package.swift").exists()

    if build:
        # Build turbo-acquirer first (independent; fails gracefully if absent).
        if has_acquirer:
            console.print(f"[dim]Building turbo-acquirer in {acquirer}...[/dim]")
            result = subprocess.run(["swift", "build"], cwd=acquirer)
            if result.returncode != 0:
                console.print("[yellow]Warning: turbo-acquirer build failed — skipping.[/yellow]")
                has_acquirer = False

        # Refresh turbo-acquirer symlink if build succeeded.
        if has_acquirer:
            acq_bin = swift_build_product_path(acquirer, "turbo-acquirer")
            if acq_bin.exists():
                link = local_bin() / "turbo-acquirer"
                refresh_symlink(link, acq_bin)
                console.print(f"[dim]Symlinked turbo-acquirer → {acq_bin}[/dim]")

        # Build TurboHUD too so the symlink target exists and the HUD can be
        # launched via a stable path. This is what gives macOS a consistent
        # binary identity for TCC grants — `swift run` rebuilds the binary
        # in place each time and produces a fresh wrapper, which would force
        # the user to re-grant permissions on every invocation.
        console.print(f"[dim]Building TurboHUD in {hud}...[/dim]")
        result = subprocess.run(["swift", "build"], cwd=hud)
        if result.returncode != 0:
            console.print("[red]TurboHUD build failed.[/red]")
            raise SystemExit(1)

    # T-26: Sync Raycast extension on every sidecar launch so per-workflow
    # commands stay in sync with models.toml without manual intervention.
    _sidecar_raycast_sync()

    hud_bin = swift_build_product_path(hud, "TurboHUD")
    if hud_bin.exists():
        hud_link = local_bin() / "TurboHUD"
        refresh_symlink(hud_link, hud_bin)
        console.print(f"[dim]Symlinked TurboHUD → {hud_bin}[/dim]")
        console.print(f"[dim]Launching HUD via {hud_link}...[/dim]")
        subprocess.run([str(hud_link)])
    else:
        # Fallback (cold start before any build, or unexpected state): use
        # `swift run` so the user is not left without a HUD. The symlink
        # path will be wired up on the next `turbo sidecar --build`.
        args = ["swift", "run"] if build else ["swift", "run", "--skip-build"]
        console.print(
            f"[yellow]HUD binary not found at {hud_bin}; "
            f"launching via `swift run` in {hud}.[/yellow]"
        )
        subprocess.run(args, cwd=hud)
