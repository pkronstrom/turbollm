"""Official Kev typed-decision runtime, isolated from Turbo's chat backends."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import click

from turbollm.hf_download import configured_path, quiet_hf, translate_hf_errors


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _adapter_complete(path: Path) -> bool:
    return all(_nonempty(path / name) for name in
               ("head.pt", "adapter_config.json", "adapter_model.safetensors"))


def _head_signature(adapter: Path) -> list[int]:
    stat = (adapter / "head.pt").stat()
    # No st_dev: macOS renumbers volumes at boot, which would hide pulled models.
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _base_complete(path: Path) -> bool:
    if not _nonempty(path / "config.json"):
        return False
    if not any(_nonempty(path / name) for name in ("tokenizer.json", "tokenizer.model", "vocab.json")):
        return False
    indices = list(path.glob("*.safetensors.index.json"))
    if indices:
        for index in indices:
            shards = set(json.loads(index.read_text()).get("weight_map", {}).values())
            if not shards or not all(_nonempty(path / shard) for shard in shards):
                return False
        return True
    return any(_nonempty(weight) for weight in path.glob("*.safetensors"))


class KevProvider:
    name = "kev"
    install_hint = ("git clone https://github.com/jaredpalmer/kev.git ~/.turbollm/runtimes/kev "
                    "&& cd ~/.turbollm/runtimes/kev && uv sync --extra serve --no-dev")

    def _python(self, model: dict | None = None) -> str:
        server = (model or {}).get("server", {})
        return os.path.expanduser(str(server.get("python") or server.get("runtime_python")
                                      or Path.home() / ".turbollm/runtimes/kev/.venv/bin/python"))

    def is_available(self, model: dict | None = None) -> bool:
        executable = self._python(model)
        return shutil.which(executable) is not None

    def _hf_home(self, model: dict) -> Path:
        return Path(model.get("server", {}).get("hf_home") or os.environ.get("HF_HOME")
                    or Path.home() / ".models/.hf-cache").expanduser()

    def _cache(self, model: dict) -> Path:
        return self._hf_home(model) / "hub"

    def _commit(self, model: dict, repo: str, revision: str | None) -> str | None:
        cache = self._cache(model) / ("models--" + repo.replace("/", "--"))
        revision = str(revision or "main")
        relative = Path(revision)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        ref = cache / "refs" / revision
        try:
            commit = ref.read_text().strip() if ref.is_file() else revision
        except (OSError, UnicodeError):
            return None
        if not commit or Path(commit).name != commit or commit in (".", ".."):
            return None
        return commit

    def _snapshot(self, model: dict, repo: str, revision: str | None) -> Path | None:
        commit = self._commit(model, repo, revision)
        if commit is None:
            return None
        cache = self._cache(model) / ("models--" + repo.replace("/", "--"))
        snapshot = cache / "snapshots" / commit
        return snapshot if snapshot.is_dir() else None

    def _local_revision_matches(self, model: dict, local: Path) -> bool:
        if not model.get("revision"):
            return True
        expected = self._commit(model, model["hf_repo"], model["revision"])
        if expected is None:
            return False
        for name in ("adapter_config.json", "head.pt", "adapter_model.safetensors"):
            metadata = local / ".cache/huggingface/download" / (name + ".metadata")
            try:
                with metadata.open() as handle:
                    commit = handle.readline().strip()
            except (OSError, UnicodeError):
                return False
            if commit != expected:
                return False
        return True

    def _adapter_path(self, model: dict) -> Path | None:
        local = configured_path(model, "local_path")
        if local is not None and _adapter_complete(local) and self._local_revision_matches(model, local):
            return local
        cached = self._snapshot(model, model["hf_repo"], model.get("revision"))
        return cached if cached is not None and _adapter_complete(cached) else None

    def get_run(self, model: dict) -> str:
        """Exact runner identity, separate from the API's kev-latest selector."""
        path = self._adapter_path(model)
        if path is not None:
            return str(path)
        repo = model["hf_repo"]
        revision = model.get("revision")
        return f"{repo}@{revision}" if revision else repo

    def build_serve_cmd(self, model: dict, port: int) -> list[str]:
        return ["env", f"HF_HOME={self._hf_home(model)}", f"HF_HUB_CACHE={self._cache(model)}",
                "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "KEV_BACKEND=mlx",
                self._python(model), "-m", "kev.serve", "--run", self.get_run(model),
                "--host", "127.0.0.1", "--port", str(port)]

    def _validate_head_metadata(self, metadata: dict) -> tuple[str, str | None]:
        base = metadata.get("base")
        if not isinstance(base, str) or not base:
            raise ValueError("head.pt has no valid base model")
        revision = metadata.get("base_revision")
        if revision is not None and (not isinstance(revision, str) or not revision):
            raise ValueError("head.pt has an invalid base_revision")
        return base, revision

    def _read_head_metadata(self, adapter: Path, model: dict) -> tuple[str, str | None]:
        # Turbo's CLI must not import torch or unpickle arbitrary checkpoint
        # objects. Match the official loader using its isolated torch runtime.
        script = ("import json,sys,torch; "
                  "m=torch.load(sys.argv[1],map_location='cpu',weights_only=True); "
                  "print(json.dumps({'base':m.get('base'),'base_revision':m.get('base_revision')}))")
        result = subprocess.run([self._python(model), "-c", script, str(adapter / "head.pt")],
                                check=True, capture_output=True, text=True, timeout=30)
        return self._validate_head_metadata(json.loads(result.stdout))

    def _base_spec(self, adapter: Path) -> tuple[str, str | None]:
        metadata = json.loads((adapter / ".turbollm-kev-head.json").read_text())
        if metadata.get("head_signature") != _head_signature(adapter):
            raise ValueError("checkpoint head changed; run turbo pull to verify its base")
        return self._validate_head_metadata(metadata)

    def _cache_head_metadata(self, adapter: Path, base: str, revision: str | None,
                             signature: list[int]) -> None:
        if _head_signature(adapter) != signature:
            raise ValueError("checkpoint head changed while verifying its base")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=adapter, delete=False) as output:
                temporary = Path(output.name)
                json.dump({"head_signature": signature, "base": base, "base_revision": revision}, output)
                output.write("\n")
            os.replace(temporary, adapter / ".turbollm-kev-head.json")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def is_downloaded(self, model: dict) -> bool:
        try:
            adapter = self._adapter_path(model)
            if adapter is None:
                return False
            base, revision = self._base_spec(adapter)
            path = Path(base).expanduser()
            if not path.is_dir():
                path = self._snapshot(model, base, revision)
            return path is not None and _base_complete(path)
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    @translate_hf_errors
    def pull(self, model: dict) -> None:
        from huggingface_hub import snapshot_download

        quiet_hf()
        kwargs = {"repo_id": model["hf_repo"], "cache_dir": str(self._cache(model)),
                  "allow_patterns": ["*.json", "*.safetensors", "*.pt", "*.txt", "*.jinja"],
                  "max_workers": 8}
        if model.get("revision"):
            kwargs["revision"] = model["revision"]
        local = configured_path(model, "local_path")
        if local is not None:
            kwargs["local_dir"] = str(local)
        adapter = Path(snapshot_download(**kwargs))
        try:
            if not _adapter_complete(adapter):
                raise ValueError("adapter weights, head.pt, or adapter_config.json are missing")
            signature = _head_signature(adapter)
            base, revision = self._read_head_metadata(adapter, model)
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            raise click.ClickException(f"Invalid Kev adapter at {adapter}: {exc}") from exc
        path = Path(base).expanduser()
        if not path.is_dir():
            base_kwargs = {"repo_id": base, "cache_dir": str(self._cache(model)),
                           "allow_patterns": ["*.json", "*.safetensors", "*.txt", "*.jinja", "*.model"],
                           "max_workers": 8}
            if revision:
                base_kwargs["revision"] = revision
            path = Path(snapshot_download(**base_kwargs))
        if not _base_complete(path):
            raise click.ClickException(f"Kev base model is incomplete at {path}")
        try:
            self._cache_head_metadata(adapter, base, revision, signature)
        except (OSError, ValueError) as exc:
            raise click.ClickException(f"Cannot cache verified Kev metadata: {exc}") from exc

    def get_model_id(self, model: dict) -> str:
        return "kev-latest"

    def pull_draft(self, model: dict) -> None:
        return
