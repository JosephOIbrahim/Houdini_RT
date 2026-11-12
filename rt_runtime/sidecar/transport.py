"""Local control transport for the HoudiniRT 3.14 sidecar.

Design record: ``IPC.md`` (same directory as this repo's work root).

One job: expose a minimal, authenticated, loopback-only control surface that
answers ``GET /health``, ``GET /status``, ``POST /shutdown``, and -- when the
host supplies a turn loop -- ``POST /turn``, ``GET /turn/<request_id>`` and
``POST /cancel``. Nothing else.

Hard properties enforced here:

* The listening socket is bound to ``127.0.0.1`` (or ``::1``) and nowhere else.
  ``0.0.0.0``, ``::``, LAN addresses and hostnames are refused before bind, and
  the bound address is re-checked after bind.
* Every route that reveals state or changes state requires the per-launch token
  in ``X-RT-Token``, compared with :func:`hmac.compare_digest`. ``/health`` is
  the one exception, and it stays token-free and turn-blind.
* Request bodies are length-checked before they are read, capped per route
  (``max_body_bytes`` for the control routes, ``max_turn_body_bytes`` for
  ``POST /turn``) and then discarded unparsed -- except ``/turn`` and
  ``/cancel``, whose bodies are parsed *after* the token check. No route reads
  a path from the caller, spawns a process, or evaluates code.
* No turn work ever runs on a handler thread. The transport only admits,
  polls, cancels and waits (``wait_ms``, bounded by the turn loop); the loop
  owns the worker.
* Requests that look like browser traffic (``Origin`` / ``Sec-Fetch-*``) are
  refused, and ``Host`` must name a loopback literal, which closes the
  DNS-rebinding path.
* Every response carries ``Connection: close``; keep-alive is not offered.

The turn loop is duck-typed, never imported: the transport expects
``parse_request``, ``submit``, ``poll``, ``wait``, ``cancel`` and
``status_fields``, and maps any exception carrying an integer ``http_status``
attribute to that status (``code``, ``message`` and ``detail`` describe it).
That keeps this module's import graph unchanged.

The client helper at the bottom deliberately uses ``http.client`` rather than
``urllib.request``: urllib honours ``HTTP_PROXY`` / ``HTTPS_PROXY`` from the
environment and would ship ``X-RT-Token`` to a proxy on a misconfigured box.

Pure stdlib; importable and testable on CPython 3.12 and later. No ``hou``.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import platform
import re
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

__all__ = [
    "BindRefused",
    "ControlServer",
    "PortUnavailable",
    "StatusFile",
    "TransportError",
    "TOKEN_HEADER",
    "SCHEMA_VERSION",
    "MAX_BODY_BYTES",
    "MAX_TURN_BODY_BYTES",
    "MAX_TURN_RESPONSE_BYTES",
    "MAX_PATH_BYTES",
    "MAX_CONNECTIONS",
    "STATUS_FILE_NAME",
    "TOKEN_FILE_NAME",
    "generate_token",
    "token_fingerprint",
    "write_token_file",
    "read_token_file",
    "restrict_file_acl",
    "read_status_file",
    "request",
    "start_control_server",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

TOKEN_HEADER = "X-RT-Token"

#: Largest request body the control surface will accept, in bytes.
MAX_BODY_BYTES = 4096

#: Largest ``POST /turn`` body, in bytes. A prompt is caller data, so the cap
#: leaves room for 16 Ki characters of worst-case UTF-8 plus the JSON envelope.
#: Mirrors ``rt_runtime.sidecar.turn_loop.MAX_TURN_BODY_BYTES``: the transport
#: enforces it before reading, the loop's parser keeps the same rule.
MAX_TURN_BODY_BYTES = 131072

#: Largest response body the client helper will read. A terminal turn document
#: carries up to 64 Ki characters of result text, and ``_send`` serialises with
#: ``ensure_ascii`` on: one non-ASCII character can become six bytes
#: (``\\uXXXX``), so a legal document can approach 384 KiB. 1 MiB reads any
#: legal response whole; the historical 64 KiB read truncated the small ones.
MAX_TURN_RESPONSE_BYTES = 1048576

#: Largest request target (path + query) accepted, in bytes.
MAX_PATH_BYTES = 2048

#: Live accept slots. Beyond this the server answers 503 and closes, instead of
#: spawning an unbounded number of handler threads.
MAX_CONNECTIONS = 16

#: How long a refused connection is drained before its socket is closed. Closing
#: with unread data makes Windows send RST, which discards the 503 response.
REFUSE_DRAIN_SECONDS = 0.25

#: File names used inside the instance directory.
STATUS_FILE_NAME = "status.json"
TOKEN_FILE_NAME = "token"

#: How often the status file heartbeat is rewritten, in seconds.
DEFAULT_HEARTBEAT_INTERVAL = 1.0

#: Shortest token accepted. ``generate_token`` produces 43 characters.
MIN_TOKEN_LENGTH = 16

#: Hosts that may be bound. Everything else is refused.
_LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: Request ids the turn routes accept. Mirrors ``turn_loop.MAX_REQUEST_ID_CHARS``.
_TURN_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Turn job states that will never change again, so ``POST /turn`` can answer
#: 200 instead of 202. Mirrors ``turn_loop.STATE_*``; the transport does not
#: import the loop to learn four words.
_TURN_TERMINAL_STATES = frozenset({"complete", "cancelled", "error", "timeout"})

_STATE_STARTING = "starting"
_STATE_READY = "ready"
_STATE_STOPPING = "stopping"
_STATE_STOPPED = "stopped"
_STATE_ERROR = "error"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TransportError(Exception):
    """Base error for the control transport."""


class BindRefused(TransportError, ValueError):
    """The requested bind address is not loopback.

    Subclasses :class:`ValueError` so ordinary argument validation catches it.
    """


class PortUnavailable(TransportError, OSError):
    """No listening socket could be created (fixed port busy, fallback off)."""


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


def generate_token(nbytes: int = 32) -> str:
    """Return a fresh URL-safe token (43 characters at the default size)."""
    return secrets.token_urlsafe(nbytes)


def token_fingerprint(token: str) -> str:
    """Return a short, non-reversible identifier for a token.

    Published in ``status.json`` so a client can confirm it is holding the
    current launch's token without the token itself ever being written to a
    world-readable place.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def restrict_file_acl(path: str | os.PathLike[str], *, user: str | None = None) -> bool:
    """Best-effort "owner only" restriction. Returns True when it was applied.

    POSIX: ``chmod 0600``.

    Windows: ``icacls /inheritance:r`` then grant the current user, SYSTEM and
    Administrators. This is the Windows spelling of 0600. Other local users are
    excluded; other processes of the *same* user are not, and cannot be -- see
    the threat model in ``IPC.md``.

    Never raises; the caller decides whether a False return is fatal.
    """
    target = os.fspath(path)

    if os.name != "nt":
        try:
            os.chmod(target, 0o600)
        except OSError:
            return False
        return True

    account = user or os.environ.get("USERNAME")
    if not account:
        return False

    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    icacls = os.path.join(system_root, "System32", "icacls.exe")
    if not os.path.isfile(icacls):
        return False

    argv = [
        icacls,
        target,
        "/inheritance:r",
        "/grant:r", f"{account}:F",
        "/grant:r", "*S-1-5-18:F",        # NT AUTHORITY\SYSTEM
        "/grant:r", "*S-1-5-32-544:F",    # BUILTIN\Administrators
    ]
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
            # icacls is a console program; without this a GUI host (Houdini)
            # gives it a console window that flashes and vanishes.
            creationflags=(0x08000000 if os.name == "nt" else 0),  # CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def write_token_file(
    path: str | os.PathLike[str],
    token: str,
    *,
    restrict: bool = True,
) -> Path:
    """Write ``token`` to ``path`` atomically, then restrict the ACL.

    The file is written to ``<path>.tmp`` and moved into place with
    :func:`os.replace`, so a reader never observes a half-written token. A
    pre-existing file is replaced, which is what a relaunch needs.
    """
    if not token or len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(
            f"token must be at least {MIN_TOKEN_LENGTH} characters "
            f"(got {len(token)})"
        )

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")

    try:
        tmp.unlink()
    except FileNotFoundError:
        pass

    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.strip().encode("ascii") + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)

    os.replace(tmp, target)
    if restrict:
        restrict_file_acl(target)
    return target


