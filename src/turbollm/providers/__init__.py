from typing import Protocol


class Provider(Protocol):
    name: str
    install_hint: str

    def is_available(self) -> bool: ...
    def build_serve_cmd(self, model: dict, port: int) -> list[str]: ...
    def pull(self, model: dict) -> None: ...
    def is_downloaded(self, model: dict) -> bool: ...


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
    raise ValueError(f"Unknown backend '{backend}'. Available: vllm-mlx, gguf, omlx, mlx-vlm")
