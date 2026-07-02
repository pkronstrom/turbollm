import shutil
from pathlib import Path

from rich.console import Console

from turbollm.hf_download import configured_path, quiet_hf, translate_hf_errors
from turbollm.registry import (
    _hf_cache_path,
    _hf_snapshot_path,
    context_default_tokens,
    effective_kv_quant,
    effective_sampling,
)

console = Console()


class GgufProvider:
    name = "llama-server"
    install_hint = "brew install llama.cpp"

    def is_available(self) -> bool:
        return self._find_binary() is not None

    def build_serve_cmd(self, model: dict, port: int) -> list[str]:
        binary = self._find_binary()
        if not binary:
            console.print(f"[red]{self.name} not found.[/red] Install with: [bold]{self.install_hint}[/bold]")
            raise SystemExit(1)

        gguf_path = self._gguf_file(model)
        srv = model.get("server", {})

        cmd = [binary, "-m", str(gguf_path), "--port", str(port),
               "-a", model["hf_repo"]]

        flag_map = {
            "ngl": "-ngl",
            "threads": "-t",
            "batch": "-b",
            "ubatch": "-ub",
            "parallel": "--parallel",
            "keep": "--keep",
        }
        for key, flag in flag_map.items():
            val = srv.get(key)
            if val is not None:
                cmd += [flag, str(val)]

        # Context. Picker writes server.context at runtime; fall back to
        # context_default for direct (non-picker) invocations.
        ctx = srv.get("context") or context_default_tokens(model)
        cmd += ["-c", str(ctx)]

        # KV cache quantization. Unified kv_quant field maps to llama-server's
        # --cache-type-k/--cache-type-v. Legacy explicit cache_type_k/v in
        # [server] still wins if set (lets you mix-and-match, e.g. q8 K + q4 V).
        kv_quant = effective_kv_quant(model)
        kv_type_map = {"off": "f16", "q8": "q8_0", "q4": "q4_0"}
        cache_k = srv.get("cache_type_k") or kv_type_map.get(kv_quant, "f16")
        cache_v = srv.get("cache_type_v") or kv_type_map.get(kv_quant, "f16")
        if cache_k != "f16":
            cmd += ["--cache-type-k", cache_k]
        if cache_v != "f16":
            cmd += ["--cache-type-v", cache_v]

        # Sampler defaults from the resolved preset. llama-server consumes
        # the full set, so pass everything through.
        sampling = effective_sampling(model)
        sampler_flags = {
            "temperature": "--temp",
            "top_p": "--top-p",
            "top_k": "--top-k",
            "min_p": "--min-p",
            "presence_penalty": "--presence-penalty",
            "repeat_penalty": "--repeat-penalty",
        }
        for key, flag in sampler_flags.items():
            if key in sampling:
                cmd += [flag, str(sampling[key])]

        bool_flags = {
            "flash_attention": "-fa",
            "swa_full": "--swa-full",
            "no_context_shift": "--no-context-shift",
            "jinja": "--jinja",
            "mlock": "--mlock",
        }
        for key, flag in bool_flags.items():
            if srv.get(key):
                cmd.append(flag)

        if srv.get("enable_thinking") is False:
            cmd += ["--chat-template-kwargs", '{"enable_thinking": false}']

        spec_type = srv.get("spec_type")
        if spec_type:
            cmd += ["--spec-type", str(spec_type)]
        spec_draft_n_max = srv.get("spec_draft_n_max")
        if spec_draft_n_max is not None:
            cmd += ["--spec-draft-n-max", str(spec_draft_n_max)]

        # Speculative decoding with draft model
        draft_file = self._draft_gguf_file(model)
        if draft_file:
            cmd += ["--model-draft", str(draft_file)]
        elif model.get("draft_hf_repo") and model.get("draft_hf_file"):
            # Configured but not found on disk (parity with vllm_mlx's
            # specprefill warning) — don't silently serve without it.
            console.print(f"  [yellow]Draft model {model['draft_hf_repo']} not found — speculative decoding disabled.[/yellow]")
            console.print(f"  [yellow]Run: turbo pull {model.get('hf_repo')}[/yellow]")

        return cmd

    @translate_hf_errors
    def pull(self, model: dict) -> None:
        from huggingface_hub import hf_hub_download

        repo = model["hf_repo"]
        hf_file = model.get("hf_file")
        if not hf_file:
            console.print("[red]GGUF models need hf_file in models.toml[/red]")
            raise SystemExit(1)

        quiet_hf()

        console.print(f"  Downloading {hf_file}...")
        local_path = configured_path(model, "local_path")
        kwargs = {"repo_id": repo, "filename": hf_file}
        if local_path is not None:
            local_path.mkdir(parents=True, exist_ok=True)
            kwargs["local_dir"] = str(local_path)
        local_file = hf_hub_download(**kwargs)
        fsize = Path(local_file).stat().st_size / 1e9
        console.print(f"  [green]done[/green] {hf_file} ({fsize:.1f}GB)")

        # Pull draft model if configured
        draft_repo = model.get("draft_hf_repo")
        draft_file = model.get("draft_hf_file")
        if draft_repo and draft_file:
            console.print(f"  Downloading draft model {draft_file}...")
            draft_path = configured_path(model, "draft_local_path")
            draft_kwargs = {"repo_id": draft_repo, "filename": draft_file}
            if draft_path is not None:
                draft_path.mkdir(parents=True, exist_ok=True)
                draft_kwargs["local_dir"] = str(draft_path)
            hf_hub_download(**draft_kwargs)
            console.print(f"  [green]done[/green] {draft_file}")

        console.print()

    def is_downloaded(self, model: dict) -> bool:
        hf_file = model.get("hf_file")
        if not hf_file:
            return False
        local = configured_path(model, "local_path")
        if local is not None:
            return (local / hf_file).exists()
        snap = _hf_snapshot_path(model["hf_repo"])
        if snap and (snap / hf_file).exists():
            return True
        cache = _hf_cache_path(model["hf_repo"])
        if cache.exists():
            return any(cache.rglob(hf_file))
        return False

    def get_model_id(self, model: dict) -> str:
        return model["hf_repo"]

    def pull_draft(self, model: dict) -> None:
        """Draft model for llama.cpp --model-draft speculative decoding.
        Pulled inline by `pull()` for this backend; nothing to do here.
        Keep the method to satisfy the Provider Protocol."""
        return

    def _find_binary(self) -> str | None:
        for name in ["llama-server", "llama.cpp-server"]:
            result = shutil.which(name)
            if result:
                return result
        return None

    def _gguf_file(self, model: dict) -> Path:
        hf_file = model.get("hf_file")
        configured = configured_path(model, "local_path")

        if configured and hf_file:
            p = configured / hf_file
            if p.exists():
                return p

        if configured:
            ggufs = list(configured.glob("*.gguf"))
            if ggufs:
                return ggufs[0]

        local = _hf_snapshot_path(model["hf_repo"])

        if local and hf_file:
            p = local / hf_file
            if p.exists():
                return p

        if local:
            ggufs = list(local.glob("*.gguf"))
            if ggufs:
                return ggufs[0]

        console.print(f"[red]GGUF file not found for {model.get('name', '?')}[/red]")
        raise SystemExit(1)

    def _draft_gguf_file(self, model: dict) -> Path | None:
        draft_repo = model.get("draft_hf_repo")
        draft_file = model.get("draft_hf_file")
        if not draft_repo or not draft_file:
            return None
        # pull() honors draft_local_path — check it first so a draft model
        # downloaded there is actually found at serve time.
        configured = configured_path(model, "draft_local_path")
        if configured:
            p = configured / draft_file
            if p.exists():
                return p
        snap = _hf_snapshot_path(draft_repo)
        if snap and (snap / draft_file).exists():
            return snap / draft_file
        return None
