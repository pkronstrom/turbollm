# MTP speculative decoding on MLX — learnings

**Date:** 2026-06-09
**Context:** Evaluating multi-token-prediction (MTP) speculative decoding for turbollm's
local models served to agentic harnesses (pi). Hardware: 128 GB M-series.
Full spike record: `docs/superpowers/specs/2026-06-09-gemma4-mtp-spike-design.md` (gitignored).

## TL;DR

- **Adopted:** Qwen3.6-27B (dense) → `mlx-vlm` + MTP. ~2× faster on the server path. Shipped.
- **Kept as-is:** Qwen3.6-35B-A3B (MoE) stays on `vllm-mlx` — it already serves faster
  than `mlx-vlm`+MTP can.
- **Parked:** Gemma 4 MTP — works in `mlx_vlm.generate` but gives ~0 gain on the
  `mlx_vlm.server` path that harnesses actually use. Revisit later.

The single most important lesson: **MTP's payoff depends on the compute profile of the
model and on which serving path you measure.** It is not a free, universal speedup.

---

## Background: two orthogonal concepts

These get conflated constantly. They are independent:

| | What it is | Affects | Source |
|---|---|---|---|
| **QAT** (quantization-aware training) | Train with simulated quantization so low-bit weights keep near-bf16 quality | model *quality* at a given bit-width | mostly a Google/Gemma thing; Qwen ships normal weights + community PTQ |
| **MTP** (multi-token prediction) | A small drafter "head" proposes several tokens; the target verifies them in parallel (speculative decoding) | decode *speed* | per-model drafter heads on HF (mlx-community, etc.) |

The `mlx-community/gemma-4-mtp-qat` collection bundles *both* (QAT weights **+** MTP
drafters), which is why they're easy to conflate. In this evaluation QAT was a
Gemma-only property; the Qwen models used plain post-training quantization (Unsloth
"UD" dynamic quant for targets, standard mlx-community quants for drafters).

**MTP drafters are not standalone models.** They are tiny heads (e.g. `Gemma4AssistantForCausalLM`,
~0.4–0.5B) loaded *alongside* a full target via `--draft-model <head> --draft-kind mtp`.

## The MLX backend landscape (June 2026)

