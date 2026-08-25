"""Claude Code harness — sets ANTHROPIC_BASE_URL pointing at turbo server."""

import os
import shutil
import subprocess

from turbollm.harnesses import register


@register("claude")
class ClaudeCodeHarness:
    def __init__(self, config: dict):
        self.name = "claude"
        self._binary = config.get("binary", "claude")
        self.install_hint = config.get("install", "npm install -g @anthropic-ai/claude-code")

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def headless(self, model_id: str, port: int, model: dict, prompt: str) -> int:
        raise NotImplementedError(f"{self.name} harness does not support headless mode")

    def launch(self, model_id: str, port: int, model: dict) -> None:
        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
        env["ANTHROPIC_AUTH_TOKEN"] = "turbollm"
        # Remove API key to avoid auth conflict warning
        env.pop("ANTHROPIC_API_KEY", None)
        subprocess.run([self._binary, "--model", model_id], env=env)
