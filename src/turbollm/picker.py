"""Interactive arrow-key picker for `turbo serve`.

Uses raw tty + ANSI escapes — no extra dependencies. Falls back to a
numbered prompt when stdin/stdout isn't a TTY (e.g. piped input, CI).

Keys:
  ↑ / ↓ / k / j     move model selection
  ← / → / h / l     decrement / increment context size
  t                 toggle reasoning (thinking) mode
  ⏎ / space         confirm
  q / esc / ^C      cancel

Returns a (alias, model_dict, overrides) tuple where overrides may contain:
  max_tokens          int (tokens) — applied to vllm-mlx / mtplx / mlx-vlm
  max_request_tokens  int — applied to vllm-mlx
  context             int — applied to gguf (llama-server --ctx-size)
  reasoning           bool — propagates to harness configs that respect it
"""

from __future__ import annotations

import sys
import termios
import tty
from dataclasses import dataclass
from typing import Iterable


# Candidate context sizes shown in the picker, in tokens. Filtered per model
# by the model's max_tokens (server cap). 64K is the default landing position.
CONTEXT_LADDER_K = (16, 32, 64, 96, 128, 192, 256)
DEFAULT_CONTEXT_K = 64


@dataclass
class PickerEntry:
    alias: str
    model: dict
    name: str
    backend: str
    size_str: str            # e.g. "27GB"
    max_ctx_k: int           # ceiling derived from server.max_tokens
    ctx_options_k: tuple[int, ...]
    ctx_idx: int             # current index into ctx_options_k
    can_reason: bool
    reasoning_on: bool


