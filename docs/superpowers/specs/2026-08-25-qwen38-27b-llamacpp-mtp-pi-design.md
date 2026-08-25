# Qwen3.8 27B Q8 + Native MTP for Pi

**Date:** 2026-08-25

**Status:** Approved design

**Primary machine:** Apple M4 Max, 128 GB unified memory

**Goal:** Replace the dense Qwen3.6 27B Turbo entry with a high-quality Qwen3.8 27B setup that is fast and dependable in long Pi coding-agent sessions.

## Context

The existing dense model is `qwen36-27b-6bit`, served by `mlx-vlm` from an Unsloth 6-bit MLX target plus a separate 5-bit MTP head. It is a good speed baseline, but Qwen3.8 materially improves coding and repository-level capability and has native MTP training.

The replacement must optimize the whole agent loop rather than an isolated decode benchmark. That makes prompt-prefix reuse, tool-call handling, reasoning controls, long-session behavior, and recoverability as important as peak tokens per second.

The selected backend is `llama.cpp`. It is the user's preferred staple runtime, already has a Turbo provider, has strong OpenAI-compatible serving and prompt caching, and supports Qwen3.8 vision, reasoning, and native MTP. MTPLX and MLX-VLM remain useful comparison backends, but are not the production default.

## Decision Summary

Turbo will add a Pi-first `qwen38-27b-q8-mtp` model backed by `llama-server` and these artifacts from `ggml-org/Qwen3.8-27B-GGUF`:

- `Qwen3.8-27B-Q8_0.gguf` as the target model.
- `mtp-Qwen3.8-27B-Q8_0.gguf` as the native MTP draft head.
- `mmproj-Qwen3.8-27B-Q8_0.gguf` as the multimodal projector.

All three artifacts will be downloaded explicitly into `~/.models/ggml-org/Qwen3.8-27B-GGUF`, not left in an opaque Hugging Face cache. The target plus sidecars occupy roughly 30 GB. Q8_0 is intentionally chosen over a 4- or 5-bit quant: the machine has ample memory, and avoiding an aggressive target quant is more valuable than saving several GB.

The MTP head affects speed, not the accepted token distribution. Every speculative token is verified by the Q8 target. A weak or incompatible MTP head should reduce acceptance or performance, not silently lower model quality; Turbo will therefore make MTP observable and safely disableable.

## Runtime Architecture

The normal request path is:

```text
Pi
  -> Turbo-generated OpenAI provider entry
  -> llama-server on 127.0.0.1
  -> Qwen3.8 Q8 target
       + native Q8 MTP verification
       + Q8 vision projector
```

The existing `GgufProvider` remains the owner of download detection, artifact resolution, and command construction. No new backend or proxy is introduced.

### Initial llama-server configuration

The production defaults are:

| Setting | Value | Reason |
|---|---:|---|
| Context | 262,144 tokens | Qwen3.8 native context; the 128 GB machine can support it |
| Parallel slots | 1 | Preserve the full context and maximize Pi prefix reuse |
| Target GPU layers | `all` | Full Metal offload |
| Draft GPU layers | `all` | Keep the small MTP path on Metal |
| Target KV | F16 | Highest-confidence cache quality; memory is available |
| Draft KV | F16 | Avoid acceptance loss from draft-cache quantization |
| Flash attention | `on` | Reduce attention cost and memory pressure |
| Batch / micro-batch | 512 / 512 initially | Conservative known-good starting point; benchmark before tuning |
| MTP type | `draft-mtp` | Qwen3.8 native multi-token prediction |
| Draft maximum | 3 tokens | Conservative starting depth with good expected acceptance |
| Prompt cache | enabled | Essential for iterative Pi turns |
| Context shifting | disabled | Prefer explicit Pi compaction over silent loss of old context |
| Reasoning preservation | enabled | Preserve prior assistant thinking as required by the model template |
| Jinja templates | enabled | Required for Qwen tool and reasoning template behavior |
| Server binding | `127.0.0.1` | Local-only service; no unauthenticated network exposure |

