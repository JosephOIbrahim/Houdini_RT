"""pytest suite for the rt-sidecar control transport.

Runs on any CPython 3.12+ with the standard library plus pytest. No Houdini, no
network beyond 127.0.0.1, no writes outside pytest's tmp_path.

Coverage map (the S1 acceptance surface):

* 401 without a token, 200 with the token              -> test_token_*
* unknown path 404, method mismatch 405                -> test_routing_*
* non-loopback bind refused                            -> test_bind_*
* oversized request rejected before it is read         -> test_body_*
* shutdown callback invoked exactly once               -> test_shutdown_*
* fixed-port collision handled                         -> test_port_*
* status.json shape and heartbeat movement             -> test_status_*
* turn routes: 401/202/200/409/429/413/404/405, cancel,
  /status counters, /health turn-blind                 -> test_turn_* / test_cancel_*
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from rt_runtime.sidecar.transport import (
    BindRefused,
    ControlServer,
    MAX_CONNECTIONS,
    PortUnavailable,
    STATUS_FILE_NAME,
    TOKEN_FILE_NAME,
    TOKEN_HEADER,
    _assert_loopback_address,
    generate_token,
    normalise_host,
    read_status_file,
    read_token_file,
    request,
    restrict_file_acl,
    start_control_server,
    token_fingerprint,
    write_token_file,
)
from rt_runtime.sidecar.turn_loop import TurnLoop

TOKEN = "test-token-0123456789abcdef"
WRONG_TOKEN = "test-token-fedcba9876543210"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def raw_http(
    host: str,
    port: int,
    *,
    method: str = "GET",
    path: str = "/health",
    headers: dict[str, str] | None = None,
    timeout: float = 2.0,
) -> http.client.HTTPResponse:
    """Send a request with full control over the headers.

    ``http.client`` normally injects ``Host`` and refuses some header shapes,
    so this uses the low-level request API to allow duplicates and omissions.
    """
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for name, value in (headers or {}).items():
        connection.putheader(name, value)
    connection.endheaders()
    return connection, connection.getresponse()


@pytest.fixture
def make_server(tmp_path):
    """Factory: ``server, calls = make_server(**overrides)``."""
    created: list[ControlServer] = []

    def factory(**overrides):
        token = overrides.pop("token", TOKEN)
        port = overrides.pop("port", 0)
        calls: list[float] = []
        overrides.setdefault(
            "status_path", tmp_path / f"status-{len(created)}.json"
        )
        overrides.setdefault("token_file", tmp_path / TOKEN_FILE_NAME)
        overrides.setdefault("heartbeat_interval", 0.05)
        overrides.setdefault("on_shutdown", lambda: calls.append(time.monotonic()))
        server = ControlServer(token=token, port=port, **overrides)
        server.start()
        created.append(server)
        return server, calls

    try:
        yield factory
    finally:
        for server in created:
            server.stop()


# ---------------------------------------------------------------------------
# Health / token
# ---------------------------------------------------------------------------


def test_health_needs_no_token(make_server):
    server, _ = make_server()

    code, body = request(server.base_url, "/health")

    assert code == 200
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["pid"] == os.getpid()
    assert body["instance_id"] == server.instance_id


def test_health_response_carries_no_secret(make_server):
    server, _ = make_server()

    _, body = request(server.base_url, "/health")

    assert TOKEN not in json.dumps(body)


def test_health_can_require_token(make_server):
    server, _ = make_server(health_requires_token=True)

    code, _ = request(server.base_url, "/health")
    assert code == 401

    code, _ = request(server.base_url, "/health", token=TOKEN)
    assert code == 200


def test_status_401_without_token(make_server):
    server, _ = make_server()

    code, body = request(server.base_url, "/status")

    assert code == 401
    assert body["error"] == "unauthorized"


def test_status_401_with_wrong_token(make_server):
    server, _ = make_server()

    code, _ = request(server.base_url, "/status", token=WRONG_TOKEN)
    assert code == 401


def test_status_200_with_token(make_server):
    server, _ = make_server()

    code, body = request(server.base_url, "/status", token=TOKEN)

    assert code == 200
    assert body["ok"] is True
    assert body["pid"] == os.getpid()
    assert body["port"] == server.port
    assert body["host"] == "127.0.0.1"
    assert body["state"] == "ready"
    assert body["token_fingerprint"] == token_fingerprint(TOKEN)
    assert body["python_version"].startswith("3.")
    assert body["schema"] == 1


def test_token_header_is_case_insensitive(make_server):
    server, _ = make_server()

    code, _ = request(
        server.base_url,
        "/status",
        token=None,
        headers={TOKEN_HEADER.lower(): TOKEN},
    )
    assert code == 200


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/admin", "/health/", "/status/extra", "/..%2f..%2fetc"])
def test_unknown_path_404(make_server, path):
    server, _ = make_server()

    code, body = request(server.base_url, path, token=TOKEN)

    assert code == 404
    assert body["error"] == "not_found"


def test_method_mismatch_405(make_server):
    server, _ = make_server()

    code, body = request(server.base_url, "/shutdown", method="PUT", token=TOKEN)
    assert code == 405
    assert body["allow"] == "POST"

    code, body = request(server.base_url, "/health", method="POST", token=TOKEN)
    assert code == 405
    assert body["allow"] == "GET, HEAD"


def test_options_is_not_cors(make_server):
    server, _ = make_server()

    code, body = request(server.base_url, "/status", method="OPTIONS", token=TOKEN)

    assert code == 405
    assert "access-control-allow-origin" not in body


def test_browser_shaped_request_refused(make_server):
    server, _ = make_server()

    code, body = request(
        server.base_url,
        "/health",
        headers={"Origin": "http://evil.example"},
    )

    assert code == 403
    assert body["error"] == "browser_requests_refused"


def test_non_loopback_host_header_refused(make_server):
    server, _ = make_server()

    connection, response = raw_http(
        server.host,
        server.port,
        headers={"Host": "evil.example", TOKEN_HEADER: TOKEN},
    )
    try:
        assert response.status == 403
        assert json.loads(response.read())["error"] == "host_not_allowed"
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Bind policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.10", "10.0.0.1", "example.com", "", "   "],
)
def test_non_loopback_bind_refused(host):
    with pytest.raises(BindRefused):
        ControlServer(token=TOKEN, host=host)


def test_bind_refusal_is_a_value_error():
    # S1 may catch either BindRefused or ValueError; both must work.
    with pytest.raises(ValueError):
        ControlServer(token=TOKEN, host="0.0.0.0")


@pytest.mark.parametrize(
    ("given", "expected"),
    [("127.0.0.1", "127.0.0.1"), ("localhost", "127.0.0.1"), ("::1", "::1")],
)
def test_loopback_hosts_normalise(given, expected):
    assert normalise_host(given) == expected


def test_post_bind_address_guard():
    _assert_loopback_address("127.0.0.1")
    _assert_loopback_address("::1")
    with pytest.raises(BindRefused):
        _assert_loopback_address("0.0.0.0")
    with pytest.raises(BindRefused):
        _assert_loopback_address("192.168.1.10")


def test_ephemeral_port_is_chosen_when_port_is_zero(make_server):
    server, _ = make_server(port=0)

    assert server.port > 0
    assert server.base_url == f"http://127.0.0.1:{server.port}"
    assert request(server.base_url, "/health")[0] == 200


def test_port_collision_falls_back_to_ephemeral(tmp_path):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy_port = blocker.getsockname()[1]

    server = ControlServer(
        token=TOKEN,
        port=busy_port,
        status_path=tmp_path / STATUS_FILE_NAME,
        heartbeat_interval=0.05,
    )
    try:
        bound_port = server.start()

        assert bound_port != busy_port
        assert request(server.base_url, "/health")[0] == 200

        status = read_status_file(tmp_path / STATUS_FILE_NAME)
        assert status is not None
        assert status["port"] == bound_port
        assert status["requested_port"] == busy_port
        assert status["bind_fallback"] is True
        assert status["bind_error"]
    finally:
        server.stop()
        blocker.close()


def test_port_collision_raises_when_fallback_disabled(tmp_path):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy_port = blocker.getsockname()[1]

    server = ControlServer(
        token=TOKEN,
        port=busy_port,
        port_fallback=False,
        status_path=tmp_path / STATUS_FILE_NAME,
    )
    try:
        with pytest.raises(PortUnavailable):
            server.start()
    finally:
        server.stop()
        blocker.close()

    assert server.port == 0


# ---------------------------------------------------------------------------
# Request size and body handling
# ---------------------------------------------------------------------------


def test_oversized_request_rejected_before_read(make_server):
    server, calls = make_server()

    connection = http.client.HTTPConnection(server.host, server.port, timeout=2.0)
    try:
        connection.putrequest("POST", "/shutdown")
        connection.putheader("Content-Length", "99999")
        connection.putheader(TOKEN_HEADER, TOKEN)
        connection.endheaders()  # headers only; no body is sent
        response = connection.getresponse()
        body = json.loads(response.read())
    finally:
        connection.close()

    assert response.status == 413
    assert body["error"] == "request_too_large"
    assert body["limit"] == 4096
    assert response.getheader("Connection") == "close"
    assert calls == []


def test_uri_too_long_rejected(make_server):
    server, _ = make_server()

    connection, response = raw_http(
        server.host,
        server.port,
        path="/" + ("a" * 3000),
    )
    try:
        body = json.loads(response.read())
    finally:
        connection.close()

    assert response.status == 414
    assert body["error"] == "uri_too_long"


def test_connection_cap_answers_503(make_server):
    server, _ = make_server()
    holders = []
    refused = []
    try:
        # Fill every slot with a handler that cannot finish: a request line
        # without its terminating blank line blocks in parse_headers, so the
        # slot stays held.
        for _ in range(MAX_CONNECTIONS):
            sock = socket.create_connection((server.host, server.port), timeout=3.0)
            sock.sendall(b"GET /health HTTP/1.1\r\n")
            holders.append(sock)

        for _ in range(2):
            sock = socket.create_connection((server.host, server.port), timeout=3.0)
            sock.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            refused.append(sock)

        statuses = []
        for sock in refused:
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            statuses.append(b"".join(chunks).split(b"\r\n", 1)[0])
    finally:
        for sock in holders + refused:
            sock.close()

    assert statuses
    assert all(b"503" in status for status in statuses)


def test_chunked_transfer_encoding_rejected(make_server):
    server, _ = make_server()

    connection, response = raw_http(
        server.host,
        server.port,
        method="POST",
        path="/shutdown",
        headers={
            "Host": f"127.0.0.1:{server.port}",
            "Transfer-Encoding": "chunked",
            TOKEN_HEADER: TOKEN,
        },
    )
    try:
        body = json.loads(response.read())
    finally:
        connection.close()

    assert response.status == 400
    assert body["error"] == "transfer_encoding_not_supported"


def test_body_on_get_rejected(make_server):
    server, _ = make_server()

    connection = http.client.HTTPConnection(server.host, server.port, timeout=2.0)
    try:
        connection.request("GET", "/health", body=b"x")
        response = connection.getresponse()
        response.read()
    finally:
        connection.close()

    assert response.status == 400


def test_body_within_limit_is_accepted_and_ignored(make_server):
    server, _ = make_server()

    code, body = request(
        server.base_url,
        "/shutdown",
        method="POST",
        token=TOKEN,
        body={"reason": "operator"},
    )

    assert code == 200
    assert body["ok"] is True


def test_bad_content_length_rejected(make_server):
    server, _ = make_server()

    connection, response = raw_http(
        server.host,
        server.port,
        method="POST",
        path="/shutdown",
        headers={
            "Host": f"127.0.0.1:{server.port}",
            "Content-Length": "not-a-number",
            TOKEN_HEADER: TOKEN,
        },
    )
    try:
        response.read()
    finally:
        connection.close()

    assert response.status == 400


def test_expect_100_is_refused_for_oversized_body(make_server):
    server, _ = make_server()

    connection, response = raw_http(
        server.host,
        server.port,
        method="POST",
        path="/shutdown",
        headers={
            "Host": f"127.0.0.1:{server.port}",
            "Content-Length": "99999",
            "Expect": "100-continue",
            TOKEN_HEADER: TOKEN,
        },
    )
    try:
        response.read()
    finally:
        connection.close()

    assert response.status == 413


# ---------------------------------------------------------------------------
# Shutdown handshake
# ---------------------------------------------------------------------------


def test_shutdown_requires_token(make_server):
    server, calls = make_server()

    code, body = request(server.base_url, "/shutdown", method="POST")

    assert code == 401
    assert body["error"] == "unauthorized"
    assert calls == []
    assert request(server.base_url, "/health")[0] == 200


def test_shutdown_with_token_invokes_callback_once(make_server):
    server, calls = make_server()

    code, body = request(server.base_url, "/shutdown", method="POST", token=TOKEN)

    assert code == 200
    assert body["ok"] is True
    assert body["state"] == "stopping"
    assert body["instance_id"] == server.instance_id

    assert wait_for(lambda: server.shutdown_requested.is_set())
    assert wait_for(lambda: len(calls) == 1)
    time.sleep(0.1)
    assert len(calls) == 1
    assert server.shutdown_calls == 1


def test_duplicate_shutdown_is_refused_and_callback_runs_once(make_server):
    server, calls = make_server()

    first_code, _ = server.request_shutdown()
    second_code, second_body = server.request_shutdown()

    assert first_code == 200
    assert second_code == 409
    assert second_body["error"] == "already_shutting_down"

    assert wait_for(lambda: len(calls) == 1)
    time.sleep(0.1)
    assert len(calls) == 1


def test_concurrent_shutdown_requests_yield_one_callback(make_server):
    server, calls = make_server()
    results: list[int] = []
    lock = threading.Lock()

    def fire():
        code, _ = server.request_shutdown()
        with lock:
            results.append(code)

    threads = [threading.Thread(target=fire) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(results) == [200] + [409] * 7
    assert wait_for(lambda: len(calls) == 1)
    assert server.shutdown_calls == 1


def test_listener_is_closed_after_shutdown(make_server):
    server, _ = make_server()
    port = server.port

    request(server.base_url, "/shutdown", method="POST", token=TOKEN)
    assert wait_for(lambda: server.listener_closed.is_set())

    with pytest.raises(OSError):
        request(f"http://127.0.0.1:{port}", "/health", timeout=1.0)


def test_shutdown_callback_error_is_reported(make_server):
    def explode():
        raise RuntimeError("payload refused to stop")

    server, _ = make_server(on_shutdown=explode)

    code, _ = request(server.base_url, "/shutdown", method="POST", token=TOKEN)
    assert code == 200
    assert wait_for(lambda: server.state == "error")

    assert wait_for(lambda: server.shutdown_requested.is_set())
    status = server.status_payload()
    assert status["import_ok"] is False
    assert "payload refused to stop" in (status["last_error"] or "")


# ---------------------------------------------------------------------------
# status.json
# ---------------------------------------------------------------------------


def test_status_file_is_written_with_expected_fields(tmp_path, make_server):
    server, _ = make_server(status_path=tmp_path / STATUS_FILE_NAME)
    try:
        status = read_status_file(tmp_path / STATUS_FILE_NAME)
        assert status is not None
        for field in (
            "schema",
            "pid",
            "instance_id",
            "state",
            "host",
            "port",
            "requested_port",
            "started_at",
            "heartbeat_at",
            "heartbeat_mono",
            "heartbeat_seq",
            "python_version",
            "python_implementation",
            "sys_prefix",
            "sys_executable",
            "entry_point",
            "import_ok",
            "last_error",
            "token_fingerprint",
            "token_file",
            "shutdown_requested",
            "stop_reason",
            "requests_total",
        ):
            assert field in status, f"status.json missing {field!r}"

        assert status["schema"] == 1
        assert status["pid"] == os.getpid()
        assert status["state"] == "ready"
        assert status["host"] == "127.0.0.1"
        assert status["port"] == server.port
        assert status["token_fingerprint"] == token_fingerprint(TOKEN)
        assert status["started_at"].endswith("Z")
    finally:
        server.stop()


def test_heartbeat_advances(tmp_path, make_server):
    server, _ = make_server(status_path=tmp_path / STATUS_FILE_NAME)
    try:
        first = read_status_file(tmp_path / STATUS_FILE_NAME)
        assert first is not None
        assert wait_for(
            lambda: (
                (snapshot := read_status_file(tmp_path / STATUS_FILE_NAME)) is not None
                and snapshot["heartbeat_seq"] > first["heartbeat_seq"]
            )
        )
    finally:
        server.stop()


def test_heartbeat_mono_is_usable_by_the_supervisor(make_server):
    server, _ = make_server()

    status = read_status_file(server.status_path)
    assert status is not None
    age = time.monotonic() - float(status["heartbeat_mono"])
    assert 0.0 <= age < 5.0


def test_stub_fields_survive_handover(tmp_path, make_server):
    """The S1 stub writes a 'starting' record; the transport must keep it."""
    status_path = tmp_path / STATUS_FILE_NAME
    status_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "pid": os.getpid(),
                "state": "starting",
                "lock_nonce": "abc123",
                "parent_pid": 4242,
                "runtime_home": r"%HOUDINI_RT_ROOT%",
                "site_packages": ["x"],
                "last_error": None,
                "port": None,
                "requested_port": None,
            }
        ),
        encoding="utf-8",
    )

    server, _ = make_server(status_path=status_path)
    try:
        status = read_status_file(status_path)
        assert status is not None
        assert status["lock_nonce"] == "abc123"
        assert status["parent_pid"] == 4242
        assert status["state"] == "ready"
        assert status["port"] == server.port
        assert status["requested_port"] is None
    finally:
        server.stop()


def test_status_from_another_pid_is_not_inherited(tmp_path, make_server):
    status_path = tmp_path / STATUS_FILE_NAME
    status_path.write_text(
        json.dumps({"pid": 999999, "lock_nonce": "stale", "state": "ready"}),
        encoding="utf-8",
    )

    server, _ = make_server(status_path=status_path)
    try:
        status = read_status_file(status_path)
        assert status is not None
        assert status["pid"] == os.getpid()
        assert "lock_nonce" not in status
        assert status["state"] == "ready"
    finally:
        server.stop()


def test_status_file_leaves_no_temporary_behind(tmp_path, make_server):
    server, _ = make_server(status_path=tmp_path / STATUS_FILE_NAME)
    try:
        assert (tmp_path / STATUS_FILE_NAME).exists()
    finally:
        server.stop()

    final = read_status_file(tmp_path / STATUS_FILE_NAME)
    assert final is not None
    assert final["state"] == "stopped"
    assert "stopped_at" in final
    # A stopped document must not name a dead socket or a secret about to go.
    assert final["port"] is None
    assert final["token_file"] is None
    assert not (tmp_path / (STATUS_FILE_NAME + ".tmp")).exists()


def test_stopped_record_nulls_heartbeat_and_endpoints(tmp_path, make_server):
    """A stopped record must never look fresh, and must keep its ready/exit facts."""
    server, _ = make_server(status_path=tmp_path / STATUS_FILE_NAME)
    live = read_status_file(tmp_path / STATUS_FILE_NAME)
    assert live is not None
    assert live["ready_at"], "the transport publishes the ready transition"
    assert live["python_implementation"] == "cpython"
    assert live["heartbeat_mono"] is not None

    server.stop()

    final = read_status_file(tmp_path / STATUS_FILE_NAME)
    assert final is not None
    assert final["state"] == "stopped"
    assert final["heartbeat_mono"] is None
    assert final["heartbeat_at"] is None
    assert final["heartbeat_seq"] == 0
    assert final["port"] is None
    assert final["token_file"] is None
    assert final["ready_at"] == live["ready_at"]
    assert final["exit"] == "graceful"
    assert final["writer"] == "host"


# ---------------------------------------------------------------------------
# Token file
# ---------------------------------------------------------------------------


def test_token_file_roundtrip(tmp_path):
    path = write_token_file(tmp_path / TOKEN_FILE_NAME, TOKEN)

    assert path.exists()
    assert read_token_file(path) == TOKEN
    assert read_token_file(path).strip() == TOKEN
    assert "\n" not in read_token_file(path)


def test_token_file_rejects_short_tokens(tmp_path):
    with pytest.raises(ValueError):
        write_token_file(tmp_path / TOKEN_FILE_NAME, "short")


def test_generated_token_is_long_and_unique():
    first = generate_token()
    second = generate_token()

    assert len(first) >= 32
    assert first != second
    assert token_fingerprint(first) != token_fingerprint(second)
    assert token_fingerprint(first) == token_fingerprint(first)


def test_token_file_mode_on_posix(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission bits do not apply on Windows")
    path = write_token_file(tmp_path / TOKEN_FILE_NAME, TOKEN)
    assert path.stat().st_mode & 0o777 == 0o600


def test_restrict_file_acl_returns_bool(tmp_path):
    path = tmp_path / "acl-probe.txt"
    path.write_text("x", encoding="utf-8")

    assert isinstance(restrict_file_acl(path), bool)


def test_token_file_is_replaced_on_relaunch(tmp_path):
    path = write_token_file(tmp_path / TOKEN_FILE_NAME, TOKEN)
    replacement = generate_token()
    write_token_file(path, replacement)

    assert read_token_file(path) == replacement


# ---------------------------------------------------------------------------
# Bootstrap helper and client
# ---------------------------------------------------------------------------


def test_start_control_server_writes_token_and_status(tmp_path):
    server = start_control_server(tmp_path, heartbeat_interval=0.05)
    try:
        token = read_token_file(tmp_path / TOKEN_FILE_NAME)

        code, body = request(server.base_url, "/status", token=token)
        assert code == 200
        assert body["token_fingerprint"] == token_fingerprint(token)
        assert body["token_file"].endswith(TOKEN_FILE_NAME)
        assert (tmp_path / STATUS_FILE_NAME).exists()
    finally:
        server.stop()


def test_client_refuses_non_loopback_base_url():
    with pytest.raises(ValueError):
        request("http://192.168.1.10:8080", "/health")
    with pytest.raises(ValueError):
        request("https://127.0.0.1:8080", "/health")


def test_shutdown_delay_keeps_listener_open_briefly(make_server):
    server, calls = make_server(shutdown_delay=0.5)

    code, _ = request(server.base_url, "/shutdown", method="POST", token=TOKEN)
    assert code == 200

    # Still serving while the delay runs, and the duplicate is refused.
    code, body = request(server.base_url, "/shutdown", method="POST", token=TOKEN)
    assert code == 409
    assert body["error"] == "already_shutting_down"

    assert wait_for(lambda: len(calls) == 1, timeout=3.0)


def test_server_stop_is_idempotent(make_server):
    server, _ = make_server()

    server.stop()
    server.stop()

    assert server.state == "stopped"
    assert server.listener_closed.is_set()


# ---------------------------------------------------------------------------
# Turn routes (POST /turn, GET /turn/<id>, POST /cancel)
# ---------------------------------------------------------------------------


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


def fast_loop() -> TurnLoop:
    """A loop whose turn completes immediately and echoes the prompt."""

    def entry(prompt, *, cancel_event=None, max_tokens=None):
        return _TurnResult(text=f"echo: {prompt}")

    return TurnLoop(entry, timeout_s=5.0)


def blocking_loop(release) -> TurnLoop:
    """A loop whose single turn stays running until *release* is set."""

    def entry(prompt, *, cancel_event=None, max_tokens=None):
        release.wait(5.0)
        return _TurnResult(text="done")

    return TurnLoop(entry, timeout_s=10.0)


def cooperative_loop(started) -> TurnLoop:
    """A loop whose turn exits as soon as the cancel event is set."""

    def entry(prompt, *, cancel_event=None, max_tokens=None):
        started.set()
        while not cancel_event.wait(0.005):
            pass
        return _TurnResult(status="cancelled")

    return TurnLoop(entry, timeout_s=5.0)


def send_json(
    server,
    path,
    body=None,
    *,
    method: str = "POST",
    token: str | None = TOKEN,
    timeout: float = 5.0,
):
    """Send JSON (or nothing) and return ``(response, parsed_body)``.

    The response object is kept after the connection closes so a test can read
    headers such as ``Retry-After``.
    """
    connection = http.client.HTTPConnection(server.host, server.port, timeout=timeout)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers[TOKEN_HEADER] = token
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        parsed = json.loads(raw) if raw else {}
    finally:
        connection.close()
    return response, parsed


def test_turn_requires_token(make_server):
    server, _ = make_server(turn_loop=fast_loop())

    response, body = send_json(server, "/turn", {"prompt": "hi"}, token=None)

    assert response.status == 401
    assert body["error"] == "unauthorized"


def test_turn_admitted_returns_202(make_server):
    release = threading.Event()
    loop = blocking_loop(release)
    server, _ = make_server(turn_loop=loop)
    try:
        response, body = send_json(server, "/turn", {"prompt": "SECRET-PROMPT"})

        assert response.status == 202
        assert body["ok"] is False
        assert body["state"] == "running"
        assert body["request_id"]
        assert body["poll"] == f"/turn/{body['request_id']}"
        assert body["result"] is None
        assert body["error_kind"] is None
        assert response.getheader("Connection") == "close"
    finally:
        release.set()
        loop.shutdown(1.0)


def test_turn_wait_ms_returns_200_terminal(make_server):
    server, _ = make_server(turn_loop=fast_loop())

    response, body = send_json(
        server, "/turn", {"prompt": "ping", "wait_ms": 2000}
    )

    assert response.status == 200
    assert body["ok"] is True
    assert body["state"] == "complete"
    assert body["error_kind"] is None
    assert body["result"]["status"] == "complete"
    assert body["result"]["text"] == "echo: ping"
    assert body["request_id"] in body["poll"]


def test_turn_duplicate_request_id_409(make_server):
    release = threading.Event()
    loop = blocking_loop(release)
    server, _ = make_server(turn_loop=loop)
    try:
        first, _ = send_json(
            server, "/turn", {"prompt": "a", "request_id": "fixed-1"}
        )
        assert first.status == 202

        second, body = send_json(
            server, "/turn", {"prompt": "b", "request_id": "fixed-1"}
        )
        assert second.status == 409
        assert body["error"] == "duplicate_request_id"
    finally:
        release.set()
        loop.shutdown(1.0)


def test_turn_busy_429_carries_retry_after(make_server):
    release = threading.Event()
    loop = blocking_loop(release)
    server, _ = make_server(turn_loop=loop)
    try:
        first, admitted = send_json(server, "/turn", {"prompt": "a"})
        assert first.status == 202

        second, body = send_json(server, "/turn", {"prompt": "b"})

        assert second.status == 429
        assert body["error"] == "turn_in_progress"
        assert body["active_request_id"] == admitted["request_id"]
        assert second.getheader("Retry-After") == "1"
    finally:
        release.set()
        loop.shutdown(1.0)


def test_turn_body_cap_is_131072(make_server):
    loop = fast_loop()
    server, _ = make_server(turn_loop=loop)

    connection = http.client.HTTPConnection(server.host, server.port, timeout=2.0)
    try:
        connection.putrequest("POST", "/turn")
        connection.putheader("Content-Length", "200000")
        connection.putheader(TOKEN_HEADER, TOKEN)
        connection.endheaders()  # headers only; the body is never sent
        response = connection.getresponse()
        body = json.loads(response.read())
    finally:
        connection.close()

    assert response.status == 413
    assert body["error"] == "request_too_large"
    assert body["limit"] == 131072
    # Refused before admission: the loop never saw a turn.
    assert loop.status_fields()["turns_total"] == 0


def test_turn_bad_request_names_the_field_not_the_value(make_server):
    server, _ = make_server(turn_loop=fast_loop())
    secret = "SECRET-PROMPT-9f3"

    connection = http.client.HTTPConnection(server.host, server.port, timeout=2.0)
    try:
        connection.request(
            "POST",
            "/turn",
            body=json.dumps({"prompt": secret, "max_tokens": 0}).encode("utf-8"),
            headers={TOKEN_HEADER: TOKEN, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read()
        body = json.loads(raw)
    finally:
        connection.close()

    assert response.status == 400
    assert body["error"] == "turn_bad_request"
    assert "max_tokens" in body["detail"]
    assert secret not in raw.decode("utf-8")


def test_turn_unknown_id_404(make_server):
    server, _ = make_server(turn_loop=fast_loop())

    response, body = send_json(server, "/turn/never-seen", method="GET")
    assert response.status == 404
    assert body["error"] == "unknown_request_id"

    # Exactly one id segment: an empty id or a deeper path is not a route.
    for path in ("/turn/", "/turn/a/b"):
        response, body = send_json(server, path, method="GET")
        assert response.status == 404
        assert body["error"] == "not_found"


def test_turn_poll_returns_the_job_document(make_server):
    release = threading.Event()
    loop = blocking_loop(release)
    server, _ = make_server(turn_loop=loop)
    try:
        _, admitted = send_json(server, "/turn", {"prompt": "x"})

        response, body = send_json(
            server, f"/turn/{admitted['request_id']}", method="GET"
        )

        assert response.status == 200
        assert body["request_id"] == admitted["request_id"]
        assert body["state"] == "running"
    finally:
        release.set()
        loop.shutdown(1.0)


def test_turn_wrong_method_405(make_server):
    server, _ = make_server(turn_loop=fast_loop())

    response, body = send_json(server, "/turn", method="GET")
    assert response.status == 405
    assert body["allow"] == "POST"

    response, body = send_json(server, "/turn/abc", method="POST")
    assert response.status == 405
    assert body["allow"] == "GET, HEAD"

    response, body = send_json(server, "/cancel", method="GET")
    assert response.status == 405
    assert body["allow"] == "POST"


def test_cancel_returns_202_then_409(make_server):
    started = threading.Event()
    loop = cooperative_loop(started)
    server, _ = make_server(turn_loop=loop)
    try:
        _, admitted = send_json(server, "/turn", {"prompt": "x"})
        assert started.wait(2.0)

        response, body = send_json(
            server, "/cancel", {"request_id": admitted["request_id"]}
        )
        assert response.status == 202
        assert body["ok"] is True
        assert body["request_id"] == admitted["request_id"]
        assert body["state"] == "cancelling"

        final = wait_for(
            lambda: (loop.poll(admitted["request_id"]) or {}).get("state")
            == "cancelled"
        )
        assert final

        response, body = send_json(
            server, "/cancel", {"request_id": admitted["request_id"]}
        )
        assert response.status == 409
        assert body["error"] == "turn_not_running"
        assert body["state"] == "cancelled"
    finally:
        loop.shutdown(1.0)


def test_cancel_unknown_id_404_and_bad_body_400(make_server):
    server, _ = make_server(turn_loop=fast_loop())

    response, body = send_json(server, "/cancel", {"request_id": "never-seen"})
    assert response.status == 404
    assert body["error"] == "unknown_request_id"

    for payload in ({}, {"request_id": ""}, {"request_id": "has space"}, {"id": "x"}):
        response, body = send_json(server, "/cancel", payload)
        assert response.status == 400
        assert body["error"] == "turn_bad_request"


def test_turn_unavailable_without_a_loop(make_server):
    server, _ = make_server()

    response, body = send_json(server, "/turn", {"prompt": "hi"})

    assert response.status == 503
    assert body["error"] == "turn_unavailable"


def test_status_merges_turn_counters(make_server):
    loop = fast_loop()
    server, _ = make_server(turn_loop=loop)

    send_json(server, "/turn", {"prompt": "count me", "wait_ms": 2000})

    code, body = request(server.base_url, "/status", token=TOKEN)

    assert code == 200
    assert body["turn_state"] == "idle"
    assert body["turns_total"] == 1
    assert body["turns_completed"] == 1
    for field in (
        "turn_active_request_id",
        "turn_active_age_s",
        "turn_shutting_down",
        "turns_cancelled",
        "turns_failed",
        "turns_timed_out",
        "turns_quarantined",
        "turns_replayed",
        "turns_rejected",
        "turns_late_after_quarantine",
        "turn_last_error_kind",
        "turn_retained_results",
        "turn_timeout_s",
        "turn_timeout_grace_s",
    ):
        assert field in body, f"/status missing {field!r}"


def test_health_stays_token_free_and_turn_blind_during_a_turn(make_server):
    release = threading.Event()
    loop = blocking_loop(release)
    server, _ = make_server(turn_loop=loop)
    try:
        _, admitted = send_json(server, "/turn", {"prompt": "SECRET-PROMPT"})

        # No token, no turn detail, and still answerable while a turn runs.
        code, health = request(server.base_url, "/health")
        assert code == 200
        assert health["ok"] is True
        assert "turns_total" not in health
        assert not any(key.startswith("turn_") for key in health)
        assert "SECRET-PROMPT" not in json.dumps(health)
        assert admitted["request_id"] not in json.dumps(health)

        code, status = request(server.base_url, "/status", token=TOKEN)
        assert code == 200
        assert status["turn_state"] == "running"
        assert status["turn_active_request_id"] == admitted["request_id"]
        # The prompt never reaches the status surface.
        assert "SECRET-PROMPT" not in json.dumps(status)
    finally:
        release.set()
        loop.shutdown(1.0)


def test_shutdown_still_answers_during_a_running_turn(make_server):
    release = threading.Event()
    loop = blocking_loop(release)
    server, calls = make_server(turn_loop=loop)
    try:
        send_json(server, "/turn", {"prompt": "x"})

        code, body = request(
            server.base_url, "/shutdown", method="POST", token=TOKEN
        )

        assert code == 200
        assert body["ok"] is True
        assert wait_for(lambda: len(calls) == 1)
    finally:
        release.set()
        loop.shutdown(1.0)
