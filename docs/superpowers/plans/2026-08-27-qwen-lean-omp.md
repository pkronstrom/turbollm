# Isolated Lean OMP Profile for Qwen3.8 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `turbo omp-lean` as an opt-in Qwen3.8 OMP launcher with isolated state, a small direct tool surface plus LSP, and only the vault skills advertised.

**Architecture:** Add a narrowly-scoped custom harness that reuses `OmpHarness`'s generated Turbo-provider entry but launches OMP with a separate `PI_CODING_AGENT_DIR` and explicit CLI flags. Package the concise Qwen policy as a prompt asset; the normal OMP harness and `~/.omp/agent` state must never be read or changed by the lean launch.

**Tech Stack:** Python 3.12+, Click, PyYAML, pytest, existing OMP 18.x CLI, Turbo’s OpenAI-compatible llama.cpp provider.

---

## File Structure

- `src/turbollm/harnesses/omp.py` — preserve the ordinary OMP harness; extract only the shared subprocess/environment construction that the lean harness requires.
- `src/turbollm/harnesses/omp_lean.py` — new custom `omp-lean` harness; creates and uses its own OMP state directory, writes the Turbo provider entry there, and supplies the lean flags.
- `src/turbollm/harnesses/__init__.py` — imports the new module so `@register("omp-lean")` runs.
- `integrations/omp/qwen-lean-system-prompt.md` — concise local-Qwen operating instructions; no duplicated OMP policy manual.
- `models.toml` — declares `[harnesses.omp-lean]` so Turbo dynamically exposes `turbo omp-lean`.
- `tests/harnesses/test_omp.py` — regression coverage for the refactored ordinary harness launch shape.
- `tests/harnesses/test_omp_lean.py` — isolated-state, command-shape, provider-entry, and headless behavior tests.
- `tests/test_harness_backend_filter.py` — dynamic harness-registration regression only if the existing CLI tests cannot cover `omp-lean` through the registry.
- `README.md` — documents the separate command, its narrow capability set, and the fact that it is experimental pending prompt-token measurement.

## Task 1: Make OMP launch state injectable without changing normal behavior

**Files:**
- Modify: `src/turbollm/harnesses/omp.py:16-132`
- Modify: `tests/harnesses/test_omp.py:30-92`

- [ ] **Step 1: Add failing ordinary-launch environment regression tests**

Add a test that constructs `OmpHarness({"binary": "omp"})`, patches `subprocess.run`, calls `launch`, and asserts that the existing ordinary path still calls exactly:

```python
run.assert_called_once_with(
    ["omp", "--model", f"turbo/{model['hf_repo']}:medium"]
)
```

Add a parallel headless assertion that verifies `-p`, `--model`, and the positional prompt remain in the existing order and that no `env=` argument is passed for the normal harness.

- [ ] **Step 2: Run the focused regression test and confirm its initial failure**

Run:

```bash
pytest tests/harnesses/test_omp.py -q
```

Expected: the new assertion fails because the shared launch helper does not exist yet, or because the initial test intentionally names the future helper’s output.

- [ ] **Step 3: Extract the smallest shared command runner**

Keep `_write_provider_config()` unchanged. Add a private method that preserves the current `subprocess.run(argv)` behavior when no environment override is supplied and uses `subprocess.run(argv, env=env)` only when an override exists:

```python
def _run(self, argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    if env is None:
        return subprocess.run(argv)
    return subprocess.run(argv, env=env)
```

Route both `launch()` and `headless()` through it. `headless()` still returns `result.returncode`.

- [ ] **Step 4: Run the focused regression test**

Run:

```bash
pytest tests/harnesses/test_omp.py -q
```

Expected: PASS. The exact normal launch call remains unchanged; no global OMP environment is mutated.

- [ ] **Step 5: Commit the refactor**

```bash
git add src/turbollm/harnesses/omp.py tests/harnesses/test_omp.py
git commit -m "refactor: share OMP launch runner"
```

## Task 2: Add the Qwen-specific prompt asset and isolated lean harness

**Files:**
- Create: `integrations/omp/qwen-lean-system-prompt.md`
- Create: `src/turbollm/harnesses/omp_lean.py`
- Modify: `src/turbollm/harnesses/__init__.py:95-100`
- Test: `tests/harnesses/test_omp_lean.py`

- [ ] **Step 1: Write failing isolated-harness tests**

Create `tests/harnesses/test_omp_lean.py`. Use `tmp_path` and monkeypatch the lean harness’s home/state resolver so no real profile path is touched. Test these invariants:

