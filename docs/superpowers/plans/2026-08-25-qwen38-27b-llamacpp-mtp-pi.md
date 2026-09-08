# Qwen3.8 27B llama.cpp MTP + Pi Implementation Plan

**Historical / superseded (2026-09-08):** The Q8 implementation was validated
and subsequently retired on 2026-08-31. Its record is in
`docs/qwen38-pi-validation.md`. The retained route is Q6 oMLX; do not rerun this
plan's downloads or cutover. Generic GGUF artifact and Pi support remain useful
and retained. Current work is recorded in `docs/stabilization-2026-09-08.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Turbo's dense Qwen3.6 27B MLX entry with an official Qwen3.8 27B Q8 GGUF target, native Q8 MTP head, vision projector, and correctly configured Pi integration, then refresh the local runtimes and retire the old artifacts after burn-in.

**Architecture:** Extend the existing GGUF provider rather than adding a backend. Keep target, draft, and projector as explicit managed artifacts under one configured `~/.models` directory; generate Pi's current custom-model schema from the same Turbo sampling/context metadata; isolate safe removal in a small storage module. MTP is a gated accelerator: the Q8 target remains usable if MTP fails validation.

**Tech Stack:** Python 3.12, Click, pytest, TOML, Hugging Face Hub, llama.cpp/Metal, Pi custom providers, Homebrew, uv, npm.

---

## Working Context

- Worktree: `.worktrees/qwen38-llamacpp-mtp-pi`
- Branch: `feature/qwen38-llamacpp-mtp-pi`
- Approved design: `docs/superpowers/specs/2026-08-25-qwen38-27b-llamacpp-mtp-pi-design.md`
- Baseline hygiene commit: `89ae597 test: isolate port stamp cleanup assertion`
- Clean baseline: `332 passed`

## File Structure

- Modify `src/turbollm/providers/gguf.py`: resolve/pull/check target, MTP, and projector artifacts; construct the modern llama.cpp Qwen3.8 command; verify required server flags.
- Modify `tests/providers/test_gguf.py`: provider artifact and command behavior.
- Modify `src/turbollm/harnesses/pi.py`: emit current Pi model schema, official sampling, dynamic chat-template kwargs, image input, and exact reasoning levels.
- Modify `tests/harnesses/test_pi.py`: Pi schema and preservation tests.
- Modify `src/turbollm/registry.py`: central Pi thinking-level vocabulary and per-model validation helper.
- Modify `src/turbollm/cli.py`: validate Pi reasoning overrides and delegate safe model removal.
- Create `src/turbollm/model_storage.py`: pure artifact inventory, shared-location protection, sizing, and deletion.
- Create `tests/test_model_storage.py`: storage inventory and deletion safety.
- Modify `tests/test_cli_server_startup.py`: `turbo rm` integration coverage.
- Modify `models.toml`: Qwen3.8 sampling preset, model/artifact/server/Pi configuration, current Pi installation hint; remove the bundled dense Qwen3.6 entry.
- Modify `tests/test_registry.py`: bundled Qwen3.8 configuration contract.
- Modify `README.md`: recommended Qwen3.8/Pi command path and fallback behavior.
- Create `docs/qwen38-pi-validation.md`: pinned versions and measured smoke/burn-in results.

### Task 1: Manage GGUF Target, MTP, and Projector Artifacts

**Files:**
- Modify: `tests/providers/test_gguf.py`
- Modify: `src/turbollm/providers/gguf.py`

- [ ] **Step 1: Write failing tests for a complete three-artifact model**

Append tests which configure all three files in one temporary directory:

```python
def _qwen38_model(tmp_path):
    return {
        "hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "hf_file": "Qwen3.8-27B-Q8_0.gguf",
        "local_path": str(tmp_path),
        "draft_hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "draft_hf_file": "mtp-Qwen3.8-27B-Q8_0.gguf",
        "draft_local_path": str(tmp_path),
        "mmproj_hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "mmproj_hf_file": "mmproj-Qwen3.8-27B-Q8_0.gguf",
        "mmproj_local_path": str(tmp_path),
        "strict_artifacts": True,
    }


def test_qwen38_is_downloaded_requires_target_draft_and_projector(tmp_path):
    model = _qwen38_model(tmp_path)
    provider = GgufProvider()
    for key in ("hf_file", "draft_hf_file", "mmproj_hf_file"):
        (tmp_path / model[key]).touch()

    assert provider.is_downloaded(model) is True
    (tmp_path / model["mmproj_hf_file"]).unlink()
    assert provider.is_downloaded(model) is False


def test_qwen38_command_uses_exact_projector(tmp_path):
    model = _qwen38_model(tmp_path)
    for key in ("hf_file", "draft_hf_file", "mmproj_hf_file"):
        (tmp_path / model[key]).touch()

    provider = GgufProvider()
    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"):
        cmd = provider.build_serve_cmd(model, 8899)

    assert cmd[cmd.index("--mmproj") + 1] == str(tmp_path / model["mmproj_hf_file"])


def test_strict_qwen38_refuses_missing_projector(tmp_path):
    import click
    import pytest

    model = _qwen38_model(tmp_path)
    (tmp_path / model["hf_file"]).touch()
    (tmp_path / model["draft_hf_file"]).touch()

    provider = GgufProvider()
    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"), \
         pytest.raises(click.ClickException, match="mmproj-Qwen3.8-27B-Q8_0.gguf"):
        provider.build_serve_cmd(model, 8899)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
