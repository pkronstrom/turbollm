"""Interactive arrow-key picker for `turbo serve`.

Uses raw tty + ANSI escapes — no extra dependencies. Falls back to a
numbered prompt when stdin/stdout isn't a TTY (e.g. piped input, CI).

Keys:
  ↑ / ↓ / k / j     move model selection
  ← / → / h / l     decrement / increment context size
  t                 toggle reasoning (thinking) mode (vllm-mlx / gguf only —
                    hidden for backends with no such knob)
  ⏎ / space         confirm
  q / esc / ^C      cancel

Returns an (alias, model_dict) tuple, or None if cancelled. All propagation
happens directly on `model_dict['server']` (context + thinking-mode keys,
translated per backend — see `context_override_for` and the reasoning block
in `pick()`).
"""

from __future__ import annotations

import select
import sys
import termios
import tty
from dataclasses import dataclass
from typing import Iterable

# Backends whose provider actually reads a thinking-mode server key.
# vllm-mlx -> server.default_chat_template_kwargs.enable_thinking
# gguf     -> server.enable_thinking
# Everything else (mlx-vlm, omlx, mlx-audio) has no such mechanism — the
# toggle would be a no-op, so the picker hides it entirely instead of
# showing a control that does nothing.
_THINKING_TOGGLE_BACKENDS = ("vllm-mlx", "gguf")


