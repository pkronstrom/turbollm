# Backend toolchain update

**Date:** 2026-08-26
**Status:** approved

## Goal

Update every installed model-serving backend on this workstation, with particular attention to `mlx-audio`, then adapt turbollm only where the upgraded backend breaks its existing provider contract.

## Installed scope

| Distribution | Backend | Current version |
|---|---|---:|
| UV tool | `mlx-audio` | 0.4.3 |
| UV tool | `mlx-vlm` | 0.6.14 |
| UV tool | `vllm-mlx` | 0.4.1 |
| Homebrew formula | `omlx` | 0.3.8 |
| Homebrew formula | `llama.cpp` | 0.2.0 |

`turbollm` itself and non-backend tools/harnesses are out of scope.

## Update strategy

1. Upgrade only the three listed UV tools, retaining their existing tool environments and extras.
2. Upgrade only the two listed Homebrew formulae.
3. Do not delete cached models, configurations, or tool environments as part of this work.
4. Inspect each upgraded executable's version and help surface where relevant before exercising turbollm's integration.

## Compatibility contract

The primary checked contract for `mlx-audio` is the `MlxAudioProvider` interface:

- `mlx_audio.server` remains discoverable on `PATH`.
- It accepts `--host 127.0.0.1 --port <port>`.
- Its transcription endpoint remains compatible with `turbo transcribe`'s multipart request and response handling.

The equivalent executable-discovery and server-command contracts apply to `mlx-vlm`, `vllm-mlx`, `omlx`, and `llama.cpp`. turbollm provider code changes only when an observed upstream change invalidates one of these contracts. The change must be narrow, update every affected caller, and be protected by the existing focused provider or CLI test suite.

## Verification

1. Confirm `turbo ls -a` recognizes each installed backend.
2. Verify upgraded executable versions and the `mlx_audio.server` startup interface.
3. Run focused tests for provider discovery and transcription/CLI behavior.
4. If any provider code changes, run the affected test plus a real smoke path for the changed backend.

## Out of scope

- Upgrading unrelated UV tools, harnesses, or the turbollm project package.
- Model downloads, model migrations, or cache pruning.
- New backend features unrelated to a compatibility break.
