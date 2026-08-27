"""Lean, isolated Oh My Pi harness for local Qwen coding sessions."""

import os
import subprocess
from pathlib import Path

from turbollm.harnesses import register
from turbollm.harnesses.omp import OmpHarness


LEAN_TOOL_FLAGS = ["--tools=read,bash,edit,write,grep,glob,lsp,ask,todo"]
HEADLESS_TOOL_FLAGS = ["--tools=read,bash,edit,write,grep,glob,lsp,todo"]
LEAN_PROMPT_PATH = (
    Path(__file__).resolve().parents[3] / "integrations" / "omp" / "qwen-lean-system-prompt.md"
)


@register("omp-lean")
class OmpLeanHarness(OmpHarness):
    """Launch OMP with a dedicated state directory and narrow local tool surface."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "omp-lean"
        configured = config.get("agent_dir")
        self._lean_agent_dir = (
            Path(os.path.expanduser(str(configured)))
            if configured
            else Path.home() / ".omp" / "agent-qwen-lean"
        )

    def _agent_dir(self) -> Path:
        return self._lean_agent_dir

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PI_CODING_AGENT_DIR"] = str(self._lean_agent_dir)
        return env

    def _args(self, model_arg: str, prompt: str | None = None) -> list[str]:
        tool_flags = HEADLESS_TOOL_FLAGS if prompt is not None else LEAN_TOOL_FLAGS
        args = [
            self._binary,
            "--no-extensions",
            "--no-rules",
            *tool_flags,
            "--skills=vault-mcp,vault-skills",
            "--system-prompt",
            str(LEAN_PROMPT_PATH),
        ]
        if prompt is not None:
            args.append("-p")
        args.extend(["--model", model_arg])
        if prompt is not None:
            args.append(prompt)
        return args

    def launch(self, model_id: str, port: int, model: dict) -> None:
        model_arg = self._write_provider_config(model_id, port, model)
        subprocess.run(self._args(model_arg), env=self._environment())

    def headless(self, model_id: str, port: int, model: dict, prompt: str) -> int:
        model_arg = self._write_provider_config(model_id, port, model)
        return subprocess.run(self._args(model_arg, prompt), env=self._environment()).returncode
