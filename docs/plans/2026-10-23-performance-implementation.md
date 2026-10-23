# Performance Implementation Plan

Goal: resolve confirmed audit bugs and improve interactive latency, with working local Kev 4B inference.

Architecture: reuse provider contracts for models; isolate persistent server ownership from harness lifetime; preserve backend-specific decision and chat APIs.

1. Add failing tests for context overrides/attachment identity and registry cache invalidation. Implement in registry.py, cli.py and provider-independent server helpers. Run targeted Python tests.
2. Add explicit update/revision download support and oMLX native settings mapping with atomic writes. Test refresh skipping, effective limits, sampling and unrelated-setting preservation.
3. Add background serve/status/stop with process-identity verification, signal forwarding and model stamps. Exercise real lightweight fixture processes and refusal to replace busy ports.
4. Fix HUD cached asynchronous permissions and subprocess pipe draining; add meaningful Swift tests. Fix incremental Gemma proxy streaming with delayed SSE integration fixtures.
5. Verify upstream Kev runtime, install isolated dependencies, download Kev 4B without replacing 0.8B. Add provider/decision client/registry integration and contract tests. Measure real inference on committed fixtures.
6. Upgrade compatible oMLX installation and refresh Qwen checkpoint if practical, preserving running services and recording revisions. Use background mode for a free coding port; validate readiness and clean ownership.
7. Review changes, run full Python/Raycast/affected Swift checks, repeat CLI timings, document exact measured gains and Kev results. Leave changes uncommitted for user review.
