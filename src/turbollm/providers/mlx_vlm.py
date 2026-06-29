import logging
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console

from turbollm.registry import _hf_snapshot_path

console = Console()


class MlxVlmProvider:
    name = "mlx-vlm"
    install_hint = "uv tool install mlx-vlm"

    def is_available(self) -> bool:
        return shutil.which("mlx_vlm.server") is not None or shutil.which("mlx_vlm") is not None

    def build_serve_cmd(self, model: dict, port: int) -> list[str]:
        local = self._model_path(model)
        if local is None:
            console.print(f"[red]Model files not found for {model.get('name', '?')}[/red]")
            raise SystemExit(1)

        srv = model.get("server", {})
        listen_port = port
        upstream_port = self._upstream_port(port) if srv.get("tool_call_shim") else port
        cmd = self._cmd_base() + [
            "--model",
            str(local),
            "--host",
            "127.0.0.1",
            "--port",
            str(upstream_port),
        ]

        draft_path = self._draft_model_path(model)
        if draft_path is not None:
            cmd += ["--draft-model", str(draft_path)]
            cmd += ["--draft-kind", str(srv.get("draft_kind", "mtp"))]
            draft_block_size = srv.get("draft_block_size")
            if draft_block_size:
                cmd += ["--draft-block-size", str(draft_block_size)]
        elif model.get("draft_hf_repo") or model.get("draft_local_path"):
            console.print("  [yellow]Draft model not found — MTP will be disabled.[/yellow]")
            console.print(f"  [yellow]Run: turbo pull {model.get('hf_repo')}[/yellow]")

        flag_map = {
            "max_tokens": "--max-tokens",
            "prefill_step_size": "--prefill-step-size",
            "kv_bits": "--kv-bits",
            "kv_quant_scheme": "--kv-quant-scheme",
            "kv_group_size": "--kv-group-size",
            "max_kv_size": "--max-kv-size",
            "quantized_kv_start": "--quantized-kv-start",
            "vision_cache_size": "--vision-cache-size",
            "top_logprobs_k": "--top-logprobs-k",
        }
        for key, flag in flag_map.items():
            value = srv.get(key)
            if value is not None:
                cmd += [flag, str(value)]

        if srv.get("trust_remote_code"):
            cmd += ["--trust-remote-code"]

        log_level = srv.get("log_level")
        if log_level:
            cmd += ["--log-level", str(log_level)]

        if srv.get("tool_call_shim") == "gemma4_bare":
            return [
                sys.executable,
                "-m",
                "turbollm.mlx_vlm_tool_proxy",
                "--listen-port",
                str(listen_port),
                "--upstream-port",
                str(upstream_port),
                "--",
                *cmd,
            ]

        return cmd

    def pull(self, model: dict) -> None:
        from huggingface_hub import snapshot_download

        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

        repo = model["hf_repo"]
        local_path = self._configured_path(model, "local_path")
        kwargs = {"repo_id": repo}
        if local_path is not None:
            local_path.mkdir(parents=True, exist_ok=True)
            kwargs["local_dir"] = str(local_path)

        snapshot = Path(snapshot_download(**kwargs))
        console.print(f"  [green]done[/green] {repo} -> {snapshot}")

        draft_repo = model.get("draft_hf_repo")
        if draft_repo:
            draft_path = self._configured_path(model, "draft_local_path")
            draft_kwargs = {"repo_id": draft_repo}
            if draft_path is not None:
                draft_path.mkdir(parents=True, exist_ok=True)
                draft_kwargs["local_dir"] = str(draft_path)
            draft_snapshot = Path(snapshot_download(**draft_kwargs))
            console.print(f"  [green]done[/green] {draft_repo} -> {draft_snapshot}")

    def is_downloaded(self, model: dict) -> bool:
        if self._model_path(model) is None:
            return False
        if model.get("draft_hf_repo") or model.get("draft_local_path"):
            return self._draft_model_path(model) is not None
        return True

    def get_model_id(self, model: dict) -> str:
        """mlx-vlm reports the served model by its local filesystem path when
        loaded from a local_path; otherwise the hf_repo (same as other backends)."""
        if model.get("local_path"):
            return str(Path(model["local_path"]).expanduser())
        return model["hf_repo"]

    def pull_draft(self, model: dict) -> None:
        """Draft model (used by --draft-model for MTP speculative decoding)
        is pulled inline by `pull()` for this backend, so no extra work needed
        here. Keep the method to satisfy the Provider Protocol."""
        return

    def _cmd_base(self) -> list[str]:
        if shutil.which("mlx_vlm.server"):
            return ["mlx_vlm.server"]
        if shutil.which("mlx_vlm"):
            return ["mlx_vlm", "server"]
        return ["mlx_vlm.server"]

    def _upstream_port(self, listen_port: int) -> int:
        if listen_port <= 55535:
            return listen_port + 10000
        return listen_port - 10000

    def _model_path(self, model: dict) -> Path | None:
        local = self._configured_path(model, "local_path")
        if local is not None and self._has_safetensors(local):
            return local

        repo = model["hf_repo"]
        snap = _hf_snapshot_path(repo)
        if snap and self._has_safetensors(snap):
            return snap
        return None

    def _draft_model_path(self, model: dict) -> Path | None:
        local = self._configured_path(model, "draft_local_path")
        if local is not None and self._has_safetensors(local):
            return local

        draft_repo = model.get("draft_hf_repo")
        if not draft_repo:
            return None
        snap = _hf_snapshot_path(draft_repo)
        if snap and self._has_safetensors(snap):
            return snap
        return None

    def _configured_path(self, model: dict, key: str) -> Path | None:
        value = model.get(key)
        if not value:
            return None
        return Path(os.path.expanduser(str(value)))

    def _has_safetensors(self, path: Path) -> bool:
        return path.exists() and any(path.rglob("*.safetensors"))
