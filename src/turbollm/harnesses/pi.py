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

    def launch(self, model_id: str, port: int, model: dict) -> None:
        pi_dir = Path(os.environ.get("PI_CODING_AGENT_DIR", Path.home() / ".pi" / "agent"))
        models_json = pi_dir / "models.json"
        model_name = str(model.get("name", model_id))
        model_repo = str(model.get("hf_repo", ""))
        is_qwen = "qwen" in model_id.lower() or "qwen" in model_name.lower() or "qwen" in model_repo.lower()
        defaults_oc = get_defaults().get("opencode", {})
        model_oc = model.get("opencode", {})
        pi_cfg = model.get("pi", {})
        srv = model.get("server", {})
        context_window = model_oc.get("context_length", srv.get("max_tokens", defaults_oc.get("context_length", 32768)))
        max_tokens = model_oc.get("output_length", defaults_oc.get("output_length", 8192))
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

        # Merge into existing models.json if present
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

        subprocess.run([self._binary, "--model", model_arg])
