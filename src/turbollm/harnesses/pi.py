"""Pi coding agent harness — creates models.json provider pointing at turbo server."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from turbollm.harnesses import register
from turbollm.registry import (
    effective_context,
    effective_sampling,
    get_defaults,
    pi_thinking_level_map,
)


@register("pi")
class PiHarness:
    def __init__(self, config: dict):
        self.name = "pi"
        self._binary = config.get("binary", "pi")
        self.install_hint = config.get("install", "npm install -g @earendil-works/pi-coding-agent")
        # Extra `-e <path>` extensions loaded only when pi is launched via turbo
        # (i.e. not active in global interactive pi sessions). Useful for
        # pi-autocompact and similar guards that we want on for delegated runs
        # but not for normal interactive use.
        self._extensions: list[str] = list(config.get("extensions") or [])

    def _extension_args(self) -> list[str]:
        """Expand configured extension paths into pi CLI flags.

        Each entry becomes a `-e <expanded-path>` pair. Missing files are
        skipped with a console hint so a stale path doesn't break every run.
        """
        out: list[str] = []
        for raw in self._extensions:
            p = Path(os.path.expanduser(str(raw)))
            if not p.exists():
                print(f"[turbo pi] warning: extension not found, skipping: {p}")
                continue
            out += ["-e", str(p)]
        return out

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
        defaults_pi = get_defaults().get("pi", {})
        pi_cfg = model.get("pi", {})
        # Picker writes pi.context_window at runtime if invoked; otherwise
        # derive from registry.effective_context() — the single source of
        # truth for what the server will actually serve (server.max_tokens
        # / server.context first, then context_default). Previously this
        # fell back through [opencode].context_length / [defaults.opencode]
        # — cross-harness borrowing that could disagree with the server.
        context_window = pi_cfg.get("context_window") or effective_context(model)
        max_tokens = pi_cfg.get("max_tokens", defaults_pi.get("output_length", 8192))
        reasoning = pi_cfg.get("reasoning", model.get("can_reason", False))

        model_entry = {
            "id": model_id,
            "name": model_name,
            "reasoning": reasoning,
            "input": list(model.get("input") or ["text"]),
            "contextWindow": context_window,
            "maxTokens": max_tokens,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        }
        sampling = effective_sampling(model)
        if sampling:
            model_entry["samplingParams"] = sampling
        if reasoning and pi_cfg.get("thinking_levels") is not None:
            model_entry["thinkingLevelMap"] = pi_thinking_level_map(model)
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
            raw_text = models_json.read_text()
            try:
                existing = json.loads(raw_text)
            except json.JSONDecodeError as e:
                backup = models_json.with_name(models_json.name + ".bak")
                backup.write_text(raw_text)
                print(
                    f"[turbo pi] warning: {models_json} is corrupt ({e}); "
                    f"backed up to {backup} and starting fresh",
                    file=sys.stderr,
                )
                existing = {}
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
        subprocess.run([self._binary, "--model", model_arg, *self._extension_args()])

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
            *self._extension_args(),
            prompt,
        ])
        return result.returncode