uv run pytest -q tests/providers/test_gguf.py -k 'qwen38 or strict'
```

Expected: failures because `is_downloaded()` ignores the projector, no `--mmproj` is emitted, and strict artifact enforcement does not exist.

- [ ] **Step 3: Add generic sidecar resolution**

In `GgufProvider`, add one exact-file resolver and use it for draft and projector:

```python
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
    if configured and (configured / filename).exists():
        return configured / filename
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
```

Import `click`, then add the exact missing-artifact behavior:

```python
def _handle_missing_artifact(self, model: dict, *, label: str, file_key: str) -> None:
    filename = model.get(file_key)
    message = f"Configured {label} artifact not found: {filename}"
    if model.get("strict_artifacts"):
        raise click.ClickException(message)
    console.print(f"  [yellow]{message} — capability disabled.[/yellow]")
```

Resolve and append the sidecars in `build_serve_cmd()`:

```python
draft_file = self._draft_gguf_file(model)
if draft_file:
    cmd += ["--spec-draft-model", str(draft_file)]
elif model.get("draft_hf_file"):
    self._handle_missing_artifact(model, label="draft", file_key="draft_hf_file")

mmproj_file = self._mmproj_gguf_file(model)
if mmproj_file:
    cmd += ["--mmproj", str(mmproj_file)]
elif model.get("mmproj_hf_file"):
    self._handle_missing_artifact(model, label="projector", file_key="mmproj_hf_file")
```

- [ ] **Step 4: Pull every configured artifact using exact filenames**

Refactor `pull()` around this helper:

```python
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
        f"  [green]done[/green] {filename} ({resolved.stat().st_size / 1e9:.1f}GB)"
    )
```

Call it for `hf_*`, `draft_hf_*`, and `mmproj_hf_*`. Update `is_downloaded()` so every configured exact artifact must resolve:

```python
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
```

Keep `pull_draft()` as the protocol-compatible no-op because `pull()` is atomic for this backend.

- [ ] **Step 5: Run provider tests and verify GREEN**

Run:

```bash
uv run pytest -q tests/providers/test_gguf.py
```

Expected: all GGUF provider tests pass.

- [ ] **Step 6: Commit artifact lifecycle support**

```bash
git add src/turbollm/providers/gguf.py tests/providers/test_gguf.py
git commit -m "feat(gguf): manage draft and projector artifacts"
```

### Task 2: Build and Validate the Native-MTP llama.cpp Command

**Files:**
- Modify: `tests/providers/test_gguf.py`
- Modify: `src/turbollm/providers/gguf.py`

- [ ] **Step 1: Write the failing Qwen3.8 command test**

```python
def test_qwen38_builds_quality_native_mtp_command(tmp_path):
    model = _qwen38_model(tmp_path)
    for key in ("hf_file", "draft_hf_file", "mmproj_hf_file"):
        (tmp_path / model[key]).touch()
    model["sampling"] = "qwen38-thinking"
    model["kv_quant"] = "off"
    model["context_default"] = 262144
    model["server"] = {
        "ngl": "all",
        "draft_ngl": "all",
        "batch": 512,
        "ubatch": 512,
        "parallel": 1,
        "flash_attention": "on",
        "no_context_shift": True,
        "cache_prompt": True,
        "reasoning_preserve": True,
        "jinja": True,
        "spec_type": "draft-mtp",
        "spec_draft_n_max": 3,
        "draft_cache_type_k": "f16",
        "draft_cache_type_v": "f16",
    }

    provider = GgufProvider()
    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"):
        cmd = provider.build_serve_cmd(model, 8899)

    expected_pairs = {
        "-c": "262144",
        "--parallel": "1",
        "-ngl": "all",
        "--spec-draft-ngl": "all",
        "--cache-type-k": "f16",
        "--cache-type-v": "f16",
        "--spec-draft-type-k": "f16",
        "--spec-draft-type-v": "f16",
        "--spec-type": "draft-mtp",
        "--spec-draft-n-max": "3",
    }
    for flag, value in expected_pairs.items():
        assert cmd[cmd.index(flag) + 1] == value
    for flag in (
        "--cache-prompt",
        "--no-context-shift",
        "--reasoning-preserve",
        "--jinja",
    ):
        assert flag in cmd
    assert cmd[cmd.index("-fa") + 1] == "on"
    assert cmd[cmd.index("--spec-draft-model") + 1].endswith("mtp-Qwen3.8-27B-Q8_0.gguf")
```

Patch `effective_sampling` in this unit test to return the official values so it does not depend on the global registry fixture.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest -q tests/providers/test_gguf.py::test_qwen38_builds_quality_native_mtp_command
```

Expected: failure on modern draft flag names, explicit F16 cache flags, draft offload, prompt caching, and reasoning preservation.

- [ ] **Step 3: Implement modern explicit flags**

Update `build_serve_cmd()` as follows:

```python
cmd += ["--cache-type-k", cache_k, "--cache-type-v", cache_v]

if "flash_attention" in srv:
    value = srv["flash_attention"]
    cmd += ["-fa", value if isinstance(value, str) else ("on" if value else "off")]

if srv.get("cache_prompt"):
    cmd.append("--cache-prompt")
if srv.get("reasoning_preserve"):
    cmd.append("--reasoning-preserve")
if srv.get("draft_ngl") is not None:
    cmd += ["--spec-draft-ngl", str(srv["draft_ngl"])]
if srv.get("draft_cache_type_k"):
    cmd += ["--spec-draft-type-k", str(srv["draft_cache_type_k"])]
if srv.get("draft_cache_type_v"):
    cmd += ["--spec-draft-type-v", str(srv["draft_cache_type_v"])]
```

