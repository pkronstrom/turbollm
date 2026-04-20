import logging
import os
import shutil
from pathlib import Path

from rich.console import Console

from turbollm.registry import _hf_cache_path, _hf_snapshot_path

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

        cmd = [binary, "-m", str(gguf_path), "--port", str(port)]

        flag_map = {
            "ngl": "-ngl",
            "threads": "-t",
            "context": "-c",
            "batch": "-b",
            "ubatch": "-ub",
            "parallel": "--parallel",
            "keep": "--keep",
            "cache_type_k": "--cache-type-k",
            "cache_type_v": "--cache-type-v",
        }
        for key, flag in flag_map.items():
            val = srv.get(key)
            if val is not None:
                cmd += [flag, str(val)]

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

        return cmd

    def pull(self, model: dict) -> None:
        from huggingface_hub import hf_hub_download

        repo = model["hf_repo"]
        hf_file = model.get("hf_file")
        if not hf_file:
            console.print("[red]GGUF models need hf_file in models.toml[/red]")
            raise SystemExit(1)

        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

        console.print(f"  Downloading {hf_file}...")
        local_file = hf_hub_download(repo_id=repo, filename=hf_file)
        fsize = Path(local_file).stat().st_size / 1e9
        console.print(f"\n  [green]Done![/green] {fsize:.1f}GB cached\n")

    def is_downloaded(self, model: dict) -> bool:
        hf_file = model.get("hf_file")
        if not hf_file:
            return False
        snap = _hf_snapshot_path(model["hf_repo"])
        if snap and (snap / hf_file).exists():
            return True
        cache = _hf_cache_path(model["hf_repo"])
        if cache.exists():
            return any(cache.rglob(hf_file))
        return False

    def _find_binary(self) -> str | None:
        for name in ["llama-server", "llama.cpp-server"]:
            result = shutil.which(name)
            if result:
                return result
        return None

    def _gguf_file(self, model: dict) -> Path:
        local = _hf_snapshot_path(model["hf_repo"])
        hf_file = model.get("hf_file")

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
