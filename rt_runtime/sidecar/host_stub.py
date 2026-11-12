#!/usr/bin/env python3
"""Tier-0 HoudiniRT sidecar host stub.

Runs on CPython 3.14, imports the standard library only, and imports **no**
HoudiniRT package. Its whole job is process identity plus a heartbeat:

1. write ``status.json`` with ``state="starting"``;
2. write ``state="ready"`` and bump the heartbeat counter;
3. rewrite the heartbeat every ``--heartbeat-interval`` seconds;
4. exit cleanly (state ``stopped``, exit 0) when the stop file appears, on
   SIGTERM/SIGINT, or when the recorded parent process dies.

With ``--serve`` the S2 control surface replaces this heartbeat: the transport
is the only status writer and answers ``/health``, ``/status``, ``/shutdown``
and -- when ``--entry-point`` yields a callable -- the turn routes
(``/turn``, ``/turn/<id>``, ``/cancel``). Without ``--serve`` the stub serves
nothing and keeps its own heartbeat.

Turn-related environment (all optional):

* ``RT_SIDECAR_LIVE_MODEL=1`` -- call the real endpoint instead of the guarded
  offline client.
* ``RT_SIDECAR_TURN_TIMEOUT`` -- soft deadline per turn, seconds (default 120).
* ``RT_SIDECAR_STARTUP_TURN=1`` -- run the one-shot payload probe on a
  *serving* host. Off by default: the first real turn proves the path, and a
  probe on every panel-driven launch spends tokens and delays ready.

Run shape (the supervisor builds this; ``-I`` matters)::

    python.exe -I -X utf8 -u host_stub.py --state-dir <dir> --instance default

``-I`` is isolated mode: no ``PYTHONPATH``, no user site-packages, and the
script's own directory is not prepended to ``sys.path``. That is deliberate:
it makes a planted ``json.py`` next to the script or in the working directory
unable to shadow the standard library.

Note on Windows: ``TerminateProcess`` does not run signal handlers, so the
SIGTERM handler below is POSIX parity, not the Windows stop path. On Windows
the graceful path is the stop file (and, later, ``POST /shutdown`` from S2).
"""

from __future__ import annotations

import argparse
import ctypes
import importlib
import inspect
import json
import os
import signal
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_ERROR = 3
EXIT_BAD_ENV = 4

STATUS_FILENAME = "status.json"
STOP_FILENAME = "stop.flag"

_STOP_REQUESTED = False
_LOG_HANDLE: Any = None  # opened by main() from --log-file


# ---------------------------------------------------------------------------
# Small helpers (intentionally duplicated from lifecycle.py: the host may not
# import it, because -I keeps the script directory off sys.path. Keep the two
# copies in sync; they are ~30 lines.)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _write_json_atomic(path: Path, payload: dict[str, Any], *, durable: bool) -> None:
    """Write JSON through a temp file + os.replace, retrying Windows sharing errors."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            if durable:
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        last_error: OSError | None = None
        for _ in range(40):
            try:
                os.replace(temp, path)
                return
            except PermissionError as exc:  # a reader holds the target open
                last_error = exc
                time.sleep(0.01)
            except OSError as exc:  # e.g. a virus scanner holding the target
                last_error = exc
                time.sleep(0.01)
        raise last_error or OSError(f"could not replace {path}")
    except BaseException:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        raise


def _log(message: str) -> None:
    """One line to the log file, mirrored to stderr when a console exists.

    The file write is primary because ``pythonw.exe`` (the post-repair
    launcher) may hand the process no usable stderr at all.
    """
    line = f"{_now_iso()} [{os.getpid()}] {message}\n"
    if _LOG_HANDLE is not None:
        try:
            _LOG_HANDLE.write(line)
            _LOG_HANDLE.flush()
        except Exception:
            pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(line)
            sys.stderr.flush()
    except Exception:  # a host must never die because logging failed
        pass


def pid_alive(pid: int | None) -> bool:
    """True when *pid* names a live process.

    Never use ``os.kill(pid, 0)`` on Windows: CPython maps any non-console
    signal to ``TerminateProcess(handle, sig)``, so a "probe" kills the target.
    """
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.GetExitCodeProcess.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    )
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid)
    )
    if not handle:
        # Access denied means the process exists but is not ours to inspect.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _handle_signal(signum: int, frame: Any) -> None:  # noqa: ARG001 - signal API
    global _STOP_REQUESTED
    _STOP_REQUESTED = True
    _log(f"signal {signum} received; stopping")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rt_sidecar_host",
        description="Tier-0 HoudiniRT sidecar host stub (heartbeat only).",
    )
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--instance", default="default")
    parser.add_argument("--status-name", default=STATUS_FILENAME)
    parser.add_argument("--stop-file", default=None)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--heartbeat-interval", type=float, default=1.0)
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--lock-nonce", default=None)
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="hand status.json to sidecar/transport.py and serve the control API",
    )
    parser.add_argument("--entry-point", default=None)
    parser.add_argument("--runtime-home", default=None)
    parser.add_argument("--site-packages", action="append", default=[])
    parser.add_argument(
        "--expect-python",
        default=None,
        help="Refuse to run unless the interpreter is this major.minor.",
    )
    return parser.parse_args(argv)


def _read_env_value(path: str | os.PathLike[str], name: str) -> str | None:
    """Read one NAME=value line from a local .env file. Never logs it."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _resolve_entry_point(spec: str) -> Any:
    """Import ``module.attr`` and return the callable."""
    module_name, _, attr = str(spec).rpartition(".")
    if not module_name or not attr:
        raise ValueError(
            f"entry point must be module.attr, got {spec!r}"
        )
    module = importlib.import_module(module_name)
    target = getattr(module, attr)
    if not callable(target):
        raise TypeError(f"entry point {spec!r} is not callable")
    return target


