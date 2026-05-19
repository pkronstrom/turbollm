"""Workflow loading, validation, template expansion, and execution.

Workflows are TOML stanzas under `[workflows.*]` in models.toml. They are
HUD-renderable named actions with typed params. See
internal HUD design spec for the full schema.
"""
from __future__ import annotations

import contextlib as _contextlib
import datetime as _dt
import fcntl as _fcntl
import os as _os
import pathlib as _pathlib
import re as _re
import shutil as _shutil
import signal as _signal
import subprocess as _subprocess
import tempfile as _tempfile
import time as _time


class WorkflowError(Exception):
    """Raised on malformed workflow definitions."""


# Param `type` values that resolve at run time (HUD performs UI; CLI typically refuses).
ACQUIRED_TYPES = {"audio-recording", "screenshot-manual", "command", "screen-recording"}

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
    workflow_name: str | None = None,
    acquirer_bin: str | None = None,
    t0_ns: int | None = None,
) -> dict[str, str]:
    """Resolve each param's value, checking sources in priority order:
    1. `overrides` (typically CLI `--param` flags)
    2. `acquirer_bin` spawn (for acquired-type params not in overrides, when acquirer is available)
    3. `auto = "..."` template (expanded against params already resolved)
    4. `default_env = "VAR"` (when the env var is set and non-empty)
    5. `default = "..."`
    6. empty string

    Acquired params (audio-recording, screenshot-manual, command, screen-recording) are spawned
    via `turbo-acquirer` when `acquirer_bin` is provided and the param is not
    already in `overrides`. Without `acquirer_bin`, they raise WorkflowError.

    Background acquirers (mode != "primary") are started concurrently with the
    primary acquirer. When the primary exits, SIGTERM is sent to all background
    acquirers; their stdout is collected before this function returns.

    Configured params (string, text, enum, file, directory) without any source
    resolve to the empty string.

    Note: `auto` templates can only reference params that appear earlier in the
    param list (the resolution is sequential and only earlier results are in
    scope). Workflow authors should order params with this constraint in mind.
    """
    resolved: dict[str, str] = {}

    # Pre-flight: start background acquired params as async processes so they
    # run concurrently with the (blocking) primary acquirer.
    _background_procs: dict[str, tuple[_subprocess.Popen, dict]] = {}
    if acquirer_bin is not None:
        for p in params:
            name = p["name"]
            if name in overrides:
                continue
            ptype = p.get("type")
            if ptype not in ACQUIRED_TYPES:
                continue
            mode = p.get("mode", _default_mode(p))
            if mode != "background":
                continue
            # Explicitly background acquired param: launch immediately (non-blocking).
            argv = _acquirer_argv(acquirer_bin, p, workflow_name=workflow_name)
            env = dict(_os.environ)
            if workflow_id:
                env["TURBO_WORKFLOW_ID"] = workflow_id
            if t0_ns is not None:
                env["TURBO_T0_NS"] = str(t0_ns)
            proc = _subprocess.Popen(
                argv,
                stdout=_subprocess.PIPE,
                stderr=_subprocess.PIPE,
                env=env,
                text=True,
            )
            _background_procs[name] = (proc, p)

    # Main loop + post-flight wrapped in try/finally so background processes are
    # always terminated even when the primary raises (prevents leaked children).
    try:
        # Main loop: resolve params sequentially; skip backgrounds (handled below).
        for p in params:
            name = p["name"]
            ptype = p.get("type")

            if name in overrides:
                resolved[name] = overrides[name]
                continue

            if name in _background_procs:
                # Will be resolved after the primary exits.
                continue

            if ptype in ACQUIRED_TYPES:
                if acquirer_bin is not None:
                    resolved[name] = _spawn_acquirer(
                        acquirer_bin,
                        p,
                        workflow_id=workflow_id,
                        workflow_name=workflow_name,
                        t0_ns=t0_ns,
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

        # After primary(ies) have returned: SIGTERM all backgrounds and collect output.
        if _background_procs:
            for _name, (proc, _p) in _background_procs.items():
                try:
                    proc.send_signal(_signal.SIGTERM)
                except ProcessLookupError:
                    pass

            for name, (proc, p) in _background_procs.items():
                stdout, stderr = proc.communicate()
                # Accept clean exit (0) or killed by SIGTERM (-signal.SIGTERM on Unix).
                if proc.returncode not in (0, -_signal.SIGTERM):
                    raise WorkflowError(
                        f"background acquirer exited {proc.returncode} for param '{p['name']}': "
                        f"{stderr.strip()}"
                    )
                value = stdout.strip()
                if not value:
                    raise WorkflowError(
                        f"background acquirer returned empty output for param '{p['name']}'"
                    )
                resolved[name] = value
    finally:
        # Terminate any background procs that are still alive.  On the normal
        # success path the post-flight loop already called communicate() so
        # proc.poll() will be non-None and this block is a no-op.  On an
        # exception path this tears down children before the error propagates.
        for _name, (proc, _p) in _background_procs.items():
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    proc.kill()

    return resolved


# HUD UserDefaults suite + key convention for per-(workflow, param) sticky
# overrides written by the scope / input-device submenus in MenuBuilder.swift.
# These bypass the `workflow.<wf>.param.<name>` convention used by
# `turbo workflows config`; the HUD writes them directly as
# `<wf>.<name>.scope` / `<wf>.<name>.input_device`.
_HUD_DEFAULTS_SUITE = "com.turbollm.hud"


def _read_hud_override(workflow_name: str, param_name: str, suffix: str) -> str | None:
    """Read a HUD-side sticky override for an acquired param.

    Returns ``None`` if the key is not set, the suite is missing, or
    ``/usr/bin/defaults`` is unavailable (non-macOS environments).
    """
    key = f"{workflow_name}.{param_name}.{suffix}"
    try:
        result = _subprocess.run(
            ["/usr/bin/defaults", "read", _HUD_DEFAULTS_SUITE, key],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _default_screenshot_dir() -> str:
    """Default output directory for screenshot acquirers."""
    from pathlib import Path
    return str(Path.home() / ".turbollm" / "screenshots")


def _acquirer_argv(
    binary: str,
    param: dict,
    *,
    workflow_name: str | None = None,
) -> list[str]:
    """Build the turbo-acquirer argv for the given acquired param definition.

    Mapping:
    - ``audio-recording`` → ``record-audio [--scope <scope>] [--device-uid <uid>]``
    - ``screenshot-manual`` → ``screenshot --output-dir <dir>``
    - ``command`` → ``command --shell <acquire>``

    HUD sticky overrides (when ``workflow_name`` is given): the HUD's scope
    and input-device submenus write to UserDefaults keys ``<wf>.<param>.scope``
    and ``<wf>.<param>.input_device`` in suite ``com.turbollm.hud``. These
    win over the static ``param.scope`` / ``param.device_uid`` from the
    workflow definition, so a user's HUD selection is honoured whether the
    workflow is launched from the HUD or from the shell.
    """
    ptype = param.get("type")
    pname = param.get("name", "")
    if ptype == "audio-recording":
        argv = [binary, "record-audio"]
        scope = param.get("scope")
        uid = param.get("device_uid")
        if workflow_name:
            scope = _read_hud_override(workflow_name, pname, "scope") or scope
            uid = _read_hud_override(workflow_name, pname, "input_device") or uid
        if scope:
            argv += ["--scope", scope]
        if uid:
            argv += ["--device-uid", uid]
        return argv
    if ptype == "screenshot-manual":
        argv = [binary, "screenshot"]
        output_dir = param.get("output_dir") or _default_screenshot_dir()
        argv += ["--output-dir", output_dir]
        return argv
    if ptype == "command":
        acquire = param.get("acquire") or param.get("acquire_script", "")
        return [binary, "command", "--shell", acquire]
    if ptype == "screen-recording":
        out_dir = _tempfile.mkdtemp(prefix="turbo-session-")
        argv = [binary, "record-screen", "--output-dir", out_dir]
        scope = param.get("scope")
        threshold = param.get("keyframe_threshold")
        min_interval = param.get("min_interval_ms")
        max_kf = param.get("max_keyframes")
        if workflow_name:
            scope = _read_hud_override(workflow_name, pname, "scope") or scope
        if scope:
            argv += ["--scope", scope]
        if scope == "region" and workflow_name:
            region = _read_hud_override(workflow_name, pname, "region")
            if region:
                argv += ["--region", region]
        if threshold is not None:
            argv += ["--keyframe-threshold", str(threshold)]
        if min_interval is not None:
            argv += ["--min-interval-ms", str(min_interval)]
        if max_kf is not None:
            argv += ["--max-keyframes", str(max_kf)]
        return argv
    raise WorkflowError(f"unknown acquired param type '{ptype}'")


def _spawn_acquirer(
    binary: str,
    param: dict,
    *,
    workflow_id: str | None,
    workflow_name: str | None = None,
    t0_ns: int | None = None,
) -> str:
    """Spawn turbo-acquirer for the given param; return its stdout (stripped).

    Propagates SIGINT/SIGTERM to the child process. Raises WorkflowError on
    non-zero exit or empty output.
    """
    argv = _acquirer_argv(binary, param, workflow_name=workflow_name)
    env = dict(_os.environ)
    if workflow_id:
        env["TURBO_WORKFLOW_ID"] = workflow_id
    if t0_ns is not None:
        env["TURBO_T0_NS"] = str(t0_ns)

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


def _workflow_lock_path(name: str) -> _pathlib.Path:
    """Lockfile location for per-workflow concurrency control."""
    run_dir = _pathlib.Path.home() / ".turbollm" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize the workflow name minimally — slashes would otherwise create
    # nested dirs. The registry already rejects most exotic chars.
    safe = name.replace("/", "_").replace("\\", "_")
    return run_dir / f"{safe}.lock"


@_contextlib.contextmanager
def _workflow_lock(name: str):
    """Hold an exclusive non-blocking flock for the lifetime of a workflow run.

    Refusing to start a second instance of the same workflow protects shared
    resources (microphone, ScreenCaptureKit, vault output paths) from racing.
    The lock is kernel-managed: held for the lifetime of the holding process,
    released automatically on exit (including crash / SIGKILL).
    """
    path = _workflow_lock_path(name)
    fd = open(path, "w")
    try:
        try:
            _fcntl.flock(fd.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except BlockingIOError as exc:
            fd.close()
            raise WorkflowError(
                f"workflow '{name}' is already running. "
                "Stop the running instance first "
                "(HUD ⏹ Stop, Raycast 'Running Workflows' command, "
                f"or remove {path} after confirming no live process holds it)."
            ) from exc
        # Stamp our PID so workflow_status() can identify the holder without a
        # separate registry. We re-open read-only to read this; flock itself is
        # still the authority for "is it running".
        fd.write(str(_os.getpid()))
        fd.flush()
        yield
    finally:
        try:
            _fcntl.flock(fd.fileno(), _fcntl.LOCK_UN)
        except Exception:
            pass
        fd.close()


def workflow_status(name: str) -> dict:
    """Probe the per-workflow flock and report state without taking it.

    Returns `{"state": "idle"|"running", "pid": int|None}`. When the lock file
    exists but is unheld (last holder exited cleanly or crashed; the kernel
    releases the flock on any process exit), we report idle and `pid` is None.
    When the lock is held, we read the PID stamp that the holder wrote; if the
    stamp is missing or unparseable we still report running but with pid=None.
    """
    path = _workflow_lock_path(name)
    if not path.exists():
        return {"state": "idle", "pid": None}
    try:
        fd = open(path, "r")
    except OSError:
        return {"state": "idle", "pid": None}
    try:
        try:
            _fcntl.flock(fd.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            # Acquired → no one was holding it. Release immediately.
            _fcntl.flock(fd.fileno(), _fcntl.LOCK_UN)
            return {"state": "idle", "pid": None}
        except BlockingIOError:
            pid_str = fd.read().strip()
            pid = int(pid_str) if pid_str.isdigit() else None
            return {"state": "running", "pid": pid}
    finally:
        fd.close()


def stop_workflow(name: str, sig: int = _signal.SIGTERM) -> int | None:
    """Signal a running workflow's process group. Returns the signaled PID or None.

    Targets the process group (negative pid via killpg) so the python parent's
    /bin/sh subprocess — and any acquirers it spawned — go down too. Relies on
    run_workflow having started the child in a new session (see Popen
    `start_new_session=True`).
    """
    info = workflow_status(name)
    if info["state"] != "running" or not info["pid"]:
        return None
    pid = info["pid"]
    try:
        pgid = _os.getpgid(pid)
    except ProcessLookupError:
        return None
    try:
        _os.killpg(pgid, sig)
    except ProcessLookupError:
        return None
    return pid


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

    A per-workflow flock at ~/.turbollm/run/<name>.lock prevents two instances
    of the same workflow from running concurrently — raises WorkflowError
    immediately if the lock can't be acquired.
    """
    from turbollm import activity as _activity

    validate_workflow(name, wf)

    with _workflow_lock(name):
        # Capture monotonic origin before spawning any acquirer.
        # Both acquirers inherit TURBO_T0_NS so their manifests' start_offset_ms fields
        # can be aligned on a common timeline.
        t0_ns: int = _time.monotonic_ns()

        aid = _activity.start_activity(
            kind="workflow", label=f"Running {name}", icon="play", color="blue",
        )
        try:
            acquirer_bin = find_acquirer_bin()
            resolved = resolve_params(
                wf.get("params", []),
                overrides,
                workflow_id=aid,
                workflow_name=name,
                acquirer_bin=acquirer_bin,
                t0_ns=t0_ns,
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
            return _spawn_and_wait(argv, env)
        finally:
            _activity.clear_activity(aid)


def _spawn_and_wait(argv: list[str], env: dict[str, str]) -> int:
    """Run the workflow command in its own process group and propagate SIGTERM.

    `start_new_session=True` puts the child shell at the head of a fresh
    session/process group, so `stop_workflow` can signal the whole tree via
    `killpg`. We also install a SIGTERM handler in the parent that forwards
    the signal to the child group before waiting, so an external `kill
    <python-pid>` cleanly tears down the shell + acquirers instead of leaving
    them orphaned.
    """
    proc = _subprocess.Popen(argv, env=env, start_new_session=True)
    child_pgid = _os.getpgid(proc.pid)

    def _forward(signum, _frame):
        try:
            _os.killpg(child_pgid, signum)
        except ProcessLookupError:
            pass

    prev_term = _signal.signal(_signal.SIGTERM, _forward)
    prev_int = _signal.signal(_signal.SIGINT, _forward)
    try:
        return proc.wait()
    finally:
        _signal.signal(_signal.SIGTERM, prev_term)
        _signal.signal(_signal.SIGINT, prev_int)


def _os_environ_copy() -> dict[str, str]:
    return dict(_os.environ)