The implementation must use the flag names supported by the installed stable llama.cpp release. The required capabilities are more important than a numeric version: startup must fail clearly if `llama-server --help` lacks native `draft-mtp`, draft-depth, reasoning-preservation, or multimodal-projector support.

The currently installed llama.cpp build 8680 predates the required Qwen3.8 MTP interface and must be upgraded. The target is stable llama.cpp v0.2.0 or a later stable release, followed by capability checks. Nightly builds are a fallback only if the stable build cannot serve the official artifacts correctly.

### Sampling and reasoning

Qwen3.8 gets a new sampling preset rather than inheriting the Qwen3.6 recipe:

```toml
temperature = 1.0
top_p = 0.95
top_k = 20
min_p = 0.0
presence_penalty = 0.0
repeat_penalty = 1.0
```

These are the official thinking-mode defaults and are the correct defaults for the Pi coding path. The former Qwen3.6 temperature of `0.6` must not leak into the new model.

Qwen3.8 supports exactly `low`, `medium`, and `xhigh` reasoning effort. Turbo will expose those values honestly and use `medium` for normal Pi sessions. Unsupported Pi levels (`minimal`, `high`, and `max`) will not be silently remapped to a different effort. Reasoning effort must reach llama.cpp through `chat_template_kwargs`; setting only the OpenAI top-level `reasoning_effort` is insufficient for this local Qwen template.

Non-thinking mode has a different official recipe (`temperature=0.7`, `top_p=0.8`, `top_k=20`, `presence_penalty=1.5`). It is outside the primary medium-reasoning path. If non-thinking use is exposed, it must be represented as an explicit model variant or request configuration with that complete sampler set, not by merely toggling thinking off while retaining the thinking sampler.

## Turbo Configuration Contract

The new model entry will declare:

- Backend `gguf`.
- Official `ggml-org` repository and exact target, MTP, and projector filenames.
- Explicit local paths under `~/.models/ggml-org/Qwen3.8-27B-GGUF`.
- `context_default = 262144` and `context_max = 262144`.
- `kv_quant = "off"` for F16 target KV.
- Vision, tools, reasoning, and MTP capability tags.
- The Qwen3.8 thinking sampling preset.
- Pi default reasoning `medium`.

The GGUF provider already resolves a target and a draft file. It will be extended narrowly to:

- Download, find, and validate a configured multimodal projector.
- Pass the projector to `llama-server`.
- Configure draft GPU layers and F16 draft KV explicitly.
- Pass reasoning preservation and explicit prompt-cache settings.
- Treat missing configured sidecars as startup errors for this model. Serving silently without MTP or vision would make the runtime differ from the selected Turbo entry.
- Report the resolved target, draft, projector, context, KV types, and MTP depth before launch.

Artifact paths are separate configuration fields even when all files share one directory. This keeps pull, serve, status, and remove behavior symmetric and testable.

## Pi Integration

Turbo will refresh Pi from the old `@mariozechner/pi-coding-agent` installation to the current stable `@earendil-works/pi-coding-agent` package and update the harness installation hint.

The generated `~/.pi/agent/models.json` entry will:

- Use the OpenAI completions-compatible Turbo endpoint.
- Advertise both `text` and `image` input.
- Advertise a 262,144-token context.
- Use an output ceiling that does not accidentally truncate long Qwen reasoning; the chosen value will be verified against the current Pi schema and model limit.
- Set the Qwen chat-template compatibility mode.
- Supply `chatTemplateKwargs` for `enable_thinking`, `preserve_thinking`, and the selected reasoning effort.
- Define a `thinkingLevelMap` in which only `low`, `medium`, and `xhigh` are valid.
- Use one source of truth for sampling parameters, avoiding conflicting server and Pi defaults.
- Continue preserving unrelated providers in the user's existing `models.json` and backing up malformed JSON before replacement.

Pi's default launch will select `medium`. Turbo's existing `--thinking` override will validate against the model's supported levels before launching. Pi compaction/session continuation must be exercised near the context limit because llama.cpp context shifting is intentionally disabled.

