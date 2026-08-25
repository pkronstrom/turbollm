# Qwen3.8 27B Q8 + MTP / Pi validation

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

Pending AR control, MTP performance/stability, Pi, and quality gates.

## Retirement

Qwen3.6 27B target and MTP artifacts remain in place until the runtime gates pass.