```python
assert (lean_dir / "models.yml").exists()
assert not (normal_dir / "models.yml").exists()
assert env["PI_CODING_AGENT_DIR"] == str(lean_dir)
assert "--no-extensions" in argv
assert "--no-rules" in argv
assert "--tools=read,bash,edit,write,grep,glob,lsp,ask,todo" in argv
assert "--skills=vault-mcp,vault-skills" in argv
assert argv[argv.index("--system-prompt") + 1] == str(prompt_path)
```

Also assert that the generated `models.yml` retains the existing Turbo Qwen compatibility entry: `thinkingFormat == "qwen-chat-template"` and `qwenTemplateReasoningEffort is True`.

- [ ] **Step 2: Run the focused lean tests and confirm failure**

Run:

```bash
pytest tests/harnesses/test_omp_lean.py -q
```

Expected: FAIL during collection because `turbollm.harnesses.omp_lean` does not exist.

- [ ] **Step 3: Create the concise Qwen prompt asset**

Create `integrations/omp/qwen-lean-system-prompt.md` with only the persistent rules needed by the selected tool loop:

```markdown
You are a local coding assistant working in the current repository.

Inspect relevant code before changing it. Use the provided tools rather than guessing. Keep edits focused on the requested behavior. Use LSP for symbol-aware navigation and rename when it is available. Run the smallest relevant verification after a change. State the changed files and observed verification result.

Use a skill only when its name is listed in the current skill catalogue.
```

Do not copy the OMP default engineering policy, generic skill list, MCP documentation, or hosted-model instructions into this file.

- [ ] **Step 4: Implement `OmpLeanHarness`**

Create `OmpLeanHarness` as a subclass of `OmpHarness` registered under `"omp-lean"`. It must:

1. Resolve a fixed lean-only state directory below `~/.omp/agent-qwen-lean` unless a test-only constructor/config override supplies `agent_dir`.
2. Write the Turbo provider entry into that directory by overriding `_agent_dir()`; never read `PI_CODING_AGENT_DIR` from the parent process for the lean path.
3. Build a child environment from `os.environ.copy()` and set only `PI_CODING_AGENT_DIR` to the lean directory.
4. Launch OMP with the generated model selector plus these exact flags:

```python
LEAN_FLAGS = [
    "--no-extensions",
    "--no-rules",
    "--tools=read,bash,edit,write,grep,glob,lsp,ask,todo",
    "--skills=vault-mcp,vault-skills",
    "--system-prompt",
    str(LEAN_PROMPT_PATH),
]
```

5. Add `-p` immediately before `--model` in `headless()`, preserving the ordinary harness’s positional prompt convention.
6. Keep the normal `OmpHarness` default state path and commands unchanged.

Use `Path(__file__).resolve().parents[3] / "integrations" / "omp" / "qwen-lean-system-prompt.md"` only if it resolves to the repository root in tests and installed package layout. If packaging already has a project-root helper, use that helper instead; do not duplicate path-discovery logic.

- [ ] **Step 5: Register the harness**

Add the import alongside the existing custom harness imports:

```python
from turbollm.harnesses import omp_lean as _omp_lean  # noqa: F401, E402
```

- [ ] **Step 6: Run focused harness tests**

Run:

```bash
pytest tests/harnesses/test_omp.py tests/harnesses/test_omp_lean.py -q
```

Expected: PASS. Tests prove that the normal and lean paths write distinct `models.yml` files and produce distinct child-process environments.

- [ ] **Step 7: Commit the lean harness**

```bash
git add src/turbollm/harnesses/omp_lean.py src/turbollm/harnesses/__init__.py integrations/omp/qwen-lean-system-prompt.md tests/harnesses/test_omp_lean.py
git commit -m "feat: add isolated lean OMP harness"
```

## Task 3: Expose `turbo omp-lean` through the registry and document its boundary

**Files:**
- Modify: `models.toml:132-136`
- Modify: `README.md:106-138`
- Test: `tests/test_harness_backend_filter.py`

- [ ] **Step 1: Write failing registry/CLI tests**

Add a test that supplies a registry containing `"omp-lean": {"binary": "omp"}` and asserts `TurboGroup.get_command(ctx, "omp-lean")` returns a command named `"omp-lean"`. Add an assertion that its backend compatibility remains the normal chat-backend default and rejects `mlx-audio`.

- [ ] **Step 2: Run the focused CLI test and confirm failure**

Run:

```bash
pytest tests/test_harness_backend_filter.py -q
```

Expected: FAIL because the configured registry does not yet declare `omp-lean`.

- [ ] **Step 3: Declare the harness**

Add adjacent to `[harnesses.omp]`:

```toml
[harnesses.omp-lean]
binary = "omp"
install = "brew install can1357/tap/omp"
```