## Version Refresh

The environment refresh is part of the migration, but only stable releases will be installed by default. Before changing anything, implementation will record current versions; afterward it will record the resolved versions and run smoke tests.

Expected refresh set:

- Turbo CLI: reinstall the current repository version after code changes.
- llama.cpp: build 8680 to stable v0.2.0 or later, with capability verification.
- Pi: 0.69.0 old package scope to the current stable `@earendil-works` release.
- `mlx-vlm`: 0.6.3 to the current stable release, retained as a fallback backend.
- `vllm-mlx`: 0.2.9 to the current stable release, followed by existing Qwen3.6 MoE regression tests.
- oMLX: 0.3.8 to the current stable release, not a release candidate.
- Installed Turbo harnesses such as Claude Code, OpenCode, Codex, Hermes, and Goose: refresh to their current stable releases where their package manager provides a normal upgrade path.

A failure in an auxiliary backend refresh must not block bringing up the selected llama.cpp path. It will be reported independently rather than hidden.

## Migration and Removal Safety

The migration is deliberately two-phase:

1. Add and download Qwen3.8, refresh the runtime, and complete all validation while Qwen3.6 remains available as rollback.
2. Remove the old dense Qwen3.6 target and MTP artifacts only after Qwen3.8 passes the acceptance gates.

The old data currently consists of approximately 28 GB at the Unsloth Qwen3.6 target cache path and 304 MB at the mlx-community MTP cache path. Removal will target those exact resolved directories only.

`turbo rm` currently handles only the primary Hugging Face and legacy cache directories. It will be corrected so a model removal accounts for:

- Primary target artifacts.
- Draft/MTP artifacts.
- Multimodal projectors.
- Explicit configured local paths.
- Shared directories or repositories without deleting files belonging to another configured model.

Removal must show all resolved targets and their aggregate size before confirmation. Tests will use temporary directories; the real Qwen3.6 deletion happens only after the new model's burn-in passes. Because `~/.models` and package-manager locations are outside the repository sandbox, the implementation will request the necessary approval when it reaches download, install, and deletion steps.

## Validation and Acceptance Gates

### Static and unit validation

- Existing Turbo test suite remains green.
- New provider tests cover target, draft, and projector download and resolution from explicit local paths.
- Command tests assert Qwen3.8 context, target/draft offload, F16 target/draft KV, MTP depth, projector, prompt caching, reasoning preservation, sampler flags, and one slot.
- Pi tests cover image capability, current schema fields, exact reasoning-level mapping, sampling, context/output limits, and preservation of unrelated configuration.
- Removal tests cover sidecars, local paths, shared-directory safety, confirmation output, and missing files.

### Runtime smoke tests

- Server loads all three official artifacts without tensor, architecture, or template errors.
- `/v1/models` and a basic text completion work.
- A real image request works through Pi or the same OpenAI request shape Pi uses.
- Reasoning is separated from final content and preserved correctly across turns.
- `low`, `medium`, and `xhigh` produce valid responses; unsupported levels fail clearly.
- Simple, nested, multiline, and parallel tool-call arguments round-trip correctly.
- At least 100 representative tool calls complete without malformed tool markup or stuck retries.

### Performance and state tests

Measure both autoregressive and MTP modes with identical prompts and sampling at short, 16K, and long contexts. Record:

- Time to first token.
- Prompt-processing rate.
- Decode rate.
- MTP accepted drafts and acceptance rate.
- Peak memory.
- Prefix-cache reuse on consecutive Pi turns.

MTP passes if it produces identical-quality target-verified output, remains stable across mixed long and short turns, and gives a meaningful end-to-end benefit. A practical initial target is at least 15% faster decode with no repeated-text, cross-request-state, tool-call, or crash regression. If it fails, the same Q8 model ships temporarily without MTP; the model migration does not fall back to a lower-quality target quant.

The test matrix specifically covers the currently reported young-MTP failure classes in llama.cpp: unexpectedly low acceptance, state leakage after long-to-short request sequences, and Apple Metal stability. Pinning the exact passing llama.cpp version is part of the deployment record.

