"""Supervisor for the HoudiniRT sidecar process.

Pure Python, standard library only, no ``hou``, no ``rt_runtime`` import: this
module is importable from Houdini's 3.13, from a 3.12 test runner, and from the
3.14 sidecar host itself.

What it owns
------------
* the launch command (interpreter + host script);
* one lock file per instance, so a double toggle cannot start two hosts;
* ``status.json`` read/write (atomic, retried against Windows sharing errors);
* start / stop / probe / status, with a bounded wait for ``state="ready"``;
* a stop handshake: shutdown hook (S2 HTTP) -> stop file -> terminate;
* cleanup of stale locks, stale stop flags, and orphaned children.

ON means: the recorded PID is alive, ``status.json`` says ``ready``, and its
heartbeat is no older than ``stale_after`` (default 5 s).

Design notes that are load-bearing:

* Never call ``os.kill(pid, 0)`` on Windows. CPython maps any non-console
  signal to ``TerminateProcess(handle, sig)``; the "probe" would kill the
  target. ``pid_alive`` uses ``OpenProcess``/``GetExitCodeProcess`` instead.
* ``time.monotonic()`` on Windows is QPC since boot and therefore comparable
  across processes on this machine. That is why the heartbeat is stored as
  ``heartbeat_mono``; ``heartbeat_at`` is for humans only.
* ``os.replace`` can fail with ``PermissionError`` while another process has
  the target open. Windows opens files without ``FILE_SHARE_DELETE`` by
  default, so both write and read helpers retry.
* ``start()`` never leaves a child running. Every failure path terminates and
  reaps the child, releases the lock, and records the error in ``status.json``.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL",
    "DEFAULT_STALE_AFTER",
    "DEFAULT_START_TIMEOUT",
    "DEFAULT_STOP_GRACE",
    "LOG_FILENAME",
    "LOCK_FILENAME",
    "SCHEMA_VERSION",
    "STATUS_FILENAME",
    "STOP_FILENAME",
    "TOKEN_FILENAME",
    "LifecycleResult",
    "LockHeldError",
    "LockUnavailableError",
    "SidecarState",
    "SidecarSupervisor",
    "StatusReport",
    "default_launcher",
    "default_state_dir",
    "pid_alive",
    "terminate_pid",
]

SCHEMA_VERSION = 1

STATUS_FILENAME = "status.json"
LOCK_FILENAME = "sidecar.lock"
STOP_FILENAME = "stop.flag"
TOKEN_FILENAME = "token"
LOG_FILENAME = "sidecar.log"

DEFAULT_HEARTBEAT_INTERVAL = 1.0
DEFAULT_STALE_AFTER = 5.0
DEFAULT_START_TIMEOUT = 10.0
DEFAULT_STOP_GRACE = 10.0
TERMINATE_WAIT = 2.0
POLL_INTERVAL = 0.05
LOG_ROTATE_BYTES = 5 * 1024 * 1024

# A lock file younger than this is assumed to be mid-creation by another
# process (between ``os.open`` and ``os.write``) and is never unlinked while
# unreadable. See ``SidecarSupervisor._acquire_lock``.
LOCK_SETTLE_SECONDS = 1.0

# One lock per process, so two threads of the same process cannot both pass
# the probe and spawn a host. ``RLock`` because ``start()`` holds it across the
# acquire -> spawn -> record-host-pid sequence and the helpers re-enter it.
_PROCESS_LOCK = threading.RLock()


class LockHeldError(RuntimeError):
    """A live holder owns ``sidecar.lock``; starting again would be a second host."""

    def __init__(self, holder: dict[str, Any]) -> None:
        super().__init__("sidecar.lock is held by a live process")
        self.holder = holder


class LockUnavailableError(RuntimeError):
    """The lock could not be claimed confidently, so no host may be launched."""


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------


class SidecarState(str, Enum):
    """Supervisor-visible sidecar state.

    ``ABSENT`` and ``STOPPED`` are healthy off states. ``STALE`` means the
    process died without a clean stop and its files can be cleaned up.
    ``ERROR`` and ``UNRESPONSIVE`` need a human or a forced restart.
    """

    ABSENT = "absent"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    UNRESPONSIVE = "unresponsive"
    STALE = "stale"
    ERROR = "error"

    @property
    def running(self) -> bool:
        return self in (
            SidecarState.STARTING,
            SidecarState.READY,
            SidecarState.STOPPING,
        )

    @property
    def runtime_state(self) -> str:
        """Tri-state for a panel that also drives the in-process adapter.

        ``"on"`` / ``"off"`` / ``"error"``, matching ``RuntimeState`` values.
        A crash reads as ``"error"``, not ``"off"``, so the operator sees it.
        """
        if self in (SidecarState.READY, SidecarState.STARTING, SidecarState.STOPPING):
            return "on"
        if self in (SidecarState.ABSENT, SidecarState.STOPPED):
            return "off"
        return "error"

    @property
    def ok(self) -> bool:
        return self.runtime_state != "error"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


@dataclass
class LifecycleResult:
    """Outcome of start / stop / restart. Field-compatible with ActivationResult."""

    ok: bool = False
    state: SidecarState = SidecarState.ABSENT
    changed: bool = False
    pid: int | None = None
    port: int | None = None
    reasons: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def runtime_state(self) -> str:
        return self.state.runtime_state


@dataclass
class StatusReport:
    """Read-only snapshot: ``probe()`` never mutates anything."""

    state: SidecarState
    pid: int | None = None
    heartbeat_age: float | None = None
    detail: str = ""
    status: dict[str, Any] | None = None
    lock: dict[str, Any] | None = None
    log_tail: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.state.running

    @property
    def runtime_state(self) -> str:
        return self.state.runtime_state

    @property
    def port(self) -> int | None:
        """The bound control port, if the transport published one."""
        value = (self.status or {}).get("port")
        return value if isinstance(value, int) and value > 0 else None


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


_KERNEL32: Any = None


def _kernel32() -> Any:
    """Cached kernel32 binding with 64-bit-safe handle signatures."""
    global _KERNEL32
    if _KERNEL32 is None:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.GetExitCodeProcess.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        _KERNEL32 = kernel32
    return _KERNEL32


def pid_alive(pid: int | None) -> bool:
    """True when *pid* names a live process. Safe to call on Windows."""
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)

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

    import ctypes

    kernel32 = _kernel32()
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def terminate_pid(pid: int | None, *, wait: float = TERMINATE_WAIT) -> bool:
    """Force-kill *pid*, then wait up to *wait* seconds. True when it is gone.

    This is the fallback after the graceful stop window, not the normal path.
    The caller is responsible for verifying the PID still belongs to the
    sidecar (the status/lock nonce match) before calling this.
    """
    if not pid_alive(pid):
        return True
    pid = int(pid)  # type: ignore[arg-type]

    if os.name == "nt":
        kernel32 = _kernel32()
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, 0, pid)
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        else:
            # Last resort: taskkill also kills any children of the host.
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                    # taskkill is a console program; a GUI host (Houdini) would
                    # give it a console window that flashes and vanishes.
                    creationflags=(0x08000000 if os.name == "nt" else 0),  # CREATE_NO_WINDOW
                )
            except (OSError, subprocess.SubprocessError):
                pass
    else:
        import signal as _signal

        try:
            os.kill(pid, _signal.SIGTERM)
        except OSError:
            pass

    deadline = time.monotonic() + max(0.0, wait)
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.02)
    return not pid_alive(pid)


def default_launcher(python_exe: Any | None = None) -> list[str]:
    """The safe launcher prefix: isolated, UTF-8, unbuffered.

    ``-I`` (isolated) implies ``-E`` and ``-s``: ``PYTHONPATH`` and user
    site-packages are ignored, and the working directory is not prepended to
    ``sys.path``. The host appends the runtime tree's site-packages itself,
    after the base interpreter's, so the base's complete cp314 binaries win.
    """
    exe = (
        python_exe
        or os.environ.get("RT_SIDECAR_PYTHON")
        or sys.executable
    )
    return [os.fspath(exe), "-I", "-X", "utf8", "-u"]


def default_state_dir(instance: str = "default") -> Path:
    """Default state directory, matching IPC.md §9.

    ``%LOCALAPPDATA%\\rt-sidecar`` for the default instance, a subdirectory per
    named instance. ``RT_SIDECAR_STATE_DIR`` names the instance directory
    outright; ``RT_SIDECAR_DIR`` names the root, as the transport expects.
    """
    explicit = os.environ.get("RT_SIDECAR_STATE_DIR")
    if explicit:
        return Path(explicit)
    root = os.environ.get("RT_SIDECAR_DIR")
    if root:
        base = Path(root)
    else:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "rt-sidecar" if local else Path.home() / ".rt-sidecar"
    return base if instance == "default" else base / instance


def _load_transport() -> Any:
    """Import the sibling transport module, or return None.

    Both import shapes are tried because this module is imported two ways:
    top-level (``import lifecycle``, as the tests do) and as a package member
    (``sidecar.lifecycle``). Failure is not an error: Tier 0 does not need it.
    """
    try:
        import transport  # type: ignore[import-not-found]
    except ImportError:
        try:
            from . import transport  # type: ignore[no-redef]
        except Exception:
            return None
    except Exception:
        return None
    return transport


# ---------------------------------------------------------------------------
# Atomic file helpers
# ---------------------------------------------------------------------------


def write_json_atomic(
    path: Path, payload: Mapping[str, Any], *, durable: bool = True
) -> None:
    """Write JSON via temp + ``os.replace``, retrying Windows sharing errors."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
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
            except OSError as exc:
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