Use `--spec-draft-model` instead of the legacy `--model-draft`. Preserve compatibility for existing entries by accepting their current configuration keys.

- [ ] **Step 4: Write a failing capability-check test**

```python
def test_required_llama_flags_fail_before_launch(tmp_path):
    import click
    import pytest

    model = _qwen38_model(tmp_path)
    for key in ("hf_file", "draft_hf_file", "mmproj_hf_file"):
        (tmp_path / model[key]).touch()
    model["server"] = {"required_flags": ["--spec-type", "--reasoning-preserve"]}
    completed = type("Result", (), {"stdout": "--spec-type", "stderr": "", "returncode": 0})()

    provider = GgufProvider()
    with patch.object(provider, "_find_binary", return_value="/tmp/llama-server"), \
         patch("subprocess.run", return_value=completed), \
         pytest.raises(click.ClickException, match="--reasoning-preserve"):
        provider.build_serve_cmd(model, 8899)
```

- [ ] **Step 5: Implement capability verification**

Import `click` and `subprocess`, then add:

```python
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
```

Call it immediately after resolving the binary, using `server.required_flags`.

- [ ] **Step 6: Run GGUF and full tests**

```bash
uv run pytest -q tests/providers/test_gguf.py
uv run pytest -q
```

Expected: all tests pass; the full-suite count is at least 332.

- [ ] **Step 7: Commit the native-MTP command**

```bash
git add src/turbollm/providers/gguf.py tests/providers/test_gguf.py
git commit -m "feat(gguf): configure native MTP serving"
```

### Task 3: Emit the Current Pi Schema and Exact Reasoning Controls

**Files:**
- Modify: `src/turbollm/registry.py`
- Modify: `src/turbollm/harnesses/pi.py`
- Modify: `src/turbollm/cli.py`
- Modify: `tests/harnesses/test_pi.py`
- Modify: `tests/test_end_to_end_phase1.py`

- [ ] **Step 1: Write a failing Qwen3.8 Pi schema test**

```python
def test_pi_qwen38_schema_preserves_quality_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    harness = PiHarness({"binary": "pi"})
    model = {
        "name": "Qwen3.8 27B Q8 + MTP",
        "hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "can_reason": True,
        "input": ["text", "image"],
        "context_default": 262144,
        "sampling": "qwen38-thinking",
        "pi": {
            "max_tokens": 32768,
            "thinking": "medium",
            "thinking_levels": ["low", "medium", "xhigh"],
            "compat": {
                "thinkingFormat": "chat-template",
                "chatTemplateKwargs": {
                    "enable_thinking": {"$var": "thinking.enabled"},
                    "preserve_thinking": True,
                    "reasoning_effort": {"$var": "thinking.effort"},
                },
            },
        },
    }
    sampling = {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repeat_penalty": 1.0,
    }

    with patch("turbollm.harnesses.pi.effective_sampling", return_value=sampling), \
         patch("subprocess.run") as run:
        harness.launch(model["hf_repo"], 8899, model)

    entry = json.loads((tmp_path / "models.json").read_text())["providers"]["turbo"]["models"][0]
    assert entry["input"] == ["text", "image"]
    assert entry["contextWindow"] == 262144
    assert entry["maxTokens"] == 32768
    assert entry["samplingParams"] == sampling
    assert entry["thinkingLevelMap"] == {
        "off": None,
        "minimal": None,
        "low": "low",
        "medium": "medium",
        "high": None,
        "xhigh": "xhigh",
        "max": None,
    }
    assert entry["compat"]["thinkingFormat"] == "chat-template"
    assert entry["compat"]["chatTemplateKwargs"]["preserve_thinking"] is True
    run.assert_called_once_with(["pi", "--model", f"turbo/{model['hf_repo']}:medium"])
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/harnesses/test_pi.py::test_pi_qwen38_schema_preserves_quality_settings
```

Expected: missing image input, `samplingParams`, and `thinkingLevelMap`.

- [ ] **Step 3: Add shared thinking-level helpers**

In `registry.py`:

```python
PI_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

def supported_thinking_levels(model: dict) -> tuple[str, ...]:
    configured = model.get("pi", {}).get("thinking_levels")
    if configured is None:
        return PI_THINKING_LEVELS
    return tuple(level for level in PI_THINKING_LEVELS if level in configured)

def pi_thinking_level_map(model: dict) -> dict[str, str | None]:
    supported = set(supported_thinking_levels(model))
    return {level: level if level in supported else None for level in PI_THINKING_LEVELS}
```

- [ ] **Step 4: Generate Pi's current model fields**

Import `effective_sampling` and `pi_thinking_level_map` in `pi.py`. Change the model entry construction to:

```python
model_entry = {
    "id": model_id,
    "name": model_name,
    "reasoning": reasoning,
    "input": list(model.get("input") or ["text"]),
    "contextWindow": context_window,
    "maxTokens": max_tokens,
    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
}
sampling = effective_sampling(model)
if sampling:
    model_entry["samplingParams"] = sampling
if reasoning and model.get("pi", {}).get("thinking_levels") is not None:
    model_entry["thinkingLevelMap"] = pi_thinking_level_map(model)
```

Keep model-level compat merging so the declarative dynamic `chatTemplateKwargs` reaches the file unchanged. Do not set `supportsReasoningEffort` true: reasoning effort travels inside `chat_template_kwargs`.

- [ ] **Step 5: Write a failing unsupported-level CLI test**

