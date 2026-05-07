# oMLX Provider + Pi.dev Harness — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add oMLX as a third inference provider and Pi.dev as a new coding agent harness to TurboLLM.

**Architecture:** oMLX is a multi-model server (auto-discovers models from a directory). TurboLLM manages symlinks from `~/.omlx/models/<org>/<model>` → HF cache snapshots. Pi.dev is a generic TOML-driven harness needing only `OPENAI_BASE_URL`.

**Tech Stack:** Python 3.11+, click, rich, huggingface-hub, shutil, pathlib

**Design doc:** `docs/plans/2026-04-21-omlx-pi-dev-integration.md`

---

### Task 1: oMLX Provider — Symlink Helpers + is_available

**Files:**
- Create: `src/turbollm/providers/omlx.py`
- Create: `tests/providers/test_omlx.py`

**Step 1: Write failing tests for symlink helpers and is_available**

```python
# tests/providers/test_omlx.py
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from turbollm.providers.omlx import OmlxProvider

OMLX_MODELS_DIR = Path.home() / ".omlx" / "models"


@pytest.fixture
def provider():
    return OmlxProvider()


@pytest.fixture
def fake_model():
    return {
        "hf_repo": "mlx-community/Qwen3.6-35B-A3B-4bit",
        "name": "Qwen3.6 35B-A3B oMLX 4bit",
        "size_gb": 20,
    }


class TestSymlinkPath:
    def test_returns_correct_path(self, provider, fake_model):
        result = provider._symlink_path(fake_model)
        expected = OMLX_MODELS_DIR / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
        assert result == expected

    def test_handles_nested_org(self, provider):
        model = {"hf_repo": "some-org/some-model-name"}
        result = provider._symlink_path(model)
        assert result == OMLX_MODELS_DIR / "some-org" / "some-model-name"


class TestIsAvailable:
    def test_available_when_binary_found(self, provider):
        with patch("shutil.which", return_value="/opt/homebrew/bin/omlx"):
            assert provider.is_available() is True

    def test_unavailable_when_binary_missing(self, provider):
        with patch("shutil.which", return_value=None):
            assert provider.is_available() is False
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/providers/test_omlx.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'turbollm.providers.omlx'`

**Step 3: Write minimal implementation**

```python
# src/turbollm/providers/omlx.py
import shutil
from pathlib import Path

OMLX_MODELS_DIR = Path.home() / ".omlx" / "models"


class OmlxProvider:
    name = "omlx"
    install_hint = "brew tap jundot/omlx && brew install omlx"

    def is_available(self) -> bool:
        return shutil.which("omlx") is not None

    def _symlink_path(self, model: dict) -> Path:
        """Path where oMLX expects to find this model: ~/.omlx/models/<org>/<model>"""
        org, name = model["hf_repo"].split("/", 1)
        return OMLX_MODELS_DIR / org / name
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/providers/test_omlx.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```
feat(omlx): add OmlxProvider skeleton with symlink path helper
```

---

### Task 2: oMLX Provider — ensure_symlink / remove_symlink

**Files:**
- Modify: `src/turbollm/providers/omlx.py`
- Modify: `tests/providers/test_omlx.py`

**Step 1: Write failing tests**

```python
# Add to tests/providers/test_omlx.py

