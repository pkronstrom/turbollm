# Isolated Lean OMP Profile for Qwen3.8

**Date:** 2026-08-27

**Status:** Approved design

**Goal:** Add `turbo omp-lean qwen38-27b-q8-mtp`: an opt-in, interactive Qwen3.8 coding shell that retains OMP's terminal UX and LSP but does not alter the existing `turbo omp` or OMP profile.

## Context

The standard OMP configuration exposes broad tool, MCP/device, extension, and skill surfaces. That is useful for frontier hosted models but expensive for local inference.

Measurements against the running `ggml-org/Qwen3.8-27B-GGUF` Turbo server establish the problem boundary:

- A direct `/v1/chat/completions` request with system message `x` and user message `Reply exactly OK.` consumed 58 prompt tokens.
- OMP routed to the same server with `--no-tools --no-skills --system-prompt x` consumed 19,403 prompt tokens.
- OMP routed to the same server with `NULL_PROMPT=true --no-tools --no-skills` still consumed 18,177 prompt tokens, including 13,636 `nonMessageTokens`.

Therefore Qwen3.8 and llama.cpp do not impose the observed prompt tax. An OMP profile can remove the controllable surface, but it may not remove the provider-side residual. The design treats that residual as a measured acceptance gate rather than promising a reduction it cannot prove.

Qwen3.8 is an agent-capable model with native tool use and `reasoning_effort`; it retains thinking by default. The lean route keeps Qwen's native chat-template behavior and Turbo's established Qwen compatibility metadata.

## User Interface and Isolation

The normal path remains unchanged:

```text
turbo omp qwen38-27b-q8-mtp
```

The new path is:

```text
turbo omp-lean qwen38-27b-q8-mtp
```

`omp-lean` is an opt-in Turbo harness. It owns a dedicated OMP profile and config overlay. It MUST NOT modify:

- `~/.omp/agent/config.yml`;
- normal OMP sessions, model selection, extensions, or skill configuration;
- the behavior of `omp` or `turbo omp`.

The profile state is disposable. Removing its state and overlay returns the system to the exact pre-feature setup.

## Lean Capability Contract

The initial profile exposes only the capability required for ordinary repository coding:

- `read`, `glob`, `grep`, `edit`, `write`, and `bash`;
- `lsp` for definitions, references, rename, and diagnostics;
- `ask` and `todo` for interactive clarification and task tracking.

It excludes browser automation, debugger/DAP, computer use, image tools, web search, task/subagents, async job control, MCP tool discovery, generated-image tools, and extension-provided tools.

No extension discovery runs in the lean profile. No dynamic `xd://` device documentation is injected. The profile does not load generic rules or a workspace tree by default.

The skill catalogue is an explicit allow-list:

- `vault-mcp`;
- `vault-skills`.

Those skills remain discoverable by name. All other skills remain installed on disk but are omitted from the profile and never advertised to Qwen.

The profile uses the `none` personality setting. It retains a concise, Qwen-specific operating prompt: inspect before changing code; use the available tools; keep edits focused; validate the changed behavior; report evidence. It does not duplicate OMP's general hosted-model policy manual.

## Runtime Architecture

```text
turbo omp-lean <model>
  -> isolated OMP profile + generated lean overlay
  -> Turbo OpenAI-compatible provider configuration
  -> llama-server on 127.0.0.1:8899
  -> Qwen3.8 27B Q8 + native chat template and reasoning controls
```

The new harness reuses the existing `OmpHarness` provider-entry generation. It must not duplicate Turbo model metadata, endpoint construction, Qwen compatibility settings, or model-selection logic.

The isolation boundary includes all OMP state relevant to the lean run: configuration, sessions, model cache/state, extension discovery, and skills. The normal profile must neither read from nor be written by the lean launcher.

## Prompt Measurement Gate

Every lean-profile validation records server-reported `prompt_tokens` and OMP's context snapshot for the same fixed request. The comparison matrix is:

1. Direct Turbo request;
2. normal `turbo omp`;
3. `turbo omp-lean`.

The lean route must remove all advertised generic-skill, extension/device, and disabled-tool material from the request. Its exact prompt-token target is intentionally deferred until the request payload is captured: the prior `NULL_PROMPT` probe shows a large OMP residual outside the normal system-prompt builder.

If the profile fails to materially reduce the normal route, implementation must identify and report the surviving request block. It must not silently weaken the normal profile or claim that the lean route is minimal. The next decision is then either a narrow OMP/Turbo adapter fix or a direct standalone client.

## Validation

### Isolation

- Launching `turbo omp-lean` leaves the normal OMP config bytes unchanged.
- Existing `omp` sessions and `turbo omp` launch behavior remain unchanged.
- The lean state/config can be removed without affecting the normal profile.

### Capability

- Qwen can read, search, edit, write, and run a command in a temporary repository.
- LSP definition/references and a rename work through the profile.
- Browser, task, debugger, web-search, and dynamic MCP tools are unavailable.
- The rendered skill catalogue names `vault-mcp` and `vault-skills`, and omits the generic catalogue.

### Qwen compatibility

- Qwen's native chat template receives the correct reasoning configuration.
- `low`, `medium`, and `xhigh` continue to work exactly as the existing Turbo OMP model metadata declares.
- A simple reasoning/tool loop returns valid tool calls and a final answer.

### Measurement

- Record prompt tokens, non-message tokens, time to first token, and prompt-processing rate for the fixed comparison request.
- Record the lean profile's first coding-tool request as a practical smoke test.
- Report any unreduced OMP residual as a blocking fact for a genuinely minimal local harness.

## Alternatives Considered

### Modify the normal OMP profile

Rejected. The user requires current sessions, configuration, extensions, and workflows to remain intact.

### Standalone direct Turbo client

This is the best route to the 58-token direct-server baseline, but loses OMP's existing interactive UI and LSP. It is a fallback only if measurement proves the isolated OMP profile cannot remove the residual framing.

### Keep the full OMP surface

Rejected. It retains broad skills, devices, and tools that are not needed for the intended local coding shell.

## References

- [Qwen3.8 27B model card](https://huggingface.co/Qwen/Qwen3.8-27B)
- [ggml-org Qwen3.8 GGUF model card](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF)
- [Qwen llama.cpp guide](https://qwen.readthedocs.io/en/latest/run_locally/llama.cpp.html)
- [OMP system-prompt builder, v18.0.5](https://github.com/can1357/oh-my-pi/blob/v18.0.5/packages/coding-agent/src/system-prompt.ts)
