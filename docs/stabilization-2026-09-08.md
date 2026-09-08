# TurboLLM stabilization — 2026-09-08

## Code and installation

- The wheel now contains the default registry and lean OMP prompt. Runtime
  lookup uses package resources, with the existing source paths for editable
  installations. Existing user registries are preserved.
- Goose is retired from the bundled registry, command examples, and generic
  harness test fixture. There was no Goose Python dependency. An existing user
  registry needs its `harnesses.goose` entry removed separately; this machine's
  registry is a symlink to the repository and therefore already follows the change.
- The existing mlx-vlm fix now consistently advertises the same local/cache
  snapshot path passed to the server. Its installation hint includes Jinja2.
- Lean OMP initializes a missing `config.yml` with `setupVersion: 2`; a new
  regression test checks that existing configuration stays byte-identical.
- The earlier Q8 GGUF and DiffusionGemma profile retirements are retained.
- A workflow regression fixture now waits until its background process installs
  its signal handler before failing the primary. Under concurrent inference load,
  the old fixture could mistake correct early termination for a leaked process.

Verification: 370 Python tests passed. A fresh wheel was installed into a
temporary directory and checked outside the source tree for registry bootstrap,
preservation of custom config, and prompt availability. The reproducible check is:

```sh
uv build --offline
.venv/bin/python tools/check-wheel.py dist/turbollm-0.1.2-py3-none-any.whl
.venv/bin/python -m pytest -q
```

## Q6 runtime evidence

Runtime: oMLX 0.6.4, Pi 0.84.3, OMP 18.0.11. Profile:
`qwen38-27b-oq6e-mtp`, served on isolated port 18999 with the registry's native
MTP configuration. Server logs confirmed the Lightning MTP path was active,
including draft acceptance during the Pi probe.

- Direct API: low, medium, and xhigh each returned exactly `OK`, with separate
  reasoning content. Wall times were 12.08 s (including 8.48 s model load),
  1.25 s, and 1.19 s respectively. These are smoke checks, not a reasoning-quality
  or effort-compliance benchmark.
- Structured tool call: returned `echo` with the exact argument
  `{"value":"TURBO_TOOL_OK"}`.
- Pi using Turbo's generated provider configuration: returned `OK`, with 7,932
  input tokens, 24 output tokens, and 40.89 s wall time.
- Initial full OMP cold prompt: 23,533 tokens; the 120 s run budget expired
  during prefill (16,384 tokens processed). The server reported memory-aware
  prefill throttling. This identifies material startup overhead, not a failed
  model load or malformed provider configuration.

With a longer run budget, both OMP routes completed successfully:

| Route | Input tokens | Output tokens | Wall time | Result |
|---|---:|---:|---:|---|
| Pi | 7,932 | 24 | 40.89 s | `OK` |
| Lean OMP | 8,587 | 19 | 55.86 s | `OK` |
| Full OMP | 23,917 | 139 | 173.81 s | `OK` |

Lean OMP reduced input tokens by 64.1% relative to the completed full OMP run.
It still carries substantial overhead. These single runs use native sampling,
different output lengths, and memory-sensitive prefill; their wall times are
observations, not a controlled speed benchmark. Full OMP discovers host tools
even with a temporary agent directory; lean OMP's explicit discovery restrictions
are part of the measured difference. Hashes of the normal OMP `config.yml` and
`models.yml` matched before and after the probes.

Acceptance: retain lean OMP as an experimental optional route. Its smaller
prompt is demonstrated, but the full interactive coding/LSP acceptance gate is
still open.

Lean OMP also completed a real tool loop: it invoked `read` on a synthetic
fixture and returned the exact unknown marker `TURBO_READ_7c42_OK` in 46.73 s.
This establishes tool execution and continuation, beyond the API-only structured
tool-call probe. The temporary validation server was stopped after the checks.

Harnesses run from temporary working and agent directories. This exercises
Turbo's provider generation and launch arguments without writing normal Pi/OMP
profiles. It does not benchmark the user's complete extension configuration.

## Repository reconciliation

`git fetch origin` confirmed main was 32 commits ahead of origin/main before
this stabilization work. No remote commits were missing locally. `git cherry
main feat/qwen27b-mtp-mlx-vlm` marked all three old branch commits as already
represented by equivalent patches on main. The old branch is retained; no merge
or cherry-pick is required. No extra worktrees remain.

`WATCHDOG.yml` is a local advisor preference, not an application dependency;
it is retained outside the stabilization commits.

The July audit has been labelled historical because subsequent Python, Swift,
and Raycast fixes already addressed many of its findings. Local foundation
phase task files now distinguish implemented work from missing manual evidence.
The old Q8 migration plan is superseded by the retained Q6 profile.

## Remaining acceptance work

- Manual microphone/TCC, region/window capture, and Raycast Stop checks have
  no current recorded result. The foundation changes remain awaiting manual
  acceptance; they are not falsely archived as fully verified. Create
  `tools/turbo-acquirer/SMOKE.md` when performing those checks.
- The full interactive lean OMP LSP definition/rename workflow and a meaningful
  coding workload still need acceptance evidence.
- Old vllm-mlx batching restrictions and the mlx-audio local patch in
  `KNOWN_ISSUES.md` remain historical observations pending targeted rechecks.
- Q6 vision, long-context burn-in, and controlled MTP-on/off comparisons are
  not established by the short text/tool probes above. The retired Q8 validation
  report is not evidence for these Q6 properties.
