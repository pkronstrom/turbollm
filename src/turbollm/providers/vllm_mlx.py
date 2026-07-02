import json
import shutil
from pathlib import Path

from rich.console import Console

from turbollm.hf_download import configured_path, download_repo_with_progress, translate_hf_errors
from turbollm.registry import (
    _hf_cache_path,
    _hf_snapshot_path,
    _legacy_path,
    context_default_tokens,
    effective_kv_quant,
    effective_sampling,
)

console = Console()


class VllmMlxProvider:
    name = "vllm-mlx"
    install_hint = "uv tool install git+https://github.com/waybarrios/vllm-mlx.git"

    def is_available(self) -> bool:
        return shutil.which("vllm-mlx") is not None

    def build_serve_cmd(self, model: dict, port: int) -> list[str]:
        local = self._model_path(model)
        if local is None:
            console.print(f"[red]Model files not found for {model.get('name', '?')}[/red]")
            raise SystemExit(1)
        srv = model.get("server", {})

        cmd = ["vllm-mlx", "serve", str(local), "--port", str(port),
               "--served-model-name", model["hf_repo"]]

        # Performance: continuous batching + paged KV cache
        if srv.get("continuous_batching"):
            cmd += ["--continuous-batching"]
        if srv.get("paged_cache"):
            cmd += ["--use-paged-cache"]

        # Memory: allocate more RAM for KV cache (default 20% is conservative)
        cache_pct = srv.get("cache_memory_percent")
        if cache_pct:
            cmd += ["--cache-memory-percent", str(cache_pct)]

        # Token limits. Picker writes server.max_tokens at runtime; fall back
        # to context_default for direct (non-picker) invocations.
        max_tokens = srv.get("max_tokens") or context_default_tokens(model)
        cmd += ["--max-tokens", str(max_tokens)]
        max_req = srv.get("max_request_tokens") or max_tokens
        cmd += ["--max-request-tokens", str(max_req)]

        # KV cache quantization. New unified `kv_quant` field; legacy
        # `kv_cache_quantization` flag still honored if explicitly set.
        kv_quant = effective_kv_quant(model)
        if kv_quant in ("q8", "q4") or srv.get("kv_cache_quantization"):
            cmd += ["--kv-cache-quantization"]
            bits = 8 if kv_quant == "q8" else 4 if kv_quant == "q4" else srv.get("kv_cache_quantization_bits")
            if bits:
                cmd += ["--kv-cache-quantization-bits", str(bits)]

        # Chunked prefill for responsiveness during long prompts
        chunked = srv.get("chunked_prefill_tokens")
        if chunked:
            cmd += ["--chunked-prefill-tokens", str(chunked)]

        # Sampler defaults from the resolved preset. vllm-mlx today only
        # consumes --default-temperature and --default-top-p — pass them
        # through; other resolved fields stay as metadata for clients.
        sampling = effective_sampling(model)
        if "temperature" in sampling:
            cmd += ["--default-temperature", str(sampling["temperature"])]
        if "top_p" in sampling:
            cmd += ["--default-top-p", str(sampling["top_p"])]
        default_chat_template_kwargs = srv.get("default_chat_template_kwargs")
        if default_chat_template_kwargs is not None:
            cmd += [
                "--default-chat-template-kwargs",
                json.dumps(default_chat_template_kwargs, separators=(",", ":")),
            ]

        # Timeout for long agentic tasks
        timeout = srv.get("timeout", 600)
        cmd += ["--timeout", str(timeout)]

        # Enable tool calling and reasoning for agentic use
        if model.get("tool_use", False):
            tool_parser = model.get("tool_call_parser", "hermes")
            cmd += ["--enable-auto-tool-choice", "--tool-call-parser", tool_parser]
        reasoning_parser = model.get("reasoning_parser")
        if reasoning_parser:
            cmd += ["--reasoning-parser", reasoning_parser]

        # SpecPrefill: draft model speeds up prefill (time to first token)
        draft_repo = model.get("draft_hf_repo")
        if draft_repo:
            draft_path = self._draft_model_path(model)
            if draft_path:
                cmd += ["--specprefill", "--specprefill-draft-model", str(draft_path)]
            else:
                console.print(f"  [yellow]Draft model {draft_repo} not found — skipping specprefill.[/yellow]")
                console.print(f"  [yellow]Run: turbo pull <model> to download it.[/yellow]")

        return cmd

    @translate_hf_errors
    def pull(self, model: dict) -> None:
        from huggingface_hub import snapshot_download

        repo = model["hf_repo"]
        local_path = configured_path(model, "local_path")
        download_repo_with_progress(repo, local_path)

        # Pull draft model if configured
        draft_repo = model.get("draft_hf_repo")
        if draft_repo:
            console.print(f"\n  Pulling draft model: [dim]{draft_repo}[/dim]")
            snapshot_download(repo_id=draft_repo)
            console.print(f"  [green]Done![/green] Draft model cached\n")

    def is_downloaded(self, model: dict) -> bool:
        p = self._model_path(model)
        return p is not None

    def get_model_id(self, model: dict) -> str:
        return model["hf_repo"]

    @translate_hf_errors
    def pull_draft(self, model: dict) -> None:
        """Download the draft model (used by --specprefill speculative decoding)."""
        draft_repo = model.get("draft_hf_repo")
        if not draft_repo:
            return
        if self._draft_model_path(model) is not None:
            return
        from huggingface_hub import snapshot_download
        console.print(f"\n  Pulling draft model: [dim]{draft_repo}[/dim]")
        snapshot_download(repo_id=draft_repo)
        console.print(f"  [green]Done![/green] Draft model cached")

    def _model_path(self, model: dict) -> Path | None:
        local = configured_path(model, "local_path")
        if local is not None and any(local.glob("*.safetensors")):
            return local

        repo = model["hf_repo"]
        snap = _hf_snapshot_path(repo)
        if snap and any(snap.glob("*.safetensors")):
            return snap
        legacy = _legacy_path(repo)
        if legacy.exists() and any(legacy.glob("*.safetensors")):
            return legacy
        return None

    def _draft_model_path(self, model: dict) -> Path | None:
        draft_repo = model.get("draft_hf_repo")
        if not draft_repo:
            return None
        snap = _hf_snapshot_path(draft_repo)
        if snap and any(snap.glob("*.safetensors")):
            return snap
        return None