def read_json(path: Path, *, attempts: int = 20) -> dict[str, Any] | None:
    """Read a JSON object, tolerating a concurrent atomic replace and garbage."""
    path = Path(path)
    for attempt in range(attempts):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            if attempt + 1 >= attempts:
                return None
            time.sleep(0.01)
            continue
        try:
            data = json.loads(text)
        except ValueError:
            # A truncated read is possible if a foreign writer is not atomic.
            if attempt + 1 >= attempts:
                return None
            time.sleep(0.01)
            continue
        return data if isinstance(data, dict) else None
    return None


def tail_lines(path: Path, lines: int = 5, max_bytes: int = 4096) -> list[str]:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return []
    text = data.decode("utf-8", "replace")
    return text.splitlines()[-lines:] if lines > 0 else []


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lock_pids(holder: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """(host_pid, supervisor_pid) recorded in a lock file."""
    return _as_int(holder.get("host_pid")), _as_int(holder.get("supervisor_pid"))


def _lock_pid(holder: Mapping[str, Any]) -> int | None:
    host_pid, supervisor_pid = _lock_pids(holder)
    return host_pid or supervisor_pid


def _lock_live(holder: Mapping[str, Any]) -> bool:
    """True when either recorded pid is alive.

    Both are checked because the supervisor (Houdini) can exit while the host
    keeps running, and the host can die while the supervisor lives.
    """
    host_pid, supervisor_pid = _lock_pids(holder)
    return pid_alive(host_pid) or pid_alive(supervisor_pid)


def _file_age_seconds(path: Path) -> float | None:
    """Age of *path* by mtime, or None when it no longer exists."""
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class SidecarSupervisor:
    """Start, stop, and probe one sidecar instance.

    ``state_dir`` holds everything: ``status.json``, ``sidecar.lock``,
    ``stop.flag``, ``token``, ``sidecar.log``. Two supervisors pointed at the
    same state dir coordinate only through those files.
    """

    def __init__(
        self,
        launcher: Sequence[str] | str,
        host_script: Any,
        state_dir: Any | None = None,
        *,
        instance: str = "default",
        runtime_home: Any | None = None,
        site_packages: Sequence[Any] = (),
        env: Mapping[str, str] | None = None,
        parent_pid: int | None = None,
        host_args: Sequence[str] = (),
        start_timeout: float = DEFAULT_START_TIMEOUT,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        stale_after: float = DEFAULT_STALE_AFTER,
        stop_grace: float = DEFAULT_STOP_GRACE,
        expect_python: str | None = "3.14",
        cwd: Any | None = None,
        shutdown_hook: Callable[[dict[str, Any]], bool] | None = None,
        terminate: Callable[[int], bool] | None = None,
        serve: bool = False,
        port: int | None = None,
        entry_point: str | None = None,
        expect_prefix: Any | None = None,
    ) -> None:
        if isinstance(launcher, (str, os.PathLike)):
            self.launcher = [os.fspath(launcher)]
        else:
            self.launcher = [os.fspath(item) for item in launcher]
        if not self.launcher:
            raise ValueError("launcher must name an interpreter")

        self.host_script = Path(os.path.abspath(os.fspath(host_script)))
        self.instance = str(instance)
        self.state_dir = Path(
            os.path.abspath(
                os.fspath(state_dir if state_dir is not None else default_state_dir(instance))
            )
        )
        self.runtime_home = None if runtime_home is None else Path(runtime_home)
        self.site_packages = [os.fspath(item) for item in site_packages]
        self.env = {str(k): str(v) for k, v in dict(env or {}).items()}
        self.parent_pid = parent_pid
        self.host_args = [str(item) for item in host_args]
        self.start_timeout = float(start_timeout)
        self.heartbeat_interval = float(heartbeat_interval)
        self.stale_after = float(stale_after)
        self.stop_grace = float(stop_grace)
        self.expect_python = None if expect_python is None else str(expect_python)
        self.cwd = None if cwd is None else Path(cwd)
        self.shutdown_hook = shutdown_hook
        self._terminate = terminate or (lambda pid: terminate_pid(pid))
        self.serve = bool(serve)
        self.port = None if port is None else int(port)
        self.entry_point = None if entry_point is None else str(entry_point)
        self.expect_prefix = (
            None if expect_prefix is None else Path(os.path.abspath(os.fspath(expect_prefix)))
        )

        # Handle to a child this supervisor started, if any. stop() works from
        # a fresh process too: the PID lives in status.json.
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock_nonce: str | None = None

    # -- paths --------------------------------------------------------------

    @property
    def status_path(self) -> Path:
        return self.state_dir / STATUS_FILENAME

    @property
    def lock_path(self) -> Path:
        return self.state_dir / LOCK_FILENAME

    @property
    def stop_path(self) -> Path:
        return self.state_dir / STOP_FILENAME

    @property
    def token_path(self) -> Path:
        return self.state_dir / TOKEN_FILENAME

    @property
    def log_path(self) -> Path:
        return self.state_dir / LOG_FILENAME

    # -- probing ------------------------------------------------------------

    def _read_status(self) -> dict[str, Any] | None:
        return read_json(self.status_path)

    def _read_lock(self, *, attempts: int = 5) -> dict[str, Any] | None:
        return read_json(self.lock_path, attempts=attempts)

    def probe(self, *, log_lines: int = 4) -> StatusReport:
        """Derive state from ``status.json`` + ``sidecar.lock``. Never mutates."""
        status = self._read_status()
        lock = self._read_lock()
        tail = tail_lines(self.log_path, log_lines) if log_lines else []

        if status is None and lock is None:
            return StatusReport(
                state=SidecarState.ABSENT,
                detail="no status file and no lock file",
                log_tail=tail,
            )

        if status is None:
            lock_pid = _lock_pid(lock or {})
            if _lock_live(lock or {}):
                return StatusReport(
                    state=SidecarState.STARTING,
                    pid=lock_pid,
                    detail="lock is held by a live process that has not written status yet",
                    lock=lock,
                    log_tail=tail,
                )
            return StatusReport(
                state=SidecarState.STALE,
                pid=lock_pid,
                detail="stale lock: no status file and no recorded pid is running",
                lock=lock,
                log_tail=tail,
            )

        pid = _as_int(status.get("pid"))
        state_field = str(status.get("state") or "")
        alive = pid_alive(pid)

        heartbeat_mono = status.get("heartbeat_mono")
        age: float | None = None
        if isinstance(heartbeat_mono, (int, float)):
            age = time.monotonic() - float(heartbeat_mono)

        # Identity: lock and status must describe the same launch. The lock
        # records the supervisor pid as well as the host pid, so pids are not
        # comparable across the two files; the nonce is.
        if lock is not None:
            lock_nonce = str(lock.get("nonce") or "")
            status_nonce = str(status.get("lock_nonce") or "")
            if lock_nonce and status_nonce and lock_nonce != status_nonce:
                return StatusReport(
                    state=SidecarState.ERROR,
                    pid=pid,
                    heartbeat_age=age,
                    detail=(
                        "status.json and sidecar.lock describe different launches; "
                        "refusing to guess"
                    ),
                    status=status,
                    lock=lock,
                    log_tail=tail,
                )

        if state_field == "stopped":
            return StatusReport(
                state=SidecarState.STOPPED,
                pid=pid,
                heartbeat_age=age,
                detail=str(status.get("stop_reason") or "stopped"),
                status=status,
                lock=lock,
                log_tail=tail,
            )

        if state_field == "error":
            return StatusReport(
                state=SidecarState.ERROR,
                pid=pid,
                heartbeat_age=age,
                detail=str(status.get("last_error") or "host reported an error"),
                status=status,
                lock=lock,
                log_tail=tail,
            )

        # T13: a document from the wrong interpreter must never read as ready.
        # A 3.13 sidecar cannot load the cp314 payload, so "alive and beating"
        # is not enough; the version has to match the launcher contract.
        version = str(status.get("python_version") or "")
        if self.expect_python and version and not version.startswith(
            self.expect_python + "."
        ):
            return StatusReport(
                state=SidecarState.ERROR,
                pid=pid,
                heartbeat_age=age,
                detail=(
                    f"wrong interpreter: expected {self.expect_python}.x, "
                    f"status reports {version}"
                ),
                status=status,
                lock=lock,
                log_tail=tail,
            )

        if self.expect_prefix is not None:
            prefix = str(status.get("sys_prefix") or "")
            if prefix and os.path.normcase(prefix) != os.path.normcase(
                os.fspath(self.expect_prefix)
            ):
                return StatusReport(
                    state=SidecarState.ERROR,
                    pid=pid,
                    heartbeat_age=age,
                    detail=(
                        f"wrong interpreter prefix: expected {self.expect_prefix}, "
                        f"status reports {prefix}"
                    ),
                    status=status,
                    lock=lock,
                    log_tail=tail,
                )

        if not alive:
            return StatusReport(
                state=SidecarState.STALE,
                pid=pid,
                heartbeat_age=age,
                detail=(
                    f"process {pid} is not running but status says "
                    f"{state_field or 'unknown'!r} (crash or kill)"
                ),
                status=status,
                lock=lock,
                log_tail=tail,
            )

        if state_field == "stopping":
            return StatusReport(
                state=SidecarState.STOPPING,
                pid=pid,
                heartbeat_age=age,
                detail="host is shutting down",
                status=status,
                lock=lock,
                log_tail=tail,
            )

        if state_field == "starting":
            return StatusReport(
                state=SidecarState.STARTING,
                pid=pid,
                heartbeat_age=age,
                detail="host is initialising",
                status=status,
                lock=lock,
                log_tail=tail,
            )

        if age is None:
            return StatusReport(
                state=SidecarState.UNRESPONSIVE,
                pid=pid,
                heartbeat_age=None,
                detail="process is alive but has never written a heartbeat",
                status=status,
                lock=lock,
                log_tail=tail,
            )

        if age > self.stale_after:
            return StatusReport(
                state=SidecarState.UNRESPONSIVE,
                pid=pid,
                heartbeat_age=age,
                detail=(
                    f"heartbeat is {age:.1f}s old (limit {self.stale_after:.1f}s) "
                    "but the process is still running"
                ),
                status=status,
                lock=lock,
                log_tail=tail,
            )

        return StatusReport(
            state=SidecarState.READY,
            pid=pid,
            heartbeat_age=age,
            detail="ready",
            status=status,
            lock=lock,
            log_tail=tail,
        )

    def status(self) -> SidecarState:
        """State only. Convenience for a panel that does not need the detail."""
        return self.probe().state

    def is_running(self) -> bool:
        return self.probe().state.running

    def wait_ready(self, timeout: float | None = None) -> StatusReport:
        deadline = time.monotonic() + (self.start_timeout if timeout is None else timeout)
        report = self.probe()
        while time.monotonic() < deadline:
            if report.state is SidecarState.READY:
                return report
            if report.state in (SidecarState.ERROR, SidecarState.STALE):
                return report
            time.sleep(POLL_INTERVAL)
            report = self.probe()
        return report

    # -- lock ---------------------------------------------------------------

    def _acquire_lock(self, nonce: str) -> None:
        """Claim ``sidecar.lock`` for *nonce*. Returns None; raises on failure.

        ``LockHeldError`` means a live process owns the lock: another host is
        running, so starting a second one is refused. ``LockUnavailableError``
        means the file could not be claimed confidently (unreadable, or the
        claim vanished), so no host may be launched at all.

        Two rules make this safe:

        * The decision is ``host_pid``-aware. A live ``host_pid`` always blocks,
          even when the recorded ``supervisor_pid`` is this process: a second
          thread of the same Houdini must not take over a running host. An
          own-process record whose host is not live is an abandoned launch and
          is safe to replace.
        * An unreadable lock younger than ``LOCK_SETTLE_SECONDS`` is treated as
          mid-creation (a creator descheduled between ``os.open`` and
          ``os.write``). It is re-read, never unlinked; if it stays unreadable
          the acquisition fails rather than deleting a live holder's file.
          A lock older than that which is still unreadable is presumed crashed
          and can be replaced.
        """
        with _PROCESS_LOCK:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "schema": SCHEMA_VERSION,
                "instance": self.instance,
                "nonce": nonce,
                "supervisor_pid": os.getpid(),
                "host_pid": None,
                "host_script": os.fspath(self.host_script),
                "launcher": list(self.launcher),
                "started_at": _now_iso(),
            }
            payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )

            for _ in range(3):
                try:
                    handle = os.open(
                        os.fspath(self.lock_path),
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o644,
                    )
                except FileExistsError:
                    holder = self._read_lock(attempts=10)
                    if holder is None:
                        age = _file_age_seconds(self.lock_path)
                        if age is None:
                            # It vanished between the failed create and the read.
                            continue
                        if age < LOCK_SETTLE_SECONDS:
                            # Possibly another process between open and write.
                            time.sleep(0.05)
                            continue
                        holder = self._read_lock(attempts=10)
                        if holder is None:
                            raise LockUnavailableError(
                                f"{self.lock_path} exists but is unreadable and "
                                f"older than {LOCK_SETTLE_SECONDS:.1f}s"
                            )

                    host_pid, supervisor_pid = _lock_pids(holder)
                    if pid_alive(host_pid):
                        # A host is running (possibly on behalf of this very
                        # process, in another thread). Never take it over.
                        raise LockHeldError(holder)
                    if supervisor_pid != os.getpid() and pid_alive(supervisor_pid):
                        raise LockHeldError(holder)

                    # No live host: a dead launch, or this process's own
                    # abandoned record. Both are safe to replace.
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        time.sleep(0.02)
                    continue

                try:
                    os.write(handle, payload)
                except OSError as exc:
                    raise LockUnavailableError(
                        f"cannot write {self.lock_path}: {exc!r}"
                    ) from exc
                finally:
                    os.close(handle)

                # Never launch on an unverified claim.
                claimed = self._read_lock(attempts=10)
                if claimed is None or str(claimed.get("nonce") or "") != nonce:
                    raise LockUnavailableError(
                        f"{self.lock_path} did not keep nonce {nonce!r} after creation"
                    )
                return

            raise LockUnavailableError(
                f"could not claim {self.lock_path} after 3 attempts"
            )

    def _update_lock_host_pid(self, host_pid: int) -> None:
        """Record the host pid so the lock outlives this supervisor process."""
        holder = self._read_lock()
        if holder is None or str(holder.get("nonce") or "") != str(self._lock_nonce or ""):
            return
        holder["host_pid"] = int(host_pid)
        try:
            write_json_atomic(self.lock_path, holder, durable=True)
        except OSError:
            pass

    def _release_lock(self, nonce: str | None) -> None:
        if nonce is None:
            return
        with _PROCESS_LOCK:
            holder = self._read_lock()
            if holder is not None and str(holder.get("nonce") or "") not in ("", nonce):
                return  # someone else's lock; leave it alone
            try:
                self.lock_path.unlink()
            except OSError:
                pass

    # -- child environment --------------------------------------------------

    def _child_env(self) -> dict[str, str]:
        env = {str(k): str(v) for k, v in os.environ.items()}
        # -I already ignores these; removing them stops a future launcher that
        # forgets -I from inheriting a shadowing path.
        for key in (
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONSTARTUP",
            "PYTHONUSERBASE",
            "PYTHONEXECUTABLE",
            "__PYVENV_LAUNCHER__",
        ):
            env.pop(key, None)

        env.update(self.env)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["RT_SIDECAR_STATE_DIR"] = os.fspath(self.state_dir)
        env["RT_SIDECAR_INSTANCE"] = self.instance
        env["RT_SIDECAR_STATUS_FILE"] = os.fspath(self.status_path)
        env["RT_SIDECAR_STOP_FILE"] = os.fspath(self.stop_path)
        env["RT_SIDECAR_TOKEN_FILE"] = os.fspath(self.token_path)
        env["RT_SIDECAR_LOG_FILE"] = os.fspath(self.log_path)
        env["RT_SIDECAR_PYTHON"] = self.launcher[0]
        if self.runtime_home is not None:
            env["RT_SIDECAR_RUNTIME_HOME"] = os.fspath(self.runtime_home)
        if self.site_packages:
            # Ordered base-first: the host appends these after site-packages.
            env["RT_SIDECAR_SITE_PACKAGES"] = os.pathsep.join(self.site_packages)
        return env

    def _rotate_log(self) -> None:
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_ROTATE_BYTES:
                rotated = self.log_path.with_suffix(".log.1")
                try:
                    if rotated.exists():
                        rotated.unlink()
                except OSError:
                    pass
                os.replace(self.log_path, rotated)
        except OSError:
            pass

    def _build_command(self) -> list[str]:
        command = list(self.launcher) + [os.fspath(self.host_script)]
        command += [
            "--state-dir",
            os.fspath(self.state_dir),
            "--instance",
            self.instance,
            "--heartbeat-interval",
            repr(float(self.heartbeat_interval)),
            "--lock-nonce",
            str(self._lock_nonce or ""),
            "--token-file",
            os.fspath(self.token_path),
            "--log-file",
            os.fspath(self.log_path),
        ]
        if self.parent_pid:
            command += ["--parent-pid", str(int(self.parent_pid))]
        if self.expect_python:
            command += ["--expect-python", self.expect_python]
        if self.runtime_home is not None:
            command += ["--runtime-home", os.fspath(self.runtime_home)]
        if self.serve:
            # The transport becomes the only status writer once it starts.
            command += ["--serve"]
            if self.port is not None:
                command += ["--port", str(int(self.port))]
        if self.entry_point:
            command += ["--entry-point", self.entry_point]
        for path in self.site_packages:
            command += ["--site-packages", path]
        command += self.host_args
        return command

    # -- start --------------------------------------------------------------

    def start(self, *, force: bool = False) -> LifecycleResult:
        """Start the sidecar. Idempotent: a live ready sidecar is left alone.

        ``force=True`` first stops an alive-but-unresponsive sidecar. Without
        it, ``start()`` refuses rather than risk two hosts on one port.
        """
        report = self.probe()
        if report.state is SidecarState.READY:
            return LifecycleResult(
                ok=True,
                state=SidecarState.READY,
                changed=False,
                pid=report.pid,
                port=report.port,
                reasons=["already running"],
            )
        if report.state is SidecarState.STARTING:
            ready = self.wait_ready(self.start_timeout)
            if ready.state is SidecarState.READY:
                return LifecycleResult(
                    ok=True,
                    state=SidecarState.READY,
                    changed=False,
                    pid=ready.pid,
                    reasons=["already starting; it became ready"],
                )
            report = ready
        if report.state is SidecarState.UNRESPONSIVE and not force:
            return LifecycleResult(
                ok=False,
                state=report.state,
                changed=False,
                pid=report.pid,
                reasons=[report.detail, "run stop() first, or start(force=True)"],
                error="sidecar is alive but unresponsive",
            )
        if report.state is SidecarState.STOPPING:
            deadline = time.monotonic() + max(2.0, self.stop_grace / 2)
            while time.monotonic() < deadline and pid_alive(report.pid):
                time.sleep(POLL_INTERVAL)
            if pid_alive(report.pid):
                return LifecycleResult(
                    ok=False,
                    state=SidecarState.STOPPING,
                    changed=False,
                    pid=report.pid,
                    reasons=["sidecar is still shutting down"],
                    error="stop did not finish",
                )

        if report.state in (SidecarState.UNRESPONSIVE, SidecarState.STOPPING) and force:
            stopped = self.stop(force=True)
            if not stopped.ok:
                return stopped

        if not self.host_script.is_file():
            return self._fail_start(
                f"host script not found: {self.host_script}", proc=None, log_handle=None
            )
        if not Path(self.launcher[0]).is_file():
            return self._fail_start(
                f"launcher not found: {self.launcher[0]}", proc=None, log_handle=None
            )

        # Everything from here through recording the host pid runs under the
        # process lock: two threads of this process must not both pass the
        # probe above and spawn a host.
        with _PROCESS_LOCK:
            # Anything left over from a dead launch is cleared before we start.
            self._clear_leftovers()

            nonce = uuid.uuid4().hex
            try:
                self._acquire_lock(nonce)
            except LockHeldError as exc:
                holder = exc.holder
                return LifecycleResult(
                    ok=False,
                    state=SidecarState.ERROR,
                    changed=False,
                    pid=_lock_pid(holder),
                    reasons=[
                        "another supervisor holds the lock with a live pid",
                        f"lock host_pid={holder.get('host_pid')} "
                        f"supervisor_pid={holder.get('supervisor_pid')}",
                    ],
                    error="lock held by a live process",
                )
            except LockUnavailableError as exc:
                return self._fail_start(
                    f"cannot acquire sidecar.lock: {exc}",
                    proc=None,
                    log_handle=None,
                )
            self._lock_nonce = nonce

            # Token file exists before the host starts; the transport reads it.
            # transport.write_token_file gives an atomic write plus a restricted
            # ACL; the fallback keeps Tier 0 working when transport is absent.
            try:
                self._write_token()
            except OSError as exc:
                self._release_lock(nonce)
                self._lock_nonce = None
                return self._fail_start(
                    f"cannot write token file {self.token_path}: {exc!r}",
                    proc=None,
                    log_handle=None,
                )

            self._rotate_log()
            try:
                log_handle = open(self.log_path, "ab")
            except OSError as exc:
                self._release_lock(nonce)
                self._lock_nonce = None
                self._remove_token()
                return self._fail_start(
                    f"cannot open log file {self.log_path}: {exc!r}",
                    proc=None,
                    log_handle=None,
                )

            creationflags = 0
            if os.name == "nt":
                creationflags = 0x08000000  # CREATE_NO_WINDOW: no console flash
            command = self._build_command()
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=os.fspath(self.cwd or self.state_dir),
                    env=self._child_env(),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                    close_fds=True,
                )
            except OSError as exc:
                try:
                    log_handle.close()
                except OSError:
                    pass
                self._release_lock(nonce)
                self._lock_nonce = None
                self._remove_token()
                return self._fail_start(
                    f"launcher failed to start: {exc}",
                    proc=None,
                    log_handle=None,
                    extra={"command": command},
                )
            finally:
                try:
                    log_handle.flush()
                except (OSError, ValueError):
                    pass

            self._proc = proc
            # Under the process lock: a concurrent start in this process waits
            # here, then sees a live host_pid and refuses instead of spawning.
            self._update_lock_host_pid(proc.pid)
            try:
                log_handle.close()  # the child holds its own duplicate
            except OSError:
                pass

        # Bounded wait for ready. Any failure terminates the child.
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            exit_code = proc.poll()
            if exit_code is not None:
                status = self._read_status() or {}
                detail = str(
                    status.get("last_error")
                    or f"host exited before ready (exit code {exit_code})"
                )
                self._proc = None
                return self._fail_start(detail, proc=proc, log_handle=None, reaped=True)

            status = self._read_status()
            if status is not None:
                same_child = _as_int(status.get("pid")) == proc.pid
                ready = str(status.get("state") or "") == "ready"
                beat = (_as_int(status.get("heartbeat_seq")) or 0) >= 1
                if same_child and ready and beat:
                    bound_port = status.get("port")
                    bound_port = (
                        bound_port
                        if isinstance(bound_port, int) and bound_port > 0
                        else None
                    )
                    reasons = [
                        f"started pid {proc.pid}",
                        f"python {status.get('python_version')}",
                        f"prefix {status.get('sys_prefix')}",
                    ]
                    if bound_port is not None:
                        reasons.append(f"control port {bound_port}")
                    if status.get("bind_fallback"):
                        # The requested port was busy; the transport moved to an
                        # ephemeral one. Worth surfacing, not worth failing.
                        reasons.append(
                            "bind fallback: requested port unavailable "
                            f"({status.get('bind_error') or 'no detail'})"
                        )
                    return LifecycleResult(
                        ok=True,
                        state=SidecarState.READY,
                        changed=True,
                        pid=proc.pid,
                        port=bound_port,
                        reasons=reasons,
                    )
            time.sleep(POLL_INTERVAL)

        return self._fail_start(
            f"sidecar did not become ready within {self.start_timeout:.1f}s",
            proc=proc,
            log_handle=None,
        )

    def _fail_start(
        self,
        message: str,
        *,
        proc: subprocess.Popen[bytes] | None,
        log_handle: Any,
        reaped: bool = False,
        extra: Mapping[str, Any] | None = None,
    ) -> LifecycleResult:
        """Tear down a failed launch. Never leaves a child running."""
        pid = None if proc is None else proc.pid
        if proc is not None and not reaped and proc.poll() is None:
            self._terminate(proc.pid)
            try:
                proc.wait(timeout=TERMINATE_WAIT)
            except subprocess.TimeoutExpired:
                pass
        elif proc is not None:
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass

        if log_handle is not None:
            try:
                log_handle.close()
            except OSError:
                pass

        self._proc = None
        self._remove_stop_flag()
        self._release_lock(self._lock_nonce)
        self._lock_nonce = None
        self._remove_token()

        tail = tail_lines(self.log_path, 6)
        payload: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "instance": self.instance,
            "writer": "supervisor",
            "pid": pid,
            "state": "error",
            "last_error": message,
            "exit": "start-failed",
            "stopped_at": _now_iso(),
            "heartbeat_mono": None,
            "heartbeat_at": None,
            "heartbeat_seq": 0,
            "token_file": None,
            "log_tail": tail,
        }
        if extra:
            payload.update({str(k): v for k, v in extra.items()})
        try:
            write_json_atomic(self.status_path, payload, durable=True)
        except OSError:
            pass

        reasons = [message]
        reasons.extend(tail[-3:])
        return LifecycleResult(
            ok=False,
            state=SidecarState.ERROR,
            changed=False,
            pid=pid,
            reasons=reasons,
            error=message,
        )

    # -- stop ---------------------------------------------------------------

    def stop(self, *, force: bool = False) -> LifecycleResult:
        """Stop the sidecar. Idempotent. Graceful, then terminate."""
        report = self.probe()

        if report.state is SidecarState.ABSENT:
            return LifecycleResult(
                ok=True,
                state=SidecarState.ABSENT,
                changed=False,
                reasons=["no sidecar"],
            )
        if report.state is SidecarState.STOPPED:
            self._release_lock((report.lock or {}).get("nonce"))
            self._remove_stop_flag()
            self._remove_token()
            return LifecycleResult(
                ok=True,
                state=SidecarState.STOPPED,
                changed=False,
                pid=report.pid,
                reasons=["already stopped"],
            )

        pid = report.pid
        cleaned_stale = report.state is SidecarState.STALE and not pid_alive(pid)
        if cleaned_stale:
            self._release_lock((report.lock or {}).get("nonce"))
            self._remove_stop_flag()
            self._remove_token()
            self._write_stopped_status(report.status, pid, "cleaned-stale")
            return LifecycleResult(
                ok=True,
                state=SidecarState.STOPPED,
                changed=True,
                pid=pid,
                reasons=["cleaned up a stale sidecar record", report.detail],
            )

        # 1. Graceful: the transport's POST /shutdown. The caller's hook wins;
        #    otherwise the transport helper is used when a port is published.
        hook = self.shutdown_hook
        if hook is None and report.port:
            hook = self._transport_shutdown
        accepted = False
        if hook is not None and pid_alive(pid):
            try:
                accepted = bool(hook(dict(report.status or {})))
            except Exception as exc:  # a broken hook must not block stopping
                report.log_tail = list(report.log_tail) + [
                    f"shutdown hook failed: {exc!r}"
                ]

        # 2. Graceful fallback: the stop file, tried only when HTTP could not be
        #    used or did not accept (IPC.md §9, shutdown step 4).
        if not accepted:
            try:
                self.stop_path.write_text(
                    json.dumps(
                        {
                            "requested_at": _now_iso(),
                            "by_pid": os.getpid(),
                            "force": bool(force),
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
            except OSError as exc:
                report.log_tail = list(report.log_tail) + [
                    f"cannot write stop flag: {exc!r}"
                ]

        grace = 0.5 if force else self.stop_grace
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                break
            time.sleep(POLL_INTERVAL)

        # 3. Fallback: terminate.
        terminated = False
        if pid_alive(pid):
            terminated = True
            if not self._terminate(pid):
                return LifecycleResult(
                    ok=False,
                    state=SidecarState.ERROR,
                    changed=True,
                    pid=pid,
                    reasons=["process survived the graceful window and terminate"],
                    error=f"could not kill pid {pid}",
                )

        if self._proc is not None:
            try:
                self._proc.wait(timeout=TERMINATE_WAIT)
            except subprocess.TimeoutExpired:
                pass
            self._proc = None

        self._release_lock((report.lock or {}).get("nonce"))
        self._remove_stop_flag()
        self._remove_token()
        self._write_stopped_status(
            report.status, pid, "terminated" if terminated else "graceful"
        )

        return LifecycleResult(
            ok=True,
            state=SidecarState.STOPPED,
            changed=True,
            pid=pid,
            reasons=[
                "terminated (graceful window expired)" if terminated else "stopped gracefully",
                f"pid {pid}",
            ],
        )

    def restart(self, *, force: bool = False) -> LifecycleResult:
        stopped = self.stop(force=force)
        if not stopped.ok:
            return stopped
        started = self.start(force=force)
        started.reasons = list(stopped.reasons) + list(started.reasons)
        return started

    # -- small internals ----------------------------------------------------

    def _clear_leftovers(self) -> None:
        """Clear dead-owner files. A stop flag must never outlive its launch.

        A live lock is never released, including one owned by this process:
        its recorded ``host_pid`` may belong to another thread's running host.
        Only a lock whose host is not live and whose owner is dead (or this
        process's abandoned launch) is removed. The token is left alone while
        a host is live, because that host needs it to shut down.
        """
        with _PROCESS_LOCK:
            self._remove_stop_flag()
            holder = self._read_lock()
            host_alive = False
            if holder is not None:
                host_pid, supervisor_pid = _lock_pids(holder)
                host_alive = pid_alive(host_pid)
                ours = supervisor_pid == os.getpid()
                if not host_alive and (ours or not _lock_live(holder)):
                    self._release_lock(str(holder.get("nonce") or ""))
            if not host_alive:
                self._remove_token()

    def _remove_stop_flag(self) -> None:
        try:
            self.stop_path.unlink()
        except OSError:
            pass

    def _remove_token(self) -> None:
        try:
            self.token_path.unlink()
        except OSError:
            pass

    def _write_token(self) -> None:
        """Write a fresh token, preferring transport's atomic + ACL-restricted writer."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        transport = _load_transport()
        if transport is not None:
            try:
                transport.write_token_file(self.token_path, token)
                return
            except Exception:
                pass  # fall through to the stdlib path

        temp = self.token_path.with_name(
            f"{self.token_path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        )
        try:
            handle = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(handle, (token + "\n").encode("ascii"))
                try:
                    os.fsync(handle)
                except OSError:
                    pass
            finally:
                os.close(handle)
            os.replace(temp, self.token_path)
        except BaseException:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            raise

    def _transport_shutdown(self, status: dict[str, Any]) -> bool:
        """Default graceful stop: POST /shutdown through the transport helper.

        ``transport.request`` uses ``http.client`` rather than ``urllib`` so a
        configured ``HTTP_PROXY`` can never receive the token. Returns True when
        the shutdown was accepted (200) or had already been accepted (409).
        """
        transport = _load_transport()
        port = status.get("port")
        if transport is None or not isinstance(port, int) or port <= 0:
            return False
        token_path = status.get("token_file") or os.fspath(self.token_path)
        try:
            token = transport.read_token_file(token_path)
            code, _body = transport.request(
                f"http://127.0.0.1:{port}",
                "/shutdown",
                method="POST",
                token=token,
                timeout=3.0,
            )
        except Exception:
            return False
        return code in (200, 409)

    def _write_stopped_status(
        self,
        previous: Mapping[str, Any] | None,
        pid: int | None,
        how: str,
    ) -> None:
        record: dict[str, Any] = dict(previous or {})
        record.update(
            {
                "schema": SCHEMA_VERSION,
                "instance": self.instance,
                "writer": "supervisor",
                "pid": pid,
                "state": "stopped",
                "heartbeat_mono": None,
                "heartbeat_at": None,
                "heartbeat_seq": 0,
                "last_error": None,
                "stop_reason": how,
                "stopped_at": _now_iso(),
                "exit": how,
                "token_file": None,
                "port": None,
            }
        )
        try:
            write_json_atomic(self.status_path, record, durable=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Tiny CLI, for manual runs and the Operator's Card
# ---------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="rt_sidecar", description="Sidecar supervisor")
    parser.add_argument("action", choices=["start", "stop", "restart", "status"])
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--instance", default="default")
    parser.add_argument("--python", default=None, help="launcher interpreter")
    parser.add_argument("--host", default=None, help="path to host_stub.py")
    parser.add_argument("--timeout", type=float, default=DEFAULT_START_TIMEOUT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="let the transport serve the control API and own status.json",
    )
    parser.add_argument("--port", type=int, default=None, help="requested control port")
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="pid whose death stops the sidecar (use the Houdini pid)",
    )
    args = parser.parse_args(argv)

    here = Path(__file__).resolve().parent
    supervisor = SidecarSupervisor(
        default_launcher(args.python),
        args.host or (here / "host_stub.py"),
        args.state_dir,
        instance=args.instance,
        start_timeout=args.timeout,
        parent_pid=args.parent_pid,
        serve=args.serve,
        port=args.port,
    )

    if args.action == "status":
        report = supervisor.probe(log_lines=8)
        print(json.dumps(
            {
                "state": report.state.value,
                "runtime_state": report.runtime_state,
                "pid": report.pid,
                "heartbeat_age": report.heartbeat_age,
                "detail": report.detail,
                "status": report.status,
            },
            indent=2,
            sort_keys=True,
        ))
        return 0 if report.state.ok else 1

    result = getattr(supervisor, args.action)(force=args.force)
    print(
        json.dumps(
            {
                "ok": result.ok,
                "state": result.state.value,
                "changed": result.changed,
                "pid": result.pid,
                "reasons": result.reasons,
                "error": result.error,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - manual entry
    sys.exit(_main(sys.argv[1:]))
