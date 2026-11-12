"""Lifecycle tests for the HoudiniRT sidecar supervisor.

Run with the 3.14 interpreter that will launch the sidecar::

    C:\\Python314\\python.exe -m pytest tests -q

Every test that starts a child either stops it or lets the fixture force-stop
it, so a failure never leaves an orphan process behind.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from rt_runtime import sidecar as rt_sidecar  # noqa: E402
from rt_runtime.sidecar import lifecycle  # noqa: E402
from rt_runtime.sidecar.lifecycle import (  # noqa: E402
    SidecarState,
    SidecarSupervisor,
    pid_alive,
    terminate_pid,
)

SIDECAR_DIR = Path(rt_sidecar.__file__).resolve().parent
HOST = SIDECAR_DIR / "host_stub.py"
PYTHON = sys.executable

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 14),
    reason="the sidecar host asserts --expect-python 3.14",
)

REQUIRED_STATUS_KEYS = {
    "schema",
    "instance",
    "writer",
    "pid",
    "parent_pid",
    "state",
    "python_version",
    "sys_prefix",
    "sys_executable",
    "started_at",
    "started_mono",
    "heartbeat_at",
    "heartbeat_mono",
    "heartbeat_seq",
    "port",
    "token_file",
    "last_error",
}


def _supervisor(tmp_path: Path, **overrides) -> SidecarSupervisor:
    options = {
        "instance": "default",
        "start_timeout": 20.0,
        "heartbeat_interval": 0.1,
        "stale_after": 1.0,
        "stop_grace": 5.0,
        "parent_pid": os.getpid(),
        "expect_python": "3.14",
    }
    options.update(overrides)
    return SidecarSupervisor(
        lifecycle.default_launcher(PYTHON),
        HOST,
        tmp_path / "state",
        **options,
    )


@pytest.fixture
def sup(tmp_path):
    supervisor = _supervisor(tmp_path)
    yield supervisor
    try:
        supervisor.stop(force=True)
    except Exception:  # teardown must never mask the test failure
        pass


def _dead_pid() -> int:
    """A pid that is certainly not running (reaped by the OS)."""
    proc = subprocess.Popen(
        [PYTHON, "-I", "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=15)
    pid = proc.pid
    assert not pid_alive(pid), "reaped process still reports alive"
    return pid


# ---------------------------------------------------------------------------
# start / heartbeat
# ---------------------------------------------------------------------------


def test_start_becomes_ready_and_heartbeats(sup):
    result = sup.start()

    assert result.ok, result
    assert result.state is SidecarState.READY
    assert result.changed is True
    assert result.pid and pid_alive(result.pid)

    report = sup.probe()
    assert report.state is SidecarState.READY
    assert report.pid == result.pid
    assert report.heartbeat_age is not None
    assert report.heartbeat_age < 2.0

    status = report.status
    assert status is not None
    missing = REQUIRED_STATUS_KEYS - set(status)
    assert not missing, f"status.json missing {sorted(missing)}"
    assert status["pid"] == result.pid
    assert status["python_version"].startswith("3.14")
    assert status["sys_prefix"]
    assert status["sys_executable"]
    assert status["port"] is None, "Tier 0 serves nothing"
    assert status["lock_nonce"]
    assert status["writer"] == "host"

    # The token exists but is never copied into status.json.
    assert sup.token_path.is_file()
    token = sup.token_path.read_text(encoding="utf-8").strip()
    assert token and token not in json.dumps(status)

    seq_before = status["heartbeat_seq"]
    time.sleep(0.45)
    seq_after = sup.probe().status["heartbeat_seq"]
    assert seq_after > seq_before


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_is_clean_and_idempotent(sup):
    started = sup.start()
    assert started.ok, started
    pid = started.pid

    stopped = sup.stop()
    assert stopped.ok, stopped
    assert stopped.changed is True
    assert stopped.state is SidecarState.STOPPED
    assert not pid_alive(pid), "child survived stop()"

    assert not sup.lock_path.exists(), "lock left behind"
    assert not sup.stop_path.exists(), "stop flag left behind"
    assert not sup.token_path.exists(), "token left behind"
    assert sup.probe().state is SidecarState.STOPPED

    again = sup.stop()
    assert again.ok and again.changed is False
    assert again.state is SidecarState.STOPPED


def test_stop_when_absent_is_a_no_op(tmp_path):
    supervisor = _supervisor(tmp_path)
    result = supervisor.stop()
    assert result.ok and result.changed is False
    assert result.state is SidecarState.ABSENT
    assert supervisor.probe().state is SidecarState.ABSENT


# ---------------------------------------------------------------------------
# double toggle
# ---------------------------------------------------------------------------


def test_double_start_is_idempotent(sup):
    first = sup.start()
    assert first.ok, first

    second = sup.start()
    assert second.ok, second
    assert second.changed is False
    assert second.pid == first.pid
    assert "already running" in " ".join(second.reasons)

    # Exactly one host: the lock names the same pid the status file does.
    lock = json.loads(sup.lock_path.read_text(encoding="utf-8"))
    assert lock["host_pid"] == first.pid
    assert sup.probe().pid == first.pid
    assert pid_alive(first.pid)


def test_concurrent_start_spawns_one_host(tmp_path):
    """Two threads, one process, one state dir: exactly one host may start."""
    first = _supervisor(tmp_path)
    second = _supervisor(tmp_path)
    supervisors = (first, second)
    results: list[object] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def run(supervisor):
        barrier.wait()
        result = supervisor.start()
        with results_lock:
            results.append(result)

    threads = [
        threading.Thread(target=run, args=(supervisor,), daemon=True)
        for supervisor in supervisors
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=40)
        assert all(not thread.is_alive() for thread in threads), "start() hung"

        assert len(results) == 2
        started = [r for r in results if r.ok and r.changed]
        assert len(started) == 1, f"expected exactly one launch, got {results!r}"
        winner = started[0]
        pid = winner.pid
        assert pid and pid_alive(pid)

        # The other call either saw the ready host or was refused by the lock;
        # it must never report a launch of its own.
        loser = next(r for r in results if r is not winner)
        assert loser.changed is False
        assert loser.ok or "lock" in (loser.error or "")

        lock_record = json.loads(first.lock_path.read_text(encoding="utf-8"))
        assert lock_record["host_pid"] == pid
        report = first.probe()
        assert report.state is SidecarState.READY
        assert report.pid == pid
    finally:
        for supervisor in supervisors:
            try:
                supervisor.stop(force=True)
            except Exception:
                pass


def test_lock_retry_exhaustion_is_refused_without_a_host(tmp_path):
    """An unreadable young lock is never unlinked; start refuses instead."""
    supervisor = _supervisor(tmp_path)
    supervisor.state_dir.mkdir(parents=True, exist_ok=True)
    supervisor.lock_path.write_text("{ this is not json", encoding="utf-8")

    result = supervisor.start()

    assert result.ok is False
    assert result.state is SidecarState.ERROR
    assert "lock" in (result.error or "").lower()
    assert supervisor.lock_path.exists(), "a young unreadable lock must not be deleted"
    status = json.loads(supervisor.status_path.read_text(encoding="utf-8"))
    assert status["state"] == "error"
    assert status["pid"] is None, "no host may launch without a lock of our own"


# ---------------------------------------------------------------------------
# stale lock
# ---------------------------------------------------------------------------


def test_stale_lock_and_status_are_recovered(sup):
    dead_pid = _dead_pid()
    sup.state_dir.mkdir(parents=True, exist_ok=True)
    nonce = "stale-nonce-0001"

    sup.lock_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "instance": "default",
                "nonce": nonce,
                "supervisor_pid": dead_pid,
                "host_pid": dead_pid,
                "started_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    sup.status_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "instance": "default",
                "writer": "host",
                "pid": dead_pid,
                "state": "ready",
                "lock_nonce": nonce,
                "heartbeat_mono": time.monotonic() - 120.0,
                "heartbeat_at": "2026-01-01T00:00:00Z",
                "heartbeat_seq": 9,
            }
        ),
        encoding="utf-8",
    )

    report = sup.probe()
    assert report.state is SidecarState.STALE
    assert report.runtime_state == "error"

    result = sup.start()
    assert result.ok, result
    assert result.changed is True
    assert result.pid != dead_pid
    assert sup.probe().state is SidecarState.READY
    assert json.loads(sup.lock_path.read_text(encoding="utf-8"))["host_pid"] == result.pid


# ---------------------------------------------------------------------------
# crash recovery
# ---------------------------------------------------------------------------


def test_killed_child_is_reported_as_error(sup):
    started = sup.start()
    assert started.ok, started
    pid = started.pid

    assert terminate_pid(pid), "could not kill the test child"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and pid_alive(pid):
        time.sleep(0.02)
    assert not pid_alive(pid)

    report = sup.probe()
    assert report.state is SidecarState.STALE
    assert report.runtime_state == "error", "a crash must not read as clean OFF"
    assert "not running" in report.detail
    assert report.log_tail, "log tail should name the last host lines"

    # Recovery: a start clears the crashed record and comes up clean.
    recovered = sup.start()
    assert recovered.ok, recovered
    assert recovered.pid != pid
    assert sup.probe().state is SidecarState.READY


# ---------------------------------------------------------------------------
# launch failures
# ---------------------------------------------------------------------------


def test_missing_launcher_fails_without_leftovers(tmp_path):
    missing = tmp_path / "nowhere" / "python.exe"
    supervisor = SidecarSupervisor(
        [str(missing)],
        HOST,
        tmp_path / "state",
        start_timeout=5.0,
        heartbeat_interval=0.1,
        parent_pid=os.getpid(),
    )

    result = supervisor.start()
    assert result.ok is False
    assert result.state is SidecarState.ERROR
    assert "launcher" in (result.error or "").lower()
    assert not supervisor.lock_path.exists()
    assert not supervisor.token_path.exists()
    assert not supervisor.stop_path.exists()
    assert supervisor.probe().state is SidecarState.ERROR
    assert "launcher" in (supervisor.probe().detail or "").lower()


def test_wrong_interpreter_is_refused_by_the_host(tmp_path):
    supervisor = _supervisor(tmp_path, expect_python="3.13")
    result = supervisor.start()

    assert result.ok is False
    assert result.state is SidecarState.ERROR
    assert "wrong interpreter" in (result.error or "")
    assert not supervisor.lock_path.exists(), "failed launch must release the lock"
    if result.pid:
        assert not pid_alive(result.pid), "failed launch left the child running"


# ---------------------------------------------------------------------------
# integration with the S2 control surface (transport.py)
# ---------------------------------------------------------------------------


def test_control_surface_handover(tmp_path):
    """serve=True: transport owns status.json, the supervisor drives /shutdown."""
    from rt_runtime.sidecar import transport

    supervisor = _supervisor(tmp_path, serve=True)
    started = supervisor.start()
    assert started.ok, started
    assert started.port and started.port > 0
    assert any("control port" in reason for reason in started.reasons)

    report = supervisor.probe()
    assert report.state is SidecarState.READY
    assert report.port == started.port
    status = report.status
    assert status["pid"] == started.pid
    assert status["host"] == "127.0.0.1"
    assert status["instance_id"], "the transport's launch identity is published"
    assert status["token_file"]
    assert status["bind_fallback"] is False
    assert status["ready_at"], "the transport publishes its ready transition"
    assert status["python_implementation"] == "cpython"

    token = transport.read_token_file(supervisor.token_path)
    assert status["token_fingerprint"] == transport.token_fingerprint(token)
    assert token not in json.dumps(status)

    base = f"http://127.0.0.1:{started.port}"
    code, body = transport.request(base, "/health")
    assert code == 200
    assert body["pid"] == started.pid
    assert "token" not in json.dumps(body).lower()

    assert transport.request(base, "/status")[0] == 401
    assert transport.request(base, "/shutdown", method="POST")[0] == 401

    code, body = transport.request(base, "/shutdown", method="POST", token=token)
    assert code == 200, body
    assert body.get("ok") is True

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and pid_alive(started.pid):
        time.sleep(0.02)
    assert not pid_alive(started.pid), "host did not exit after POST /shutdown"

    report = supervisor.probe()
    assert report.state is SidecarState.STOPPED
    stopped_record = report.status or {}
    assert stopped_record.get("stop_reason") == "http-shutdown"
    # A stopped record must not look fresh or name a dead socket or secret.
    assert stopped_record.get("heartbeat_mono") is None
    assert stopped_record.get("heartbeat_at") is None
    assert stopped_record.get("heartbeat_seq") == 0
    assert stopped_record.get("port") is None
    assert stopped_record.get("token_file") is None
    # The handover's ready transition and exit semantics survive the stop.
    assert stopped_record.get("ready_at") == status["ready_at"]
    assert stopped_record.get("exit") == "graceful"
    assert stopped_record.get("writer") == "host"

    cleaned = supervisor.stop()
    assert cleaned.ok
    assert not supervisor.token_path.exists()
    assert not supervisor.lock_path.exists()


# ---------------------------------------------------------------------------
# Houdini restart behaviour
# ---------------------------------------------------------------------------


def test_host_exits_when_its_parent_dies(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    dead_parent = _dead_pid()

    command = [
        PYTHON,
        "-I",
        "-X",
        "utf8",
        "-u",
        str(HOST),
        "--state-dir",
        str(state_dir),
        "--instance",
        "default",
        "--heartbeat-interval",
        "0.1",
        "--parent-pid",
        str(dead_parent),
        "--expect-python",
        "3.14",
    ]
    proc = subprocess.Popen(
        command,
        cwd=str(state_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:  # pragma: no cover - safety net
            proc.kill()
            proc.wait(timeout=10)

    record = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
    assert record["state"] == "stopped"
    assert record["stop_reason"] == "parent-exit"
    assert record["exit"] == "graceful"
    assert record["ready_at"], "the ready transition happened before the stop"
    # A stopped record is never fresh: the beat is cleared, not left at its last value.
    assert record["heartbeat_mono"] is None
    assert record["heartbeat_at"] is None
    assert record["heartbeat_seq"] == 0