Add an end-to-end dispatch test which resolves a model with `pi.thinking_levels = ["low", "medium", "xhigh"]`, calls `turbo pi <model> --thinking high`, and asserts exit code 2 plus:

```text
Qwen3.8 27B supports Pi thinking levels: low, medium, xhigh
```

- [ ] **Step 6: Validate per-model overrides before server startup**

In `_apply_overrides()` inside `_dispatch_harness()`:

```python
if thinking is not None:
    supported = supported_thinking_levels(d)
    if thinking not in supported:
        raise click.UsageError(
            f"{d.get('name', d.get('hf_repo', 'model'))} supports Pi thinking levels: "
            + ", ".join(supported)
        )
    pi_cfg = {**d.get("pi", {}), "thinking": thinking}
    d = {**d, "pi": pi_cfg}
```

Import `PI_THINKING_LEVELS` and use it for `_VALID_THINKING_LEVELS`, adding Pi's current `max` level globally.

- [ ] **Step 7: Run Pi, dispatch, and full tests**

```bash
uv run pytest -q tests/harnesses/test_pi.py tests/test_end_to_end_phase1.py
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Pi schema support**

```bash
git add src/turbollm/registry.py src/turbollm/harnesses/pi.py src/turbollm/cli.py tests/harnesses/test_pi.py tests/test_end_to_end_phase1.py
git commit -m "feat(pi): configure Qwen reasoning and sampling"
```

### Task 4: Make `turbo rm` Sidecar-Aware and Shared-Location Safe

**Files:**
- Create: `src/turbollm/model_storage.py`
- Create: `tests/test_model_storage.py`
- Modify: `src/turbollm/cli.py`
- Modify: `tests/test_cli_server_startup.py`

- [ ] **Step 1: Write failing pure storage-inventory tests**

Create tests for these cases:

```python
from turbollm.model_storage import gather_removal_targets


def test_gather_removal_targets_lists_exact_local_sidecars(tmp_path):
    model = {
        "hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "hf_file": "target.gguf",
        "local_path": str(tmp_path),
        "draft_hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "draft_hf_file": "mtp.gguf",
        "draft_local_path": str(tmp_path),
        "mmproj_hf_repo": "ggml-org/Qwen3.8-27B-GGUF",
        "mmproj_hf_file": "mmproj.gguf",
        "mmproj_local_path": str(tmp_path),
    }
    for filename in ("target.gguf", "mtp.gguf", "mmproj.gguf"):
        (tmp_path / filename).write_bytes(b"x")

    targets = gather_removal_targets(model, other_models=[])
    assert {target.path.name for target in targets} == {"target.gguf", "mtp.gguf", "mmproj.gguf"}
    assert all(not target.is_dir for target in targets)


def test_gather_removal_targets_includes_old_mlx_target_and_draft_caches(tmp_path, monkeypatch):
    from turbollm import registry

    monkeypatch.setattr(registry, "HF_CACHE", tmp_path / "hub")
    monkeypatch.setattr(registry, "LEGACY_DIR", tmp_path / "legacy")
    model = {
        "hf_repo": "unsloth/Qwen3.6-27B-UD-MLX-6bit",
        "draft_hf_repo": "mlx-community/Qwen3.6-27B-MTP-5bit",
    }
    for repo in (model["hf_repo"], model["draft_hf_repo"]):
        path = registry._hf_cache_path(repo)
        path.mkdir(parents=True)
        (path / "weights.bin").write_bytes(b"abc")

    targets = gather_removal_targets(model, other_models=[])
    assert {target.path for target in targets} == {
        registry._hf_cache_path(model["hf_repo"]),
        registry._hf_cache_path(model["draft_hf_repo"]),
    }


def test_gather_removal_targets_protects_repo_used_by_another_model(tmp_path, monkeypatch):
    from turbollm import registry

    monkeypatch.setattr(registry, "HF_CACHE", tmp_path / "hub")
    repo = "org/shared"
    cache = registry._hf_cache_path(repo)
    cache.mkdir(parents=True)
    (cache / "weights.bin").write_bytes(b"abc")
    current = {"hf_repo": repo}
    other = {"hf_repo": repo, "name": "another quant"}

    assert gather_removal_targets(current, other_models=[other]) == []
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/test_model_storage.py
```

Expected: import failure because `model_storage.py` does not exist.

- [ ] **Step 3: Implement pure artifact inventory**

Create `model_storage.py` with:

```python
from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from typing import Iterable

from turbollm.hf_download import configured_path
from turbollm.registry import _hf_cache_path, _legacy_path


@dataclasses.dataclass(frozen=True)
class ModelRemovalTarget:
    path: Path
    size_bytes: int
    is_dir: bool
    label: str


_ARTIFACTS = (
    ("target", "hf_repo", "hf_file", "local_path"),
    ("draft", "draft_hf_repo", "draft_hf_file", "draft_local_path"),
    ("projector", "mmproj_hf_repo", "mmproj_hf_file", "mmproj_local_path"),
)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _artifact_references(model: dict) -> set[tuple[str, str]]:
    refs = set()
    for _label, repo_key, file_key, path_key in _ARTIFACTS:
        repo = model.get(repo_key)
        local = configured_path(model, path_key)
        filename = model.get(file_key)
        if local and filename:
            refs.add(("file", str(local / filename)))
        elif local:
            refs.add(("dir", str(local)))
        elif repo:
            refs.add(("repo", str(repo)))
    return refs