| Backend | Role | MTP support | Agentic/tool-calling |
|---|---|---|---|
| **mlx-vlm** (0.6.2) | VLM + speculative decoding | ✅ MTP/EAGLE for Gemma 4, MTP for Qwen (server-path maturity varies) | ✅ tool calls, Anthropic `/v1/messages`, `/v1/responses` (0.6.0+) |
| **vllm-mlx** | production server (continuous batching, paged cache) | ⚠️ only SpecPrefill, **broken on Qwen MoE** (vllm#36872) | ✅ mature tool/reasoning parsers |
| **mlx-lm** | reference lib | native MTP (no external drafter) is **unmerged** (PR #990) | minimal server |

turbollm's `mlx-vlm` provider already wires MTP: `hf_repo` (target) + `draft_hf_repo`
(drafter) + `server.draft_kind = "mtp"` (`src/turbollm/providers/mlx_vlm.py:36-45`).
No code change was needed to use MTP — just a `models.toml` entry.

---

## Empirical results

Method: single request, 400 tokens, temp 0, one prompt. Wall-clock tok/s (server) and
decode tok/s (generate). **Indicative, not a rigorous benchmark** — concurrency,
streaming, and long context will shift absolute numbers.

### `mlx_vlm.generate` (one-shot path) — MTP works everywhere

| Model | off | on | Δ | acceptance |
|---|---|---|---|---|
| Gemma 4 26B-A4B | 111 | 144 | +30% | 82% |
| Qwen3.6-27B | 15.9 | 27.3 | +71% | 89.7% |
| Qwen3.6-35B-A3B (MoE) | 98 | 164 | +68% | 91% |

### `mlx_vlm.server` (continuous-batching path — what pi/harnesses use)

| Model | mlx-vlm off | mlx-vlm + MTP | **vllm-mlx (current)** | Winner |
|---|---|---|---|---|
| Gemma 4 26B-A4B | 80 | 83 (**+4%, noise**) | 86.5 | vllm-mlx |
| **Qwen3.6-27B** | 13.4 | **21.0 (+57%)** | 10.7 | **mlx-vlm+MTP (~2×)** |
| **Qwen3.6-35B-A3B (MoE)** | 68.6 | 86.7 (+26%) | **98.7** | **vllm-mlx** |

---

## Key learnings

### 1. Measure the *server* path, not `mlx_vlm.generate`
The two diverge sharply. Gemma got +30% in `generate` but **~0% on the server**
(drafter loads and says "enabled," but continuous batching doesn't benefit). Harnesses
talk to the server, so the server number is the only one that matters. A maintainer's
headline benchmark is usually a `generate` number — don't assume it transfers.

### 2. MTP's payoff scales with how compute-bound decode is
- **Heavy dense model** (Qwen3.6-27B, all 27B params active per token): decode is
  compute-bound, the drafter amortizes a lot → **near-2×**.
- **Light MoE** (Qwen3.6-35B-A3B, only ~3B active): already fast and bandwidth-bound;
  speculative overhead eats more of the relative gain → smaller win, and not enough to
  beat a better-optimized backend.

### 3. Backend choice depends on the compute regime
- For the **heavy dense 27B**, `mlx-vlm`'s base serving ≥ `vllm-mlx`, and MTP makes it win.
- For the **light MoE**, `vllm-mlx`'s optimized serving (continuous batching + paged
  cache) is ~44% faster at the base than `mlx-vlm`, more than MTP's +26% can recover.
- **The catch-22 for MoE:** the fast backend (`vllm-mlx`) has no working MoE MTP
  (SpecPrefill is broken on MoE mixed-attention); the MTP-capable backend (`mlx-vlm`)
  serves MoE slower. So the MoE is already on its best setup.

### 4. MTP does NOT require QAT, and MoE mixed-attention does NOT break MTP
The Qwen wins used plain PTQ weights. And the MoE's mixed (linear + SWA + softmax)
attention — which breaks `vllm-mlx` SpecPrefill — works fine under `mlx-vlm` MTP
(91% acceptance). MTP is a different mechanism than SpecPrefill.

**Corollary — QAT is a standalone quality lever, orthogonal to MTP.** We deleted the
QAT Gemma target because its *MTP speedup* failed, but QAT's *quality* value was
independent of that. The current Gemma entries run on **Unsloth UD** 4-bit (a
quality-oriented dynamic PTQ), *not* QAT. QAT-4bit could be adopted purely for quality
on `vllm-mlx` (no drafter, no speed change) — but UD-4bit is itself a quality quant, so
QAT-vs-UD is **unevaluated** and not an obvious win. Justify any swap with an actual
quality comparison, not the assumption that "QAT > PTQ."

### 5. Cross-conversion drafter pairing works
Pairing drafters and targets from *different* converters/quant schemes still yielded
82–91% acceptance (e.g. unsloth 6bit target + mlx-community 5bit MTP drafter). The MTP
head consumes hidden states, which are largely quant-independent. No need to chase a
perfectly matched target.

### 6. Run the drafter at *high* bit-width
Drafters are tiny (~0.4–0.5B), so 8-bit/bf16 costs a few hundred MB but maximizes draft
acceptance = more speedup. "Go lower to save space/speed" is the right instinct for the
*target*, the wrong one for the *drafter*.

### 7. Context switching & compaction across backends
- **pi compaction is backend-agnostic** — pi derives `contextWindow` from the model's
  `context_default` (`harnesses/pi.py:60-69`) and auto-compacts at 80%.
- `vllm-mlx` translates `context_default` into a hard server cap; **`mlx-vlm` does not**
  (it uses the model's native max context and relies on pi compaction to govern effective
  window). Functionally fine on 128 GB; a future provider tweak could derive
  `--max-kv-size` from `context_default` for parity.

---

## Decisions

- `qwen36-27b-6bit` converted in place to `backend = mlx-vlm` + MTP drafter
  (`mlx-community/Qwen3.6-27B-MTP-5bit`, `server.draft_kind = mtp`). tool-calling +
  headless pi verified.
- `qwen36-35b-4bit` (MoE) **unchanged** — stays on `vllm-mlx`.
- Gemma 4 MTP **not adopted**; MTP spike drafters deleted. Revisit when `mlx-vlm` Gemma
  server-MTP matures.
- Gemma 4 **QAT-4bit adopted** (separately from MTP, purely for quality): both
  `gemma4-26b-a4b-it-mlx-4bit` and `gemma4-31b-it-mlx-4bit` repointed from Unsloth UD-4bit
  to `mlx-community/gemma-4-{26B-A4B,31B}-it-qat-4bit`, still on `vllm-mlx`. Behavioral
  eval (9 verifiable coding/instruction tasks) was **9/9 == 9/9** (QAT == UD, no
  regression); both load + respond on vllm-mlx. Same size/speed; QAT's trained-in
  low-bit fidelity is the upside on margins the short eval can't probe. **Note:** the
  reasoning/thinking config was unchanged, so any verbose-thinking behavior is Gemma-4 +
  `reasoning-parser gemma4`, not a QAT regression.

## Open threads

1. **Gemma decode gains *now*:** llama.cpp speculative decoding via the existing `gguf`
   backend (small Gemma draft) — sidesteps `mlx-vlm` server-MTP immaturity.
2. **Re-test Gemma + MoE server-MTP** when `mlx-vlm` ships the "Improve Gemma4 MTP server
   batching" follow-ups (#1166) or when `mlx-lm` native MTP (#990) merges.
3. **pi headless permissions:** tool-calls round-trip, but pi's "medium" permission level
   blocks shell execution in headless runs — bump it for fully autonomous delegated runs.
4. ~~Gemma QAT as a quality upgrade~~ — **done** (adopted QAT-4bit for both Gemma entries;
   see Decisions). A deeper perplexity/benchmark eval could still quantify QAT's margin
   over UD beyond the saturated behavioral tasks, if it ever matters.

## How to reproduce a backend/MTP comparison

```bash
# generate path (acceptance + raw decode speed):
mlx_vlm.generate --model <TARGET> --max-tokens 300 --prompt "<P>"            # off
mlx_vlm.generate --model <TARGET> --draft-model <DRAFTER> --draft-kind mtp \
                 --max-tokens 300 --prompt "<P>"                            # on (prints acceptance)

# server path (what harnesses use) — measure wall tok/s on a fixed 400-token request:
mlx_vlm.server --model <TARGET> [--draft-model <DRAFTER> --draft-kind mtp] --port 8899
#   then POST /v1/chat/completions and divide completion_tokens by wall time.

# Always compare against the current backend too:
turbo serve <alias>   # vllm-mlx baseline
```
