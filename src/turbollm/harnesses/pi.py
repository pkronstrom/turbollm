"""Pi coding agent harness — creates models.json provider pointing at turbo server."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from turbollm.harnesses import register
from turbollm.registry import get_defaults


@register("pi")
class PiHarness:
    def __init__(self, config: dict):
        self.name = "pi"
        self._binary = config.get("binary", "pi")
        self.install_hint = config.get("install", "npm install -g @mariozechner/pi-coding-agent")

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def _write_provider_config(self, model_id: str, port: int, model: dict) -> str:
        """Write/refresh ~/.pi/agent/models.json with a turbo provider for this model.

        Returns the model arg to pass to `pi --model` (e.g. ``turbo/<id>`` plus optional ``:thinking``).
        """
        pi_dir = Path(os.environ.get("PI_CODING_AGENT_DIR", Path.home() / ".pi" / "agent"))
        models_json = pi_dir / "models.json"
        model_name = str(model.get("name", model_id))
        model_repo = str(model.get("hf_repo", ""))
        is_qwen = "qwen" in model_id.lower() or "qwen" in model_name.lower() or "qwen" in model_repo.lower()
        defaults_oc = get_defaults().get("opencode", {})
        model_oc = model.get("opencode", {})
        pi_cfg = model.get("pi", {})
        srv = model.get("server", {})
        # Picker writes pi.context_window at runtime if invoked; otherwise
        # derive from the model's unified context_default (with legacy
        # [opencode].context_length and [server].max_tokens as fallbacks).
        context_window = pi_cfg.get(
            "context_window",
            model.get(
                "context_default",
                model_oc.get(
                    "context_length",
                    srv.get("max_tokens", defaults_oc.get("context_length", 32768)),
                ),
            ),
        )
        max_tokens = pi_cfg.get(
            "max_tokens",
            model_oc.get("output_length", defaults_oc.get("output_length", 8192)),
        )
        reasoning = pi_cfg.get("reasoning", model.get("can_reason", False))

        model_entry = {
            "id": model_id,
            "name": model_name,
            "reasoning": reasoning,
            "input": ["text"],
            "contextWindow": context_window,
            "maxTokens": max_tokens,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        }
        if is_qwen and model.get("can_reason", False):
            # Local Qwen 3.6 servers expect thinking toggles via chat_template_kwargs.
            model_entry["compat"] = {"thinkingFormat": "qwen-chat-template"}
        if pi_cfg.get("thinking_format"):
            model_entry.setdefault("compat", {})["thinkingFormat"] = pi_cfg["thinking_format"]
        if pi_cfg.get("compat"):
            model_entry.setdefault("compat", {}).update(pi_cfg["compat"])

        turbo_provider = {
            "baseUrl": f"http://127.0.0.1:{port}/v1",
            "api": "openai-completions",
            "apiKey": "sk-local",
            "compat": {
                "supportsDeveloperRole": False,
                "supportsReasoningEffort": False,
            },
            "models": [model_entry],
        }

        if models_json.exists():
            existing = json.loads(models_json.read_text())
        else:
            pi_dir.mkdir(parents=True, exist_ok=True)
            existing = {}

        existing.setdefault("providers", {})["turbo"] = turbo_provider
        models_json.write_text(json.dumps(existing, indent=2) + "\n")

        model_arg = f"turbo/{model_id}"
        if pi_cfg.get("thinking"):
            model_arg = f"{model_arg}:{pi_cfg['thinking']}"
        return model_arg

    def launch(self, model_id: str, port: int, model: dict) -> None:
        model_arg = self._write_provider_config(model_id, port, model)
        subprocess.run([self._binary, "--model", model_arg])

    def headless(self, model_id: str, port: int, model: dict, prompt: str) -> int:
        model_arg = self._write_provider_config(model_id, port, model)
        # pi treats positional args as messages; it does not honor the GNU `--`
        # separator, so we don't pass one. A prompt starting with `-` will be
        # misparsed as a pi flag — uncommon enough to accept as a known limit.
        result = subprocess.run([
            self._binary,
            "-p",
            "--model", model_arg,
            "--no-context-files",
            prompt,
        ])
        return result.returncode
