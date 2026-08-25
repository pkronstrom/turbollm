import shutil
import subprocess
from pathlib import Path

import click
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
        self._require_server_flags(binary, list(srv.get("required_flags") or []))

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
        cmd += ["--cache-type-k", cache_k, "--cache-type-v", cache_v]

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
            "swa_full": "--swa-full",
            "no_context_shift": "--no-context-shift",
            "cache_prompt": "--cache-prompt",
            "reasoning_preserve": "--reasoning-preserve",
            "jinja": "--jinja",
            "mlock": "--mlock",
        }
        for key, flag in bool_flags.items():
            if srv.get(key):
                cmd.append(flag)

        if "flash_attention" in srv:
            flash_attention = srv["flash_attention"]
            if isinstance(flash_attention, str):
                flash_value = flash_attention
            else:
                flash_value = "on" if flash_attention else "off"
            cmd += ["-fa", flash_value]

        if srv.get("enable_thinking") is False:
            cmd += ["--chat-template-kwargs", '{"enable_thinking": false}']

        spec_type = srv.get("spec_type")
        if spec_type:
            cmd += ["--spec-type", str(spec_type)]
        spec_draft_n_max = srv.get("spec_draft_n_max")
        if spec_draft_n_max is not None:
            cmd += ["--spec-draft-n-max", str(spec_draft_n_max)]
        if srv.get("draft_ngl") is not None:
            cmd += ["--spec-draft-ngl", str(srv["draft_ngl"])]
        if srv.get("draft_cache_type_k"):
            cmd += ["--spec-draft-type-k", str(srv["draft_cache_type_k"])]
        if srv.get("draft_cache_type_v"):
            cmd += ["--spec-draft-type-v", str(srv["draft_cache_type_v"])]

        # Speculative decoding with draft model
        draft_file = self._draft_gguf_file(model)
        if draft_file:
            cmd += ["--spec-draft-model", str(draft_file)]
        elif model.get("draft_hf_file"):
            self._handle_missing_artifact(
                model,
                label="draft",
                repo_key="draft_hf_repo",
                file_key="draft_hf_file",
            )

        mmproj_file = self._mmproj_gguf_file(model)
        if mmproj_file:
            cmd += ["--mmproj", str(mmproj_file)]
        elif model.get("mmproj_hf_file"):
            self._handle_missing_artifact(
                model,
                label="projector",
                repo_key="mmproj_hf_repo",
                file_key="mmproj_hf_file",
            )

        return cmd

    @translate_hf_errors
    def pull(self, model: dict) -> None:
        if not model.get("hf_file"):
            console.print("[red]GGUF models need hf_file in models.toml[/red]")
            raise SystemExit(1)

        quiet_hf()

        self._download_artifact(
            model,
            repo_key="hf_repo",
            file_key="hf_file",
            path_key="local_path",
            label="target",
        )
        self._download_artifact(
            model,
            repo_key="draft_hf_repo",
            file_key="draft_hf_file",
            path_key="draft_local_path",
            label="draft",
        )
        self._download_artifact(
            model,
            repo_key="mmproj_hf_repo",
            file_key="mmproj_hf_file",
            path_key="mmproj_local_path",
            label="projector",
        )

        console.print()

    def is_downloaded(self, model: dict) -> bool:
        hf_file = model.get("hf_file")
        if not hf_file:
            return False
        local = configured_path(model, "local_path")
        if local is not None:
            target_exists = (local / hf_file).exists()
        else:
            snap = _hf_snapshot_path(model["hf_repo"])
            target_exists = bool(snap and (snap / hf_file).exists())
            if not target_exists:
                cache = _hf_cache_path(model["hf_repo"])
                target_exists = cache.exists() and any(cache.rglob(hf_file))
        if not target_exists:
            return False
        if model.get("draft_hf_file") and self._draft_gguf_file(model) is None:
            return False
        if model.get("mmproj_hf_file") and self._mmproj_gguf_file(model) is None:
            return False
        return True

    def get_model_id(self, model: dict) -> str:
        return model["hf_repo"]

    def pull_draft(self, model: dict) -> None:
        """Draft model for llama.cpp --model-draft speculative decoding.
        Pulled inline by `pull()` for this backend; nothing to do here.
        Keep the method to satisfy the Provider Protocol."""
        return

    def _download_artifact(
        self,
        model: dict,
        *,
        repo_key: str,
        file_key: str,
        path_key: str,
        label: str,
    ) -> None:
        from huggingface_hub import hf_hub_download

        repo = model.get(repo_key)
        filename = model.get(file_key)
        if not repo or not filename:
            return
        destination = configured_path(model, path_key)
        kwargs = {"repo_id": repo, "filename": filename}
        if destination is not None:
            destination.mkdir(parents=True, exist_ok=True)
            kwargs["local_dir"] = str(destination)
        console.print(f"  Downloading {label} {filename}...")
        resolved = Path(hf_hub_download(**kwargs))
        console.print(
            f"  [green]done[/green] {filename} "
            f"({resolved.stat().st_size / 1e9:.1f}GB)"
        )

    def _handle_missing_artifact(
        self,
        model: dict,
        *,
        label: str,
        repo_key: str,
        file_key: str,
    ) -> None:
        repo = model.get(repo_key, "?")
        filename = model.get(file_key, "?")
        message = f"Configured {label} artifact not found: {repo}/{filename}"
        if model.get("strict_artifacts"):
            raise click.ClickException(message)
        console.print(f"  [yellow]{message} — capability disabled.[/yellow]")
        console.print(f"  [yellow]Run: turbo pull {model.get('hf_repo')}[/yellow]")

    def _find_binary(self) -> str | None:
        for name in ["llama-server", "llama.cpp-server"]:
            result = shutil.which(name)
            if result:
                return result
        return None

    def _require_server_flags(self, binary: str, required: list[str]) -> None:
        if not required:
            return
        result = subprocess.run(
            [binary, "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        help_text = result.stdout + result.stderr
        missing = [flag for flag in required if flag not in help_text]
        if missing:
            raise click.ClickException(
                "llama-server is too old for this model; missing flags: "
                + ", ".join(missing)
            )

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

    def _sidecar_file(
        self,
        model: dict,
        *,
        repo_key: str,
        file_key: str,
        path_key: str,
    ) -> Path | None:
        repo = model.get(repo_key)
        filename = model.get(file_key)
        if not repo or not filename:
            return None
        configured = configured_path(model, path_key)
        if configured is not None:
            p = configured / filename
            if p.exists():
                return p
        snap = _hf_snapshot_path(repo)
        if snap and (snap / filename).exists():
            return snap / filename
        return None

    def _draft_gguf_file(self, model: dict) -> Path | None:
        return self._sidecar_file(
            model,
            repo_key="draft_hf_repo",
            file_key="draft_hf_file",
            path_key="draft_local_path",
        )

    def _mmproj_gguf_file(self, model: dict) -> Path | None:
        return self._sidecar_file(
            model,
            repo_key="mmproj_hf_repo",
            file_key="mmproj_hf_file",
            path_key="mmproj_local_path",
        )