def gather_removal_targets(model: dict, other_models: Iterable[dict]) -> list[ModelRemovalTarget]:
    protected = set().union(*(_artifact_references(other) for other in other_models))
    candidates: list[tuple[Path, bool, str, tuple[str, str]]] = []
    for label, repo_key, file_key, path_key in _ARTIFACTS:
        repo = model.get(repo_key)
        filename = model.get(file_key)
        local = configured_path(model, path_key)
        if local and filename:
            candidates.append((local / filename, False, label, ("file", str(local / filename))))
        elif local:
            candidates.append((local, True, label, ("dir", str(local))))
        elif repo:
            candidates.extend([
                (_hf_cache_path(repo), True, label, ("repo", str(repo))),
                (_legacy_path(repo), True, label, ("repo", str(repo))),
            ])

    result = []
    seen = set()
    for path, is_dir, label, reference in candidates:
        if reference in protected or path in seen or not path.exists():
            continue
        seen.add(path)
        result.append(ModelRemovalTarget(path, _size(path), is_dir, label))
    return result


def delete_removal_targets(targets: Iterable[ModelRemovalTarget]) -> tuple[int, int]:
    deleted = freed = 0
    for target in targets:
        try:
            if target.is_dir:
                shutil.rmtree(target.path)
            else:
                target.path.unlink()
        except FileNotFoundError:
            continue
        deleted += 1
        freed += target.size_bytes
    return deleted, freed
```

- [ ] **Step 4: Integrate inventory into `turbo rm`**

Replace the current hard-coded primary-cache deletion with:

```python
from turbollm.model_storage import delete_removal_targets, gather_removal_targets

registry = load_registry()
models = registry.get("models", {})
current_alias = next((alias for alias, configured in models.items() if configured == m), None)
other_models = [configured for alias, configured in models.items() if alias != current_alias]
targets = gather_removal_targets(m, other_models)
if not targets:
    console.print(f"[yellow]No safely removable artifacts found:[/yellow] {model}")
    return

total = sum(target.size_bytes for target in targets)
console.print(f"\n  [bold]{m['name']}[/bold]")
for target in targets:
    console.print(f"  {target.label:>9}: {target.path}")
if not yes:
    click.confirm(f"Remove {len(targets)} artifact(s) ({total / 1e9:.1f}GB)?", abort=True)
deleted, freed = delete_removal_targets(targets)
console.print(f"[green]Removed[/green] {deleted} artifact(s), {freed / 1e9:.1f}GB")
```

This comparison deliberately treats an unregistered raw repository as sharing any matching registered artifact, which favors refusal over deleting another configured model's data.

- [ ] **Step 5: Add CLI coverage for old target plus draft**

Replace the old primary-only removal test with an assertion that both the old target and draft HF cache directories are displayed and removed, while a cache directory referenced by another model remains.

- [ ] **Step 6: Run removal and full tests**

```bash
uv run pytest -q tests/test_model_storage.py tests/test_cli_server_startup.py -k 'remov or rm_'
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit safe removal**

```bash
git add src/turbollm/model_storage.py src/turbollm/cli.py tests/test_model_storage.py tests/test_cli_server_startup.py
git commit -m "feat: remove complete model artifact sets safely"
```

### Task 5: Replace the Bundled Dense Model Configuration

**Files:**
- Modify: `models.toml`
- Modify: `tests/test_registry.py`
- Modify: `README.md`

- [ ] **Step 1: Write a failing bundled-registry contract test**

```python
def test_bundled_qwen38_q8_mtp_contract(monkeypatch):
    from turbollm import registry

    monkeypatch.setattr(registry, "USER_TOML", Path("/definitely/missing/models.toml"))
    data = registry._load_toml(registry.BUNDLED_TOML)
    model = data["models"]["qwen38-27b-q8-mtp"]

    assert "qwen36-27b-6bit" not in data["models"]
    assert model["backend"] == "gguf"
    assert model["hf_repo"] == "ggml-org/Qwen3.8-27B-GGUF"
    assert model["hf_file"] == "Qwen3.8-27B-Q8_0.gguf"
    assert model["draft_hf_file"] == "mtp-Qwen3.8-27B-Q8_0.gguf"
    assert model["mmproj_hf_file"] == "mmproj-Qwen3.8-27B-Q8_0.gguf"
    assert model["context_default"] == 262144
    assert model["kv_quant"] == "off"
    assert model["pi"]["thinking"] == "medium"
    assert model["pi"]["thinking_levels"] == ["low", "medium", "xhigh"]
    assert data["sampling"]["qwen38-thinking"]["temperature"] == 1.0
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest -q tests/test_registry.py::test_bundled_qwen38_q8_mtp_contract
```

Expected: missing `qwen38-27b-q8-mtp`.

- [ ] **Step 3: Add the official Qwen3.8 thinking preset**

```toml
[sampling.qwen38-thinking]
temperature = 1.0
top_p = 0.95
top_k = 20
min_p = 0.0
presence_penalty = 0.0
repeat_penalty = 1.0
```

- [ ] **Step 4: Replace only the dense 27B bundled entry**

Remove `[models.qwen36-27b-6bit]` and its nested server/Pi tables. Add:

