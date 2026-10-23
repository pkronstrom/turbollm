import importlib.util
from pathlib import Path
import signal
import subprocess
from unittest.mock import Mock, call


spec = importlib.util.spec_from_file_location(
    "kev_benchmark", Path(__file__).parents[1] / "tools/benchmarks/kev-local.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_benchmark_overrides_inherited_hub_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", "/unrelated/cache")
    monkeypatch.setenv("KEV_TEMPERATURE", "99")
    environment = benchmark.runtime_environment(tmp_path)
    assert environment["HF_HOME"] == str(tmp_path)
    assert environment["HF_HUB_CACHE"] == str(tmp_path / "hub")
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert "KEV_TEMPERATURE" not in environment


def test_benchmark_kills_and_reaps_child_after_termination_timeout():
    child = Mock()
    child.poll.return_value = None
    child.wait.side_effect = [subprocess.TimeoutExpired("kev", 15),
                              subprocess.TimeoutExpired("kev", 10), 0]
    benchmark.stop_child(child)
    child.send_signal.assert_called_once_with(signal.SIGINT)
    child.terminate.assert_called_once()
    child.kill.assert_called_once()
    assert child.wait.call_args_list == [call(timeout=15), call(timeout=10), call(timeout=10)]


def test_benchmark_reads_runtime_python_from_isolated_interpreter(monkeypatch, tmp_path):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{"runtime_python":"3.13.17","runtime_packages":{}}'))
    monkeypatch.setattr(benchmark.subprocess, "run", run)
    assert benchmark.runtime_metadata(tmp_path)["runtime_python"] == "3.13.17"
    assert run.call_args.args[0][0] == str(tmp_path / ".venv/bin/python")
