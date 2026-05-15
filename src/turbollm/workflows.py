"""Workflow loading, validation, template expansion, and execution.

Workflows are TOML stanzas under `[workflows.*]` in models.toml. They are
HUD-renderable named actions with typed params. See
internal HUD design spec for the full schema.
"""
from __future__ import annotations

import datetime as _dt
import os as _os
import re as _re
import subprocess as _subprocess


class WorkflowError(Exception):
    """Raised on malformed workflow definitions."""


# Param `type` values that resolve at run time (HUD performs UI; CLI typically refuses).
ACQUIRED_TYPES = {"audio-recording", "screenshot-manual", "command"}

# Param `type` values resolved ahead of time (configured by user in HUD or CLI).
CONFIGURED_TYPES = {"string", "text", "enum", "file", "directory"}

KNOWN_TYPES = ACQUIRED_TYPES | CONFIGURED_TYPES


def load_workflows(registry: dict) -> dict[str, dict]:
    """Return the `[workflows.*]` table from a loaded registry dict.

    Returns an empty dict if no workflows are configured. Performs no
    validation — callers should pass each entry through `validate_workflow`.
    """
    return registry.get("workflows", {})


def validate_workflow(name: str, wf: dict) -> None:
    """Raise WorkflowError if the workflow is malformed.

    Checks:
    - exactly one of `command` (inline shell) or `script` (named [scripts.*] reference) is set
    - every param declares a `name` and a known `type`
    - at most one acquired param has mode == "primary"
    - `command`-type acquired params declare `acquire` or `acquire_script`
    """
    has_command = bool(wf.get("command"))
    has_script = bool(wf.get("script"))
    if has_command == has_script:
        raise WorkflowError(
            f"workflow '{name}' must set exactly one of `command` or `script`"
        )

    params = wf.get("params", [])
    for p in params:
        if "name" not in p:
            raise WorkflowError(
                f"workflow '{name}' has a param missing the required `name` field"
            )
        if "type" not in p:
            raise WorkflowError(
                f"workflow '{name}' param '{p['name']}' is missing the required `type` field"
            )
        if p["type"] not in KNOWN_TYPES:
            raise WorkflowError(
                f"workflow '{name}' param '{p['name']}' has unknown type '{p['type']}' "
                f"(known: {sorted(KNOWN_TYPES)})"
            )

    primaries = [
        p for p in params
        if p["type"] in ACQUIRED_TYPES and p.get("mode", _default_mode(p)) == "primary"
    ]
    if len(primaries) > 1:
        raise WorkflowError(
            f"workflow '{name}' has {len(primaries)} primary acquired params; "
            "exactly one primary is allowed"
        )

    for p in params:
        if p["type"] == "command":
            if not (p.get("acquire") or p.get("acquire_script")):
                raise WorkflowError(
                    f"workflow '{name}' param '{p['name']}' is type=command "
                    "and must set `acquire` or `acquire_script`"
                )


def _default_mode(param: dict) -> str:
    """Default mode for an acquired param if not explicitly set."""
    t = param.get("type")
    if t == "screenshot-manual":
        return "trigger"
    return "primary"


_TEMPLATE_RE = _re.compile(r"\{\{([^}]+)\}\}")


def expand_template(template: str, params: dict[str, str]) -> str:
    """Expand `{{date:FMT}}`, `{{env:VAR}}`, and `{{param-name}}` tokens.

    Raises WorkflowError on `{{name}}` references that aren't in `params`.
    Missing env vars expand to the empty string.
    """
    def _replace(match: _re.Match) -> str:
        token = match.group(1).strip()
        if token.startswith("date:"):
            fmt = token[len("date:"):]
            return _dt.datetime.now().strftime(fmt)
        if token.startswith("env:"):
            var = token[len("env:"):]
            return _os.environ.get(var, "")
        if token in params:
            return str(params[token])
        raise WorkflowError(f"undefined param '{token}' in template")

    return _TEMPLATE_RE.sub(_replace, template)


def resolve_params(params: list[dict], overrides: dict[str, str]) -> dict[str, str]:
    """Resolve each param's value, checking sources in priority order:
    1. `overrides` (typically CLI `--param` flags)
    2. `auto = "..."` template (expanded against params already resolved)
    3. `default_env = "VAR"` (when the env var is set and non-empty)
    4. `default = "..."`
    5. empty string

    Acquired params (audio-recording, screenshot-manual, command) must be
    provided in `overrides` from the CLI — the CLI cannot perform acquisition.
    Configured params (string, text, enum, file, directory) without any source
    resolve to the empty string.

    Note: `auto` templates can only reference params that appear earlier in the
    param list (the resolution is sequential and only earlier results are in
    scope). Workflow authors should order params with this constraint in mind.
    """
    resolved: dict[str, str] = {}
    for p in params:
        name = p["name"]
        ptype = p.get("type")

        if name in overrides:
            resolved[name] = overrides[name]
            continue

        if ptype in ACQUIRED_TYPES:
            raise WorkflowError(
                f"acquired param '{name}' (type={ptype}) requires --param {name}=<value> "
                "when running from the CLI; the HUD performs acquisition natively"
            )

        if "auto" in p:
            resolved[name] = expand_template(p["auto"], params=resolved)
            continue

        env_var = p.get("default_env")
        if env_var and _os.environ.get(env_var):
            resolved[name] = _os.environ[env_var]
            continue

        resolved[name] = p.get("default", "")

    return resolved


def run_workflow(
    name: str,
    wf: dict,
    *,
    overrides: dict[str, str],
    registry: dict,
) -> int:
    """Resolve params, expand command template, run as a subprocess.

    Writes an activity file for the duration. Returns the subprocess exit code.
    """
    from turbollm import activity as _activity

    validate_workflow(name, wf)
    resolved = resolve_params(wf.get("params", []), overrides)

    # Pick the command string: inline or via script reference.
    if wf.get("command"):
        command = expand_template(wf["command"], resolved)
    else:
        script_name = wf["script"]
        scripts = registry.get("scripts", {})
        if script_name not in scripts:
            raise WorkflowError(
                f"workflow '{name}' references script '{script_name}' "
                "but no such [scripts.*] entry exists"
            )
        script_cmd = scripts[script_name].get("command", "")
        positional = [expand_template(a, resolved) for a in wf.get("args", [])]
        command = script_cmd  # the script's own template uses $1..$N
        # We will pass `positional` as positional args to /bin/sh below.

    env_overrides = {
        k: expand_template(v, resolved) for k, v in wf.get("env", {}).items()
    }

    aid = _activity.start_activity(
        kind="workflow", label=f"Running {name}", icon="play", color="blue",
    )
    try:
        env = {**_os_environ_copy(), **env_overrides}
        if wf.get("command"):
            argv = ["/bin/sh", "-c", command, name]
        else:
            argv = ["/bin/sh", "-c", command, name, *positional]
        return _subprocess.run(argv, env=env).returncode
    finally:
        _activity.clear_activity(aid)


def _os_environ_copy() -> dict[str, str]:
    return dict(_os.environ)