```toml
[models.qwen38-27b-q8-mtp]
name = "Qwen3.8 27B Q8 + native MTP"
backend = "gguf"
hf_repo = "ggml-org/Qwen3.8-27B-GGUF"
hf_file = "Qwen3.8-27B-Q8_0.gguf"
local_path = "~/.models/ggml-org/Qwen3.8-27B-GGUF"
draft_hf_repo = "ggml-org/Qwen3.8-27B-GGUF"
draft_hf_file = "mtp-Qwen3.8-27B-Q8_0.gguf"
draft_local_path = "~/.models/ggml-org/Qwen3.8-27B-GGUF"
mmproj_hf_repo = "ggml-org/Qwen3.8-27B-GGUF"
mmproj_hf_file = "mmproj-Qwen3.8-27B-Q8_0.gguf"
mmproj_local_path = "~/.models/ggml-org/Qwen3.8-27B-GGUF"
strict_artifacts = true
size_gb = 30
min_memory_gb = 48
tool_use = true
can_reason = true
input = ["text", "image"]
tags = ["coding", "dense", "qwen", "vision", "mtp", "q8"]
context_default = 262144
context_max = 262144
sampling = "qwen38-thinking"
kv_quant = "off"

[models.qwen38-27b-q8-mtp.server]
ngl = "all"
draft_ngl = "all"
batch = 512
ubatch = 512
parallel = 1
flash_attention = "on"
no_context_shift = true
cache_prompt = true
reasoning_preserve = true
jinja = true
spec_type = "draft-mtp"
spec_draft_n_max = 3
draft_cache_type_k = "f16"
draft_cache_type_v = "f16"
required_flags = ["--spec-type", "--spec-draft-n-max", "--spec-draft-model", "--reasoning-preserve", "--mmproj"]

[models.qwen38-27b-q8-mtp.pi]
max_tokens = 32768
thinking = "medium"
thinking_levels = ["low", "medium", "xhigh"]

[models.qwen38-27b-q8-mtp.pi.compat]
thinkingFormat = "chat-template"
chatTemplateKwargs = { enable_thinking = { "$var" = "thinking.enabled" }, preserve_thinking = true, reasoning_effort = { "$var" = "thinking.effort" } }
```

Update `[harnesses.pi].install` to `npm install -g @earendil-works/pi-coding-agent`.

- [ ] **Step 5: Document the preferred path**

In `README.md`, add the concise workflow:

```bash
turbo pull qwen38-27b-q8-mtp
turbo pi qwen38-27b-q8-mtp
turbo pi qwen38-27b-q8-mtp --thinking xhigh
```

State that `medium` is default, only `low|medium|xhigh` are supported, Q8 target quality is unchanged if MTP is disabled, and Qwen3.6 is retained until validation passes.

- [ ] **Step 6: Run registry and full tests**