def _build_entry(alias: str, m: dict, backend: str) -> PickerEntry:
    # Read the unified context fields (with legacy fallback handled inside
    # the registry helpers).
    from turbollm.registry import context_default_tokens, context_max_tokens
    max_ctx_tokens = context_max_tokens(m)
    default_ctx_tokens = context_default_tokens(m)
    max_ctx_k = max(1, max_ctx_tokens // 1024)
    default_ctx_k = max(1, default_ctx_tokens // 1024)
    options = tuple(k for k in CONTEXT_LADDER_K if k <= max_ctx_k)
    if not options:
        options = (max_ctx_k,)
    # Land on the model's declared default; fall back to the largest option
    # below it, then the smallest available.
    if default_ctx_k in options:
        ctx_idx = options.index(default_ctx_k)
    else:
        candidates = [i for i, k in enumerate(options) if k <= default_ctx_k]
        ctx_idx = candidates[-1] if candidates else 0
    size_gb = m.get("size_gb")
    size_str = f"{size_gb:g}GB" if isinstance(size_gb, (int, float)) else "  ?"
    return PickerEntry(
        alias=alias,
        model=m,
        name=str(m.get("name", alias)),
        backend=backend,
        size_str=size_str,
        max_ctx_k=max_ctx_k,
        ctx_options_k=options,
        ctx_idx=ctx_idx,
        can_reason=bool(m.get("can_reason", False)),
        reasoning_on=bool(m.get("can_reason", False)),
    )


def _hide_cursor() -> str: return "\x1b[?25l"
def _show_cursor() -> str: return "\x1b[?25h"
def _clear_line() -> str: return "\x1b[2K\r"
def _move_up(n: int) -> str: return f"\x1b[{n}A" if n > 0 else ""


# ANSI styling — kept inline rather than importing rich so the picker can paint
# itself with cursor positioning that rich's Console doesn't help with.
def _dim(s: str) -> str: return f"\x1b[2m{s}\x1b[0m"
def _bold(s: str) -> str: return f"\x1b[1m{s}\x1b[0m"
def _cyan(s: str) -> str: return f"\x1b[36m{s}\x1b[0m"
def _green(s: str) -> str: return f"\x1b[32m{s}\x1b[0m"
def _yellow(s: str) -> str: return f"\x1b[33m{s}\x1b[0m"


def _render(entries: list[PickerEntry], sel: int, name_width: int) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(_bold("  Select a model:"))
    lines.append("")
    for i, e in enumerate(entries):
        marker = _cyan("▸") if i == sel else " "
        name = e.name.ljust(name_width)
        if i == sel:
            name = _bold(name)
        ctx_now = e.ctx_options_k[e.ctx_idx]
        has_prev = e.ctx_idx > 0
        has_next = e.ctx_idx < len(e.ctx_options_k) - 1
        left = _cyan("◀") if has_prev else _dim("·")
        right = _cyan("▶") if has_next else _dim("·")
        ctx_str = f"{left} {_bold(f'{ctx_now}K'):>4} {right}"
        if not has_prev and not has_next:
            ctx_str = _dim(f"  {ctx_now}K  ")
        reasoning_str = ""
        if e.can_reason:
            label = _green("think:on") if e.reasoning_on else _dim("think:off")
            reasoning_str = f"  {label}"
        meta = _dim(f"[{e.backend}] {e.size_str}  max {e.max_ctx_k}K")
        lines.append(f"  {marker} {name}  {ctx_str}{reasoning_str}  {meta}")
    lines.append("")
    lines.append(_dim("  ↑↓ model · ←→ context · t reasoning · ⏎ start · q quit"))
    return "\n".join(lines) + "\n"


def _read_key(fd: int) -> str:
    """Read a single key (or escape sequence) from raw-mode stdin."""
    ch = sys.stdin.read(1)
    if ch != "\x1b":
        return ch
    # Possible CSI sequence. Try to peek 2 more bytes.
    try:
        ch2 = sys.stdin.read(1)
    except Exception:
        return "esc"
    if ch2 != "[":
        return "esc"
    ch3 = sys.stdin.read(1)
    return {
        "A": "up", "B": "down", "C": "right", "D": "left",
    }.get(ch3, f"esc[{ch3}")


def _interactive_pick(entries: list[PickerEntry]) -> tuple[PickerEntry, bool] | None:
    """Run the TTY picker. Returns (entry, started) or None on cancel."""
    fd = sys.stdin.fileno()
    name_width = max(len(e.name) for e in entries)
    old_attrs = termios.tcgetattr(fd)
    sel = 0
    rendered_lines = 0
    sys.stdout.write(_hide_cursor())
    try:
        tty.setcbreak(fd)
        while True:
            frame = _render(entries, sel, name_width)
            # Repaint: move cursor back over previously drawn block and overwrite.
            if rendered_lines:
                sys.stdout.write(_move_up(rendered_lines))
            sys.stdout.write(frame)
            sys.stdout.flush()
            rendered_lines = frame.count("\n")

            key = _read_key(fd)
            if key in ("\r", "\n", " "):
                return entries[sel], True
            if key in ("q", "esc", "\x03"):  # q / esc / ^C
                return None
            if key in ("up", "k"):
                sel = (sel - 1) % len(entries)
            elif key in ("down", "j"):
                sel = (sel + 1) % len(entries)
            elif key in ("left", "h"):
                e = entries[sel]
                if e.ctx_idx > 0:
                    e.ctx_idx -= 1
            elif key in ("right", "l"):
                e = entries[sel]
                if e.ctx_idx < len(e.ctx_options_k) - 1:
                    e.ctx_idx += 1
            elif key == "t":
                e = entries[sel]
                if e.can_reason:
                    e.reasoning_on = not e.reasoning_on
            # Other keys: ignore.
    finally:
        sys.stdout.write(_show_cursor())
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def _fallback_numbered(entries: list[PickerEntry]) -> tuple[PickerEntry, bool] | None:
    """Non-TTY fallback: print numbered list, read integer choice."""
    print("\n  Select a model:\n")
    for i, e in enumerate(entries, 1):
        ctx_now = e.ctx_options_k[e.ctx_idx]
        print(f"  {i}) {e.name}  [{e.backend}] {e.size_str}  ctx {ctx_now}K (max {e.max_ctx_k}K)")
    print()
    try:
        raw = input("  Choice (number): ")
    except EOFError:
        return None
    try:
        idx = int(raw.strip()) - 1
    except ValueError:
        return None
    if not (0 <= idx < len(entries)):
        return None
    return entries[idx], True


def context_override_for(model: dict, ctx_tokens: int) -> dict:
    """Return the [server] keys that should be overridden to apply ctx_tokens
    for this model's backend. Caller deep-merges into model['server']."""
    backend = model.get("backend", "vllm-mlx")
    overrides: dict = {}
    if backend == "gguf":
        overrides["context"] = ctx_tokens
    else:
        # vllm-mlx, mlx-vlm, omlx all use max_tokens as the context ceiling.
        overrides["max_tokens"] = ctx_tokens
        if model.get("server", {}).get("max_request_tokens") is not None:
            overrides["max_request_tokens"] = ctx_tokens
    return overrides


def pick(
    candidates: Iterable[tuple[str, dict, str]],
) -> tuple[str, dict, dict] | None:
    """Run the picker over (alias, model, backend) triples.

    Returns (alias, model_with_overrides_applied, overrides) on success or
    None if the user cancelled. The returned model has its [server] block
    deep-copied and mutated; original registry entries are not modified.
    """
    entries = [_build_entry(a, m, b) for a, m, b in candidates]
    if not entries:
        return None

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    result = _interactive_pick(entries) if is_tty else _fallback_numbered(entries)
    if result is None:
        return None
    entry, _ = result

    ctx_tokens = entry.ctx_options_k[entry.ctx_idx] * 1024
    overrides = context_override_for(entry.model, ctx_tokens)
    if entry.can_reason:
        overrides["reasoning_on"] = entry.reasoning_on

    # Deep-copy mutable sub-blocks so we don't mutate the registry.
    new_model = dict(entry.model)
    new_model["server"] = {**entry.model.get("server", {})}
    new_model["opencode"] = {**entry.model.get("opencode", {})}
    new_model["pi"] = {**entry.model.get("pi", {})}
    for k, v in overrides.items():
        if k == "reasoning_on":
            continue
        new_model["server"][k] = v

    # Propagate to harness-facing context fields so agents (opencode, pi) see
    # the same window as the server. Only override if a value exists on the
    # registry entry — don't invent fields. pi.context_window and
    # opencode.context_length are what the harnesses report to their agent.
    if "context_length" in entry.model.get("opencode", {}):
        new_model["opencode"]["context_length"] = ctx_tokens
    if "context_window" in entry.model.get("pi", {}):
        new_model["pi"]["context_window"] = ctx_tokens

    # Reasoning toggle: wire to chat_template_kwargs.enable_thinking. This is
    # the Qwen3.6 / Gemma 4 lever for per-server thinking-mode default. Models
    # without this knob (e.g. backends that don't propagate chat_template_kwargs)
    # are unaffected — the toggle is best-effort.
    if entry.can_reason:
        ctk = {**new_model["server"].get("default_chat_template_kwargs", {})}
        if entry.reasoning_on:
            ctk.pop("enable_thinking", None)
        else:
            ctk["enable_thinking"] = False
        if ctk:
            new_model["server"]["default_chat_template_kwargs"] = ctk
        elif "default_chat_template_kwargs" in new_model["server"]:
            del new_model["server"]["default_chat_template_kwargs"]

    return entry.alias, new_model, overrides
