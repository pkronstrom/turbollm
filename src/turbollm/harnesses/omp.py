"""Oh My Pi harness — writes OMP's native models.yml provider config."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from turbollm.harnesses import register
from turbollm.registry import effective_context, get_defaults


@register("omp")
class OmpHarness:
    def __init__(self, config: dict):
        self.name = "omp"
        self._binary = config.get("binary", "omp")
        self.install_hint = config.get(
            "install", "brew install can1357/tap/omp"
        )

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def _agent_dir(self) -> Path:
        # OMP 18.x retains Pi's historical relocation variable even though its
        # default home is ~/.omp/agent.
        configured = os.environ.get("PI_CODING_AGENT_DIR")
        return Path(configured) if configured else Path.home() / ".omp" / "agent"

    def _write_provider_config(self, model_id: str, port: int, model: dict) -> str:
        agent_dir = self._agent_dir()
        models_yaml = agent_dir / "models.yml"
        agent_dir.mkdir(parents=True, exist_ok=True)

        if models_yaml.exists():
            raw_text = models_yaml.read_text()
            try:
                existing = yaml.safe_load(raw_text) or {}
                if not isinstance(existing, dict):
                    raise yaml.YAMLError("root must be a mapping")
            except yaml.YAMLError as error:
                backup = models_yaml.with_name(models_yaml.name + ".bak")
                backup.write_text(raw_text)
                print(
                    f"[turbo omp] warning: {models_yaml} is corrupt ({error}); "
                    f"backed up to {backup} and starting fresh",
                    file=sys.stderr,
                )
                existing = {}
        else:
            existing = {}

        # Pi and OMP can share general limits/defaults, but their compatibility
        # schemas are different.  In particular, Pi's ``chat-template`` value
        # is not a valid OMP thinkingFormat.
        pi_cfg = model.get("pi", {})
        omp_specific = model.get("omp", {})
        omp_cfg = {**pi_cfg, **omp_specific}
        defaults_pi = get_defaults().get("pi", {})
        context_window = omp_cfg.get("context_window") or effective_context(model)
        max_tokens = omp_cfg.get(
            "max_tokens", defaults_pi.get("output_length", 8192)
        )
        reasoning = omp_cfg.get("reasoning", model.get("can_reason", False))

        entry = {
            "id": model_id,
            "name": str(model.get("name", model_id)),
            "reasoning": reasoning,
            "input": list(model.get("input") or ["text"]),
            "cost": {
                "input": 0,
                "output": 0,
                "cacheRead": 0,
                "cacheWrite": 0,
            },
            "contextWindow": int(context_window),
            "maxTokens": int(max_tokens),
        }

        thinking_levels = omp_cfg.get("thinking_levels")
        if reasoning and thinking_levels:
            entry["thinking"] = {
                "mode": "effort",
                "efforts": list(thinking_levels),
            }

        is_qwen = "qwen" in " ".join(
            [model_id, str(model.get("name", "")), str(model.get("hf_repo", ""))]
        ).lower()
        if is_qwen and reasoning:
            entry["compat"] = {
                "supportsDeveloperRole": False,
                "thinkingFormat": "qwen-chat-template",
                "qwenTemplateReasoningEffort": True,
                "reasoningContentField": "reasoning_content",
            }
        if omp_specific.get("compat"):
            entry.setdefault("compat", {}).update(omp_specific["compat"])

        existing.setdefault("providers", {})["turbo"] = {
            "baseUrl": f"http://127.0.0.1:{port}/v1",
            "api": "openai-completions",
            "auth": "none",
            "models": [entry],
        }
        models_yaml.write_text(
            yaml.safe_dump(existing, sort_keys=False, allow_unicode=True)
        )

        model_arg = f"turbo/{model_id}"
        if omp_cfg.get("thinking"):
            model_arg = f"{model_arg}:{omp_cfg['thinking']}"
        return model_arg

    def launch(self, model_id: str, port: int, model: dict) -> None:
        model_arg = self._write_provider_config(model_id, port, model)
        subprocess.run([self._binary, "--model", model_arg])

    def headless(self, model_id: str, port: int, model: dict, prompt: str) -> int:
        model_arg = self._write_provider_config(model_id, port, model)
        result = subprocess.run([
            self._binary,
            "-p",
            "--model",
            model_arg,
            prompt,
        ])
        return result.returncode
