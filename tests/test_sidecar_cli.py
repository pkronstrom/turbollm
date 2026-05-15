from unittest.mock import patch
from pathlib import Path

from click.testing import CliRunner

from turbollm import cli as turbo_cli


def test_sidecar_errors_when_swift_package_missing(monkeypatch, tmp_path):
    fake_repo_root = tmp_path / "fake-repo"
    fake_repo_root.mkdir()
    # No tools/turbo-hud/ inside fake_repo_root
    runner = CliRunner()
    with patch("turbollm.cli.Path") as mock_path:
        mock_path.return_value.resolve.return_value.parent.parent.parent = fake_repo_root
        result = runner.invoke(turbo_cli.cli, ["sidecar"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_sidecar_invokes_swift_run(tmp_path):
    hud_dir = tmp_path / "tools" / "turbo-hud"
    hud_dir.mkdir(parents=True)
    (hud_dir / "Package.swift").write_text("")
    runner = CliRunner()
    with patch("turbollm.cli.subprocess.run") as mock_run:
        with patch.object(turbo_cli, "_hud_dir", return_value=hud_dir):
            result = runner.invoke(turbo_cli.cli, ["sidecar"])
    assert result.exit_code == 0
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0][:2] == ["swift", "run"]
    assert kwargs.get("cwd") == hud_dir