def _env_flag(name: str) -> bool:
    """True for the usual on spellings; absent or anything else is off."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _sidecar_prompt(context: dict[str, Any] | None) -> str:
    return str((context or {}).get("prompt") or "sidecar payload check")


@dataclass
class _TurnEntry:
    """What ``--entry-point`` resolved to, and the turn closure it yielded.

    ``closure`` -- when set -- has exactly the shape ``TurnLoop`` requires:
    ``fn(prompt, *, cancel_event=None, max_tokens=None)``. Building it once at
    startup is what keeps the model client, the sidecar-scoped policy file and
    ``SYNAPSE_MODEL_POLICY`` startup-scoped instead of per-turn work.
    """

    spec: str
    status: str  # "ready" | "needs-arguments" | "error"
    closure: Callable[..., Any] | None = None
    one_shot: bool = False
    required: str | None = None
    error: str | None = None


def _build_turn_entry(
    spec: str, context: dict[str, Any], log: Any = None
) -> _TurnEntry:
    """Resolve a payload entry point and adapt it to the turn-loop shape.

    Three shapes are understood, unchanged from the S1 probe:

    * no required argument -- called with no arguments;
    * one required argument -- called with the sidecar context dict;
    * ``(client, dispatcher, user_prompt)`` -- the named payload
      (``synapse.cognitive.agent_loop.run_turn``), given a guarded model client
      (no network) and a sidecar-scoped policy that approves exactly this model
      and endpoint, or the live endpoint when ``RT_SIDECAR_LIVE_MODEL=1``.

    Anything else is reported and *not* called: the host will not invent
    credentials, a client, or a Houdini dispatcher. A mis-shaped payload is
    recorded here and never kills the host.
    """
    log = log or (lambda message: None)
    try:
        target = _resolve_entry_point(spec)
        signature = inspect.signature(target)
        required = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Parameter.empty
            and parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        required_names = [parameter.name for parameter in required]

        if not required:

            def closure(
                prompt: str, *, cancel_event: Any = None, max_tokens: Any = None
            ) -> Any:
                return target()

            return _TurnEntry(spec=spec, status="ready", closure=closure, one_shot=True)

        if len(required) == 1:

            def closure(
                prompt: str, *, cancel_event: Any = None, max_tokens: Any = None
            ) -> Any:
                return target(context)

            return _TurnEntry(spec=spec, status="ready", closure=closure, one_shot=True)

        if required_names == ["client", "dispatcher", "user_prompt"]:
            # The named payload (synapse.cognitive.agent_loop.run_turn) needs a
            # model client, a dispatcher and a prompt. Build a guarded client
            # with a no-network transport and a sidecar-scoped policy that
            # approves exactly this model and endpoint. The turn is real
            # (the agent loop runs), but the model response is local.
            from synapse import model_access
            from synapse.cognitive.agent_loop import AgentTurnConfig
            from synapse.cognitive.dispatcher import Dispatcher

            model = str(AgentTurnConfig().model)
            base = str(
                os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
            ).rstrip("/")
            endpoint = base + "/v1/messages"
            live = _env_flag("RT_SIDECAR_LIVE_MODEL")
            log(f"entry point probe mode: {'live' if live else 'offline'}")
            state_dir = Path(str(context.get("state_dir") or "."))
            policy_path = state_dir / "model_access.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "mode": "ask",
                        "approved_models": [
                            {
                                "provider": "claude",
                                "model": model,
                                "endpoint": endpoint,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.environ["SYNAPSE_MODEL_POLICY"] = os.fspath(policy_path)

            if live:
                api_key = os.environ.get("ANTHROPIC_API_KEY") or _read_env_value(
                    os.environ.get("RT_SIDECAR_ANTHROPIC_ENV_FILE")
                    or str(Path.home() / "SYNAPSE" / ".env"),
                    "ANTHROPIC_API_KEY",
                )
                if not api_key:
                    raise RuntimeError(
                        "RT_SIDECAR_LIVE_MODEL is set but ANTHROPIC_API_KEY is not"
                    )
                client = model_access.make_anthropic_client(
                    api_key,
                    base_url=base,
                )
            else:
                http = model_access.sdk_http_module()

                def _probe_response(request: Any) -> Any:  # noqa: ARG001
                    return http.Response(
                        200,
                        json={
                            "id": "msg_sidecar_probe",
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [
                                {"type": "text", "text": "sidecar payload check ok"}
                            ],
                            "stop_reason": "end_turn",
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                        },
                    )

                client = model_access.make_anthropic_client(
                    "offline-sidecar-probe",
                    transport=http.MockTransport(_probe_response),
                )

            default_max_tokens = int(os.environ.get("RT_SIDECAR_MAX_TOKENS", "4096"))
            accepts_config = "config" in signature.parameters
            accepts_cancel = "cancel_event" in signature.parameters

            def closure(
                prompt: str, *, cancel_event: Any = None, max_tokens: Any = None
            ) -> Any:
                """One turn: fresh dispatcher and config, shared guarded client.

                A fresh ``Dispatcher`` per turn is cheap and removes the only
                shared-state risk if a quarantined worker overlaps a new turn.
                ``cancel_event`` is the payload's existing cooperative hook.
                """
                turn_config = AgentTurnConfig(
                    max_tokens=int(max_tokens or default_max_tokens),
                    system="Reply with one short sentence.",
                )
                kwargs: dict[str, Any] = {}
                if accepts_config:
                    kwargs["config"] = turn_config
                if accepts_cancel:
                    kwargs["cancel_event"] = cancel_event
                return target(
                    client,
                    Dispatcher(is_testing=True),
                    prompt,
                    **kwargs,
                )

            return _TurnEntry(spec=spec, status="ready", closure=closure, one_shot=True)

        names = ", ".join(required_names)
        log(f"entry point {spec} needs arguments ({names}); not invoked")
        return _TurnEntry(
            spec=spec, status="needs-arguments", required=names, one_shot=False
        )
    except BaseException as exc:  # noqa: BLE001 - a payload must not kill the host
        log(f"entry point {spec} failed: {type(exc).__name__}: {exc}")
        return _TurnEntry(
            spec=spec,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            one_shot=False,
        )


def _invoke_once(
    entry: _TurnEntry, prompt: str, log: Any = None
) -> dict[str, Any]:
    """Run one probe turn through the built closure and describe what happened."""
    log = log or (lambda message: None)
    if entry.status == "needs-arguments":
        return {"status": "needs-arguments", "required": entry.required}
    if entry.closure is None:
        return {"status": "error", "error": entry.error or entry.status}
    try:
        result = entry.closure(prompt)
        result_status = getattr(result, "status", None)
        result_error = getattr(result, "error", None)
        log(
            f"entry point {entry.spec} invoked ({type(result).__name__}"
            + (f": {result_status}" if result_status else "")
            + ")"
        )
        if result_error:
            log(f"entry point {entry.spec} result error: {str(result_error)[:300]}")
        return {
            "status": "called",
            "result": repr(result)[:500],
            "result_status": None if result_status is None else str(result_status),
            "result_error": None if not result_error else str(result_error)[:300],
        }
    except BaseException as exc:  # noqa: BLE001 - a payload must not kill the host
        log(f"entry point {entry.spec} failed: {type(exc).__name__}: {exc}")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _invoke_entry_point(spec: str, context: dict[str, Any],
                        log: Any = None) -> dict[str, Any]:
    """Build the turn entry and run today's one-shot probe (S1 behaviour).

    Kept as the single callable the S1 tests drive. A no-argument callable is
    called with no arguments, a one-argument callable receives the sidecar
    context dict, and the named agent-turn payload gets built doubles.
    """
    entry = _build_turn_entry(spec, context, log)
    return _invoke_once(entry, _sidecar_prompt(context), log)


def _base_record(args: argparse.Namespace, started_mono: float) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "instance": str(args.instance),
        "writer": "host",
        "pid": os.getpid(),
        "parent_pid": args.parent_pid,
        "state": "starting",
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "python_implementation": sys.implementation.name,
        "sys_prefix": sys.prefix,
        "sys_executable": sys.executable,
        "runtime_home": args.runtime_home,
        "site_packages": list(args.site_packages),
        "lock_nonce": args.lock_nonce,
        "entry_point": args.entry_point,
        "started_at": _now_iso(),
        "started_mono": started_mono,
        "ready_at": None,
        "heartbeat_at": None,
        "heartbeat_mono": None,
        "heartbeat_seq": 0,
        "port": None,
        "requested_port": args.port,
        "token_file": args.token_file,
        "last_error": None,
        "stop_reason": None,
        "stopped_at": None,
        "exit": None,
    }


def main(argv: list[str] | None = None) -> int:
    global _LOG_HANDLE

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    state_dir = Path(args.state_dir)
    status_path = state_dir / args.status_name
    stop_path = Path(args.stop_file) if args.stop_file else state_dir / STOP_FILENAME

    if args.log_file:
        try:
            Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
            _LOG_HANDLE = open(args.log_file, "a", encoding="utf-8", newline="\n")
        except OSError:
            _LOG_HANDLE = None  # stderr, if any, is the only remaining channel

    if args.heartbeat_interval <= 0:
        _log("invalid --heartbeat-interval; must be > 0")
        return EXIT_BAD_ENV

    if args.expect_python:
        running = "%d.%d" % sys.version_info[:2]
        if running != str(args.expect_python):
            _log(f"wrong interpreter: need {args.expect_python}, running {running}")
            record = _base_record(args, time.monotonic())
            record["state"] = "error"
            record["last_error"] = (
                f"wrong interpreter: need {args.expect_python}, running {running}"
            )
            record["exit"] = "bad-env"
            try:
                _write_json_atomic(status_path, record, durable=True)
            except OSError:
                pass
            return EXIT_BAD_ENV

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _handle_signal)
        except (ValueError, OSError):
            pass

    started_mono = time.monotonic()
    record = _base_record(args, started_mono)

    try:
        _write_json_atomic(status_path, record, durable=True)
    except OSError as exc:
        _log(f"cannot write status file {status_path}: {exc!r}")
        return EXIT_ERROR

    _log(
        f"host starting instance={args.instance} python={record['python_version']} "
        f"prefix={record['sys_prefix']} state_dir={state_dir}"
    )

    # ---- Tier 0 payload: nothing to import. ---------------------------------
    # Tier 1+ would append --site-packages entries AFTER the base interpreter's
    # site-packages are already on sys.path, so the base's complete cp314
    # binaries win over the runtime tree's known-missing ones. Never prepend.

    interval = float(args.heartbeat_interval)
    sleep_for = min(0.25, max(0.02, interval / 2.0))
    stop_reason = "stop-file"

    # ---- SERVE: hand the status document to the control surface ------------
    # With --serve the transport becomes the ONLY status writer (S2 contract,
    # "one writer"). The stub never starts its own heartbeat loop, and the
    # transport binds before it publishes state="ready", so a reader that sees
    # ready can connect. Without --serve this whole block is skipped and the
    # stub keeps its own heartbeat: Tier 0, serves nothing.
    server = None
    turn_loop = None
    shutdown_event = threading.Event()

    # One closure drives both the one-shot probe and, with --serve, the turn
    # loop. Resolution failures are logged and carried; they never stop the
    # host from publishing /health and /status.
    def _entry_context() -> dict[str, Any]:
        return {
            "instance": args.instance,
            "pid": os.getpid(),
            "state_dir": os.fspath(state_dir),
            "status_path": os.fspath(status_path),
            "runtime_home": args.runtime_home,
            "site_packages": list(args.site_packages),
            "python_version": record["python_version"],
            "sys_prefix": record["sys_prefix"],
        }

    context: dict[str, Any] | None = None
    entry: _TurnEntry | None = None

    if args.serve:
        try:
            # -I keeps the script directory off sys.path, so the sibling
            # transport module is added explicitly. Append, never prepend.
            here = os.fspath(Path(__file__).resolve().parent)
            if here not in sys.path:
                sys.path.append(here)
            import transport  # the sidecar's own module, never an RT package

            # A serving host must resolve the payload before it can build the
            # turn loop; this is the only ordering change --serve introduces.
            if args.entry_point:
                context = _entry_context()
                entry = _build_turn_entry(args.entry_point, context, _log)

            if not args.token_file:
                raise RuntimeError("--serve requires --token-file")
            token = transport.read_token_file(args.token_file)
            requested_port = (
                args.port
                if args.port is not None
                else int(os.environ.get("RT_SIDECAR_PORT") or 0)
            )
            extra_status = {
                key: record[key]
                for key in (
                    "instance",
                    "parent_pid",
                    "lock_nonce",
                    "runtime_home",
                    "site_packages",
                    "sys_prefix",
                    "sys_executable",
                    "started_at",
                    "started_mono",
                )
                if record.get(key) is not None
            }

            if entry is not None and entry.closure is not None:
                # The turn loop lives next to this host and imports no payload
                # module, so importing it here keeps the Tier 0 graph intact.
                import turn_loop as turn_loop_module

                try:
                    turn_timeout = float(
                        os.environ.get("RT_SIDECAR_TURN_TIMEOUT") or 120.0
                    )
                except ValueError:
                    turn_timeout = 120.0
                if turn_timeout <= 0:
                    turn_timeout = 120.0
                turn_loop = turn_loop_module.TurnLoop(
                    entry.closure, timeout_s=turn_timeout
                )
                _log(f"turn loop ready (timeout {turn_timeout:.0f}s)")

            server = transport.ControlServer(
                token=token,
                host="127.0.0.1",
                port=requested_port,
                status_path=status_path,
                token_file=args.token_file,
                on_shutdown=shutdown_event.set,
                heartbeat_interval=interval,
                entry_point=args.entry_point,
                turn_loop=turn_loop,
                extra_status=extra_status,
                log_sink=_log,
            )
            bound_port = server.start()
            _log(f"control listening on 127.0.0.1:{bound_port}")
        except BaseException as exc:  # noqa: BLE001 - record, never half-serve
            if turn_loop is not None:
                try:
                    turn_loop.shutdown(2.0)
                except BaseException:
                    pass
            if server is not None:
                try:
                    server.stop()
                except BaseException:
                    pass
            record["state"] = "error"
            record["last_error"] = f"control surface failed: {type(exc).__name__}: {exc}"
            record["exit"] = "serve-failed"
            record["stopped_at"] = _now_iso()
            try:
                _write_json_atomic(status_path, record, durable=True)
            except OSError:
                pass
            _log(f"state=error {record['last_error']}")
            return EXIT_ERROR

    if server is None:
        record["state"] = "ready"
        record["ready_at"] = _now_iso()
        record["heartbeat_mono"] = time.monotonic()
        record["heartbeat_at"] = _now_iso()
        record["heartbeat_seq"] = 1
        try:
            _write_json_atomic(status_path, record, durable=True)
        except OSError as exc:
            _log(f"cannot publish ready state: {exc!r}")
            return EXIT_ERROR
        _log("state=ready")

    # The startup probe is a compatibility check, not a service.
    # * Non-serving host: ready is published first, then the probe runs in a
    #   daemon thread -- exactly the S1 lifecycle. A slow payload import can
    #   never delay (or deny) the ready transition.
    # * Serving host: opt in with RT_SIDECAR_STARTUP_TURN=1. A serving launch
    #   already proves the payload loaded through /status, and the first real
    #   turn is the better end-to-end check; an unconditional probe would spend
    #   tokens on every panel-driven launch.
    if not args.serve and args.entry_point:
        context = _entry_context()
        entry = _build_turn_entry(args.entry_point, context, _log)

    run_probe = entry is not None and entry.one_shot and (
        not args.serve or _env_flag("RT_SIDECAR_STARTUP_TURN")
    )
    if run_probe and entry is not None:
        threading.Thread(
            target=_invoke_once,
            args=(entry, _sidecar_prompt(context), _log),
            name="rt-entry-point",
            daemon=True,
        ).start()

    last_heartbeat = time.monotonic()

    try:
        while True:
            if _STOP_REQUESTED:
                stop_reason = "signal"
                break
            if shutdown_event.is_set():
                stop_reason = "http-shutdown"
                break
            if stop_path.exists():
                stop_reason = "stop-file"
                break
            if args.parent_pid and not pid_alive(args.parent_pid):
                stop_reason = "parent-exit"
                break

            if server is None:
                now = time.monotonic()
                if now - last_heartbeat >= interval:
                    record["heartbeat_mono"] = now
                    record["heartbeat_at"] = _now_iso()
                    record["heartbeat_seq"] = int(record["heartbeat_seq"]) + 1
                    try:
                        _write_json_atomic(status_path, record, durable=False)
                    except OSError as exc:
                        # A transient sharing error is survivable; a persistent
                        # one shows up as a stale heartbeat and the supervisor acts.
                        _log(f"heartbeat write failed: {exc!r}")
                    last_heartbeat = now

            time.sleep(sleep_for)
    except BaseException as exc:  # noqa: BLE001 - a crash must be recorded
        if turn_loop is not None:
            try:
                turn_loop.shutdown(2.0)
            except BaseException:
                pass
        if server is not None:
            try:
                server.stop()
            except BaseException:
                pass
        record["state"] = "error"
        record["last_error"] = f"{type(exc).__name__}: {exc}"
        record["exit"] = "crash"
        record["stopped_at"] = _now_iso()
        try:
            _write_json_atomic(status_path, record, durable=True)
        except OSError:
            pass
        _log(f"state=error {record['last_error']}")
        return EXIT_ERROR

    # ---- Graceful shutdown --------------------------------------------------
    _log(f"stopping reason={stop_reason}")

    # Cancel a running turn and give it at most 2 s to stop. The worker is a
    # daemon thread, so a payload that ignores the event cannot delay exit.
    if turn_loop is not None:
        try:
            cleared = turn_loop.shutdown(2.0)
            if not cleared:
                _log(
                    "turn worker did not stop within 2.0s; it is a daemon and "
                    "will die with the process"
                )
        except BaseException as exc:  # noqa: BLE001
            _log(f"turn loop shutdown failed: {exc!r}")

    if server is not None:
        # The transport writes stopping -> stopped, closes the listener, and
        # records stop_reason (http-shutdown / local-stop / shutdown-error).
        try:
            server.stop()
        except BaseException as exc:  # noqa: BLE001
            _log(f"control surface stop failed: {exc!r}")
    else:
        record["state"] = "stopping"
        record["stop_reason"] = stop_reason
        try:
            _write_json_atomic(status_path, record, durable=True)
        except OSError:
            pass

        record["state"] = "stopped"
        record["exit"] = "graceful"
        record["stopped_at"] = _now_iso()
        # A stopped record must never look fresh to a heartbeat-age check.
        record["heartbeat_mono"] = None
        record["heartbeat_at"] = None
        record["heartbeat_seq"] = 0
        # The token is deleted by the supervisor after stop; do not name it.
        record["token_file"] = None
        record["port"] = None
        try:
            _write_json_atomic(status_path, record, durable=True)
        except OSError as exc:
            _log(f"cannot publish stopped state: {exc!r}")
            return EXIT_ERROR

    _log("state=stopped")

    try:
        if stop_path.exists():
            stop_path.unlink()
    except OSError:
        pass
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
