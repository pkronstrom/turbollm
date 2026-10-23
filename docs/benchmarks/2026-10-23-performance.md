# October performance changes and validation

Machine: M4 Max, 128 GB. Date: 2026-10-23.

## Changes

- Context overrides update provider, Pi, OMP and OpenCode limits together. Attachments preserve the running model ID, backend, revision and context policy, including after registry edits.
- A shared server supervisor provides `serve --background`, `status`, and `stop`. Startup holds a per-port lock, refuses arbitrary TCP listeners, verifies listener ownership, and stamps only ready children. Stop checks process birth/command identity and a unique launch token. Cleanup includes workers surviving their leader and logs from cleanly stopped services; failure logs remain available.
- Qwen and Kev profiles keep auto-started servers warm. Qwen uses oMLX's native 1800-second idle model TTL; Kev remains resident until stopped. Other models retain per-command teardown unless opted in.
- Registry parsing caches by file identity, size and nanosecond modification metadata, with independent returned dictionaries. Changes invalidate the cache.
- `pull --update` refreshes existing files; `--revision` selects a download. Provider paths validate pinned local metadata, and downloads use concurrent Hub snapshot fetching.
- oMLX settings preserve unrelated values, use locked atomic updates, and map prompt limits, output limits, sampling and residency to native fields.
- HUD menus read cached permissions and refresh asynchronously. Registry refreshes coalesce; child output drains while the process runs, with bounded cleanup.
- The Gemma shim forwards ordinary SSE promptly and buffers only possible tool calls. Incomplete, malformed or length-truncated calls remain prose; disconnects close upstream requests.
- Kev 4B has an isolated native MLX runtime, explicit adapter/base download checks, a finite-decision API client and durable evaluation fixtures.

## Qwen streaming smoke measurement

The installed backend was already oMLX 0.7.2 at implementation preflight. The
Qwen checkpoint was refreshed to `dc2eaae3f1ed016a42f5e7320d5f639e1b9acf20`
(October 11), preserving the previous directory separately. Text MTP activation
and draft acceptance were observed in the backend log.

Three sequential requests used an identical 5,339-token prompt, low reasoning
effort and a 256-token output cap. All answered `289` correctly. No other local
model was generating during this measurement. CLI microtiming overlapped part
of the run; ordinary applications were not controlled.

| Request | First streamed content or reasoning | First answer content | Complete | Cached prompt tokens |
| --- | ---: | ---: | ---: | ---: |
| Initial model load and prefill | 31.83 s | 34.12 s | 34.13 s | 0 |
| Identical repeat | 0.367 s | 1.604 s | 1.605 s | 5,334 |
| Identical repeat | 0.263 s | 1.529 s | 1.533 s | 5,334 |

The initial response reported 4.9 seconds of model loading. These numbers show
the benefit of retaining the server and a stable prefix. They are not a
before/after backend-version comparison, a general coding benchmark, or evidence
that every growing conversation will reuse the same amount of cache.

Reproduce against an existing server:

```sh
python tools/benchmarks/turbo-latency.py --out /tmp/qwen-latency.json
```

[Raw requests and responses](../../tools/benchmarks/qwen38-2026-10-23-latency.json)
and [driver](../../tools/benchmarks/turbo-latency.py) are retained.

## CLI and Kev

The original audit measured seven TOML parses for `ls` and thirteen for help.
Help now parses once per fresh process; subsequent in-process commands reuse
the parse. A seven-run sample measured help at 74.7 ms, workflow listing at
74.7 ms and model listing at 94.9 ms. Those samples overlap model activity and
include new Kev discovery; they do not establish a broad CLI wall-time gain.
The reduction in repeated parsing is directly verified by regression tests.
[Raw CLI sample](../../tools/benchmarks/cli-2026-10-23.json).

The [Kev report](2026-10-23-kev-4b.md) compares 4B with the retained 0.8B on an
isolated, labeled smoke set. 4B improved accuracy but was slower. Integrated
`turbo decide tools/benchmarks/kev-example.json` also passed against the managed
service, then two repeat CLI invocations returned valid JSON only on stdout,
with correct billing/paid decisions and wall times of 206 and 176 ms. These are
three-question requests while Qwen remains loaded, distinct from the isolated
single-question benchmark.

## Verification and local state

- 516 Python tests passed after the external review fixes. The unchanged Raycast and Swift HUD code passed 70 and 81 tests respectively; TypeScript checks passed.
- Wheel built and isolated installed-wheel verification passed.
- Real fixture servers verified busy-port refusal, startup failure, token-safe stop and cleanup after a backend leader exits.
- Native Kev inference and Qwen streaming/prefix cache behavior were exercised. No real Gemma model was loaded; proxy streaming uses controlled delayed HTTP fixtures.
- Qwen on 8899 and Kev on 8901 are managed services. Use `turbo status --port PORT` or `turbo stop --port PORT`.
- Old Qwen weights remain at `~/.models/Jundot/Qwen3.8-27B-oQ6e-mtp-before-dc2eaae3`. The user registry and oMLX model-settings backups have `.before-performance-*` suffixes. The retained Kev 0.8B files were unchanged.
- The installed Turbo CLI is editable and sees these changes. The HUD debug binary behind its existing symlink was rebuilt; no HUD process was running at final inspection.

## External review

Claude Sonnet reviewed the implementation and approved design with read-only
repository access. It reported one medium and three low findings, with no
critical or high findings. All four were verified and addressed:

- Clean server shutdown removes its per-launch log; startup failures retain diagnostics.
- Interrupted chunked HTTP streams flush buffered candidate text without producing tool calls or handler tracebacks.
- Concurrent automatic warm starts use a bounded startup lock, then validate and attach to the ready server. Explicit `serve` and ephemeral starts keep their existing busy-port behavior.
- Unused stamp-writing, stamp-cleanup and log-tail helpers were removed.

Real-process and chunked-HTTP regression tests reproduced the first three
issues before the fixes and passed afterwards. Independent in-session reviews
were also completed before this external pass.
