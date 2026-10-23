# Performance and Kev 4B

Approved scope: implement the October 23 audit findings and performance changes; try and add Kev 4B. Existing user files and live services must remain intact.

## Configuration and attachment

Use a shared context override that updates provider and harness limits together. A running server's recorded effective configuration is authoritative when attaching; explicit model selection must validate identity too. Record alias, provider model ID, backend and context in port stamps. Match provider-specific IDs, not only Hugging Face repo names. Do not infer that an oMLX catalog listing means every model is loaded.

Cache parsed registries using file identity/modification metadata, returning independent data to callers. Preserve user registries and invalidate on edits. Add explicit pull update/revision controls; do not silently replace weights on ordinary commands.

## Residency

Provide an explicit background serve mode with status and owned-process stop controls. Repeated harness/transcription calls attach to that server and preserve its loaded model/cache. oMLX's own idle model TTL controls weight residency without killing active inference. Do not add a generic idle-kill timer that cannot observe HTTP requests. Existing foreground and ephemeral launch behavior remains available. On this machine, enable the warm coding service only if its port is free; never replace an existing listener.

Persist oMLX prompt limits, sampling and residency using native model-settings fields; distinguish prompt context from output limits. Write configuration atomically, preserving unrelated settings. Keep concurrency at one for the current MTP profile until measured otherwise. Preserve lean OMP as an explicit profile.

## UI and proxy

HUD permission refresh must be asynchronous and cached; menu building must not spawn/wait for processes. Workflow subprocesses must drain both streams while running and have bounded termination. Coalesce concurrent registry refreshes.

The Gemma compatibility proxy must forward ordinary streamed prose promptly and buffer only a possible bare tool-call prefix/body until it can safely classify it. Preserve upstream errors, reasoning, finish reasons and disconnect cleanup. Do not emit partial tool arguments as executable calls.

## Kev

Add a dedicated decision backend and Kev 4B registry entry, excluded from chat harness pickers. Use the verified upstream Kev runner and native MLX path, not an ordinary chat model loader. Add a small CLI client accepting the upstream typed-decision request JSON, returning machine-readable responses. Keep downloads explicit and loopback serving on a separate port.

Install/download Kev in an isolated runtime, preserve the existing 0.8B checkpoint, run real choice/boolean/score requests, and measure cold start plus warm/repeated-state latency and correctness on durable fixtures. Record model/runtime revisions and memory where observable. No hosted inference is needed.

## Validation

Review refinements: coordinate startup with a per-port lock and check TCP occupancy independently of API health. Only record ownership of a verified child; stop must verify a process birth/unique launch identity, not a bare PID. Context overrides include Pi, OMP and OpenCode fields. Attachment validation belongs in the shared path, including transcription. A streamed bare tool call requires balanced structure and a clean finish; truncation/EOF must remain non-executable. Protect upstream process cleanup even if the proxy cannot bind.

Regression tests first for context, model identity, settings, lifecycle, streaming and decision contracts. Run Python, Raycast and affected Swift tests, repeat CLI timings, smoke-test background start/attach/stop, and perform Kev inference. Upgrade oMLX and refresh Qwen only after checking installation compatibility and preserving rollback data; report environmental limitations honestly.