Do not add model-specific settings or another endpoint declaration. `OmpLeanHarness` owns profile isolation; `OmpHarness` remains the one source of Turbo provider metadata.

- [ ] **Step 4: Document explicit opt-in use**

In the Qwen3.8 setup section, add:

```markdown
### Lean local OMP profile

`turbo omp-lean qwen38-27b-q8-mtp` starts a separate OMP profile for local Qwen coding. It exposes file tools, bash, LSP, ask, todo, and the `vault-mcp` / `vault-skills` catalogue only. It never changes the normal `turbo omp` profile.

The lean profile is an experiment: use its prompt-token measurement to decide whether OMP’s local-provider residual is acceptable. If it is not, use the normal OMP route or a future direct client; do not alter the normal profile to compensate.
```

- [ ] **Step 5: Run registry and existing harness tests**

Run:

```bash
pytest tests/test_harness_backend_filter.py tests/harnesses/test_omp.py tests/harnesses/test_omp_lean.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit registry and documentation**

```bash
git add models.toml README.md tests/test_harness_backend_filter.py
git commit -m "feat: expose lean OMP profile"
```

## Task 4: Perform end-to-end prompt and tool-loop validation

**Files:**
- No source changes required unless this task exposes a concrete defect; add a focused regression test alongside the defect fix.

- [ ] **Step 1: Verify normal-profile isolation before launch**

Capture the normal configuration checksum:

```bash
shasum -a 256 ~/.omp/agent/config.yml ~/.omp/agent/models.yml
```

Run the lean shell once and exit immediately:

```bash
turbo omp-lean qwen38-27b-q8-mtp
```

Repeat the checksum. Expected: byte-identical output for both normal-profile files.

- [ ] **Step 2: Verify the lean model route and token accounting**

With the Qwen3.8 Turbo server active, run the same non-interactive fixed probe through each path, saving sessions in separate temporary directories:

```bash
omp --model turbo/ggml-org/Qwen3.8-27B-GGUF --session-dir /tmp/omp-normal-probe -p 'Reply exactly OK.'
PI_CODING_AGENT_DIR="$HOME/.omp/agent-qwen-lean" omp --model turbo/ggml-org/Qwen3.8-27B-GGUF --session-dir /tmp/omp-lean-probe --no-extensions --no-rules --tools=read,bash,edit,write,grep,glob,lsp,ask,todo --skills=vault-mcp,vault-skills --system-prompt integrations/omp/qwen-lean-system-prompt.md -p 'Reply exactly OK.'
```

Read each resulting JSONL and record `usage.input`, `contextSnapshot.promptTokens`, `contextSnapshot.nonMessageTokens`, `ttft`, and prompt-processing rate. Expected: the lean run omits disabled capability material. If `nonMessageTokens` remains large, record it as an OMP local-provider residual rather than claiming a minimal prompt.

- [ ] **Step 3: Smoke-test the interactive tool loop**

In `turbo omp-lean qwen38-27b-q8-mtp`, ask the model to: list Python files, find the OMP harness class, explain one method, make a reversible comment-only edit in a temporary repository, and run the smallest relevant command. Then exercise LSP definition and rename on a temporary typed Python or TypeScript fixture.

Expected: all selected tools work; browser, task, debugger, web search, dynamic MCP/device tools, and generic skills are unavailable; the two vault skills are named in the catalogue.

- [ ] **Step 4: Record the acceptance decision**

Add the measured before/after token and latency figures to the implementation PR/commit message. If the lean route does not materially reduce the normal route, stop after recording the result and open a separate design for payload capture plus an OMP/Turbo adapter patch or standalone client. Do not broaden this feature into that fallback implementation.

- [ ] **Step 5: Commit any defect-only regression fix**

If a defect was found and fixed during the smoke test:

```bash
git add <changed source files> <focused test files>
git commit -m "fix: correct lean OMP profile behavior"
```

If no defect was found, no additional commit is required.

## Plan Self-Review

- **Spec coverage:** Tasks 1–3 implement the isolated command, narrow tools plus LSP, vault-only skills, Qwen prompt, normal-profile preservation, and documentation. Task 4 covers the direct/normal/lean measurement gate, model compatibility, tool loop, and fallback boundary.
- **Placeholder scan:** No task uses TBD/TODO or unspecified test work. The only conditional path is defect remediation, where the exact changed file legitimately depends on a discovered, concrete defect.
- **Type consistency:** `OmpLeanHarness`, `omp-lean`, `PI_CODING_AGENT_DIR`, `qwen-lean-system-prompt.md`, and the exact selected tool/skill flag lists are used consistently across all tasks.