def _supports_thinking_toggle(backend: str) -> bool:
    return backend in _THINKING_TOGGLE_BACKENDS


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
    show_thinking_toggle: bool  # backend actually has a thinking-mode knob


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
    if backend == "omlx":
        # providers/omlx.py never reads a context/max_tokens key — the model
        # serves whatever its single configured context is. Collapse to one
        # fixed option so the picker doesn't offer a selector that does
        # nothing (see context_override_for).
        options = (default_ctx_k,)
    # Land on the model's declared default; fall back to the largest option
    # below it, then the smallest available.
    if default_ctx_k in options:
        ctx_idx = options.index(default_ctx_k)
    else:
        candidates = [i for i, k in enumerate(options) if k <= default_ctx_k]
        ctx_idx = candidates[-1] if candidates else 0
    size_gb = m.get("size_gb")
    size_str = f"{size_gb:g}GB" if isinstance(size_gb, (int, float)) else "  ?"

    can_reason = bool(m.get("can_reason", False))
    srv = m.get("server", {})
    # Initialize from the configured default so a Gemma 4 entry with
    # enable_thinking = false lands on think:off, instead of always
    # defaulting to on and silently flipping the model's shipped config the
    # moment the user confirms without touching `t`.
    if backend == "gguf":
        thinking_default_on = srv.get("enable_thinking", True) is not False
    else:
        thinking_default_on = (
            srv.get("default_chat_template_kwargs", {}).get("enable_thinking", True) is not False
        )
    reasoning_on = can_reason and thinking_default_on

    return PickerEntry(
        alias=alias,
        model=m,
        name=str(m.get("name", alias)),
        backend=backend,
        size_str=size_str,
        max_ctx_k=max_ctx_k,
        ctx_options_k=options,
        ctx_idx=ctx_idx,
        can_reason=can_reason,
        reasoning_on=reasoning_on,
        show_thinking_toggle=can_reason and _supports_thinking_toggle(backend),
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
        if e.show_thinking_toggle:
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
    # Possible CSI sequence — but a bare ESC (the documented cancel key)
    # produces the same first byte with nothing following. Peek with a short
    # timeout instead of blocking-reading the next byte, so bare ESC returns
    # immediately instead of hanging until another key arrives.
    ready, _, _ = select.select([sys.stdin], [], [], 0.05)
    if not ready:
        return "esc"
    ch2 = sys.stdin.read(1)
    if ch2 != "[":
        return "esc"
    ch3 = sys.stdin.read(1)
    return {
        "A": "up", "B": "down", "C": "right", "D": "left",
    }.get(ch3, f"esc[{ch3}")


def _interactive_pick(entries: list[PickerEntry]) -> PickerEntry | None:
    """Run the TTY picker. Returns the chosen entry, or None on cancel."""
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
                return entries[sel]
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
                if e.show_thinking_toggle:
                    e.reasoning_on = not e.reasoning_on
            # Other keys: ignore.
    finally:
        sys.stdout.write(_show_cursor())
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def _fallback_numbered(entries: list[PickerEntry]) -> PickerEntry | None:
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
    return entries[idx]


def context_override_for(model: dict, ctx_tokens: int) -> dict:
    """Return the [server] keys that should be overridden to apply ctx_tokens
    for this model's backend. Caller deep-merges into model['server'].

    omlx returns {} — providers/omlx.py never reads a context/max_tokens key
    (the model serves whatever its single configured context is), so there's
    nothing to override. `_build_entry` also collapses omlx's picker context
    selector to a single fixed value so the UI doesn't offer a control that
    would silently do nothing.
    """
    backend = model.get("backend", "vllm-mlx")
    overrides: dict = {}
    if backend == "gguf":
        overrides["context"] = ctx_tokens
    elif backend == "omlx":
        pass
    else:
        # vllm-mlx and mlx-vlm use max_tokens as the context ceiling.
        overrides["max_tokens"] = ctx_tokens
        if model.get("server", {}).get("max_request_tokens") is not None:
            overrides["max_request_tokens"] = ctx_tokens
    return overrides


def pick(
    candidates: Iterable[tuple[str, dict, str]],
) -> tuple[str, dict] | None:
    """Run the picker over (alias, model, backend) triples.

    Returns (alias, model_with_overrides_applied) on success or None if the
    user cancelled. The returned model has its [server] block deep-copied and
    mutated; original registry entries are not modified.
    """
    entries = [_build_entry(a, m, b) for a, m, b in candidates]
    if not entries:
        return None

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    entry = _interactive_pick(entries) if is_tty else _fallback_numbered(entries)
    if entry is None:
        return None

    ctx_tokens = entry.ctx_options_k[entry.ctx_idx] * 1024

    # Deep-copy mutable sub-blocks so we don't mutate the registry.
    new_model = dict(entry.model)
    new_model["server"] = {**entry.model.get("server", {})}
    new_model["opencode"] = {**entry.model.get("opencode", {})}
    new_model["pi"] = {**entry.model.get("pi", {})}
    new_model["server"].update(context_override_for(entry.model, ctx_tokens))

    # Propagate to harness-facing context fields so agents (opencode, pi) see
    # the same window as the server. Only override if a value exists on the
    # registry entry — don't invent fields. pi.context_window and
    # opencode.context_length are what the harnesses report to their agent.
    if "context_length" in entry.model.get("opencode", {}):
        new_model["opencode"]["context_length"] = ctx_tokens
    if "context_window" in entry.model.get("pi", {}):
        new_model["pi"]["context_window"] = ctx_tokens

    # Reasoning toggle: translate to the server key the backend actually
    # reads (gguf.py reads server.enable_thinking; vllm_mlx.py reads
    # server.default_chat_template_kwargs.enable_thinking). Backends with no
    # such mechanism (mlx-vlm, omlx, mlx-audio) never reach here — the `t`
    # control is hidden for them (show_thinking_toggle is False).
    if entry.show_thinking_toggle:
        if entry.backend == "gguf":
            if entry.reasoning_on:
                new_model["server"].pop("enable_thinking", None)
            else:
                new_model["server"]["enable_thinking"] = False
        else:
            ctk = {**new_model["server"].get("default_chat_template_kwargs", {})}
            if entry.reasoning_on:
                ctk.pop("enable_thinking", None)
            else:
                ctk["enable_thinking"] = False
            if ctk:
                new_model["server"]["default_chat_template_kwargs"] = ctk
            elif "default_chat_template_kwargs" in new_model["server"]:
                del new_model["server"]["default_chat_template_kwargs"]

    return entry.alias, new_model