```bash
uv run pytest -q tests/test_registry.py tests/harnesses/test_pi.py tests/providers/test_gguf.py
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit model configuration**

```bash
git add models.toml tests/test_registry.py README.md
git commit -m "feat: add Qwen3.8 Q8 MTP Pi profile"
```

### Task 6: Verify Repository Changes and Install Turbo from the Worktree

**Files:**
- Verify repository files from Tasks 1-5.

- [ ] **Step 1: Run static and unit verification**

```bash
git diff --check main...HEAD
uv run pytest -q
uv run turbo ls --available
```

Expected: no whitespace errors; every test passes; `qwen38-27b-q8-mtp` is listed with backend `gguf` and the old bundled dense alias is absent.

- [ ] **Step 2: Install this worktree build**

Run outside the repository sandbox after approval:

```bash
uv tool install --force .worktrees/qwen38-llamacpp-mtp-pi
turbo --help
turbo ls --available
```

Expected: Turbo reports version 0.1.2 behavior, loads the worktree's bundled registry when explicitly tested, and recognizes the new alias.

- [ ] **Step 3: Synchronize the user's registry without overwriting customizations**

Compare `~/.turbollm/models.toml` with the pre-change bundled file. Create `models.toml.user-merged` inside the worktree by applying only Task 5's preset/model/Pi-hint changes to a copy of the user's file, inspect `diff -u`, then:

```bash
cp ~/.turbollm/models.toml ~/.turbollm/models.toml.pre-qwen38-20260825
cp models.toml.user-merged ~/.turbollm/models.toml
turbo ls --available
```

Expected: custom harness extensions/workflows remain; `qwen38-27b-q8-mtp` appears; the old dense Qwen3.6 entry is absent. Delete the temporary merged file from the worktree after copying so it cannot be committed.

### Task 7: Refresh Stable Servers and Harnesses

**Files:**
- Create and then update from actual command output: `docs/qwen38-pi-validation.md`

- [ ] **Step 1: Record pre-refresh versions**

Run the commands, then create `docs/qwen38-pi-validation.md` containing the date, hardware (`Apple M4 Max, 128 GB`), and a version table populated with the exact outputs (no empty status rows):

```bash
llama-server --version
uv tool list
npm list -g --depth=0
omlx --version
claude --version
opencode --version
codex --version
goose --version
hermes --version
```

Expected baseline includes llama.cpp build 8680 and Pi 0.69.0 under the old package scope.

- [ ] **Step 2: Upgrade stable llama.cpp and verify capabilities**

```bash
brew update
brew upgrade llama.cpp
llama-server --version
llama-server --help
```

Expected: stable v0.2.0 or later, and help contains `--spec-type`, `draft-mtp`, `--spec-draft-n-max`, `--spec-draft-model`, `--reasoning-preserve`, and `--mmproj`. If Homebrew stable lacks one, install the pinned official b10566-or-later macOS arm64 release and record the exact build instead of silently continuing.

- [ ] **Step 3: Move Pi to the current package scope**

```bash
npm install -g @earendil-works/pi-coding-agent@latest
npm uninstall -g @mariozechner/pi-coding-agent
pi --version
```

Expected: Pi 0.84.2 or the newer stable version returned by npm on 2026-08-25; the `pi` binary remains available.

- [ ] **Step 4: Refresh auxiliary Turbo servers**

```bash
uv tool install --force 'mlx-vlm==0.6.14'
uv tool install --force 'vllm-mlx==0.4.1'
brew upgrade omlx
mlx_vlm.server --help
vllm-mlx --version
omlx --version
```

Expected: stable versions only. Run the existing Qwen3.6 35B-A3B vllm-mlx smoke request after upgrading; if 0.4.1 regresses it, restore 0.2.9 and record the pin.

- [ ] **Step 5: Refresh installed harnesses through their owning managers**

```bash
claude install latest
brew upgrade opencode
brew upgrade --cask codex
uv tool install --force git+https://github.com/NousResearch/hermes-agent.git
```

Then rerun the version commands from Step 1. The existing `goose` is the unrelated Python pipeline toolkit, not Block's Goose CLI expected by Turbo; record it as an incompatible auxiliary harness and do not uninstall or overwrite it during this model migration. The force reinstall repairs Hermes's currently broken virtual-environment interpreter path. A remaining auxiliary failure does not block llama.cpp/Pi validation.

- [ ] **Step 6: Commit resolved versions**

Replace the validation record's version cells with exact outputs:

```bash
git add docs/qwen38-pi-validation.md
git commit -m "docs: record refreshed local runtime versions"
```

### Task 8: Download and Verify Official Qwen3.8 Artifacts

**Files:**
- Modify after measurement: `docs/qwen38-pi-validation.md`

- [ ] **Step 1: Pull through Turbo**

Run outside the repository sandbox after approval:

```bash
turbo pull qwen38-27b-q8-mtp
```

Expected exact files under `~/.models/ggml-org/Qwen3.8-27B-GGUF`:

```text
Qwen3.8-27B-Q8_0.gguf
mtp-Qwen3.8-27B-Q8_0.gguf
mmproj-Qwen3.8-27B-Q8_0.gguf
```

- [ ] **Step 2: Verify artifact identity and size**

```bash
ls -lh ~/.models/ggml-org/Qwen3.8-27B-GGUF
shasum -a 256 ~/.models/ggml-org/Qwen3.8-27B-GGUF/*.gguf
turbo ls
```

Expected: target is approximately 26.9 GB, sidecars bring the set to roughly 30 GB, hashes complete without read errors, and Turbo reports the model downloaded.

- [ ] **Step 3: Record the Hugging Face revision**

Record repository revision `0669b98607d47046c7c2b3f801011d54a08cfccf` unless the pull resolves a newer official revision; in that case record the resolved revision and verify that all three filenames still match the configuration.

### Task 9: Validate Q8 Autoregressive Serving Before MTP

**Files:**
- Modify: `docs/qwen38-pi-validation.md`

- [ ] **Step 1: Launch the same profile with MTP disabled in a temporary user override**

Copy the Qwen3.8 entry to a temporary user alias `qwen38-27b-q8-ar` with `spec_type` and draft fields omitted, then run:

```bash
turbo serve qwen38-27b-q8-ar
```

Expected: llama-server binds only to `127.0.0.1:8899`, loads Q8 target plus projector, reports 262,144 context, one slot, F16 K/V, and no MTP context.

- [ ] **Step 2: Smoke text, reasoning, tools, and vision**

From another shell, send OpenAI-compatible requests that verify:

- `chat_template_kwargs.enable_thinking=true`, `preserve_thinking=true`, and `reasoning_effort=medium` produce separated reasoning and final content.
- A function with nested object/array parameters returns schema-valid arguments.
- Two parallel function choices are parsed as two tool calls.
- A small local PNG sent as an image data URL is described correctly.

Every request uses the Task 5 sampling values and `max_tokens=32768`. Record request JSON and concise results in the validation document without copying private reasoning traces.

- [ ] **Step 3: Verify prefix reuse**

Send a 16K-token conversation twice, changing only the last user message. Record llama-server prompt timings; the second request must reuse the common prefix and have materially lower prompt-evaluation work.

- [ ] **Step 4: Run Pi against AR mode**

```bash
turbo pi qwen38-27b-q8-ar --thinking medium
```

Perform one read/search/edit/test loop in a disposable fixture repository, then resume the Pi session and perform a second loop. Confirm `models.json` contains image input, 262,144 context, 32,768 output, official sampling, dynamic chat kwargs, and only `low|medium|xhigh`.

### Task 10: Enable MTP and Run Stability/Performance Burn-In

**Files:**
- Modify: `docs/qwen38-pi-validation.md`

- [ ] **Step 1: Launch the production profile**

```bash
turbo serve qwen38-27b-q8-mtp
```

Expected: target, MTP, and projector all resolve from `~/.models`; native `draft-mtp` starts with depth 3; target and draft are fully offloaded; target/draft KV are F16.

- [ ] **Step 2: Compare identical AR and MTP prompts**

Run the same 512-token generation at short, 16K, and at least 64K prompt lengths with fixed seed and identical sampling. Record TTFT, prompt tokens/s, decode tokens/s, acceptance rate, accepted mean length, and peak memory for both modes.

Pass threshold: MTP improves decode by at least 15%, acceptance is non-degenerate, and responses do not exhibit repetition or cross-request state.

- [ ] **Step 3: Exercise the reported long-to-short state failure**

In one server process, alternate a long tool-enabled prompt and a short prose prompt for 20 cycles. Fail the MTP gate if the short response repeats text/tools from the preceding long request, loops, or differs structurally from the AR control.

- [ ] **Step 4: Run 100 tool-call burn-in requests**

Run 25 each of simple scalar arguments, nested objects, multiline strings, and parallel calls. Count malformed XML/JSON, missing required arguments, retry loops, and timeouts.

Pass threshold: 100/100 requests are parseable by Pi, with no stuck retries. A semantically wrong tool choice is recorded separately from malformed transport/template output.

- [ ] **Step 5: Run real Pi medium and xhigh sessions**

```bash
turbo pi qwen38-27b-q8-mtp --thinking medium
turbo pi qwen38-27b-q8-mtp --thinking xhigh
```

Use the same disposable repository task in both sessions. Verify multi-turn prefix reuse, tool arguments, compaction/session resume, and that xhigh reaches `chat_template_kwargs.reasoning_effort` rather than a top-level field ignored by llama.cpp.

- [ ] **Step 6: Compare the old and new model on a fixed quality set**

Before deleting Qwen3.6, run both models on the same ten prompts: two repository-navigation questions, two multi-file change plans, two nested tool-schema tasks, two 32K retrieval tasks with known answers, and two instruction-conflict tasks. Use each model's documented thinking sampler, medium reasoning for Qwen3.8, and fixed seeds where supported. Record pass/fail per prompt based on known repository paths, schema-valid arguments, retrieved sentinel text, and explicit instruction compliance. Stop the cutover if Qwen3.8 shows a repeatable runtime/template/tool regression; do not fail it merely for different wording.

- [ ] **Step 7: Exercise Pi compaction near the configured limit**

Use a synthetic session with more than 220K input tokens plus a requested 32K output reserve. Confirm Pi compacts or summarizes before llama.cpp's 262,144-token hard limit, then resumes with correct tool state. Because `--no-context-shift` is enabled, any silent server-side truncation or context-overflow error fails this gate.

- [ ] **Step 8: Decide the production MTP state**

- If every stability gate passes, retain `spec_type = "draft-mtp"` and depth 3.
- If quality is correct but speed gain is under 15%, try depth 2 once and record both measurements; retain only the faster stable depth.
- If any state leakage, crash, malformed-call regression, or degenerate acceptance occurs, remove only the MTP server fields from the production entry, retain the Q8 target/projector/Pi settings, and record MTP as disabled with the exact failing llama.cpp build.

- [ ] **Step 9: Commit the completed validation record and any measured depth change**

```bash
git add docs/qwen38-pi-validation.md models.toml
git commit -m "docs: validate Qwen3.8 Q8 Pi serving"
```

### Task 11: Retire the Old Dense Qwen3.6 Artifacts

**Files:**
- Modify: `docs/qwen38-pi-validation.md`

- [ ] **Step 1: Confirm rollback gates are green**

The validation record must show pass results for AR text/reasoning/tools/vision, Pi medium/xhigh, prefix reuse, session resume, and either a passing MTP burn-in or an explicit MTP-disabled decision.

- [ ] **Step 2: Preview the exact old targets**

Use the pre-migration user-registry backup to resolve the removed alias, or temporarily restore only its entry under `qwen36-27b-6bit-retire`, then run `turbo rm` without `--yes` and inspect the confirmation list.

Expected exact cache roots:

```text
~/.cache/huggingface/hub/models--unsloth--Qwen3.6-27B-UD-MLX-6bit
~/.cache/huggingface/hub/models--mlx-community--Qwen3.6-27B-MTP-5bit
```

No broad `~/.models`, Hugging Face hub root, or shared repository directory may appear.

- [ ] **Step 3: Remove after confirmation**

```bash
turbo rm qwen36-27b-6bit-retire --yes
```

Expected: approximately 28.3 GB freed; both exact old cache roots disappear; the new Qwen3.8 directory remains.

- [ ] **Step 4: Remove the temporary retirement alias and verify inventory**

Remove `qwen36-27b-6bit-retire` from the user registry, then:

```bash
turbo ls
turbo ls --available
```

Expected: Qwen3.8 is downloaded and available; the retired dense Qwen3.6 alias is absent; unrelated Qwen3.6 35B and Gemma entries are unchanged.

- [ ] **Step 5: Record deletion and commit final runtime evidence**

Update the validation document with the exact freed size and note that deletion was irreversible except by redownloading:

```bash
git add docs/qwen38-pi-validation.md
git commit -m "docs: complete Qwen3.8 cutover"
```

### Task 12: Final Verification and Branch Handoff

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run repository verification**

```bash
git diff --check main...HEAD
uv run pytest -q
git status --short
```

Expected: no whitespace errors, all tests pass, clean worktree.

- [ ] **Step 2: Run live cutover verification**

```bash
llama-server --version
pi --version
turbo ls
turbo pi qwen38-27b-q8-mtp --thinking medium --prompt "Reply with exactly: qwen38-ready"
```

Expected: pinned passing llama.cpp and Pi versions, downloaded Qwen3.8, and a successful headless Pi response without server/template errors.

- [ ] **Step 3: Review commit history**

```bash
git log --oneline main..HEAD
```

Expected: small commits for baseline isolation, GGUF artifacts, native MTP command, Pi schema, safe removal, model config, version record, validation, and cutover.

- [ ] **Step 4: Use the finishing-development-branch workflow**

Run the required `superpowers:verification-before-completion` and `superpowers:finishing-a-development-branch` skills. Present merge/PR/keep-worktree choices without deleting the worktree automatically.
