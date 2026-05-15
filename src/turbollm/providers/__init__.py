from typing import Protocol


class Provider(Protocol):
    name: str
    install_hint: str

    def is_available(self) -> bool: ...
    def build_serve_cmd(self, model: dict, port: int) -> list[str]: ...
    def pull(self, model: dict) -> None: ...
    def is_downloaded(self, model: dict) -> bool: ...
    def get_model_id(self, model: dict) -> str:
        """The id the server will report (matches --served-model-name / -a).

        Defaults to the model's hf_repo. Override when the backend reports
        something else (e.g. mlx-vlm uses the local filesystem path)."""
        ...
    def pull_draft(self, model: dict) -> None:
        """Pull the configured draft model, if any. Default: no-op.

        Providers that support speculative decoding (vllm-mlx, mlx-vlm, gguf)
        override this to download the draft sidecar."""
        ...


def get_provider(backend: str) -> Provider:
    if backend == "gguf":
        from turbollm.providers.gguf import GgufProvider
        return GgufProvider()
    if backend in ("vllm-mlx", "mlx"):
        from turbollm.providers.vllm_mlx import VllmMlxProvider
        return VllmMlxProvider()
    if backend == "omlx":
        from turbollm.providers.omlx import OmlxProvider
        return OmlxProvider()
    if backend == "mlx-vlm":
        from turbollm.providers.mlx_vlm import MlxVlmProvider
        return MlxVlmProvider()
    if backend == "mlx-audio":
        from turbollm.providers.mlx_audio import MlxAudioProvider
        return MlxAudioProvider()
    raise ValueError(f"Unknown backend '{backend}'. Available: vllm-mlx, gguf, omlx, mlx-vlm, mlx-audio")
