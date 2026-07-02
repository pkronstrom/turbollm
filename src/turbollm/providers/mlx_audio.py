import shutil
from pathlib import Path

from rich.console import Console

from turbollm.hf_download import quiet_hf, translate_hf_errors
from turbollm.registry import _hf_snapshot_path

console = Console()


class MlxAudioProvider:
    name = "mlx-audio"
    install_hint = (
        "uv tool install mlx-audio --with uvicorn --with fastapi "
        "--with python-multipart --with webrtcvad-wheels "
        "--with librosa --with soundfile"
    )

    def is_available(self) -> bool:
        return shutil.which("mlx_audio.server") is not None

    def build_serve_cmd(self, model: dict, port: int) -> list[str]:
        # mlx_audio.server doesn't bind a model at startup — the client
        # picks the model per-request via the `model=` form field. We still
        # take `model` for symmetry with the Provider protocol; it's used
        # by `is_downloaded` / `pull` to pre-cache the weights so the first
        # transcription call doesn't stall on a download.
        return ["mlx_audio.server", "--host", "127.0.0.1", "--port", str(port)]

    @translate_hf_errors
    def pull(self, model: dict) -> None:
        from huggingface_hub import snapshot_download

        repo = model["hf_repo"]
        quiet_hf()

        console.print(f"  [dim]downloading {repo}…[/dim]")
        local = snapshot_download(repo_id=repo)
        snapshot_path = Path(local)
        total = sum(f.stat().st_size for f in snapshot_path.rglob("*") if f.is_file())
        console.print(f"  [green]Done![/green] {total / 1e9:.2f}GB total")

    def is_downloaded(self, model: dict) -> bool:
        snap = _hf_snapshot_path(model["hf_repo"])
        if not snap:
            return False
        # Either MLX safetensors or HF weights count as "downloaded"
        return any(snap.glob("*.safetensors")) or any(snap.glob("*.bin"))

    def get_model_id(self, model: dict) -> str:
        return model["hf_repo"]

    def pull_draft(self, model: dict) -> None:
        return
