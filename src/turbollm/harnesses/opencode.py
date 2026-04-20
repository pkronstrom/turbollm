"""OpenCode harness — custom launcher with JSON config generation."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from turbollm.harnesses import register
from turbollm.registry import get_defaults


@register("opencode")
class OpenCodeHarness:
    def __init__(self, config: dict):
        self.name = "opencode"
        self._binary = config.get("binary", "opencode")
        self.install_hint = config.get("install", "go install github.com/opencode-ai/opencode@latest")

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def launch(self, model_id: str, port: int, model: dict) -> None:
        defaults_oc = get_defaults().get("opencode", {})
        model_oc = model.get("opencode", {})
        ctx = model_oc.get("context_length", defaults_oc.get("context_length", 32768))
        out = model_oc.get("output_length", defaults_oc.get("output_length", 8192))

        turbo_cfg = {
            "provider": {
                "turbo": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": f"TurboLLM ({model.get('name', model_id)})",
                    "options": {"baseURL": f"http://127.0.0.1:{port}/v1"},
                    "models": {
                        model_id: {
                            "name": model.get("name", model_id),
                            "tool_use": model.get("tool_use", False),
                            "can_reason": model.get("can_reason", False),
                            "limit": {"context": ctx, "output": out},
                        }
                    },
                }
            },
            "model": {"chat": f"turbo/{model_id}"},
        }

        oc_path = Path.home() / ".config" / "opencode" / "opencode.json"
        if oc_path.exists():
            existing = json.loads(oc_path.read_text())
            existing.setdefault("provider", {}).update(turbo_cfg["provider"])
            config = existing
        else:
            config = turbo_cfg

        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        subprocess.run([self._binary], env=env)