class TestEnsureSymlink:
    def test_creates_symlink(self, provider, fake_model, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"

        with patch.object(provider, "_symlink_path", return_value=target):
            provider._ensure_symlink(fake_model, snapshot)

        assert target.is_symlink()
        assert target.resolve() == snapshot.resolve()

    def test_replaces_stale_symlink(self, provider, fake_model, tmp_path):
        old_snapshot = tmp_path / "old"
        old_snapshot.mkdir()
        new_snapshot = tmp_path / "new"
        new_snapshot.mkdir()
        (new_snapshot / "model.safetensors").touch()
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
        target.parent.mkdir(parents=True)
        target.symlink_to(old_snapshot)

        with patch.object(provider, "_symlink_path", return_value=target):
            provider._ensure_symlink(fake_model, new_snapshot)

        assert target.resolve() == new_snapshot.resolve()


class TestRemoveSymlink:
    def test_removes_symlink(self, provider, fake_model, tmp_path):
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"
        target.parent.mkdir(parents=True)
        target.symlink_to(tmp_path)

        with patch.object(provider, "_symlink_path", return_value=target):
            provider._remove_symlink(fake_model)

        assert not target.exists()

    def test_noop_if_no_symlink(self, provider, fake_model, tmp_path):
        target = tmp_path / "omlx_models" / "mlx-community" / "Qwen3.6-35B-A3B-4bit"

        with patch.object(provider, "_symlink_path", return_value=target):
            provider._remove_symlink(fake_model)  # should not raise
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/providers/test_omlx.py -v -k "Symlink or Remove"`
Expected: FAIL — `AttributeError: 'OmlxProvider' has no attribute '_ensure_symlink'`

**Step 3: Write implementation**

Add to `src/turbollm/providers/omlx.py`:

```python
def _ensure_symlink(self, model: dict, snapshot_path: Path) -> None:
    """Create or update symlink from oMLX model dir to HF snapshot."""
    link = self._symlink_path(model)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(snapshot_path)

def _remove_symlink(self, model: dict) -> None:
    """Remove oMLX symlink for a model."""
    link = self._symlink_path(model)
    if link.is_symlink() or link.exists():
        link.unlink()
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/providers/test_omlx.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```
feat(omlx): add symlink create/remove helpers
```

---

### Task 3: oMLX Provider — build_serve_cmd

**Files:**
- Modify: `src/turbollm/providers/omlx.py`
- Modify: `tests/providers/test_omlx.py`

**Step 1: Write failing tests**

```python
# Add to tests/providers/test_omlx.py
from unittest.mock import MagicMock

class TestBuildServeCmd:
    def test_minimal_cmd(self, provider):
        model = {"hf_repo": "mlx-community/test-model", "server": {}}
        with patch("turbollm.providers.omlx.get_defaults", return_value={}):
            cmd = provider.build_serve_cmd(model, 8899)

        assert cmd[:2] == ["omlx", "serve"]
        assert "--port" in cmd
        assert "8899" in cmd
        assert "--host" in cmd
        assert "127.0.0.1" in cmd
        assert "--model-dir" in cmd

    def test_ssd_cache_flags(self, provider):
        model = {"hf_repo": "mlx-community/test-model", "server": {}}
        defaults = {
            "omlx": {
                "paged_ssd_cache_dir": "/tmp/cache",
                "paged_ssd_cache_max_size": "50GB",
                "hot_cache_max_size": "10%",
            }
        }
        with patch("turbollm.providers.omlx.get_defaults", return_value=defaults):
            cmd = provider.build_serve_cmd(model, 8899)

        assert "--paged-ssd-cache-dir" in cmd
        idx = cmd.index("--paged-ssd-cache-dir")
        assert cmd[idx + 1] == "/tmp/cache"
        assert "--paged-ssd-cache-max-size" in cmd
        assert "--hot-cache-max-size" in cmd

    def test_per_model_server_overrides(self, provider):
        model = {
            "hf_repo": "mlx-community/test-model",
            "server": {"max_concurrent_requests": 4},
        }
        with patch("turbollm.providers.omlx.get_defaults", return_value={}):
            cmd = provider.build_serve_cmd(model, 8899)

        assert "--max-concurrent-requests" in cmd
        idx = cmd.index("--max-concurrent-requests")
        assert cmd[idx + 1] == "4"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/providers/test_omlx.py::TestBuildServeCmd -v`
Expected: FAIL

**Step 3: Write implementation**

Add to `src/turbollm/providers/omlx.py`:

```python
from turbollm.registry import get_defaults

# ... existing code ...

def build_serve_cmd(self, model: dict, port: int) -> list[str]:
    defaults = get_defaults()
    omlx_defaults = defaults.get("omlx", {})
    srv = model.get("server", {})

    model_dir = srv.get("model_dir") or omlx_defaults.get("model_dir") or str(OMLX_MODELS_DIR)
    model_dir = os.path.expanduser(model_dir)

    cmd = ["omlx", "serve",
           "--model-dir", model_dir,
           "--port", str(port),
           "--host", "127.0.0.1"]

    # SSD KV cache
    cache_dir = srv.get("paged_ssd_cache_dir") or omlx_defaults.get("paged_ssd_cache_dir")
    if cache_dir:
        cmd += ["--paged-ssd-cache-dir", os.path.expanduser(cache_dir)]

    cache_max = srv.get("paged_ssd_cache_max_size") or omlx_defaults.get("paged_ssd_cache_max_size")
    if cache_max:
        cmd += ["--paged-ssd-cache-max-size", str(cache_max)]

    hot_cache = srv.get("hot_cache_max_size") or omlx_defaults.get("hot_cache_max_size")
    if hot_cache:
        cmd += ["--hot-cache-max-size", str(hot_cache)]

    # Concurrency
    max_conc = srv.get("max_concurrent_requests") or omlx_defaults.get("max_concurrent_requests")
    if max_conc:
        cmd += ["--max-concurrent-requests", str(max_conc)]

    return cmd
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/providers/test_omlx.py -v`
Expected: PASS (11 tests)

**Step 5: Commit**

```
feat(omlx): add build_serve_cmd with SSD cache and concurrency flags
```

---

### Task 4: oMLX Provider — pull and is_downloaded

**Files:**
- Modify: `src/turbollm/providers/omlx.py`
- Modify: `tests/providers/test_omlx.py`

**Step 1: Write failing tests**

```python
# Add to tests/providers/test_omlx.py

class TestIsDownloaded:
    def test_true_when_hf_snapshot_and_symlink_exist(self, provider, fake_model, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        link = tmp_path / "link"
        link.symlink_to(snapshot)

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=snapshot), \
             patch.object(provider, "_symlink_path", return_value=link):
            assert provider.is_downloaded(fake_model) is True

    def test_false_when_no_snapshot(self, provider, fake_model, tmp_path):
        link = tmp_path / "link"

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=None), \
             patch.object(provider, "_symlink_path", return_value=link):
            assert provider.is_downloaded(fake_model) is False

    def test_false_when_no_symlink(self, provider, fake_model, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "model.safetensors").touch()
        link = tmp_path / "nonexistent_link"

        with patch("turbollm.providers.omlx._hf_snapshot_path", return_value=snapshot), \
             patch.object(provider, "_symlink_path", return_value=link):
            assert provider.is_downloaded(fake_model) is False
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/providers/test_omlx.py::TestIsDownloaded -v`
Expected: FAIL

**Step 3: Write implementation**

Add to `src/turbollm/providers/omlx.py`:

```python
from turbollm.registry import _hf_snapshot_path, get_defaults

# ... in OmlxProvider class ...

def is_downloaded(self, model: dict) -> bool:
    snap = _hf_snapshot_path(model["hf_repo"])
    if not snap or not any(snap.glob("*.safetensors")):
        return False
    link = self._symlink_path(model)
    return link.is_symlink()

def pull(self, model: dict) -> None:
    from huggingface_hub import hf_hub_download, list_repo_files, snapshot_download
    from rich.progress import (
        BarColumn, DownloadColumn, Progress, SpinnerColumn,
        TextColumn, TimeRemainingColumn, TransferSpeedColumn,
    )

    repo = model["hf_repo"]

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    files = list_repo_files(repo_id=repo)
    model_files = [f for f in files if not f.startswith(".")]
    safetensor_files = [f for f in model_files if f.endswith(".safetensors")]
    other_files = [f for f in model_files if not f.endswith(".safetensors")]

    for f in other_files:
        hf_hub_download(repo_id=repo, filename=f)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.fields[filename]}"),
        BarColumn(bar_width=30), DownloadColumn(),
        TransferSpeedColumn(), TimeRemainingColumn(),
        console=console, transient=True,
    ) as progress:
        for f in safetensor_files:
            fname = f.split("/")[-1]
            task = progress.add_task("dl", filename=fname, total=None, start=True)
            local_file = hf_hub_download(repo_id=repo, filename=f)
            fsize = Path(local_file).stat().st_size
            progress.update(task, completed=fsize, total=fsize)
            progress.remove_task(task)
            console.print(f"  [green]done[/green] {fname} ({fsize / 1e9:.1f}GB)")

    local = snapshot_download(repo_id=repo)
    snapshot_path = Path(local)
    total_size = sum(f.stat().st_size for f in snapshot_path.rglob("*") if f.is_file()) / 1e9
    console.print(f"\n  [green]Done![/green] {total_size:.1f}GB total")

    # Create oMLX symlink
    self._ensure_symlink(model, snapshot_path)
    console.print(f"  [dim]Symlinked → {self._symlink_path(model)}[/dim]")
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/providers/test_omlx.py -v`
Expected: PASS (14 tests)

**Step 5: Commit**

```
feat(omlx): add pull (HF download + symlink) and is_downloaded
```

---

### Task 5: Register oMLX Provider

**Files:**
- Modify: `src/turbollm/providers/__init__.py`

**Step 1: Edit `get_provider` to add omlx**

```python
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
    raise ValueError(f"Unknown backend '{backend}'. Available: vllm-mlx, gguf, omlx")
```

**Step 2: Verify import works**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -c "from turbollm.providers import get_provider; p = get_provider('omlx'); print(p.name)"`
Expected: `omlx`

**Step 3: Commit**

```
feat(omlx): register omlx backend in provider factory
```

---

### Task 6: models.toml — oMLX defaults, model entry, Pi harness

**Files:**
- Modify: `models.toml`

**Step 1: Add oMLX defaults, Pi harness, and oMLX model entry**

After `[defaults.opencode]`:
```toml
[defaults.omlx]
model_dir = "~/.omlx/models"
paged_ssd_cache_dir = "~/.omlx/cache"
paged_ssd_cache_max_size = "100GB"
hot_cache_max_size = "20%"
max_concurrent_requests = 8
```

After `[harnesses.qwen-code]`:
```toml
[harnesses.pi]
binary = "pi"
install = "npm install -g @mariozechner/pi-coding-agent"
cmd = ["pi"]
env = { OPENAI_BASE_URL = "http://127.0.0.1:{port}/v1" }
```

After the GGUF models section:
```toml
# --- oMLX models (served via omlx) ---

[models.qwen36-35b-omlx-4bit]
name = "Qwen3.6 35B-A3B oMLX 4bit"
backend = "omlx"
hf_repo = "mlx-community/Qwen3.6-35B-A3B-4bit"
size_gb = 20
min_memory_gb = 24
tool_use = true
can_reason = true
tags = ["coding", "moe"]
```

**Step 2: Verify TOML parses**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -c "import tomllib; d = tomllib.loads(open('models.toml').read()); print(list(d['models'].keys())); print(list(d['harnesses'].keys()))"`
Expected: model and harness lists including the new entries

**Step 3: Verify `turbo ls -a` shows the new model**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m turbollm.cli ls -a`
Expected: table includes `qwen36-35b-omlx-4bit` with backend `omlx`

**Step 4: Commit**

```
feat: add oMLX defaults, Pi.dev harness, and oMLX model entry to models.toml
```

---

### Task 7: Smoke test full flow

**Step 1: Verify provider integration end-to-end**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m pytest tests/ -v`
Expected: All tests pass

**Step 2: Verify CLI lists everything correctly**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m turbollm.cli ls -a`
Expected: All 3 models shown (vllm-mlx, gguf, omlx)

**Step 3: Verify harness commands are discovered**

Run: `cd /Users/pkronstrom/Projects/own/turbollm && python -m turbollm.cli --help`
Expected: `pi` appears in the command list alongside opencode, hermes, goose, etc.

**Step 4: Final commit**

```
chore: verify oMLX provider + Pi.dev harness integration
```