def read_token_file(path: str | os.PathLike[str]) -> str:
    """Read a token written by :func:`write_token_file`."""
    token = Path(path).read_text(encoding="ascii").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"token file {path!s} does not contain a usable token")
    return token


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _utc_now(clock: Callable[[], float] = time.time) -> str:
    moment = datetime.fromtimestamp(clock(), tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _assert_loopback_address(address: Any) -> None:
    """Post-bind guard: the socket must have landed on a loopback address."""
    literal = str(address).split("%", 1)[0]
    try:
        parsed = ipaddress.ip_address(literal)
    except ValueError as exc:  # pragma: no cover - defensive
        raise BindRefused(
            f"bound address {address!r} is not an IP literal; refusing to serve"
        ) from exc
    if not parsed.is_loopback:
        raise BindRefused(
            f"bound address {address!r} is not loopback; refusing to serve"
        )


def normalise_host(host: str) -> str:
    """Return a numeric loopback literal, or raise :class:`BindRefused`."""
    if not isinstance(host, str):
        raise BindRefused(f"bind host must be a string, got {type(host).__name__}")
    candidate = host.strip().lower()
    if candidate not in _LOOPBACK_BIND_HOSTS:
        raise BindRefused(
            f"refusing to bind {host!r}: loopback only "
            "(127.0.0.1, ::1 or localhost)"
        )
    if candidate == "localhost":
        return "127.0.0.1"
    return candidate


# ---------------------------------------------------------------------------
# Status file
# ---------------------------------------------------------------------------


class StatusFile:
    """Atomic ``status.json`` writer with a heartbeat thread.

    The payload is produced by ``tick`` on every beat, so the file and the HTTP
    body are the same object built by the same function. Writes go through a
    temporary file plus :func:`os.replace`: a reader sees either the previous
    complete document or the next one, never a partial write.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        tick: Callable[[], Mapping[str, Any]],
        interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.interval = max(0.01, float(interval))
        self._tick = tick
        self._clock = clock
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.write_errors = 0
        self.writes = 0

    # -- writing ----------------------------------------------------------

    def update(self, **fields: Any) -> bool:
        """Merge ``fields`` into the document and rewrite it."""
        with self._lock:
            self._data.update(fields)
            return self._write_locked()

    def _write_locked(self) -> bool:
        payload = {"schema": SCHEMA_VERSION}
        payload.update(self._data)
        text = json.dumps(
            payload, indent=2, sort_keys=True, ensure_ascii=False, default=str
        ) + "\n"

        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError:
            self.write_errors += 1
            return False
        self.writes += 1
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def seed_from_existing(self, *, pid: int) -> None:
        """Adopt fields another writer left for *this* process.

        The S1 host stub writes the ``state="starting"`` record before the
        transport exists. Those process-only fields (``lock_nonce``,
        ``parent_pid``, ``runtime_home``, ...) must survive the handover, so an
        existing document whose ``pid`` is our own is merged underneath the
        authoritative fields. A document from any other pid is ignored: stale
        keys from a previous launch are never inherited.
        """
        existing = read_status_file(self.path)
        if not existing or existing.get("pid") != pid:
            return
        with self._lock:
            for key, value in existing.items():
                if key != "schema":
                    self._data.setdefault(key, value)

    # -- heartbeat --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="rt-sidecar-status", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                fields = dict(self._tick())
            except Exception:  # pragma: no cover - defensive
                self.write_errors += 1
                continue
            self.update(**fields)

    def stop(self, *, final: Mapping[str, Any] | None = None) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.interval * 3))
        self._thread = None
        if final is not None:
            self.update(**dict(final))


def read_status_file(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Read ``status.json``. Returns ``None`` for a missing or unreadable file."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _host_header_allowed(value: str | None) -> bool:
    """DNS-rebinding guard: ``Host`` must name a loopback literal."""
    if not value:
        return True
    candidate = value.strip()
    if candidate.startswith("["):
        end = candidate.find("]")
        if end == -1:
            return False
        host = candidate[1:end]
    elif candidate.count(":") == 1:
        host = candidate.rsplit(":", 1)[0]
    else:
        host = candidate
    host = host.strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


class _LoopbackHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` that refuses to serve a non-loopback socket."""

    daemon_threads = True
    # Windows: SO_REUSEADDR lets a second process steal a live port. Off.
    allow_reuse_address = False
    request_queue_size = 8
    control: "ControlServer | None" = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            control = self.control
            if control is not None:
                control._log("connection refused: slot limit reached")
            # Answer off the accept loop: a short-lived thread per refusal keeps
            # serve_forever responsive, and every such thread dies within
            # REFUSE_DRAIN_SECONDS.
            threading.Thread(
                target=self._refuse_busy,
                args=(request,),
                name="rt-sidecar-busy",
                daemon=True,
            ).start()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def _refuse_busy(self, request: Any) -> None:
        body = b'{"error": "busy", "ok": false}\n'
        head = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n\r\n"
        )
        try:
            request.sendall(head + body)
            # Drain the caller's request before closing. Closing with unread
            # data makes Windows answer with RST, which discards the 503.
            request.settimeout(REFUSE_DRAIN_SECONDS)
            try:
                while request.recv(4096):
                    pass
            except OSError:
                pass
        except OSError:
            pass
        finally:
            self.shutdown_request(request)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def server_bind(self) -> None:
        # Skip HTTPServer.server_bind(), which calls socket.getfqdn() and can
        # stall startup on a machine with no reverse DNS. The sidecar has a
        # 10 s start budget and pythonw has no console to show a hang in.
        socketserver.TCPServer.server_bind(self)
        address = self.server_address
        self.server_name = str(address[0])
        self.server_port = int(address[1])
        _assert_loopback_address(address[0])

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: D401
        control = self.control
        if control is not None:
            control._log_exc(f"connection error from {client_address!r}")
            return
        super().handle_error(request, client_address)  # pragma: no cover


