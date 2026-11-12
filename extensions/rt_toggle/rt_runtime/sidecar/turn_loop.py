#!/usr/bin/env python3
"""One-turn-at-a-time request loop for the HoudiniRT sidecar.

Design record: ``TURN_LOOP.md`` (same directory). This module is the payload
half of that design. It owns admission, the worker thread, cancellation, the
bounded result registry, and the JSON-safe projection of ``AgentTurnResult``.
It does not own HTTP: ``sidecar/transport.py`` calls :meth:`TurnLoop.start`,
:meth:`TurnLoop.poll`, :meth:`TurnLoop.cancel` and :meth:`TurnLoop.shutdown`
from its route handlers. The host builds the entry-point closure and hands it
here.

Hard properties, in the order they matter:

* **One turn at a time.** The active slot is taken under one lock; a second
  admission is refused with :class:`TurnBusy`. Turn work never runs on an HTTP
  request thread, so ``/health``, ``/status`` and ``/shutdown`` cannot be
  blocked by a turn.
* **No payload imports.** No ``hou``, no ``anthropic``, no ``synapse``, no
  ``rt_runtime``. The entry point is a callable the host supplies; this module
  only knows its signature shape. Importable and testable on CPython 3.12+.
* **The prompt is caller data.** It is never logged, never written to
  ``status.json``, never placed in an error message, and is dropped from
  memory as soon as the worker finishes.
* **Every public value is JSON-safe.** :meth:`TurnLoop.poll` and
  :meth:`TurnLoop.status_fields` can be serialised with ``json.dumps``
  without a ``default=`` hook.
* **Waiting is bounded.** A caller can wait at most ``MAX_WAIT_MS`` on the
  request thread; everything longer is polled.
* **A hung payload cannot be killed from Python.** The timeout is cooperative
  (``run_turn`` checks a ``threading.Event`` at three yield points) plus a
  quarantine for a worker that ignores the event. There is no thread kill;
  the process-level stop remains the only true hard stop.

Run shape (tests, from the extension root)::

    py -3.12 -m pytest tests/test_turn_loop.py -q

Merged into the repo at ``rt_runtime/sidecar/turn_loop.py``. Nothing else in
this module changed: it imports no sibling, so the sidecar's Tier 0 import
graph is untouched.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "AGENT_STATUSES",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_RESULT_TTL_S",
    "DEFAULT_TIMEOUT_GRACE_S",
    "DEFAULT_TURN_TIMEOUT_S",
    "MAX_ERROR_CHARS",
    "MAX_MAX_TOKENS",
    "MAX_PROMPT_CHARS",
    "MAX_REQUEST_ID_CHARS",
    "MAX_RETAINED_RESULTS",
    "MAX_RESULT_TEXT_CHARS",
    "MAX_TURN_BODY_BYTES",
    "MAX_WAIT_MS",
    "SCHEMA_VERSION",
    "STATE_CANCELLED",
    "STATE_CANCELLING",
    "STATE_COMPLETE",
    "STATE_ERROR",
    "STATE_RUNNING",
    "STATE_TIMEOUT",
    "STATUS_API_ERROR",
    "STATUS_CANCELLED",
    "STATUS_COMPLETE",
    "STATUS_MAX_ITERATIONS",
    "STATUS_UNKNOWN_STOP",
    "TurnBadRequest",
    "TurnBusy",
    "TurnDuplicate",
    "TurnError",
    "TurnLoop",
    "TurnNotRunning",
    "TurnRequest",
    "TurnShuttingDown",
    "TurnUnknownRequest",
    "call_entry_point",
    "error_response",
    "json_safe_result",
    "parse_turn_request",
]

SCHEMA_VERSION = 1

#: Largest request body the transport should accept for ``/turn``. It is
#: larger than the control routes' 4 KiB because a prompt is caller data:
#: ``4 * MAX_PROMPT_CHARS`` (worst-case UTF-8) plus a JSON envelope.
MAX_TURN_BODY_BYTES = 131072

#: Longest accepted prompt, in characters. 16 Ki characters is a generous
#: single instruction; anything larger is a document upload, not a turn.
MAX_PROMPT_CHARS = 16384

#: Longest result text returned to the caller, in characters. Truncation is
#: reported with ``text_truncated`` rather than being silent.
MAX_RESULT_TEXT_CHARS = 65536

#: Longest error string carried in a result, in characters.
MAX_ERROR_CHARS = 2000

MAX_REQUEST_ID_CHARS = 64

#: Longest ``wait_ms`` a caller may request. The transport must set its own
#: socket timeout to at least ``wait_ms / 1000 + 1.0``.
MAX_WAIT_MS = 2000

MIN_MAX_TOKENS = 1
MAX_MAX_TOKENS = 32768

#: Sidecar policy default. The loop itself does not apply it: when the caller
#: omits ``max_tokens`` the host closure decides (it owns the model policy).
DEFAULT_MAX_TOKENS = 4096

#: Soft deadline for one turn. At this point the cancel event is set and the
#: job is published as ``timeout``; a cooperative loop stops at its next yield.
DEFAULT_TURN_TIMEOUT_S = 120.0

#: Extra time the slot is held after the soft deadline, waiting for a
#: cooperative worker to exit. After this the worker is quarantined and its
#: late result is discarded.
DEFAULT_TIMEOUT_GRACE_S = 5.0

#: How long a terminal result stays pollable.
DEFAULT_RESULT_TTL_S = 300.0

#: How many terminal results are retained. Bounded memory: 8 * 64 KiB of text.
MAX_RETAINED_RESULTS = 8

# -- Payload status vocabulary ---------------------------------------------
# Mirrors synapse.cognitive.agent_loop without importing it.

STATUS_COMPLETE = "complete"
STATUS_CANCELLED = "cancelled"
STATUS_API_ERROR = "api_error"
STATUS_MAX_ITERATIONS = "max_iterations"
STATUS_UNKNOWN_STOP = "unknown_stop_reason"

AGENT_STATUSES = frozenset(
    {
        STATUS_COMPLETE,
        STATUS_CANCELLED,
        STATUS_API_ERROR,
        STATUS_MAX_ITERATIONS,
        STATUS_UNKNOWN_STOP,
    }
)

# -- Sidecar job states -----------------------------------------------------

STATE_RUNNING = "running"
STATE_CANCELLING = "cancelling"
STATE_COMPLETE = "complete"
STATE_CANCELLED = "cancelled"
STATE_ERROR = "error"
STATE_TIMEOUT = "timeout"

_TERMINAL_STATES = frozenset(
    {STATE_COMPLETE, STATE_CANCELLED, STATE_ERROR, STATE_TIMEOUT}
)

#: Payload status -> sidecar job state. ``max_iterations`` is a real
#: ``run_turn`` status and is a failure: reporting it as ``complete`` would lie.
_STATE_BY_STATUS = {
    STATUS_COMPLETE: STATE_COMPLETE,
    STATUS_CANCELLED: STATE_CANCELLED,
    STATUS_API_ERROR: STATE_ERROR,
    STATUS_MAX_ITERATIONS: STATE_ERROR,
    STATUS_UNKNOWN_STOP: STATE_ERROR,
}

_ERROR_KIND_BY_STATUS = {
    STATUS_API_ERROR: "api_error",
    STATUS_MAX_ITERATIONS: "max_iterations",
    STATUS_UNKNOWN_STOP: "unknown_stop_reason",
}

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,%d}$" % MAX_REQUEST_ID_CHARS)
_TURN_FIELDS = frozenset({"prompt", "max_tokens", "request_id", "wait_ms"})
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


# ---------------------------------------------------------------------------
# Errors (mapped to HTTP by the transport)
# ---------------------------------------------------------------------------


class TurnError(Exception):
    """Base error. ``http_status`` and ``code`` are the transport contract."""

    code = "turn_error"
    http_status = 500

    def __init__(self, message: str = "", **detail: Any) -> None:
        super().__init__(message or self.code)
        self.message = str(message or "")
        # Detail values are scalars only; they reach a JSON response.
        self.detail: dict[str, Any] = {str(key): value for key, value in detail.items()}


class TurnBadRequest(TurnError):
    """The JSON body or one of its fields is not usable."""

    code = "turn_bad_request"
    http_status = 400


class TurnBusy(TurnError):
    """A turn is already running; this sidecar runs one at a time."""

    code = "turn_in_progress"
    http_status = 429

    def __init__(self, active_request_id: str | None = None) -> None:
        super().__init__(
            "a turn is already running",
            active_request_id=active_request_id,
            retry_after_s=1,
        )


class TurnDuplicate(TurnError):
    """The supplied request id is already running."""

    code = "duplicate_request_id"
    http_status = 409

    def __init__(self, active_request_id: str | None = None) -> None:
        super().__init__(
            "request_id is already running; poll it or use a new id",
            active_request_id=active_request_id,
        )


class TurnShuttingDown(TurnError):
    """The sidecar is shutting down and accepts no new turns."""

    code = "turn_shutting_down"
    http_status = 503


class TurnUnknownRequest(TurnError):
    """No retained job has that request id (never seen or evicted)."""

    code = "unknown_request_id"
    http_status = 404


class TurnNotRunning(TurnError):
    """Cancel was requested for a turn that already reached a terminal state."""

    code = "turn_not_running"
    http_status = 409

    def __init__(self, state: str) -> None:
        super().__init__(
            "the turn already finished", state=str(state)
        )


def error_response(exc: TurnError) -> tuple[int, dict[str, Any]]:
    """Project a :class:`TurnError` to ``(http_status, json_body)``.

    The transport calls this after duck-typing the exception, so it never has
    to import this module. The prompt is not part of any error by construction.
    """
    payload: dict[str, Any] = {"ok": False, "error": exc.code}
    if exc.message:
        payload["detail"] = _clip(str(exc.message), MAX_ERROR_CHARS)
    payload.update(exc.detail)
    return exc.http_status, payload


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnRequest:
    """One validated ``POST /turn`` body."""

    prompt: str
    max_tokens: int | None = None
    request_id: str | None = None
    wait_ms: int = 0


def parse_turn_request(raw: Any) -> TurnRequest:
    """Parse and validate a ``/turn`` body.

    Accepts the raw bytes, a UTF-8 string, or an already-decoded mapping.
    Raises :class:`TurnBadRequest` with a message that names the field, never
    its value.
    """
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_TURN_BODY_BYTES:
            raise TurnBadRequest(f"body exceeds {MAX_TURN_BODY_BYTES} bytes")
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TurnBadRequest("body is not valid UTF-8") from exc

    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except RecursionError as exc:
            raise TurnBadRequest("body is nested too deeply") from exc
        except ValueError as exc:
            raise TurnBadRequest(f"body is not valid JSON: {exc}") from exc
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        raise TurnBadRequest("body must be a JSON object")

    if not isinstance(data, dict):
        raise TurnBadRequest("body must be a JSON object")

    unknown = sorted(str(key) for key in set(data) - _TURN_FIELDS)
    if unknown:
        raise TurnBadRequest("unknown field(s): " + ", ".join(unknown))

    prompt = data.get("prompt")
    if not isinstance(prompt, str):
        raise TurnBadRequest("prompt must be a string")
    if not prompt.strip():
        raise TurnBadRequest("prompt must not be empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise TurnBadRequest(
            f"prompt exceeds {MAX_PROMPT_CHARS} characters"
        )

    max_tokens = data.get("max_tokens")
    if max_tokens is not None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            raise TurnBadRequest("max_tokens must be an integer")
        if not MIN_MAX_TOKENS <= max_tokens <= MAX_MAX_TOKENS:
            raise TurnBadRequest(
                f"max_tokens must be between {MIN_MAX_TOKENS} and {MAX_MAX_TOKENS}"
            )

    request_id = data.get("request_id")
    if request_id is not None:
        if not isinstance(request_id, str) or not _REQUEST_ID_RE.match(request_id):
            raise TurnBadRequest(
                "request_id must be 1-%d characters of [A-Za-z0-9_-]"
                % MAX_REQUEST_ID_CHARS
            )

    wait_ms = data.get("wait_ms", 0)
    if isinstance(wait_ms, bool) or not isinstance(wait_ms, int):
        raise TurnBadRequest("wait_ms must be an integer")
    if not 0 <= wait_ms <= MAX_WAIT_MS:
        raise TurnBadRequest(f"wait_ms must be between 0 and {MAX_WAIT_MS}")

    return TurnRequest(
        prompt=prompt,
        max_tokens=max_tokens,
        request_id=request_id,
        wait_ms=wait_ms,
    )


# ---------------------------------------------------------------------------
# JSON-safe projection of AgentTurnResult
# ---------------------------------------------------------------------------


def json_safe_result(value: Any) -> dict[str, Any]:
    """Project an ``AgentTurnResult``-shaped object into a JSON-safe dict.

    The five contracted keys are always present: ``status``, ``iterations``,
    ``tool_calls_made``, ``error``, ``text``. ``usage`` and ``text_truncated``
    are included because they are cheap and honest.

    ``text`` is not a field on the real ``AgentTurnResult``; it is derived from
    the last assistant message's text blocks, or taken from an explicit
    ``.text`` attribute when a test double provides one.
    """
    if value is None or not hasattr(value, "status"):
        return _failure_result(
            "bad_result",
            f"entry point returned {type(value).__name__}, not an AgentTurnResult",
        )

    raw_status = getattr(value, "status", None)
    status = raw_status if isinstance(raw_status, str) else ""
    error = _clip(_stringify(getattr(value, "error", "")), MAX_ERROR_CHARS)

    if status not in AGENT_STATUSES:
        note = f"payload returned status {status!r}"
        if error:
            note = f"{note}: {error}"
        return _failure_result("unknown_status", note)

    text, truncated = _clip_text(_extract_text(value))
    return {
        "status": status,
        "iterations": _nonneg_int(getattr(value, "iterations", 0)),
        "tool_calls_made": _nonneg_int(getattr(value, "tool_calls_made", 0)),
        "error": error,
        "text": text,
        "text_truncated": truncated,
        "usage": _safe_usage(getattr(value, "usage", None)),
    }


def _failure_result(status: str, error: str) -> dict[str, Any]:
    return {
        "status": status,
        "iterations": 0,
        "tool_calls_made": 0,
        "error": _clip(str(error), MAX_ERROR_CHARS),
        "text": "",
        "text_truncated": False,
        "usage": [],
    }


def _extract_text(value: Any) -> str:
    """Final assistant text: explicit ``.text`` first, then ``messages``."""
    direct = getattr(value, "text", None)
    if isinstance(direct, str):
        return direct

    messages = getattr(value, "messages", None)
    if not isinstance(messages, (list, tuple)):
        return ""

    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if not isinstance(content, (list, tuple)):
            continue
        chunks: list[str] = []
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        if chunks:
            return "\n".join(chunks)
    return ""


def _safe_usage(value: Any) -> list[dict[str, int]]:
    """Keep only the four token counters, as non-negative ints."""
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[dict[str, int]] = []
    for row in value:
        if not isinstance(row, Mapping):
            continue
        cleaned: dict[str, int] = {}
        for key in _USAGE_KEYS:
            count = row.get(key)
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                cleaned[key] = count
        if cleaned:
            rows.append(cleaned)
    return rows


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _clip_text(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_RESULT_TEXT_CHARS:
        return text, False
    return text[:MAX_RESULT_TEXT_CHARS], True


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - a payload getter must not break a turn
        return "<unprintable>"


def _nonneg_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


# ---------------------------------------------------------------------------
# Entry-point call plan
# ---------------------------------------------------------------------------

_POSITIONAL_KINDS = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
)

_ROLE_CANCEL = "cancel_event"
_ROLE_MAX_TOKENS = "max_tokens"


def _shape_hint(name: str) -> str:
    return (
        f"turn entry point parameter {name!r} cannot be supplied by the "
        "sidecar; bind the model client and dispatcher in the host and expose "
        "fn(prompt, *, cancel_event=None, max_tokens=None)"
    )


@dataclass(frozen=True)
class _CallPlan:
    """How to call one entry point, decided once from its signature."""

    positional_roles: tuple[str, ...]
    keyword_roles: frozenset[str]
    accepts_var_keyword: bool

    def invoke(
        self,
        entry_point: Callable[..., Any],
        prompt: str,
        cancel_event: threading.Event | None,
        max_tokens: int | None,
    ) -> Any:
        values = {
            "prompt": prompt,
            _ROLE_CANCEL: cancel_event,
            _ROLE_MAX_TOKENS: max_tokens,
        }
        args = [values[role] for role in self.positional_roles]
        kwargs: dict[str, Any] = {}
        for role in self.keyword_roles:
            if role == _ROLE_MAX_TOKENS and max_tokens is None:
                continue
            kwargs[role] = values[role]
        if self.accepts_var_keyword:
            for role in (_ROLE_CANCEL, _ROLE_MAX_TOKENS):
                if role in self.keyword_roles or role in self.positional_roles:
                    continue
                if role == _ROLE_MAX_TOKENS and max_tokens is None:
                    continue
                kwargs[role] = values[role]
        return entry_point(*args, **kwargs)


def _plan_call(entry_point: Any) -> _CallPlan:
    """Validate the callable's shape and record how to call it.

    Preferred shape::

        fn(prompt, *, cancel_event=None, max_tokens=None)

    Accepted: a cancel parameter named ``cancel_event`` in any parameter kind,
    a ``max_tokens`` parameter, keyword-only parameters with defaults, and
    ``**kwargs``. Refused: a callable that needs a client, a dispatcher, or any
    other required argument. Binding those is the host's job, not the loop's.
    """
    if not callable(entry_point):
        raise TypeError("turn entry point must be callable")

    try:
        signature = inspect.signature(entry_point)
    except (TypeError, ValueError):
        # Builtins without an introspectable signature: assume fn(prompt).
        return _CallPlan(("prompt",), frozenset(), False)

    parameters = list(signature.parameters.values())
    positional = [p for p in parameters if p.kind in _POSITIONAL_KINDS]
    accepts_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters)

    if not positional:
        raise ValueError(
            "turn entry point must accept the prompt as its first positional "
            "parameter: fn(prompt, *, cancel_event=None, max_tokens=None)"
        )

    positional_roles: list[str] = ["prompt"]
    fill_positional = True
    for parameter in positional[1:]:
        if not fill_positional:
            if (
                parameter.kind == inspect.Parameter.POSITIONAL_ONLY
                and parameter.default is inspect.Parameter.empty
            ):
                raise ValueError(_shape_hint(parameter.name))
            continue
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            if parameter.name in (_ROLE_CANCEL, _ROLE_MAX_TOKENS):
                positional_roles.append(parameter.name)
            elif parameter.default is inspect.Parameter.empty:
                raise ValueError(_shape_hint(parameter.name))
            else:
                # A defaulted, unrelated positional slot. Anything after it
                # cannot be filled positionally without filling this one too.
                fill_positional = False
        elif parameter.name not in (_ROLE_CANCEL, _ROLE_MAX_TOKENS):
            if parameter.default is inspect.Parameter.empty:
                raise ValueError(_shape_hint(parameter.name))
            # A defaulted unrelated positional-or-keyword parameter is fine:
            # it is simply not passed.

    keyword_roles: set[str] = set()
    for parameter in parameters:
        if parameter.name in (_ROLE_CANCEL, _ROLE_MAX_TOKENS):
            if parameter.name in positional_roles:
                continue
            if parameter.kind in _POSITIONAL_KINDS or parameter.kind == inspect.Parameter.KEYWORD_ONLY:
                keyword_roles.add(parameter.name)
            continue
        if (
            parameter.kind == inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
        ):
            raise ValueError(_shape_hint(parameter.name))

    # A declared cancel_event / max_tokens that cannot be reached (for example
    # a positional-only slot behind a defaulted positional-only slot) is a
    # misconfiguration, not a quietly uncancellable turn.
    named = {parameter.name for parameter in parameters}
    for role in (_ROLE_CANCEL, _ROLE_MAX_TOKENS):
        if role in named and role not in positional_roles and role not in keyword_roles:
            if not accepts_var_keyword:
                raise ValueError(_shape_hint(role))

    return _CallPlan(tuple(positional_roles), frozenset(keyword_roles), accepts_var_keyword)


def call_entry_point(
    entry_point: Any,
    prompt: str,
    cancel_event: threading.Event | None = None,
    max_tokens: int | None = None,
) -> Any:
    """Call one entry point with the sidecar's three values, by shape.

    Raises ``TypeError`` / ``ValueError`` for an unsupported shape. Any
    exception raised by the callable is the caller's to handle.
    """
    plan = _plan_call(entry_point)
    return plan.invoke(entry_point, prompt, cancel_event, max_tokens)


# ---------------------------------------------------------------------------
# Internal job
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    request_id: str
    prompt: str
    max_tokens: int | None
    started_mono: float
    state: str = STATE_RUNNING
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    worker_done_event: threading.Event = field(default_factory=threading.Event)
    timer: threading.Timer | None = None
    result: dict[str, Any] | None = None
    error_kind: str | None = None
    finished_mono: float | None = None
    deadline_hit: bool = False
    quarantined: bool = False


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class TurnLoop:
    """Admission, one daemon worker, bounded wait, cancellation, retention.

    The transport holds one instance per ``ControlServer`` and calls it from
    route handlers. All public methods are thread-safe.

    ``parse_request`` is the module's parser, published on the instance so the
    transport can validate a ``/turn`` body without importing this module
    (``getattr(loop, "parse_request", None)``).
    """

    parse_request = staticmethod(parse_turn_request)

    def __init__(
        self,
        entry_point: Callable[..., Any],
        *,
        timeout_s: float = DEFAULT_TURN_TIMEOUT_S,
        timeout_grace_s: float = DEFAULT_TIMEOUT_GRACE_S,
        result_ttl_s: float = DEFAULT_RESULT_TTL_S,
        max_retained_results: int = MAX_RETAINED_RESULTS,
        max_prompt_chars: int = MAX_PROMPT_CHARS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if float(timeout_s) <= 0:
            raise ValueError("timeout_s must be greater than zero")
        if float(timeout_grace_s) < 0:
            raise ValueError("timeout_grace_s must not be negative")
        if float(result_ttl_s) <= 0:
            raise ValueError("result_ttl_s must be greater than zero")
        if int(max_retained_results) < 1:
            raise ValueError("max_retained_results must be at least 1")

        # Signature validation is deliberately eager: a mis-shaped payload is
        # a startup error, not a 500 on the first turn.
        self._plan = _plan_call(entry_point)
        self._entry_point = entry_point

        self._timeout_s = float(timeout_s)
        self._timeout_grace_s = float(timeout_grace_s)
        self._result_ttl_s = float(result_ttl_s)
        self._max_retained_results = int(max_retained_results)
        self._max_prompt_chars = int(max_prompt_chars)
        self._clock = clock

        self._lock = threading.RLock()
        self._jobs: "OrderedDict[str, _Job]" = OrderedDict()
        self._active: _Job | None = None
        self._shutting_down = False
        self._last_error_kind: str | None = None
        self._counters: dict[str, int] = {
            "total": 0,
            "completed": 0,
            "cancelled": 0,
            "failed": 0,
            "timed_out": 0,
            "quarantined": 0,
            "replays": 0,
            "rejected": 0,
            "late_after_quarantine": 0,
        }

    # -- public surface -----------------------------------------------------

    def start(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate and admit one turn. Returns the public job document.

        Raises :class:`TurnBadRequest`, :class:`TurnBusy`,
        :class:`TurnDuplicate` or :class:`TurnShuttingDown`.
        """
        body: dict[str, Any] = {"prompt": prompt}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if request_id is not None:
            body["request_id"] = request_id
        return self.submit(parse_turn_request(body))

    def submit(self, request: TurnRequest) -> dict[str, Any]:
        """Admit an already-validated :class:`TurnRequest`."""
        if len(request.prompt) > self._max_prompt_chars:
            raise TurnBadRequest(
                f"prompt exceeds {self._max_prompt_chars} characters"
            )

        with self._lock:
            self._reap_locked()

            if self._shutting_down:
                self._counters["rejected"] += 1
                raise TurnShuttingDown("sidecar is shutting down")

            if request.request_id:
                existing = self._jobs.get(request.request_id)
                if existing is not None:
                    if existing.state == STATE_RUNNING:
                        self._counters["rejected"] += 1
                        raise TurnDuplicate(existing.request_id)
                    # Terminal: idempotent replay of the stored outcome.
                    self._counters["replays"] += 1
                    return self._public_locked(existing)

            if self._active is not None:
                self._counters["rejected"] += 1
                raise TurnBusy(self._active.request_id)

            request_id = request.request_id or self._new_request_id_locked()
            job = _Job(
                request_id=request_id,
                prompt=request.prompt,
                max_tokens=request.max_tokens,
                started_mono=self._clock(),
            )
            self._jobs[request_id] = job
            self._active = job
            self._counters["total"] += 1

            timer = threading.Timer(
                self._timeout_s, self._on_soft_deadline, args=(request_id,)
            )
            timer.name = "rt-sidecar-turn-deadline"
            timer.daemon = True
            job.timer = timer
            timer.start()

            worker = threading.Thread(
                target=self._run,
                args=(job,),
                name="rt-sidecar-turn",
                daemon=True,
            )
            worker.start()

            return self._public_locked(job)

    def poll(self, request_id: str) -> dict[str, Any] | None:
        """Return the job document, or ``None`` when the id is not retained."""
        with self._lock:
            self._reap_locked()
            job = self._jobs.get(str(request_id))
            if job is None:
                return None
            return self._public_locked(job)

    def wait(self, request_id: str, timeout_s: float) -> dict[str, Any] | None:
        """Wait up to ``timeout_s`` for a terminal state, then poll.

        ``timeout_s`` is clamped to ``MAX_WAIT_MS`` so a request thread can
        never be held longer than the wire contract allows.
        """
        with self._lock:
            job = self._jobs.get(str(request_id))
        if job is None:
            return None
        limit = min(max(0.0, float(timeout_s)), MAX_WAIT_MS / 1000.0)
        if limit > 0:
            job.done_event.wait(limit)
        return self.poll(request_id)

    def cancel(self, request_id: str) -> dict[str, Any]:
        """Request cooperative cancellation of a running turn.

        The event is set; ``run_turn`` observes it at its next yield point.
        Idempotent while the turn is still running. Raises
        :class:`TurnUnknownRequest` or :class:`TurnNotRunning`.
        """
        with self._lock:
            job = self._jobs.get(str(request_id))
            if job is None:
                raise TurnUnknownRequest("no retained turn with that request_id")
            if job.state != STATE_RUNNING:
                raise TurnNotRunning(job.state)
            job.cancel_event.set()
            return self._public_locked(job)

    def shutdown(self, grace_s: float = 2.0) -> bool:
        """Refuse new turns, cancel the active one, and wait at most ``grace_s``.

        Returns True when the admission slot cleared. A hung worker makes this
        return False; the caller proceeds anyway (the worker is a daemon thread)
        and the process-level stop is the hard backstop. A worker already
        quarantined by the hard deadline is not waited for: it no longer holds
        the slot.
        """
        with self._lock:
            self._shutting_down = True
            job = self._active
            if job is not None:
                job.cancel_event.set()
        if job is None:
            return True
        return bool(job.worker_done_event.wait(max(0.0, float(grace_s))))

    def status_fields(self) -> dict[str, Any]:
        """Counters for ``GET /status`` and ``status.json``.

        Contains no prompt, no result text, and no error message: only counts,
        ids, ages and error kinds.
        """
        with self._lock:
            self._reap_locked()
            active = self._active
            return {
                "turn_state": STATE_RUNNING if active is not None else "idle",
                "turn_active_request_id": (
                    active.request_id if active is not None else None
                ),
                "turn_active_age_s": (
                    round(self._clock() - active.started_mono, 3)
                    if active is not None
                    else None
                ),
                "turn_shutting_down": self._shutting_down,
                "turns_total": self._counters["total"],
                "turns_completed": self._counters["completed"],
                "turns_cancelled": self._counters["cancelled"],
                "turns_failed": self._counters["failed"],
                "turns_timed_out": self._counters["timed_out"],
                "turns_quarantined": self._counters["quarantined"],
                "turns_replayed": self._counters["replays"],
                "turns_rejected": self._counters["rejected"],
                "turns_late_after_quarantine": self._counters[
                    "late_after_quarantine"
                ],
                "turn_last_error_kind": self._last_error_kind,
                "turn_retained_results": len(self._jobs),
                "turn_timeout_s": self._timeout_s,
                "turn_timeout_grace_s": self._timeout_grace_s,
            }

    # -- internals ----------------------------------------------------------

    def _new_request_id_locked(self) -> str:
        request_id = uuid.uuid4().hex
        while request_id in self._jobs:
            request_id = uuid.uuid4().hex
        return request_id

    def _run(self, job: _Job) -> None:
        try:
            try:
                value = self._plan.invoke(
                    self._entry_point, job.prompt, job.cancel_event, job.max_tokens
                )
            except BaseException as exc:  # noqa: BLE001 - a payload must not escape
                message = f"{type(exc).__name__}: {exc}"
                self._finish(
                    job,
                    STATE_ERROR,
                    result=_failure_result("error", message),
                    error_kind="entry_point_error",
                )
                return

            result = json_safe_result(value)
            status = str(result.get("status") or "")
            state = _STATE_BY_STATUS.get(status, STATE_ERROR)
            error_kind = _ERROR_KIND_BY_STATUS.get(status)
            if state == STATE_ERROR and error_kind is None:
                error_kind = "unknown_status"
            self._finish(job, state, result=result, error_kind=error_kind)
        finally:
            # Separate from done_event: done_event marks the result as terminal
            # (the soft deadline sets it too); this one marks the thread gone.
            job.worker_done_event.set()

    def _finish(
        self,
        job: _Job,
        state: str,
        *,
        result: dict[str, Any] | None,
        error_kind: str | None,
    ) -> None:
        with self._lock:
            if job.timer is not None:
                job.timer.cancel()

            if job.quarantined:
                # The slot was released at the hard deadline. The late result
                # belongs to nobody; discard it without touching the registry.
                self._counters["late_after_quarantine"] += 1
                return

            if job.deadline_hit:
                # The soft deadline already published "timeout". Keep it, drop
                # the late result, and release the slot now that the worker is
                # actually gone.
                if self._active is job:
                    self._active = None
                job.prompt = ""
                job.done_event.set()
                return

            if job.state != STATE_RUNNING:
                return

            job.state = state
            job.result = result
            job.error_kind = error_kind
            job.finished_mono = self._clock()
            job.prompt = ""  # drop caller data as soon as it is no longer needed

            if state == STATE_COMPLETE:
                self._counters["completed"] += 1
            elif state == STATE_CANCELLED:
                self._counters["cancelled"] += 1
            else:
                self._counters["failed"] += 1
                self._last_error_kind = error_kind or "error"

            if self._active is job:
                self._active = None

        job.done_event.set()

    def _on_soft_deadline(self, request_id: str) -> None:
        with self._lock:
            job = self._jobs.get(str(request_id))
            if job is None or job.state != STATE_RUNNING:
                return
            job.deadline_hit = True
            job.state = STATE_TIMEOUT
            job.error_kind = "timeout"
            job.result = None
            job.finished_mono = self._clock()
            self._counters["timed_out"] += 1
            self._last_error_kind = "timeout"
            # Cooperative stop. The worker's late result will be discarded.
            job.cancel_event.set()
        job.done_event.set()

    def _reap_locked(self) -> None:
        """Expire results, enforce the soft/hard deadline, and evict."""
        now = self._clock()
        active = self._active

        if active is not None:
            # Belt and braces: a timer that never fired must not wedge the
            # slot forever.
            if (
                active.state == STATE_RUNNING
                and now - active.started_mono >= self._timeout_s
            ):
                active.deadline_hit = True
                active.state = STATE_TIMEOUT
                active.error_kind = "timeout"
                active.finished_mono = now
                self._counters["timed_out"] += 1
                self._last_error_kind = "timeout"
                active.cancel_event.set()
                active.done_event.set()

            if (
                active.state == STATE_TIMEOUT
                and active.finished_mono is not None
                and now - active.finished_mono >= self._timeout_grace_s
            ):
                # Hard deadline: release the one-turn slot. The worker may
                # still be alive; it is quarantined and its result is ignored.
                active.quarantined = True
                self._counters["quarantined"] += 1
                self._active = None

        # TTL eviction of terminal results.
        for request_id in list(self._jobs):
            job = self._jobs.get(request_id)
            if job is None or job is self._active:
                continue
            if job.state not in _TERMINAL_STATES or job.finished_mono is None:
                continue
            if now - job.finished_mono >= self._result_ttl_s:
                del self._jobs[request_id]

        # Size eviction: oldest terminal, never the active job.
        for request_id in list(self._jobs):
            if len(self._jobs) <= self._max_retained_results:
                break
            job = self._jobs.get(request_id)
            if job is None or job is self._active:
                continue
            if job.state in _TERMINAL_STATES:
                del self._jobs[request_id]

    def _public_locked(self, job: _Job) -> dict[str, Any]:
        state = job.state
        cancel_requested = job.cancel_event.is_set()
        if state == STATE_RUNNING and cancel_requested:
            state = STATE_CANCELLING
        return {
            "schema": SCHEMA_VERSION,
            "ok": state == STATE_COMPLETE,
            "request_id": job.request_id,
            "state": state,
            "cancel_requested": cancel_requested,
            "age_s": round(self._clock() - job.started_mono, 3),
            "result": job.result,
            "error_kind": job.error_kind,
            "poll": f"/turn/{job.request_id}",
        }