### Quality checks

The old Qwen3.6 model and new Qwen3.8 Q8 model will run a small deterministic agent-oriented comparison set covering:

- Repository navigation and code-edit planning.
- Multi-file coding reasoning.
- Tool selection and schema adherence.
- Long-context retrieval.
- Instruction following under medium and xhigh reasoning.

Qwen3.8 must show no obvious tool-use or instruction-following regression. The purpose is to catch runtime/template/sampling mistakes, not to recreate the model vendor's benchmark suite.

## Failure Handling and Rollback

- Missing target: do not launch; instruct `turbo pull qwen38-27b-q8-mtp`.
- Missing or invalid MTP/projector: do not silently degrade the named full-capability entry; report the exact missing artifact.
- MTP instability: disable MTP flags while retaining Q8 target, vision, Pi settings, and llama.cpp.
- Stable llama.cpp lacks a required capability: try the newest stable package source available; use a pinned nightly only as a documented fallback.
- Pi schema incompatibility: retain a backup of `models.json`, restore it, and leave the server usable via direct OpenAI calls.
- Quality regression: keep Qwen3.6 installed and stop before removal.
- Auxiliary server/harness upgrade regression: report and pin or roll back that component without reverting the validated llama.cpp route.

## Alternatives Considered

### MTPLX with `Youssofal/Qwen3.8-27B-MTPLX-Optimized-Quality`

MTPLX can be the fastest tested Apple-Silicon option and its optimized 8-bit target is attractive. It is not selected because the backend and model packaging are younger, add a new Turbo provider, and have less accumulated Pi/tool-call operational evidence. It remains a later performance experiment after the stable path is established.

### MLX-VLM plus BF16 MTP

This is the preferred MLX fallback. The BF16 mlx-community MTP head has demonstrated approximately 82% acceptance and a 1.74x decode improvement in one report. However, the published 4-bit MTP artifact is corrupt, and the server's agent-loop prefix reuse and Qwen3.8 MTP path have less production evidence than llama.cpp. Raw decode can be competitive, but the full Pi experience is the priority.

### oMLX plus MTP

oMLX is polished for MLX serving and has useful activation-aware quantizations, but its Qwen3.8 native MTP path is also recent and benchmark results vary substantially by quant. It is retained as an auxiliary backend, not the selected replacement.

### Smaller GGUF target quants

Q6 or high-quality Q5 variants would reduce model size and may improve raw throughput. They are not the default because 128 GB memory removes the main pressure to accept extra quantization error. They can be benchmarked later against Q8 if Q8 speed proves inadequate.

## References

- [Official Qwen3.8 27B model card](https://huggingface.co/Qwen/Qwen3.8-27B)
- [Official ggml-org Qwen3.8 GGUF artifacts](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF)
- [llama.cpp server reference](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [llama.cpp stable v0.2.0 release and versioning policy](https://github.com/ggml-org/llama.cpp/releases/tag/v0.2.0)
- [Qwen3.8 llama.cpp MTP low-acceptance report](https://github.com/ggml-org/llama.cpp/issues/27151)
- [Qwen3.8 llama.cpp long/short-request MTP state report](https://github.com/ggml-org/llama.cpp/issues/27296)
- [mlx-vlm corrupt 4-bit versus working BF16 MTP report](https://github.com/Blaizzy/mlx-vlm/issues/1931)
- [Pi custom-model configuration reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md)

## Acceptance Criteria

The design is complete when:

- Turbo can pull, list, serve, and safely remove the exact Qwen3.8 target, MTP, and projector artifacts from `~/.models`.
- Pi launches the model with correct vision, sampling, context, and reasoning metadata.
- A pinned stable llama.cpp build serves Qwen3.8 Q8 successfully with reliable prefix caching and tool calls.
- Native MTP either passes the stability/performance gates or is explicitly disabled without changing the Q8 target.
- Relevant server and harness tools are refreshed to stable versions and recorded.
- The old dense Qwen3.6 target and MTP are deleted only after the new path passes its burn-in.
