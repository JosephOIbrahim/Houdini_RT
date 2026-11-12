"""The sidecar host invokes a compatible --entry-point payload.

The lifecycle host is Windows-first and asserts Python 3.14, so these tests
run only on 3.14. They spawn the host as a real child, but the payload is a
tiny local module; no Houdini, no network, no RT package.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rt_runtime.sidecar import host_stub

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 14),
    reason="the sidecar host asserts --expect-python 3.14",
)

HOST = Path(host_stub.__file__).resolve()
PYTHON = sys.executable


def test_host_invokes_a_one_argument_entry_point(tmp_path):
    marker = tmp_path / "called.txt"
    (tmp_path / "entry_fake.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def run(context):\n"
        f"    Path({str(marker)!r}).write_text(context['instance'], encoding='utf-8')\n",
        encoding="utf-8",
    )
    state = tmp_path / "state"
    state.mkdir()

    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    proc = subprocess.Popen(
        [
            PYTHON,
            "-X",
            "utf8",
            "-u",
            str(HOST),
            "--state-dir",
            str(state),
            "--instance",
            "entry",
            "--heartbeat-interval",
            "0.1",
            "--entry-point",
            "entry_fake.run",
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not marker.exists():
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        assert marker.exists(), (
            "the host did not invoke the entry point; stderr="
            + proc.stderr.read().decode("utf-8", "replace")[-1500:]
        )
        assert marker.read_text(encoding="utf-8") == "entry"
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - safety net
            proc.kill()
            proc.wait(timeout=10)


def test_other_multi_argument_entry_point_is_reported_not_called(tmp_path, monkeypatch):
    sentinel = tmp_path / "called.txt"
    (tmp_path / "entry_multi.py").write_text(
        "def run(alpha, beta, gamma):\n"
        f"    open({str(sentinel)!r}, 'w').write('called')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    result = host_stub._invoke_entry_point("entry_multi.run", {})

    assert result["status"] == "needs-arguments"
    assert "alpha, beta, gamma" in result["required"]
    assert not sentinel.exists()


def test_run_turn_shaped_entry_point_is_invoked_with_doubles(tmp_path, monkeypatch):
    pytest.importorskip("synapse")
    sentinel = tmp_path / "called.txt"
    (tmp_path / "entry_turn.py").write_text(
        "from types import SimpleNamespace\n"
        "\n"
        "def run(client, dispatcher, user_prompt):\n"
        f"    open({str(sentinel)!r}, 'w').write(user_prompt)\n"
        "    assert hasattr(client, 'messages')\n"
        "    return SimpleNamespace(status='ok')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    result = host_stub._invoke_entry_point(
        "entry_turn.run",
        {"prompt": "hello sidecar", "state_dir": str(tmp_path)},
    )

    assert result["status"] == "called"
    assert result["result_status"] == "ok"
    assert sentinel.read_text(encoding="utf-8") == "hello sidecar"


def test_guarded_run_turn_returns_complete(tmp_path):
    pytest.importorskip("synapse")
    result = host_stub._invoke_entry_point(
        "synapse.cognitive.agent_loop.run_turn",
        {"state_dir": str(tmp_path), "prompt": "ping"},
    )

    assert result["status"] == "called"
    assert result["result_status"] == "complete"
    assert (tmp_path / "model_access.json").is_file()
