"""Harness system — modular interface for agentic CLI tools.

Most harnesses are TOML-driven via GenericHarness (env vars + cmd template).
Custom harnesses (e.g. opencode) register via the @register decorator.
"""

import os
import shutil
import subprocess
from typing import Protocol


class Harness(Protocol):
    name: str
    install_hint: str

    def is_available(self) -> bool: ...
    def launch(self, model_id: str, port: int, model: dict) -> None: ...


class GenericHarness:
    """TOML-configured harness for OpenAI-compatible CLI tools.

    Template variables in ``cmd`` and ``env`` values:
        {model_id} — model identifier reported by the server
        {port}     — server port number
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self._binary = config.get("binary", name)
        self.install_hint = config.get("install", f"Install {name}")
        self._cmd = config.get("cmd", [self._binary])
        self._env = config.get("env", {})

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def launch(self, model_id: str, port: int, model: dict) -> None:
        env = os.environ.copy()
        env["OPENAI_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
        env.setdefault("OPENAI_API_KEY", "not-needed")

        ctx = {"model_id": model_id, "port": port}
        for k, v in self._env.items():
            env[k] = v.format(**ctx)

        cmd = [part.format(**ctx) for part in self._cmd]
        subprocess.run(cmd, env=env)


# --- Custom harness registry ---

_CUSTOM: dict[str, type] = {}


def register(name: str):
    """Decorator to register a custom harness implementation."""
    def decorator(cls):
        _CUSTOM[name] = cls
        return cls
    return decorator


def get_harness(name: str, config: dict) -> Harness:
    """Get a harness by name. Uses custom class if registered, else GenericHarness."""
    if name in _CUSTOM:
        return _CUSTOM[name](config)
    return GenericHarness(name, config)


# Import custom harnesses to trigger registration
from turbollm.harnesses import opencode as _opencode  # noqa: F401, E402
