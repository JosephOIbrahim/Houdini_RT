"""Tests for the sidecar turn client and poller.

Real control server on 127.0.0.1 with an injected ``TurnLoop``: no Houdini, no
Qt, no network beyond loopback, no writes outside ``tmp_path``. Mirrors the
harness style in ``tests/test_transport.py``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from rt_runtime.sidecar.transport import ControlServer, write_token_file
from rt_runtime.sidecar.turn_loop import TurnLoop
from rt_runtime.turn_control import (
    TERMINAL_TURN_STATES,
    TurnControl,
    TurnPoller,
    submit_turn,
)
from rt_runtime.adapter import RuntimeAdapter, RuntimeTarget

TOKEN = "turn-control-token-0123456789"
WRONG_TOKEN = "wrong-token-0123456789abcd"


class _TurnResult:
    """Minimal ``AgentTurnResult`` stand-in: the loop only reads attributes."""

    def __init__(self, status: str = "complete", text: str = "") -> None:
        self.status = status
        self.iterations = 1
        self.tool_calls_made = 0
        self.error = ""
        self.messages = []
        self.usage = []
        if text:
            self.text = text


def _echo(prompt, *, cancel_event=None, max_tokens=None):
    return _TurnResult(text=f"echo: {prompt}")


def _blocking(release: threading.Event):
    def entry(prompt, *, cancel_event=None, max_tokens=None):
        release.wait(5.0)
        return _TurnResult(text=f"done: {prompt}")

    return entry


def _cooperative(started: threading.Event):
    def entry(prompt, *, cancel_event=None, max_tokens=None):
        started.set()
        while not cancel_event.wait(0.005):
            pass
        return _TurnResult(status="cancelled")

    return entry


@pytest.fixture
def make_control(tmp_path):
    """Factory: ``server, loop, control = make_control(entry=...)``."""
    created: list[ControlServer] = []

    def factory(entry=_echo, timeout_s=5.0):
        token_file = tmp_path / "token"
        write_token_file(token_file, TOKEN)
        loop = TurnLoop(entry, timeout_s=timeout_s)
        server = ControlServer(
            token=TOKEN,
            port=0,
            status_path=tmp_path / "status.json",
            token_file=token_file,
            heartbeat_interval=0.05,
            turn_loop=loop,
        )
        server.start()
        created.append(server)
        control = TurnControl(server.base_url, tmp_path, default_timeout=2.0)
        return server, loop, control

    try:
        yield factory
    finally:
        for server in created:
            server.stop()


def _poll_until_done(poller, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not poller.done and time.monotonic() < deadline:
        poller.refresh()
        if not poller.done:
            time.sleep(0.01)
    return poller


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


def test_token_is_read_from_the_state_dir(make_control, tmp_path):
    _server, _loop, control = make_control()

    assert control.token() == TOKEN
    assert control.state_dir == tmp_path


def test_missing_token_raises_before_any_request(make_control, tmp_path):
    _server, _loop, control = make_control()
    (tmp_path / "token").unlink()

    with pytest.raises(OSError):
        control.token()


def test_rotated_token_is_rejected(make_control, tmp_path):
    """A cached token would fail exactly this way; the client must not cache."""
    _server, _loop, control = make_control()
    write_token_file(tmp_path / "token", WRONG_TOKEN)

    code, body = control.submit("hello")

    assert code == 401
    assert body["error"] == "unauthorized"


# ---------------------------------------------------------------------------
# Submitting and polling
# ---------------------------------------------------------------------------


def test_submit_returns_202_while_running(make_control):
    release = threading.Event()
    _server, loop, control = make_control(entry=_blocking(release))
    try:
        code, body = control.submit("SECRET-PROMPT")

        assert code == 202
        assert body["state"] == "running"
        assert body["request_id"]
        assert body["result"] is None
    finally:
        release.set()
        loop.shutdown(1.0)


def test_poller_reaches_a_terminal_result(make_control):
    _server, _loop, control = make_control()

    code, body = control.submit("ping")
    assert code in (200, 202)
    poller = TurnPoller(control, body["request_id"])
    poller.document = dict(body)

    _poll_until_done(poller)

    assert poller.done is True
    assert poller.state in TERMINAL_TURN_STATES
    assert poller.state == "complete"
    assert poller.result["text"] == "echo: ping"


def test_wait_ms_returns_the_terminal_document_inline(make_control):
    _server, _loop, control = make_control()

    code, body = control.submit("straight", wait_ms=1500)

    assert code == 200
    assert body["state"] == "complete"
    assert body["result"]["text"] == "echo: straight"


def test_status_of_unknown_id_is_404_and_ends_the_poll(make_control):
    _server, _loop, control = make_control()
    poller = TurnPoller(control, "0" * 32)

    poller.refresh()

    assert poller.done is True
    assert "not found" in poller.error


def test_cancel_is_cooperative_and_ends_cancelled(make_control):
    started = threading.Event()
    _server, loop, control = make_control(entry=_cooperative(started))
    try:
        _code, body = control.submit("stop me")
        poller = TurnPoller(control, body["request_id"])
        poller.document = dict(body)
        assert started.wait(2.0)

        code, cancel_body = control.cancel(poller.request_id)
        assert code == 202
        assert cancel_body["state"] in ("cancelling", "cancelled")

        _poll_until_done(poller)
        assert poller.state == "cancelled"
    finally:
        loop.shutdown(1.0)


def test_submit_rejects_unknown_body_keys(make_control):
    _server, _loop, control = make_control()

    # TurnControl only sends accepted keys; a hand-built body must still fail,
    # which proves the server-side validation is what rejects it.
    from rt_runtime.sidecar import transport

    code, _body = transport.request(
        control.base_url,
        "/turn",
        method="POST",
        token=TOKEN,
        body={"prompt": "x", "bogus": 1},
    )
    assert code == 400


# ---------------------------------------------------------------------------
# Adapter wiring
# ---------------------------------------------------------------------------


def test_in_process_adapter_has_no_turn_control():
    target = RuntimeTarget(
        name="demo",
        home=Path("."),
        python_version="3.12",
        site_packages=Path("."),
    )

    assert RuntimeAdapter(target).turn_control() is None


def test_sidecar_adapter_returns_no_control_when_the_probe_is_not_ready(
    tmp_path, monkeypatch
):
    from rt_runtime import sidecar_adapter as sa

    launcher = tmp_path / "python314.exe"
    launcher.write_bytes(b"")
    host = tmp_path / "host_stub.py"
    host.write_text("# fake host\n", encoding="utf-8")
    settings = sa.SidecarSettings(
        launcher=launcher,
        host_script=host,
        state_dir=tmp_path / "state",
        runtime_home=tmp_path,
        site_packages=tmp_path,
        base_site_packages=tmp_path,
        start_timeout=2.0,
        health_timeout=0.5,
        health_interval=0.1,
    )
    adapter = sa.SidecarAdapter("rt", settings)

    class _Supervisor:
        def probe(self):
            return type("Report", (), {"state": "starting", "status": {}})()

    monkeypatch.setattr(adapter, "_supervisor", lambda policy: _Supervisor())

    assert adapter.turn_control() is None


# ---------------------------------------------------------------------------
# Transport contract and recovery
# ---------------------------------------------------------------------------


def test_timeout_budget_and_raw_passthrough(tmp_path, monkeypatch):
    from rt_runtime.sidecar import transport

    control = TurnControl("http://127.0.0.1:1", tmp_path)
    write_token_file(tmp_path / "token", TOKEN)
    calls = []
    sentinel = {"raw": True}

    def fake_request(base_url, path, **kwargs):
        calls.append({"base_url": base_url, "path": path, **kwargs})
        return 418, sentinel

    monkeypatch.setattr(transport, "request", fake_request)

    assert control.status("abc") == (418, sentinel)
    assert calls[-1]["timeout"] == 2.0

    assert control.submit("x", wait_ms=4000) == (418, sentinel)
    assert calls[-1]["timeout"] == 5.0
    assert calls[-1]["body"] == {"prompt": "x", "wait_ms": 4000}


def test_token_is_reread_after_a_valid_rotation(tmp_path, monkeypatch):
    from rt_runtime.sidecar import transport

    replacement = "replacement-token-0123456789"
    control = TurnControl("http://127.0.0.1:1", tmp_path)
    token_path = tmp_path / "token"
    write_token_file(token_path, TOKEN)
    seen_tokens = []

    def fake_request(base_url, path, **kwargs):
        seen_tokens.append(kwargs["token"])
        return 200, {}

    monkeypatch.setattr(transport, "request", fake_request)
    control.status("one")
    write_token_file(token_path, replacement)
    control.status("two")

    assert seen_tokens == [TOKEN, replacement]


@pytest.mark.parametrize(
    "state, expected_done",
    [
        ("running", False),
        ("cancelling", False),
        ("unknown", False),
        ("complete", True),
        ("cancelled", True),
        ("error", True),
        ("timeout", True),
    ],
)
def test_poller_state_done_matrix(state, expected_done):
    sentinel = {"text": "result"}
    body = {"state": state, "result": sentinel}

    class _Control:
        def __init__(self):
            self.calls = 0

        def status(self, request_id, **kwargs):
            self.calls += 1
            return 200, dict(body)

    control = _Control()
    poller = TurnPoller(control, "request-id")

    poller.refresh()

    assert poller.done is expected_done
    assert poller.document == body
    assert poller.result is sentinel
    assert poller.error == ""
    poller.refresh()
    assert control.calls == (1 if expected_done else 2)


@pytest.mark.parametrize(
    "outcome, expected_error, terminal",
    [
        (TimeoutError("timed out"), "TimeoutError: timed out", False),
        ((500, {}), "HTTP 500", False),
        ((401, {}), "HTTP 401: control token rejected", True),
        ((403, {}), "HTTP 403: control token rejected", True),
        (
            (404, {}),
            "turn not found (expired, or the sidecar restarted)",
            True,
        ),
    ],
)
def test_poller_faults_follow_their_recoverability(outcome, expected_error, terminal):
    class _Control:
        def __init__(self):
            self.calls = 0

        def status(self, request_id, **kwargs):
            self.calls += 1
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    control = _Control()
    poller = TurnPoller(control, "request-id")

    poller.refresh()

    assert poller.done is terminal
    assert poller.document == {}
    assert poller.state == "error"
    assert poller.error == expected_error

    poller.refresh()
    assert control.calls == (1 if terminal else 2)


def test_transient_poll_failure_can_recover_to_cancelled():
    class _Control:
        def __init__(self):
            self.calls = 0

        def status(self, request_id, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("timed out")
            return 200, {"state": "cancelled", "request_id": request_id}

    control = _Control()
    poller = TurnPoller(control, "request-id")

    poller.refresh()
    assert poller.done is False
    assert poller.error == "TimeoutError: timed out"

    poller.refresh()
    assert poller.done is True
    assert poller.state == "cancelled"
    assert poller.error == ""


def test_uncertain_submission_keeps_id_and_recovers_without_re_admission():
    class _Control:
        def __init__(self):
            self.submissions = 0
            self.request_id = None

        def submit(self, prompt, **kwargs):
            self.submissions += 1
            self.request_id = kwargs["request_id"]
            raise TimeoutError("response timed out")

        def status(self, request_id, **kwargs):
            assert request_id == self.request_id
            return 200, {"state": "cancelled", "request_id": request_id}

    control = _Control()

    code, poller = submit_turn(control, "hello")

    assert code is None
    assert control.submissions == 1
    assert poller.done is False
    assert "submit uncertain: TimeoutError" in poller.error

    poller.refresh()
    assert poller.done is True
    assert poller.state == "cancelled"
    assert poller.error == ""
    assert control.submissions == 1
