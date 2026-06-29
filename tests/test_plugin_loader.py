"""Tests for the optional-plugin loader (turbollm.plugins.register_all).

The loader must (1) attach a supported plugin's commands, (2) attach nothing and
raise nothing for an unsupported plugin, and (3) never let a plugin that raises
during registration break core turbo — while still surfacing the failure under
TURBO_PLUGIN_DEBUG=1. Tests run against a fresh click.Group, never the
import-time cli, so they don't depend on host platform detection.
"""
from __future__ import annotations

from unittest.mock import patch

import click

from turbollm import plugins
from turbollm.plugins import mac


def _fresh_group() -> click.Group:
    @click.group()
    def g():
        pass

    return g


def test_register_all_attaches_mac_commands_when_supported():
    g = _fresh_group()
    with patch.object(mac, "is_supported", return_value=True):
        plugins.register_all(g)
    assert "sidecar" in g.commands
    assert "raycast" in g.commands


def test_register_all_attaches_nothing_when_unsupported():
    g = _fresh_group()
    with patch.object(mac, "is_supported", return_value=False):
        plugins.register_all(g)
    assert "sidecar" not in g.commands
    assert "raycast" not in g.commands


def test_register_all_swallows_plugin_errors(capsys):
    """A plugin raising during registration must not break the core group, and
    must stay silent unless TURBO_PLUGIN_DEBUG is set."""
    g = _fresh_group()
    with (
        patch.object(mac, "is_supported", return_value=True),
        patch.object(mac, "register_root_commands", side_effect=RuntimeError("boom")),
        patch.dict("os.environ", {}, clear=False),
    ):
        # Ensure the debug flag is off.
        with patch.dict("os.environ", {"TURBO_PLUGIN_DEBUG": ""}):
            plugins.register_all(g)  # must not raise

    assert "sidecar" not in g.commands
    err = capsys.readouterr().err
    assert "boom" not in err  # swallowed silently when debug off


def test_register_all_surfaces_errors_under_debug(capsys):
    g = _fresh_group()
    with (
        patch.object(mac, "is_supported", return_value=True),
        patch.object(mac, "register_root_commands", side_effect=RuntimeError("boom")),
        patch.dict("os.environ", {"TURBO_PLUGIN_DEBUG": "1"}),
    ):
        plugins.register_all(g)  # must not raise

    err = capsys.readouterr().err
    assert "boom" in err  # traceback printed under TURBO_PLUGIN_DEBUG=1


def test_mac_is_supported_false_off_darwin():
    """On a non-macOS platform the mac plugin reports unsupported."""
    with patch("sys.platform", "linux"):
        assert mac.is_supported() is False