class _LoopbackHTTPServerV6(_LoopbackHTTPServer):
    address_family = socket.AF_INET6


class _Handler(BaseHTTPRequestHandler):
    """The whole route table. Three paths, two methods, everything else 404/405."""

    server_version = "rt-sidecar"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 5.0

    # -- entry points -----------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch("OPTIONS")

    def _dispatch(self, method: str) -> None:
        self._responded = False
        control = self.server.control
        try:
            self._route(method, control)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:  # pragma: no cover - defensive
            control._log_exc("handler failure")
            if not self._responded:
                self._send(500, {"ok": False, "error": "internal_error"})

    # -- routing ----------------------------------------------------------

    def _route(self, method: str, control: "ControlServer") -> None:
        control._count_request()

        if len(self.path) > MAX_PATH_BYTES:
            self._send(414, {"ok": False, "error": "uri_too_long"})
            return

        try:
            path = urlsplit(self.path).path or "/"
        except ValueError:
            self._send(400, {"ok": False, "error": "bad_request_target"})
            return

        if control.reject_browser_requests and self._browser_shaped():
            self._send(403, {"ok": False, "error": "browser_requests_refused"})
            return

        if control.enforce_host_header and not _host_header_allowed(
            self.headers.get("Host")
        ):
            self._send(403, {"ok": False, "error": "host_not_allowed"})
            return

        if self.headers.get("Transfer-Encoding") is not None:
            self._send(
                400, {"ok": False, "error": "transfer_encoding_not_supported"}
            )
            return

        body: bytes | None = None
        if method in ("GET", "HEAD"):
            raw_length = self.headers.get("Content-Length")
            if raw_length is not None:
                try:
                    has_body = int(raw_length) > 0
                except (TypeError, ValueError):
                    self._send(400, {"ok": False, "error": "bad_content_length"})
                    return
                if has_body:
                    self._send(400, {"ok": False, "error": "body_not_allowed"})
                    return
        elif method in ("POST", "PUT", "PATCH", "DELETE"):
            body, problem = self._consume_body(control, path)
            if problem is not None:
                self._send(*problem)
                return

        if path == "/health":
            if method not in ("GET", "HEAD"):
                self._method_not_allowed("GET, HEAD")
                return
            if control.health_requires_token and not self._token_ok(control):
                self._unauthorized()
                return
            self._send(200, control.health_payload())
            return

        if path == "/status":
            if method not in ("GET", "HEAD"):
                self._method_not_allowed("GET, HEAD")
                return
            if not self._token_ok(control):
                self._unauthorized()
                return
            self._send(200, control.status_payload())
            return

        if path == "/shutdown":
            if method != "POST":
                self._method_not_allowed("POST")
                return
            if not self._token_ok(control):
                self._unauthorized()
                return
            code, payload = control.request_shutdown()
            self._send(code, payload)
            return

        # -- turn routes (absent unless the host supplied a turn loop) --------
        if path == "/turn":
            self._route_turn(method, control, body)
            return

        if path.startswith("/turn/"):
            self._route_turn_poll(method, control, path)
            return

        if path == "/cancel":
            self._route_cancel(method, control, body)
            return

        self._send(404, {"ok": False, "error": "not_found"})

    # -- turn routes ------------------------------------------------------

    def _route_turn(
        self, method: str, control: "ControlServer", body: bytes | None
    ) -> None:
        """``POST /turn``: admit a turn, optionally waiting ``wait_ms``."""
        if method != "POST":
            self._method_not_allowed("POST")
            return
        if not self._token_ok(control):
            self._unauthorized()
            return

        loop = control.turn_loop
        if loop is None:
            self._send(503, {"ok": False, "error": "turn_unavailable"})
            return
        parser = getattr(loop, "parse_request", None)
        if parser is None:
            # A loop without a parser cannot enforce the documented body
            # contract; refuse rather than admit an unvalidated prompt.
            self._send(503, {"ok": False, "error": "turn_unavailable"})
            return

        try:
            request = parser(body if body is not None else b"")
            document = loop.submit(request)
        except Exception as exc:  # noqa: BLE001 - mapped by http_status
            if self._send_turn_error(exc):
                return
            raise

        wait_ms = int(getattr(request, "wait_ms", 0) or 0)
        request_id = document.get("request_id")
        if wait_ms > 0 and request_id:
            settled = loop.wait(request_id, wait_ms / 1000.0)
            if settled is not None:
                document = settled

        code = 200 if document.get("state") in _TURN_TERMINAL_STATES else 202
        self._send(code, document)

    def _route_turn_poll(
        self, method: str, control: "ControlServer", path: str
    ) -> None:
        """``GET|HEAD /turn/<request_id>``: the job document, at any time."""
        if method not in ("GET", "HEAD"):
            self._method_not_allowed("GET, HEAD")
            return
        if not self._token_ok(control):
            self._unauthorized()
            return

        loop = control.turn_loop
        if loop is None:
            self._send(503, {"ok": False, "error": "turn_unavailable"})
            return

        request_id = path[len("/turn/"):]
        if not request_id or "/" in request_id:
            self._send(404, {"ok": False, "error": "not_found"})
            return
        if not _TURN_REQUEST_ID_RE.match(request_id):
            self._send(404, {"ok": False, "error": "unknown_request_id"})
            return

        try:
            document = loop.poll(request_id)
        except Exception as exc:  # noqa: BLE001 - mapped by http_status
            if self._send_turn_error(exc):
                return
            raise

        if document is None:
            self._send(404, {"ok": False, "error": "unknown_request_id"})
            return
        self._send(200, document)

    def _route_cancel(
        self, method: str, control: "ControlServer", body: bytes | None
    ) -> None:
        """``POST /cancel``: set the running turn's cancel event."""
        if method != "POST":
            self._method_not_allowed("POST")
            return
        if not self._token_ok(control):
            self._unauthorized()
            return

        loop = control.turn_loop
        if loop is None:
            self._send(503, {"ok": False, "error": "turn_unavailable"})
            return

        request_id = self._parse_cancel_body(body)
        if request_id is None:
            return  # the 400 is already sent

        try:
            document = loop.cancel(request_id)
        except Exception as exc:  # noqa: BLE001 - mapped by http_status
            if self._send_turn_error(exc):
                return
            raise

        self._send(
            202,
            {
                "ok": True,
                "request_id": document.get("request_id", request_id),
                "state": document.get("state", "cancelling"),
            },
        )

    def _parse_cancel_body(self, body: bytes | None) -> str | None:
        """Return a valid request id, or answer 400 and return ``None``."""
        problem: str | None = None
        request_id: Any = None

        if not body:
            problem = "body must be a JSON object"
        else:
            try:
                data = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                problem = "body is not valid JSON"
            else:
                if not isinstance(data, dict):
                    problem = "body must be a JSON object"
                else:
                    unknown = sorted(str(key) for key in set(data) - {"request_id"})
                    if unknown:
                        problem = "unknown field(s): " + ", ".join(unknown)
                    else:
                        request_id = data.get("request_id")
                        if not isinstance(request_id, str) or not (
                            _TURN_REQUEST_ID_RE.match(request_id)
                        ):
                            problem = (
                                "request_id must be 1-64 characters of "
                                "[A-Za-z0-9_-]"
                            )

        if problem is not None:
            self._send(
                400,
                {"ok": False, "error": "turn_bad_request", "detail": problem},
            )
            return None
        return str(request_id)

    def _send_turn_error(self, exc: BaseException) -> bool:
        """Answer a turn error without importing the turn loop's hierarchy.

        The loop's exceptions carry ``http_status`` (int), ``code``,
        ``message`` and ``detail``. Anything else returns False so the caller
        can re-raise into the generic 500 path.
        """
        status = getattr(exc, "http_status", None)
        if isinstance(status, bool) or not isinstance(status, int):
            return False

        payload: dict[str, Any] = {
            "ok": False,
            "error": str(getattr(exc, "code", "turn_error")),
        }
        message = getattr(exc, "message", "")
        if message:
            payload["detail"] = str(message)[:2000]
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            payload.update({str(key): value for key, value in detail.items()})

        # One turn at a time: tell the caller when to come back.
        extra = (("Retry-After", "1"),) if status == 429 else ()
        self._send(status, payload, extra_headers=extra)
        return True

    # -- request helpers --------------------------------------------------

    def _browser_shaped(self) -> bool:
        for name in ("Origin", "Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-Dest"):
            if self.headers.get(name):
                return True
        return False

    def _token_ok(self, control: "ControlServer") -> bool:
        supplied = self.headers.get(TOKEN_HEADER)
        if supplied is None:
            return False
        return control.check_token(supplied)

    def _consume_body(
        self, control: "ControlServer", path: str
    ) -> tuple[bytes | None, tuple[int, dict] | None]:
        """Read the body (bounded, per-route cap) and return it.

        The body is returned rather than discarded so ``/turn`` and ``/cancel``
        can parse it *after* the token check. Every other route ignores the
        value, which keeps the historical "read and discard" behaviour.
        """
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None, None

        cap = control.body_cap_for(path)
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            return None, (400, {"ok": False, "error": "bad_content_length"})
        if length < 0:
            return None, (400, {"ok": False, "error": "bad_content_length"})
        if length > cap:
            return (
                None,
                (
                    413,
                    {
                        "ok": False,
                        "error": "request_too_large",
                        "limit": cap,
                    },
                ),
            )
        if not length:
            return b"", None
        try:
            data = self.rfile.read(length)
        except (TimeoutError, OSError):
            return None, (400, {"ok": False, "error": "body_read_failed"})
        return data, None

    def handle_expect_100(self) -> bool:  # noqa: D401
        """Validate size before inviting the body, instead of after."""
        control = self.server.control
        try:
            path = urlsplit(self.path).path or "/"
        except ValueError:
            path = "/"
        cap = control.body_cap_for(path)
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except (TypeError, ValueError):
            self._send(400, {"ok": False, "error": "bad_content_length"})
            return False
        if length > cap:
            self._send(
                413,
                {
                    "ok": False,
                    "error": "request_too_large",
                    "limit": cap,
                },
            )
            return False
        self.send_response_only(100)
        self.end_headers()
        return True

    # -- response helpers -------------------------------------------------

    def _send(
        self,
        code: int,
        payload: Mapping[str, Any],
        *,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        body = (
            json.dumps(dict(payload), sort_keys=True, default=str).encode("utf-8")
            + b"\n"
        )
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        self._responded = True
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _method_not_allowed(self, allow: str) -> None:
        self._send(
            405,
            {"ok": False, "error": "method_not_allowed", "allow": allow},
            extra_headers=(("Allow", allow),),
        )

    def _unauthorized(self) -> None:
        self._send(
            401,
            {"ok": False, "error": "unauthorized"},
            extra_headers=(("WWW-Authenticate", 'RT-Token realm="rt-sidecar"'),),
        )

    # -- logging ----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        control = getattr(self.server, "control", None)
        if control is None:
            return
        try:
            control._log(fmt % args)
        except Exception:  # pragma: no cover - defensive
            pass


# ---------------------------------------------------------------------------
# Control server
# ---------------------------------------------------------------------------


class ControlServer:
    """The sidecar's control surface.

    Typical use from the sidecar host::

        server = ControlServer(token=token, status_path=..., on_shutdown=stop_payload)
        port = server.start()
        ...
        server.stop()

    ``on_shutdown`` is called exactly once per server lifetime, after a
    successful ``POST /shutdown``. It must *request* process exit (set an event,
    schedule a clean teardown from the main thread); raising ``SystemExit``
    inside it only ends the calling thread. The transport closes its own
    listening socket immediately after the callback; process exit belongs to
    the caller.
    """

    def __init__(
        self,
        *,
        token: str,
        host: str = "127.0.0.1",
        port: int = 0,
        status_path: str | os.PathLike[str] | None = None,
        token_file: str | os.PathLike[str] | None = None,
        on_shutdown: Callable[[], None] | None = None,
        shutdown_delay: float = 0.0,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        max_body_bytes: int = MAX_BODY_BYTES,
        turn_loop: Any = None,
        max_turn_body_bytes: int = MAX_TURN_BODY_BYTES,
        health_requires_token: bool = False,
        reject_browser_requests: bool = True,
        enforce_host_header: bool = True,
        port_fallback: bool = True,
        instance_id: str | None = None,
        entry_point: str | None = None,
        extra_status: Mapping[str, Any] | None = None,
        clock: Callable[[], float] = time.time,
        log_sink: Callable[[str], None] | None = None,
        verbose: bool = False,
    ) -> None:
        if not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(
                f"token must be a string of at least {MIN_TOKEN_LENGTH} "
                "characters; use transport.generate_token()"
            )
        if int(port) < 0 or int(port) > 65535:
            raise ValueError(f"port out of range: {port!r}")
        if int(max_body_bytes) <= 0:
            raise ValueError("max_body_bytes must be positive")
        if int(max_turn_body_bytes) <= 0:
            raise ValueError("max_turn_body_bytes must be positive")

        self._host = normalise_host(host)
        self._host_actual: str | None = None
        self._port_requested = int(port)
        self._port = 0
        self._port_fallback = bool(port_fallback)
        self._bind_fallback = False
        self._bind_error: str | None = None

        self._token = token
        self._token_bytes = token.encode("utf-8")

        self.instance_id = instance_id or uuid.uuid4().hex
        self.entry_point = entry_point
        self.max_body_bytes = int(max_body_bytes)
        # Duck-typed: parse_request / submit / poll / wait / cancel /
        # status_fields. None means this host serves no turns (503).
        self.turn_loop = turn_loop
        self.max_turn_body_bytes = int(max_turn_body_bytes)
        self.health_requires_token = bool(health_requires_token)
        self.reject_browser_requests = bool(reject_browser_requests)
        self.enforce_host_header = bool(enforce_host_header)
        self.shutdown_delay = max(0.0, float(shutdown_delay))

        self.token_file = Path(token_file) if token_file is not None else None

        self._on_shutdown = on_shutdown
        self._clock = clock
        self._log_sink = log_sink
        self._verbose = bool(verbose)

        self._server: _LoopbackHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._listener_lock = threading.Lock()
        self._listener_closed = False

        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False
        self._shutdown_thread: threading.Thread | None = None
        self.shutdown_requested = threading.Event()
        self.listener_closed = threading.Event()
        self.shutdown_calls = 0

        self._state = _STATE_STARTING
        self._error: str | None = None
        self._stop_reason: str | None = None
        self._heartbeat_seq = 0
        self._heartbeat_lock = threading.Lock()
        self._requests_total = 0
        self._started_at: str | None = None
        self._ready_at: str | None = None
        self._started_monotonic = time.monotonic()

        self._extra_status = dict(extra_status or {})

        self._status: StatusFile | None = None
        if status_path is not None:
            self._status = StatusFile(
                status_path,
                tick=self._heartbeat_fields,
                interval=float(heartbeat_interval),
                clock=clock,
            )

    # -- properties -------------------------------------------------------

    @property
    def host(self) -> str:
        return self._host_actual or self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        host = self.host
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self._port}"

    @property
    def state(self) -> str:
        return self._state

    @property
    def requests_total(self) -> int:
        return self._requests_total

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_started

    @property
    def status_path(self) -> Path | None:
        return self._status.path if self._status is not None else None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> int:
        """Bind, serve, publish ``status.json``. Returns the bound port."""
        if self._server is not None:
            raise RuntimeError("control server already started")

        host = self._host
        server_class = (
            _LoopbackHTTPServerV6 if host == "::1" else _LoopbackHTTPServer
        )
        requested = self._port_requested

        try:
            server = server_class((host, requested), _Handler)
        except OSError as exc:
            self._bind_error = f"{type(exc).__name__}: {exc}"
            if requested == 0 or not self._port_fallback:
                raise PortUnavailable(
                    f"cannot bind {host}:{requested}: {exc}"
                ) from exc
            server = server_class((host, 0), _Handler)
            self._bind_fallback = True

        server.control = self
        self._server = server
        bound = server.server_address
        self._host_actual = str(bound[0])
        self._port = int(bound[1])

        self._thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="rt-sidecar-http",
            daemon=True,
        )
        self._thread.start()

        self._state = _STATE_READY
        self._started_at = _utc_now(self._clock)
        # The listener is bound at this point, so this is the moment the
        # documented "ready" transition happens. Written once and preserved.
        self._ready_at = self._started_at
        self._started_monotonic = time.monotonic()

        if self._status is not None:
            self._status.seed_from_existing(pid=os.getpid())
            self._status.update(**self.status_fields())
            self._status.start()

        self._log(
            f"control listening on {self.base_url} "
            f"(instance {self.instance_id}, requested port {requested})"
        )
        return self._port

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop serving and finalise ``status.json``. Idempotent."""
        self._close_listener()

        thread = self._shutdown_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.5, timeout))

        if self._state != _STATE_ERROR:
            self._state = _STATE_STOPPED
        if self._stop_reason is None:
            self._stop_reason = "local-stop"

        if self._status is not None:
            exited_cleanly = self._state != _STATE_ERROR
            self._status.stop(
                final={
                    "state": self._state,
                    "stopped_at": _utc_now(self._clock),
                    "pid": os.getpid(),
                    "instance_id": self.instance_id,
                    # A stopped document must never look fresh: the beat is
                    # dead the moment the writer stops.
                    "heartbeat_mono": None,
                    "heartbeat_at": None,
                    "heartbeat_seq": 0,
                    # The listener is already closed: a stopped document must not
                    # name a dead port or a token that is about to be deleted.
                    "port": None,
                    "token_file": None,
                    # The host wrote this record, including its last one.
                    "writer": "host",
                    "exit": "graceful" if exited_cleanly else "crash",
                }
            )

    def _close_listener(self) -> None:
        with self._listener_lock:
            if self._listener_closed:
                return
            self._listener_closed = True
            server = self._server
            if server is None:
                self.listener_closed.set()
                return
            try:
                server.shutdown()
            except Exception:  # pragma: no cover - defensive
                pass
            try:
                server.server_close()
            except Exception:  # pragma: no cover - defensive
                pass
            self.listener_closed.set()
            self._log("control listener closed")

    # -- request handling -------------------------------------------------

    def _count_request(self) -> None:
        self._requests_total += 1

    def body_cap_for(self, path: str) -> int:
        """Per-route body cap.

        Only ``POST /turn`` accepts caller-sized input; ``/cancel`` carries one
        request id and keeps the control cap. ``Expect: 100-continue`` and the
        reader both ask here, so a legal 16 Ki-character prompt is not rejected
        at 4 KiB before the route is known.
        """
        return self.max_turn_body_bytes if path == "/turn" else self.max_body_bytes

    def check_token(self, supplied: str) -> bool:
        """Constant-time token comparison."""
        try:
            candidate = supplied.encode("utf-8", "strict")
        except (AttributeError, UnicodeError):
            return False
        return hmac.compare_digest(candidate, self._token_bytes)

    def health_payload(self) -> dict[str, Any]:
        """Liveness only. No token, no paths, no payload detail."""
        return {
            "ok": True,
            "status": self._state,
            "pid": os.getpid(),
            "instance_id": self.instance_id,
            "uptime_s": round(time.monotonic() - self._started_monotonic, 3),
            "python_version": platform.python_version(),
        }

    def status_payload(self) -> dict[str, Any]:
        """Full state. Token required by the route, not by this method."""
        payload = self.status_fields()
        payload["ok"] = True
        return payload

    def status_fields(self) -> dict[str, Any]:
        """The single source of truth for ``status.json`` and ``GET /status``.

        Field names follow contract S1 (``lifecycle.py`` is the reader):
        ``requested_port``, ``last_error``, ``sys_executable`` and
        ``heartbeat_mono`` are consumed there by name. ``heartbeat_mono`` is
        ``time.monotonic()``, which on Windows (``GetTickCount64``) and on
        POSIX (``CLOCK_MONOTONIC``) shares an origin across processes on the
        same machine, so the supervisor can age it.
        """
        fields: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "pid": os.getpid(),
            "instance_id": self.instance_id,
            "state": self._state,
            "host": self.host,
            "port": self._port,
            "requested_port": self._port_requested or None,
            "bind_fallback": self._bind_fallback,
            "bind_error": self._bind_error,
            "started_at": self._started_at,
            "ready_at": self._ready_at,
            "heartbeat_at": _utc_now(self._clock),
            "heartbeat_mono": time.monotonic(),
            "heartbeat_seq": self._heartbeat_seq,
            "uptime_s": round(time.monotonic() - self._started_monotonic, 3),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation().lower(),
            "sys_prefix": sys.prefix,
            "sys_executable": sys.executable,
            "entry_point": self.entry_point,
            "import_ok": self._error is None,
            "last_error": self._error,
            "token_fingerprint": token_fingerprint(self._token),
            "token_file": str(self.token_file) if self.token_file else None,
            "shutdown_requested": self._shutdown_started,
            "stop_reason": self._stop_reason,
            "requests_total": self._requests_total,
            "max_body_bytes": self.max_body_bytes,
            "status_write_errors": (
                self._status.write_errors if self._status is not None else 0
            ),
        }
        fields.update(self._extra_status)

        # Turn counters last: they are live, and they are the one part of the
        # document this class does not own. Never prompt, result text or error
        # message -- ``TurnLoop.status_fields`` guarantees counts only.
        loop = self.turn_loop
        if loop is not None:
            try:
                turn_fields = loop.status_fields()
            except Exception:  # noqa: BLE001 - status must never break a beat
                turn_fields = None
            if isinstance(turn_fields, dict):
                fields.update(turn_fields)
        return fields

    def _heartbeat_fields(self) -> dict[str, Any]:
        with self._heartbeat_lock:
            self._heartbeat_seq += 1
            sequence = self._heartbeat_seq
        fields = self.status_fields()
        fields["heartbeat_seq"] = sequence
        return fields

    def request_shutdown(self) -> tuple[int, dict[str, Any]]:
        """Accept a shutdown once. Returns ``(http_status, payload)``."""
        with self._shutdown_lock:
            if self._shutdown_started:
                return (
                    409,
                    {
                        "ok": False,
                        "error": "already_shutting_down",
                        "instance_id": self.instance_id,
                        "state": self._state,
                    },
                )
            self._shutdown_started = True
            self.shutdown_calls += 1
            self._state = _STATE_STOPPING
            self._stop_reason = "http-shutdown"

        self._shutdown_thread = threading.Thread(
            target=self._run_shutdown, name="rt-sidecar-shutdown", daemon=True
        )
        self._shutdown_thread.start()

        return (
            200,
            {
                "ok": True,
                "state": _STATE_STOPPING,
                "pid": os.getpid(),
                "instance_id": self.instance_id,
            },
        )

    def _run_shutdown(self) -> None:
        try:
            if self.shutdown_delay:
                time.sleep(self.shutdown_delay)
            if self._status is not None:
                self._status.update(**self.status_fields())
            if self._on_shutdown is not None:
                self._on_shutdown()
        except Exception as exc:
            self._error = f"shutdown callback failed: {type(exc).__name__}: {exc}"
            self._state = _STATE_ERROR
            self._stop_reason = "shutdown-error"
            if self._status is not None:
                self._status.update(**self.status_fields())
            self._log_exc("shutdown callback failed")
        finally:
            self.shutdown_requested.set()
            self._close_listener()

    # -- logging ----------------------------------------------------------

    def _log(self, message: str) -> None:
        if self._log_sink is not None:
            try:
                self._log_sink(str(message))
                return
            except Exception:  # pragma: no cover - defensive
                return
        if self._verbose:
            try:
                print(f"[rt-sidecar] {message}", file=sys.stderr, flush=True)
            except Exception:  # pragma: no cover - pythonw has no stderr
                pass

    def _log_exc(self, message: str) -> None:
        # Never include request headers: X-RT-Token must not reach a log.
        if self._log_sink is not None or self._verbose:
            self._log(message)


# ---------------------------------------------------------------------------
# Client (host side / tests)
# ---------------------------------------------------------------------------


def _parse_base_url(base_url: str) -> tuple[str, int]:
    parts = urlsplit(str(base_url))
    if parts.scheme != "http":
        raise ValueError(f"control client speaks http only, got {parts.scheme!r}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("base_url has no host")
    if host == "localhost":
        host = "127.0.0.1"
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            f"control client is loopback-only; refusing host {host!r}"
        ) from exc
    if not parsed.is_loopback:
        raise ValueError(f"control client is loopback-only; refusing host {host!r}")
    return host, int(parts.port or 80)


def request(
    base_url: str,
    path: str = "/health",
    *,
    method: str = "GET",
    token: str | None = None,
    timeout: float = 2.0,
    body: bytes | str | Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Call the control surface. Returns ``(status_code, parsed_body)``.

    Uses ``http.client`` directly so ``HTTP_PROXY`` in the environment can never
    route ``X-RT-Token`` off the machine.

    A caller that sets ``wait_ms`` on ``POST /turn`` must pass
    ``timeout >= wait_ms / 1000 + 1.0``: the default 2.0 s is exactly the
    maximum wait and leaves no room for the response.
    """
    host, port = _parse_base_url(base_url)

    payload: bytes | None
    if body is None:
        payload = None
    elif isinstance(body, (bytes, bytearray)):
        payload = bytes(body)
    elif isinstance(body, str):
        payload = body.encode("utf-8")
    else:
        payload = json.dumps(dict(body)).encode("utf-8")

    send_headers: dict[str, str] = {}
    if payload is not None and not any(
        key.lower() == "content-type" for key in (headers or {})
    ):
        send_headers["Content-Type"] = "application/json"
    if token:
        send_headers[TOKEN_HEADER] = token
    if headers:
        send_headers.update(headers)

    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request(method, path, body=payload, headers=send_headers)
        response = connection.getresponse()
        # A terminal turn document can approach MAX_TURN_RESPONSE_BYTES; the old
        # 64 KiB read would silently truncate it into invalid JSON.
        raw = response.read(MAX_TURN_RESPONSE_BYTES)
        status = int(response.status)
    finally:
        connection.close()

    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        parsed = {}
    return status, (parsed if isinstance(parsed, dict) else {})


def start_control_server(
    directory: str | os.PathLike[str],
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
    **kwargs: Any,
) -> ControlServer:
    """One-call bootstrap used by the sidecar host and by tests.

    Generates a token, writes it with a restricted ACL, and starts the server.
    ``status.json`` lands in the same directory.
    """
    instance_dir = Path(directory)
    instance_dir.mkdir(parents=True, exist_ok=True)

    token_path = instance_dir / TOKEN_FILE_NAME
    status_path = instance_dir / STATUS_FILE_NAME

    active_token = token or generate_token()
    write_token_file(token_path, active_token)

    server = ControlServer(
        token=active_token,
        host=host,
        port=port,
        status_path=status_path,
        token_file=token_path,
        **kwargs,
    )
    server.start()
    return server
