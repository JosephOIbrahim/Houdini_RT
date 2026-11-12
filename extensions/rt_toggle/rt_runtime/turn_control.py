"""Turn client for the sidecar control surface.

Standard library only, no ``hou``, no Qt, no network at import time. This is the
piece a Houdini surface calls to submit a turn and follow it to a result; the
polling logic lives here rather than in the panel so it can be tested without
Houdini (see ``tests/test_turn_control.py``).

Wire contract, from ``rt_runtime.sidecar.transport`` and
``rt_runtime.sidecar.turn_loop``:

* ``POST /turn``            -> 202 admitted, or 200 when the turn already ended
                               (``wait_ms`` was used).
* ``GET  /turn/<id>``       -> 200 with the job document, or 404 once evicted.
* ``POST /cancel``          -> 202 with ``state: cancelling``.

The job document carries ``request_id``, ``state``, ``result``, ``error_kind``
and ``poll``. Terminal states are ``complete``, ``cancelled``, ``error`` and
``timeout``.

Every method returns the raw ``(http_status, body)`` pair, unchanged. A caller
must not collapse 202 into failure: a running turn is a success.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: File name the supervisor writes the per-launch token to (``lifecycle.py``).
TOKEN_FILENAME = "token"

#: The loop's terminal states. Mirrors ``turn_loop._TERMINAL_STATES``.
TERMINAL_TURN_STATES = frozenset({"complete", "cancelled", "error", "timeout"})


def _transport() -> Any:
    """Lazy import: keeps this module importable without the sidecar package."""
    from .sidecar import transport

    return transport


@dataclass(frozen=True)
class TurnControl:
    """A bound control surface: base URL plus the directory holding the token.

    The token is read on every call and never cached. The supervisor writes a
    fresh token each launch (``lifecycle.py``), so a cached value becomes a 401
    after a restart.
    """

    base_url: str
    state_dir: Path
    default_timeout: float = 2.0

    # -- transport ----------------------------------------------------------

    def token(self) -> str:
        """Read the current token, raising if it is missing or malformed."""
        return _transport().read_token_file(Path(self.state_dir) / TOKEN_FILENAME)

    def _call(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        wait_ms: int = 0,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        limit = self.default_timeout if timeout is None else float(timeout)
        if wait_ms > 0:
            # Documented rule: a caller that sets wait_ms must leave room for
            # the response on top of the wait itself.
            limit = max(limit, wait_ms / 1000.0 + 1.0)
        return _transport().request(
            self.base_url,
            path,
            method=method,
            token=self.token(),
            timeout=limit,
            body=body,
        )

    # -- public surface -----------------------------------------------------

    def submit(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        wait_ms: int = 0,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Admit a turn. ``wait_ms=0`` returns 202 immediately.

        A caller-supplied ``request_id`` lets the caller recover after an
        ambiguous response timeout. Only keys the loop accepts are sent;
        unknown keys are rejected with 400.
        """
        body: dict[str, Any] = {"prompt": str(prompt), "wait_ms": int(wait_ms)}
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        if request_id is not None:
            body["request_id"] = str(request_id)
        return self._call(
            "/turn", method="POST", body=body, wait_ms=int(wait_ms), timeout=timeout
        )

    def status(
        self, request_id: str, *, timeout: float | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Read one turn document: 200 with the document, 404 once evicted."""
        return self._call(f"/turn/{request_id}", timeout=timeout)

    def cancel(
        self, request_id: str, *, timeout: float | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Ask the running turn to stop. Cooperative: the loop never kills."""
        return self._call(
            "/cancel",
            method="POST",
            body={"request_id": str(request_id)},
            timeout=timeout,
        )


@dataclass
class TurnPoller:
    """Follow one admitted turn without Qt, threads or Houdini.

    ``refresh()`` is called by whoever owns a clock: a Qt timer in the panel, a
    plain loop in a test. Once ``done`` is true the poller stops issuing
    requests and returns the last document.
    """

    control: TurnControl
    request_id: str
    document: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    _done: bool = field(default=False, init=False)

    @property
    def state(self) -> str:
        if self.document:
            return str(self.document.get("state") or "unknown")
        return "error" if self.error else "running"

    @property
    def done(self) -> bool:
        return self._done

    @property
    def result(self) -> Any:
        return self.document.get("result")

    def refresh(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Poll once, optionally bounding the request by ``timeout`` seconds.

        Never raises. Transport exceptions and 5xx responses are transient:
        the request id and poller stay live so the turn can still be cancelled
        and a later poll can recover. Only a terminal document, an evicted id,
        or rejected credentials end the poller.
        """
        if self._done:
            return self.document
        try:
            if timeout is None:
                code, body = self.control.status(self.request_id)
            else:
                code, body = self.control.status(
                    self.request_id, timeout=max(0.0, float(timeout))
                )
        except Exception as exc:  # noqa: BLE001 - the UI must not see a socket
            self.error = f"{type(exc).__name__}: {exc}"
            return self.document

        if code == 200:
            self.document = dict(body)
            self.error = ""
            self._done = self.state in TERMINAL_TURN_STATES
            return self.document
        if code == 404:
            self.error = "turn not found (expired, or the sidecar restarted)"
            self._done = True
        elif code in (401, 403):
            self.error = f"HTTP {code}: control token rejected"
            self._done = True
        else:
            # 5xx and other transient statuses: keep following the id.
            self.error = f"HTTP {code}"
        return self.document


def submit_turn(
    control: TurnControl, prompt: str, *, max_tokens: int | None = None
) -> tuple[int | None, TurnPoller]:
    """Generate an id, admit one turn, and always return a poller for it.

    The id is chosen before the request, so a timeout between admission and the
    response does not strand the turn: the poller can recover it with
    ``GET /turn/<id>`` and the cancel controls still know where to send a stop.
    Returns ``(http_status, poller)``; an exception during submission yields
    ``(None, poller)`` with the error recorded and the poller left active.
    """
    request_id = uuid.uuid4().hex
    try:
        code, body = control.submit(
            prompt, max_tokens=max_tokens, wait_ms=0, request_id=request_id
        )
    except Exception as exc:  # noqa: BLE001 - an uncertain admission is not fatal
        poller = TurnPoller(control, request_id)
        poller.error = f"submit uncertain: {type(exc).__name__}: {exc}"
        return None, poller

    body = dict(body) if isinstance(body, dict) else {}
    poller = TurnPoller(control, str(body.get("request_id") or request_id))
    poller.document = body
    return code, poller
