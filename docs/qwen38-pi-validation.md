# Qwen3.8 27B Q8 + MTP / Pi historical validation

Date: 2026-08-25  
Machine: Apple M4 Max, 128 GB unified memory  
Profile: `qwen38-27b-q8-mtp`

## Pre-refresh inventory

| Component | Observed version or state |
|---|---|
| TurboLLM | 0.1.1 before this migration; local 0.1.2 installed after repository verification |
| llama.cpp / llama-server | build 8680 (`15f786e65`), AppleClang 17.0.0, Darwin arm64 |
| Pi | `@earendil-works/pi-coding-agent@0.83.0` (already on the current package scope) |
| mlx-vlm | 0.6.3 |
| vllm-mlx | 0.2.9 |
| oMLX | Homebrew 0.3.8 installation broken: missing `libexec/bin/python3.11` interpreter |
| Claude Code | 2.1.236 |
| OpenCode | 1.18.15 |
| Codex CLI | 0.149.1 |
| Hermes Agent | uv reports 0.10.0; launcher broken because `~/.hermes/hermes-agent/venv/bin/python3` is missing |
| `goose` | Python package 0.3.0; this is not Block's Goose CLI, and is intentionally left untouched |

Repository verification before runtime changes: 344 tests passed in 14.90 seconds; `git diff --check` was clean. The live `~/.turbollm/models.toml` is a symlink to the repository registry, so no separate user registry or custom entries required merging.

## Post-refresh inventory

| Component | Verified version or state |
|---|---|
| TurboLLM | 0.1.2, installed from the verified local checkout |
| llama.cpp / llama-server | 0.2.0, build 10566 (`bb4caa754`) |
| Pi | `@earendil-works/pi-coding-agent@0.84.3` |
| mlx-vlm | 0.6.14; server help initializes with host Metal access and exposes MTP draft options |
| vllm-mlx | 0.4.1 |
| oMLX | Remains broken at Homebrew 0.3.8. Homebrew requires persistent trust for the third-party `jundot/omlx/omlx` formula; that trust expansion was not performed. This does not affect the llama.cpp/Pi path. |
| Claude Code | PATH-preferred Homebrew cask 2.1.243; native self-installer also placed 2.1.245 at `~/.local/bin/claude` |
| OpenCode | 1.18.20 |
| Codex CLI | 0.149.1 (already current in Homebrew) |
| Oh My Pi | 18.0.4 from `can1357/tap/omp`; native Turbo provider configuration validates and appears in `omp models` |
| Hermes Agent | 0.20.5 (2026.8.19), upstream `4c1f53be`; repaired with the official installer, preserving `.env` and `config.yaml` |
| `goose` | Python package 0.3.0; intentionally untouched because it is not Block's Goose CLI |

llama-server help contains `--spec-type` with `draft-mtp`, `--spec-draft-n-max`, `--spec-draft-model`, `--reasoning-preserve`, and `--mmproj`. The retained Qwen3.6 35B-A3B profile also loaded successfully under vllm-mlx 0.4.1 at a 4,096-token smoke context, returned HTTP 200, and decoded the short probe at 17.0 tok/s. The initial 32-token probe ended inside its reasoning preamble, so this was treated as a server/load regression check rather than an instruction-quality result.

Hermes upstream no longer supports wheel or `uv tool install` distribution. Turbo's bundled and fallback install hints were updated to the official installer URL and covered by a registry regression test.

## Artifact identity

All three artifacts came from `ggml-org/Qwen3.8-27B-GGUF` at revision
`0669b98607d47046c7c2b3f801011d54a08cfccf` and are stored under
`~/.models/ggml-org/Qwen3.8-27B-GGUF`.

| Artifact | Size | SHA-256 |
|---|---:|---|
| `Qwen3.8-27B-Q8_0.gguf` | 28.6 GB | `f5c702d8820d36fb55985bb238fc83ee3a313e920f4b752a437c3a6a9e14e4c8` |
| `mtp-Qwen3.8-27B-Q8_0.gguf` | 3.16 GB | `cbf60a0c48b431bb61f1d49b8948dc88ac29c398d6dbdbbb2e6e89ef77eacc9a` |
| `mmproj-Qwen3.8-27B-Q8_0.gguf` | 629 MB | `2e968a6af97ce35d8971890b257b9b7edabf20ad91450501fa53162a19ee33eb` |

