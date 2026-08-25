"""OpenCode harness — custom launcher with JSON config generation."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from turbollm.harnesses import register
from turbollm.registry import effective_context, get_defaults


@register("opencode")
class OpenCodeHarness:
    def __init__(self, config: dict):
        self.name = "opencode"
        self._binary = config.get("binary", "opencode")
        self.install_hint = config.get("install", "brew install opencode")

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def headless(self, model_id: str, port: int, model: dict, prompt: str) -> int:
        raise NotImplementedError(f"{self.name} harness does not support headless mode")

    def launch(self, model_id: str, port: int, model: dict) -> None:
        defaults_oc = get_defaults().get("opencode", {})
        model_oc = model.get("opencode", {})
        # Registry's single source of truth for the server's actual context
        # window, not a fixed 32768 that ignores what the picker/model chose.
        ctx = model_oc.get("context_length") or effective_context(model)
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
        existing = None
        if oc_path.exists():
            raw_text = oc_path.read_text()
            try:
                existing = json.loads(raw_text)
            except json.JSONDecodeError as e:
                print(
                    f"[turbo opencode] warning: {oc_path} is corrupt ({e}); "
                    f"falling back to a turbo-only config",
                    file=sys.stderr,
                )

        if existing is not None:
            existing.setdefault("provider", {}).update(turbo_cfg["provider"])
            # The turbo-served model must actually be selected — this config
            # is passed via OPENCODE_CONFIG_CONTENT (not persisted back to
            # opencode.json), so overriding the user's previous default here
            # is safe and doesn't touch their file.
            existing["model"] = turbo_cfg["model"]
            config = existing
        else:
            config = turbo_cfg

        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        subprocess.run([self._binary], env=env)
