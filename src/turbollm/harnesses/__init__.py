"""Harness system — modular interface for agentic CLI tools.

Most harnesses are TOML-driven via GenericHarness (env vars + cmd template).
Custom harnesses (e.g. opencode) register via the @register decorator.
"""

import os
import shutil
import subprocess
from typing import Protocol

import click


class Harness(Protocol):
    name: str
    install_hint: str

    def is_available(self) -> bool: ...
    def launch(self, model_id: str, port: int, model: dict) -> None: ...
    def headless(self, model_id: str, port: int, model: dict, prompt: str) -> int: ...


def _format_template(template: str, ctx: dict, what: str) -> str:
    """`str.format` a TOML-supplied cmd/env value against {model_id}/{port}.

    Harness authors can put arbitrary literal text (including a stray `{`)
    in models.toml `cmd`/`env` entries; `str.format` raises on that with a
    traceback that doesn't name the offending config value. Catch and
    re-raise with a clear pointer instead.
    """
    try:
        return template.format(**ctx)
    except (KeyError, IndexError, ValueError) as e:
        raise click.UsageError(
            f"Invalid template in harness config ({what}): {template!r}: {e}"
        )


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
        if not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = "sk-local-no-auth-needed"

        ctx = {"model_id": model_id, "port": port}
        for k, v in self._env.items():
            env[k] = _format_template(v, ctx, f"env.{k}")

        cmd = [_format_template(part, ctx, "cmd") for part in self._cmd]
        subprocess.run(cmd, env=env)

    def headless(self, model_id: str, port: int, model: dict, prompt: str) -> int:
        raise NotImplementedError(f"{self.name} harness does not support headless mode")


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
from turbollm.harnesses import claude_code as _claude_code  # noqa: F401, E402
from turbollm.harnesses import hermes as _hermes  # noqa: F401, E402
from turbollm.harnesses import opencode as _opencode  # noqa: F401, E402
from turbollm.harnesses import omp as _omp  # noqa: F401, E402
from turbollm.harnesses import omp_lean as _omp_lean  # noqa: F401, E402
from turbollm.harnesses import pi as _pi  # noqa: F401, E402
