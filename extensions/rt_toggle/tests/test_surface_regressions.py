"""Regression tests for the Houdini surfaces, without Houdini or Qt.

The node and shelf callbacks are called directly under ``fake_hou``. This does
not claim GUI event dispatch; it tests the callback bodies and their adapter
calls.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import fake_hou

EXT_ROOT = Path(__file__).resolve().parent.parent


def _load_surface(name):
    if name in sys.modules:
        return sys.modules[name]
    path = EXT_ROOT / "houdini" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def node_surface():
    fake_hou.install()
    module = _load_surface("rt_node")
    try:
        yield module
    finally:
        fake_hou.ui.clear()
        fake_hou.uninstall()


def _result(state):
    return SimpleNamespace(
        state=state,
        active=state in ("active", "paused"),
        viewer_name="solaris",
        renderer="HoudiniRT" if state in ("active", "paused") else "Karma XPU",
        message="Observed native viewer state",
        error="",
    )


class _DirectionalAdapter:
    def __init__(self):
        self.calls = []

    def resolve_viewer(self, **kwargs):
        return SimpleNamespace(name=lambda: "solaris")

    def activate(self, viewer):
        self.calls.append("activate")
        return _result("active")

    def deactivate(self, viewer):
        self.calls.append("deactivate")
        return _result("off")

    def toggle(self, viewer):
        self.calls.append("toggle")
        return _result("off")


@pytest.mark.parametrize(
    "requested_on, expected_call, expected_status",
    [(True, "activate", "ACTIVE"), (False, "deactivate", "OFF")],
)
def test_node_toggle_uses_direction_and_result_state(
    node_surface, monkeypatch, requested_on, expected_call, expected_status
):
    """A stale checkbox must not invert the operation or lie about its state."""
    from rt_runtime import render_session

    adapter = _DirectionalAdapter()
    monkeypatch.setattr(render_session, "get_render_controller", lambda: adapter)

    node = fake_hou.Node("RT_TOGGLE_TEST")
    enable = node.addParm(
        fake_hou.ToggleParmTemplate("enable", default_value=not requested_on)
    )
    status = node.addParm(fake_hou.StringParmTemplate("rt_status"))
    enable.set(1 if requested_on else 0)

    node_surface.toggle_callback({"node": node, "parm": enable})

    assert adapter.calls == [expected_call]
    assert "toggle" not in adapter.calls
    assert enable.eval() == (1 if requested_on else 0)
    assert status.eval().startswith(expected_status + " |")


def test_cancelled_shelf_prompt_never_submits(monkeypatch):
    shelf = _load_surface("rt_shelf")
    monkeypatch.delenv("RT_TURN_PROMPT", raising=False)
    fake_hou.install()
    try:
        monkeypatch.setattr(
            fake_hou.ui,
            "readInput",
            lambda *args, **kwargs: (1, "do not run"),
            raising=False,
        )
        assert shelf._read_turn_prompt() == ""

        class _Control:
            def __init__(self):
                self.submissions = 0

            def submit(self, *args, **kwargs):
                self.submissions += 1

        control = _Control()
        adapter = SimpleNamespace(turn_control=lambda: control)
        from rt_runtime import session

        monkeypatch.setattr(session, "get_adapter", lambda: adapter)

        assert shelf.turn_from_shelf() is None
        assert control.submissions == 0
    finally:
        fake_hou.ui.clear()
        fake_hou.uninstall()


def test_ok_shelf_prompt_returns_the_text(monkeypatch):
    shelf = _load_surface("rt_shelf")
    monkeypatch.delenv("RT_TURN_PROMPT", raising=False)
    fake_hou.install()
    try:
        monkeypatch.setattr(
            fake_hou.ui,
            "readInput",
            lambda *args, **kwargs: (0, "run this"),
            raising=False,
        )
        assert shelf._read_turn_prompt() == "run this"
    finally:
        fake_hou.ui.clear()
        fake_hou.uninstall()


@pytest.mark.parametrize("surface_name", ["rt_shelf", "rt_node"])
def test_bounded_poll_passes_the_remaining_budget(surface_name):
    """A refresh at the deadline must not start another full-length request."""
    surface = _load_surface(surface_name)

    class _SlowPoller:
        def __init__(self):
            self.done = False
            self.timeouts = []

        def refresh(self, *, timeout=None):
            self.timeouts.append(timeout)
            # The old implementation omitted timeout, making this one second.
            time.sleep(1.0 if timeout is None else timeout)

    poller = _SlowPoller()
    started = time.monotonic()
    surface._poll_to_end(poller, 0.1)
    elapsed = time.monotonic() - started

    assert elapsed < 0.6, elapsed
    assert len(poller.timeouts) <= 2
    assert all(timeout is not None for timeout in poller.timeouts)
    assert all(0.0 <= timeout <= 0.11 for timeout in poller.timeouts)


def test_panel_uses_the_nonblocking_submit_helper():
    """Check executable calls, not a docstring containing ``wait_ms=0``."""
    panel_source = (EXT_ROOT / "houdini" / "rt_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(panel_source)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_on_run_turn_clicked"
    ]
    assert len(functions) == 1

    calls = [
        call.func.id
        for call in ast.walk(functions[0])
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "submit_turn"
    ]
    assert calls, "the panel must use submit_turn()"


def test_submit_helper_keeps_wait_ms_zero_in_the_executable_call():
    source = (EXT_ROOT / "rt_runtime" / "turn_control.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "submit_turn"
    )

    submit_calls = [
        node
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "submit"
    ]
    assert len(submit_calls) == 1
    wait_keywords = {
        keyword.arg: keyword.value
        for keyword in submit_calls[0].keywords
        if keyword.arg == "wait_ms"
    }
    assert isinstance(wait_keywords["wait_ms"], ast.Constant)
    assert wait_keywords["wait_ms"].value == 0


def test_config_override_selects_an_isolated_verification_config(
    tmp_path, monkeypatch
):
    """GUI checks must not edit or borrow the extension's default state."""
    from rt_runtime import session

    config = tmp_path / "rt_toggle.json"
    config.write_text(
        """
        {
          "version": 1,
          "default": "verification_target",
          "targets": {
            "verification_target": {
              "home": ".",
              "python_version": "3.12",
              "site_packages": ".",
              "allow_pure_python_only": true
            }
          }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("RT_TOGGLE_CONFIG", str(config))
    session.reset_cache()
    try:
        adapter = session.get_adapter()
        assert adapter.target.name == "verification_target"
        assert adapter.state_path == tmp_path / ".rt_toggle_state.json"
    finally:
        session.reset_cache()


def test_status_detail_exposes_sidecar_health_when_requested():
    from rt_runtime.adapter import RuntimeAdapter, RuntimeTarget

    adapter = RuntimeAdapter(
        RuntimeTarget(
            name="demo",
            home=Path("."),
            python_version="3.12",
            site_packages=Path("."),
        )
    )
    adapter._sidecar = SimpleNamespace(
        status_detail=lambda *, check_health: {
            "runtime_state": "on",
            "health": {"ok": True, "code": 200},
        }
    )

    detail = adapter.status_detail(check_health=True)
    assert detail["runtime_state"] == "on"
    assert detail["health"]["ok"] is True
