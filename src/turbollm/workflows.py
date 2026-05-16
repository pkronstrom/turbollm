"""Workflow loading, validation, template expansion, and execution.

Workflows are TOML stanzas under `[workflows.*]` in models.toml. They are
HUD-renderable named actions with typed params. See
internal HUD design spec for the full schema.
"""
from __future__ import annotations

import datetime as _dt
import os as _os
import re as _re
import shutil as _shutil
import signal as _signal
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


def resolve_params(
    params: list[dict],
    overrides: dict[str, str],
    *,
    workflow_id: str | None = None,
    acquirer_bin: str | None = None,
) -> dict[str, str]:
    """Resolve each param's value, checking sources in priority order:
    1. `overrides` (typically CLI `--param` flags)
    2. `acquirer_bin` spawn (for acquired-type params not in overrides, when acquirer is available)
    3. `auto = "..."` template (expanded against params already resolved)
    4. `default_env = "VAR"` (when the env var is set and non-empty)
    5. `default = "..."`
    6. empty string

    Acquired params (audio-recording, screenshot-manual, command) are spawned
    via `turbo-acquirer` when `acquirer_bin` is provided and the param is not
    already in `overrides`. Without `acquirer_bin`, they raise WorkflowError.

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
            if acquirer_bin is not None:
                resolved[name] = _spawn_acquirer(
                    acquirer_bin, p, workflow_id=workflow_id
                )
                continue
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


def _acquirer_argv(binary: str, param: dict) -> list[str]:
    """Build the turbo-acquirer argv for the given acquired param definition.

    Mapping:
    - ``audio-recording`` → ``record-audio [--scope <scope>] [--device-uid <uid>]``
    - ``screenshot-manual`` → ``screenshot [--output-dir <dir>]``
    - ``command`` → ``command --shell <acquire>``
    """
    ptype = param.get("type")
    if ptype == "audio-recording":
        argv = [binary, "record-audio"]
        scope = param.get("scope")
        if scope:
            argv += ["--scope", scope]
        uid = param.get("device_uid")
        if uid:
            argv += ["--device-uid", uid]
        return argv
    if ptype == "screenshot-manual":
        argv = [binary, "screenshot"]
        output_dir = param.get("output_dir")
        if output_dir:
            argv += ["--output-dir", output_dir]
        return argv
    if ptype == "command":
        acquire = param.get("acquire") or param.get("acquire_script", "")
        return [binary, "command", "--shell", acquire]
    raise WorkflowError(f"unknown acquired param type '{ptype}'")


def _spawn_acquirer(
    binary: str,
    param: dict,
    *,
    workflow_id: str | None,
) -> str:
    """Spawn turbo-acquirer for the given param; return its stdout (stripped).

    Propagates SIGINT/SIGTERM to the child process. Raises WorkflowError on
    non-zero exit or empty output.
    """
    argv = _acquirer_argv(binary, param)
    env = dict(_os.environ)
    if workflow_id:
        env["TURBO_WORKFLOW_ID"] = workflow_id

    proc = _subprocess.Popen(
        argv,
        stdout=_subprocess.PIPE,
        stderr=_subprocess.PIPE,
        env=env,
        text=True,
    )

    def _forward_signal(signum, _frame):
        try:
            proc.send_signal(signum)
        except ProcessLookupError:
            pass

    old_sigint = _signal.signal(_signal.SIGINT, _forward_signal)
    old_sigterm = _signal.signal(_signal.SIGTERM, _forward_signal)
    try:
        stdout, stderr = proc.communicate()
    finally:
        _signal.signal(_signal.SIGINT, old_sigint)
        _signal.signal(_signal.SIGTERM, old_sigterm)

    if proc.returncode != 0:
        raise WorkflowError(
            f"turbo-acquirer exited {proc.returncode} for param '{param['name']}': "
            f"{stderr.strip()}"
        )
    value = stdout.strip()
    if not value:
        raise WorkflowError(
            f"turbo-acquirer returned empty output for param '{param['name']}'"
        )
    return value


def find_acquirer_bin() -> str | None:
    """Return the path to turbo-acquirer, or None if not found.

    Resolution order:
    1. ``$TURBO_ACQUIRER_BIN`` env var (test seam / CI override)
    2. ``shutil.which("turbo-acquirer")`` (PATH, including ~/.local/bin)
    """
    override = _os.environ.get("TURBO_ACQUIRER_BIN")
    if override:
        return override
    return _shutil.which("turbo-acquirer")


def run_workflow(
    name: str,
    wf: dict,
    *,
    overrides: dict[str, str],
    registry: dict,
) -> int:
    """Resolve params, expand command template, run as a subprocess.

    For acquired params (audio-recording, screenshot-manual, command), spawns
    the turbo-acquirer binary if it is available on PATH (or TURBO_ACQUIRER_BIN
    is set). Passes TURBO_WORKFLOW_ID so the acquirer can link its activity file
    to this workflow.

    Writes an activity file for the duration. Returns the subprocess exit code.
    """
    from turbollm import activity as _activity

    validate_workflow(name, wf)

    aid = _activity.start_activity(
        kind="workflow", label=f"Running {name}", icon="play", color="blue",
    )
    try:
        acquirer_bin = find_acquirer_bin()
        resolved = resolve_params(
            wf.get("params", []),
            overrides,
            workflow_id=aid,
            acquirer_bin=acquirer_bin,
        )

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
