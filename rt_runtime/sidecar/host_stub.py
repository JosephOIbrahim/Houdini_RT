#!/usr/bin/env python3
"""Tier-0 HoudiniRT sidecar host stub.

Runs on CPython 3.14, imports the standard library only, and imports **no**
HoudiniRT package. Its whole job is process identity plus a heartbeat:

1. write ``status.json`` with ``state="starting"``;
2. write ``state="ready"`` and bump the heartbeat counter;
3. rewrite the heartbeat every ``--heartbeat-interval`` seconds;
4. exit cleanly (state ``stopped``, exit 0) when the stop file appears, on
   SIGTERM/SIGINT, or when the recorded parent process dies.

It serves nothing. The health endpoint and token check are contract S2 and
belong in the block marked ``SHUTDOWN HOOKS`` / ``SERVE``: adding them must not
change the lifecycle below.

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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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


class _NoopMessages:
    """Minimal ``anthropic`` messages client for a startup payload probe."""

    def create(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[],
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
            model=kwargs.get("model"),
        )


class _NoopClient:
    """A client shaped like ``anthropic.Anthropic`` with no network."""

    def __init__(self) -> None:
        self.messages = _NoopMessages()


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


def _invoke_entry_point(spec: str, context: dict[str, Any],
                        log: Any = None) -> dict[str, Any]:
    """Invoke a payload entry point safely.

    A no-argument callable is called with no arguments. A one-argument
    callable receives the sidecar context dict. Anything requiring more
    arguments is reported and *not* called: the host will not invent
    credentials, a client, or a Houdini dispatcher for a multistep agent turn.
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
            result = target()
        elif len(required) == 1:
            result = target(context)
        elif required_names == ["client", "dispatcher", "user_prompt"]:
            # The named payload (synapse.cognitive.agent_loop.run_turn) needs a
            # model client, a dispatcher and a prompt. Use a no-network client
            # and an empty test dispatcher for the startup probe; the guarded
            # SDK reports its own status instead of sending project content.
            from synapse.cognitive.dispatcher import Dispatcher

            prompt = str(context.get("prompt") or "sidecar payload check")
            result = target(_NoopClient(), Dispatcher(is_testing=True), prompt)
        else:
            names = ", ".join(required_names)
            log(f"entry point {spec} needs arguments ({names}); not invoked")
            return {"status": "needs-arguments", "required": names}
        result_status = getattr(result, "status", None)
        log(
            f"entry point {spec} invoked ({type(result).__name__}"
            + (f": {result_status}" if result_status else "")
            + ")"
        )
        return {
            "status": "called",
            "result": repr(result)[:500],
            "result_status": None if result_status is None else str(result_status),
        }
    except BaseException as exc:  # noqa: BLE001 - a payload must not kill the host
        log(f"entry point {spec} failed: {type(exc).__name__}: {exc}")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


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
    shutdown_event = threading.Event()
    if args.serve:
        try:
            # -I keeps the script directory off sys.path, so the sibling
            # transport module is added explicitly. Append, never prepend.
            here = os.fspath(Path(__file__).resolve().parent)
            if here not in sys.path:
                sys.path.append(here)
            import transport  # the sidecar's own module, never an RT package

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
            server = transport.ControlServer(
                token=token,
                host="127.0.0.1",
                port=requested_port,
                status_path=status_path,
                token_file=args.token_file,
                on_shutdown=shutdown_event.set,
                heartbeat_interval=interval,
                entry_point=args.entry_point,
                extra_status=extra_status,
                log_sink=_log,
            )
            bound_port = server.start()
            _log(f"control listening on 127.0.0.1:{bound_port}")
        except BaseException as exc:  # noqa: BLE001 - record, never half-serve
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

    if args.entry_point:
        context = {
            "instance": args.instance,
            "pid": os.getpid(),
            "state_dir": os.fspath(state_dir),
            "status_path": os.fspath(status_path),
            "runtime_home": args.runtime_home,
            "site_packages": list(args.site_packages),
            "python_version": record["python_version"],
            "sys_prefix": record["sys_prefix"],
        }
        threading.Thread(
            target=_invoke_entry_point,
            args=(args.entry_point, context, _log),
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