Each local Hugging Face metadata record names the same revision and SHA-256.

## Runtime validation

All measurements below were taken locally on the machine named above. The AR
control and MTP runs used the same Q8 target, projector, 262,144-token server
context, one parallel slot, and production sampler. Only the MTP draft was
removed for the control.

| Check | Result |
|---|---|
| Functional API smoke | Exact-format text, separated reasoning, nested tools, parallel tools, and vision passed with and without MTP |
| Deterministic 512-token AR control | 15.32 tok/s; 33.82 s wall time |
| Deterministic 512-token native MTP | 29.51 tok/s; 17.79 s wall time; 383/383 proposed draft tokens accepted |
| MTP uplift | 92.7% higher decode throughput; 47.4% lower wall time |
| Medium-reasoning sample | 28.13 tok/s; 114/120 draft tokens accepted |
| Long-context prefill | 17,643 tokens at 178.98 tok/s; repeat reused about 17,125 cached tokens and completed prefill in 3.61 s |
| 70K-context prefill | 70,437 prompt tokens, including 17,131 cached; 53,306 new tokens at 110.85 tok/s |
| State isolation | 20/20 alternating long/short prompt cycles passed |
| Tool burn-in | 100/100 passed after replacing an ambiguous literal `\\n` test prompt; scalar, nested, and parallel calls were clean |

The first burn-in wording produced six misses because the prompt contained the
two literal characters `\\n`. Deterministic replay showed the model spending
its response budget deciding whether that meant a backslash or a newline, not
MTP corruption. The same six seeds passed with an unambiguous two-line
instruction. No malformed calls, state leaks, server crashes, or draft-induced
answer changes were observed.

### Quality and settings

| Configuration | Semantic result | Observation |
|---|---:|---|
| Qwen3.8 Q8, native sampler, medium reasoning, MTP | 10/10 | Concise correct final answers; nested and parallel tools passed |
| Qwen3.8 Q8, greedy and thinking disabled | 7/10 | Missed arithmetic, ordering, and a logic item |
| Retired Qwen3.6 27B MLX 6-bit, thinking enabled | 9/10 | Correct ordering was rejected only by a whitespace-sensitive checker; one Python answer exhausted 2,048 reasoning tokens and emitted no final answer |

The raw automated score was 8/10 for both thinking-enabled runs. Manual
semantic review corrected a whitespace-only checker failure for both models and
a wording-only checker failure for Qwen3.8. Qwen3.6's missing Python final was a
real failure. The Qwen3.8 ten-case run took about 70 seconds; Qwen3.6 took more
than eight minutes and frequently over-reasoned. These are small regression
probes, not general benchmark claims, but they are decisive for this machine's
interactive harness use.

The production profile therefore keeps Qwen's native sampling (`temperature =
1.0`, `top_p = 0.95`, `top_k = 20`, `min_p = 0`, `presence_penalty = 0`,
`repeat_penalty = 1`) and defaults Pi/OMP to medium reasoning. The draft model
does not need a separate reasoning quality: it proposes tokens and the Q8 target
verifies them. MTP remains explicitly disableable for diagnosis.

### Harness validation

- Pi 0.84.3 returned the exact smoke response through Turbo after the stale
  0.69.0 NVM-global package was replaced.
- OMP 18.0.4 lists the native `turbo` provider with text/image input, 262,144
  context, 32,768 output, reasoning, and low/medium/xhigh effort levels.
- OMP returned exact responses through both an already-running server and a
  cold start initiated by `turbo-autoserve.ts`.
- The auto-serve extension is lazy: it starts `turbo serve` on the first request
  for a Turbo model. It never kills or replaces an active server; a model
  mismatch directs the user to the Turbo panel.

## Retirement

The runtime and quality gates passed. The exact Qwen3.6 27B target and MTP cache
roots were permanently removed on 2026-08-25, reclaiming about 28.3 GB. They are
recoverable by downloading them again from Hugging Face. The separate Qwen3.6
35B-A3B profile was not removed.

The Q8 GGUF target, MTP draft, and vision projector were removed on 2026-08-31
after the native-MTP oMLX Q6 profile became the retained Qwen3.8 route. This
record remains as the measured validation of the removed implementation.
