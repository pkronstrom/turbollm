# Backend Toolchain Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade every installed turbollm model-serving backend and prove its existing provider contract still works, adapting source only for an observed compatibility break.

**Architecture:** The installed backends are independently managed UV tools or Homebrew formulae; turbollm keeps only thin command-construction providers. Update packages first, then validate discovery, command interfaces, and the focused CLI/provider tests. No source change is expected unless a concrete upgrade failure identifies a changed executable, argument, endpoint, or response contract.

**Tech Stack:** UV tools, Homebrew, Python 3.11+, Click, pytest, MLX Audio/FastAPI.

---

## File structure

- No source files are scheduled for modification before a compatibility failure is observed.
- `src/turbollm/providers/mlx_audio.py` owns the `mlx_audio.server` command contract.
- `src/turbollm/providers/omlx.py`, `src/turbollm/providers/mlx_vlm.py`, `src/turbollm/providers/vllm_mlx.py`, and `src/turbollm/providers/gguf.py` own their respective server commands.
- `src/turbollm/cli.py` owns backend discovery and `turbo transcribe` request/response normalization.
- `tests/test_transcribe.py`, `tests/test_transcribe_split.py`, `tests/test_cli_server_startup.py`, and `tests/providers/test_{omlx,mlx_vlm,vllm_mlx}.py` cover the focused current contracts.

### Task 1: Upgrade UV-managed backends

**Files:**
- Modify: UV tool environments for `mlx-audio`, `mlx-vlm`, `vllm-mlx`
- Test: command versions in Task 3

- [ ] **Step 1: Upgrade only the installed UV backends**

Run:
```bash
uv tool upgrade mlx-audio mlx-vlm vllm-mlx
```

Expected: UV resolves and installs each existing tool requirement successfully. Do not use `--all`; it would upgrade unrelated tools.

- [ ] **Step 2: Record installed tool versions**

Run:
```bash
uv tool list
```

Expected: one entry each for `mlx-audio`, `mlx-vlm`, and `vllm-mlx`, with their upgraded version and executable list.

- [ ] **Step 3: Commit source changes only when any exist**

No repository source is expected to change in this task. Do not create an empty commit.

### Task 2: Upgrade Homebrew-managed backends

**Files:**
- Modify: Homebrew installations for `omlx` and `llama.cpp`
- Test: command versions in Task 3

- [ ] **Step 1: Refresh Homebrew metadata and upgrade only backend formulae**

Run:
```bash
brew update && brew upgrade omlx llama.cpp
```

Expected: both requested formulae are upgraded or reported already current. Do not run `brew upgrade` without formula names.

- [ ] **Step 2: Record Homebrew backend versions**

Run:
```bash
brew list --versions omlx llama.cpp
```

Expected: one installed version for each requested formula.

- [ ] **Step 3: Commit source changes only when any exist**

No repository source is expected to change in this task. Do not create an empty commit.

### Task 3: Verify executable and turbollm discovery contracts

**Files:**
- Test: `src/turbollm/providers/__init__.py:26-42`
- Test: `src/turbollm/providers/mlx_audio.py:12-29`
- Test: `src/turbollm/cli.py:58-72`

- [ ] **Step 1: Verify turbollm recognizes all installed backend executables**

Run:
```bash
uv run turbo ls -a
```

Expected: `mlx-audio`, `mlx-vlm`, `vllm-mlx`, `omlx`, and `gguf` are reported installed/available; no provider reports its install hint.

- [ ] **Step 2: Verify the supported command surfaces without loading models**

Run:
```bash
mlx_audio.server --help && mlx_vlm.server --help && vllm-mlx --help && omlx serve --help && llama-server --help
```

Expected: every command exits zero and prints help. In particular, `mlx_audio.server --help` documents `--host` and `--port`, which `MlxAudioProvider.build_serve_cmd()` passes.

- [ ] **Step 3: Start the MLX Audio server with turbollm's provider arguments**

Start the process through the supervised process manager:
```text
mlx_audio.server --host 127.0.0.1 --port 18900
```

Expected: port `18900` accepts connections. Then request `http://127.0.0.1:18900/openapi.json` and expect HTTP 200 before stopping the server. This validates startup without selecting or downloading a model.

- [ ] **Step 4: Run focused compatibility tests**

Run:
```bash
pytest tests/test_transcribe.py tests/test_transcribe_split.py tests/test_cli_server_startup.py tests/providers/test_omlx.py tests/providers/test_mlx_vlm.py tests/providers/test_vllm_mlx.py -q
```

Expected: all collected tests pass.

### Task 4: Adapt only an observed compatibility break

**Files:**
- Modify only the provider or CLI file implicated by a failed Task 3 contract.
- Test: the focused existing test file for that provider or CLI behavior.

- [ ] **Step 1: Classify the failure before editing**

Capture the exact failed command, stderr, and the old turbollm assumption it contradicts. Classify it as one of: executable discovery, server arguments, startup/readiness endpoint, transcription request shape, or transcription response shape.

- [ ] **Step 2: Add a focused regression test for the observed contract**

Add the test beside the existing contract owner. For an MLX Audio request or response change, extend `tests/test_transcribe.py`. For an MLX Audio executable-argument change, create `tests/providers/test_mlx_audio.py`. For an omlx, mlx-vlm, or vllm-mlx command change, extend its existing `tests/providers/test_omlx.py`, `tests/providers/test_mlx_vlm.py`, or `tests/providers/test_vllm_mlx.py` file. The assertion must encode the observed upstream interface rather than mock an unrelated implementation detail.

- [ ] **Step 3: Make the smallest provider or CLI change**

Update only the command construction or request/response handling needed to satisfy the new upstream interface. Migrate every callsite through the single provider implementation; do not add compatibility aliases or fallback commands unless the upstream package explicitly requires a multi-version bridge.

- [ ] **Step 4: Verify the repaired behavior**

Run the new focused test, repeat the failed Task 3 command or smoke path, then run the Task 3 focused test command. Expected: the concrete failing contract now passes without breaking the pre-existing tests.

- [ ] **Step 5: Commit the source and regression test**

Run:
```bash
git add src/turbollm tests
git commit -m "fix: support updated backend interface"
```

Do not commit unrelated files.
