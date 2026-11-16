"""Behavioral checks for shelf, radial and native parameter renderer actions."""

from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

import fake_hou

EXT_ROOT = Path(__file__).resolve().parents[1]


def surface(name):
    spec = importlib.util.spec_from_file_location(
        name, EXT_ROOT / "houdini" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def observed(state="active", error=""):
    return SimpleNamespace(
        state=state, active=state in ("active", "paused"),
        viewer_name="solaris_a", renderer="HoudiniRT", previous_renderer="Karma",
        owned=True, message="Native renderer selected", error=error,
    )


class Controller:
    def __init__(self):
        self.viewer = SimpleNamespace(name=lambda: "solaris_a")
        self.calls = []
        self.result = observed()
        self.failure = None

    def resolve_viewer(self, **kwargs):
        self.calls.append(("resolve", kwargs))
        if self.failure:
            raise ValueError(self.failure)
        return self.viewer

    def toggle(self, viewer):
        self.calls.append(("toggle", viewer))
        return self.result

    def toggle_pause(self, viewer):
        self.calls.append(("pause", viewer))
        return self.result

    def activate(self, viewer):
        self.calls.append(("activate", viewer))
        return self.result

    def deactivate(self, viewer):
        self.calls.append(("deactivate", viewer))
        return self.result

    def status(self, viewer):
        self.calls.append(("status", viewer))
        return self.result


@pytest.fixture
def controller(monkeypatch):
    from rt_runtime import render_session, session

    control = Controller()
    monkeypatch.setattr(render_session, "get_render_controller", lambda: control)

    def forbidden():
        raise AssertionError("A renderer command must not start/probe the sidecar")

    monkeypatch.setattr(session, "get_adapter", forbidden)
    fake_hou.install()
    yield control
    fake_hou.uninstall()


@pytest.mark.parametrize("helper,command", [
    ("toggle_from_shelf", "toggle"), ("pause_from_shelf", "pause"),
    ("refresh_from_shelf", "status"),
])
def test_shelf_passes_invoking_pane_to_shared_controller(controller, helper, command):
    shelf = surface("rt_shelf")
    pane = object()
    result = getattr(shelf, helper)(viewer=pane)
    assert result is controller.result
    assert controller.calls == [
        ("resolve", {"viewer": pane, "viewer_name": None}),
        (command, controller.viewer),
    ]


def test_shelf_reports_ambiguous_target_without_dispatch(controller, capsys):
    controller.failure = "Choose a Solaris viewer"
    assert surface("rt_shelf").toggle_from_shelf() is None
    assert [call[0] for call in controller.calls] == ["resolve"]
    assert "Choose a Solaris viewer" in capsys.readouterr().out


def node_controls():
    node = fake_hou.Node("RT_TOGGLE")
    node.addParm(fake_hou.ToggleParmTemplate("enable", default_value=False))
    node.addParm(fake_hou.StringParmTemplate("rt_status", default_value=""))
    node.addParm(fake_hou.StringParmTemplate("rt_viewer", default_value=""))
    return node


def test_node_pins_resolved_viewer_then_off_uses_that_target(controller):
    module = surface("rt_node")
    node = node_controls()
    enable = node.parm("enable")
    pane = object()
    enable.set(True)
    module.toggle_callback({"node": node, "parm": enable, "pane": pane})
    assert node.parm("rt_viewer").eval() == "solaris_a"
    assert controller.calls[0] == ("resolve", {"viewer": pane, "viewer_name": None})
    assert controller.calls[1] == ("activate", controller.viewer)
    controller.calls.clear()
    controller.result = observed("off")
    enable.set(False)
    module.toggle_callback({"node": node, "parm": enable, "pane": object()})
    assert controller.calls == [
        ("resolve", {"viewer": None, "viewer_name": "solaris_a"}),
        ("deactivate", controller.viewer),
    ]
    assert not enable.eval()


def test_node_uses_observed_renderer_after_restore_failure(controller):
    module = surface("rt_node")
    node = node_controls()
    controller.result = observed("paused", error="Previous renderer unavailable")
    module.toggle_callback({"node": node, "parm": node.parm("enable")})
    assert node.parm("enable").eval()
    assert node.parm("rt_status").eval().startswith("PAUSED |")
    assert "Previous renderer unavailable" in node.parm("rt_status").eval()


@pytest.mark.parametrize("callback,expected", [
    ("pause_callback", "pause"), ("refresh_render_callback", "status"),
])
def test_node_pause_and_refresh_share_pinned_controller(controller, callback, expected):
    module = surface("rt_node")
    node = node_controls()
    node.parm("rt_viewer").set("solaris_a")
    getattr(module, callback)({"node": node})
    assert controller.calls[-1] == (expected, controller.viewer)
    assert node.parm("enable").eval()


def test_node_failure_does_not_claim_an_observed_off_state(controller):
    module = surface("rt_node")
    node = node_controls()
    node.parm("enable").set(True)
    controller.failure = "Viewer was closed"
    module.toggle_callback({"node": node, "parm": node.parm("enable")})
    assert [call[0] for call in controller.calls] == ["resolve"]
    assert node.parm("rt_status").eval() == "UNKNOWN | Viewer was closed"


@pytest.mark.parametrize("requested", [False, True])
def test_node_readback_error_preserves_checkbox_on_refresh(controller, requested):
    module = surface("rt_node")
    node = node_controls()
    node.parm("enable").set(requested)
    controller.result = observed("error", error="Native readback failed")
    module.refresh_render_callback({"node": node})
    assert bool(node.parm("enable").eval()) is requested
    assert node.parm("rt_status").eval().startswith("ERROR |")


@pytest.mark.parametrize("requested", [False, True])
def test_node_readback_error_reverts_checkbox_request(controller, requested):
    module = surface("rt_node")
    node = node_controls()
    node.parm("enable").set(requested)
    controller.result = observed("error", error="Native readback failed")
    module.toggle_callback({"node": node})
    assert bool(node.parm("enable").eval()) is not requested


def test_node_explicit_restore_retries_after_checkbox_is_off(controller):
    module = surface("rt_node")
    node = node_controls()
    node.parm("rt_viewer").set("solaris_a")
    controller.result = observed("off", error="Previous pause state was not restored")
    module.toggle_callback({"node": node})
    assert not node.parm("enable").eval()
    controller.calls.clear()
    controller.result = observed("off")
    module.restore_render_callback({"node": node})
    assert controller.calls[-1] == ("deactivate", controller.viewer)
    assert not node.parm("enable").eval()


@pytest.mark.parametrize("state,pending,checked", [
    ("off", "enable", False), ("active", "pause", True),
    ("paused", "resume", True), ("off", "restore", False),
])
def test_node_pending_status_keeps_observed_checkbox(controller, state, pending, checked):
    module = surface("rt_node")
    node = node_controls()
    node.parm("enable").set(not checked)
    controller.result = observed(state)
    controller.result.pending = pending
    module.refresh_render_callback({"node": node})
    assert bool(node.parm("enable").eval()) is checked
    assert "Pending " + pending in node.parm("rt_status").eval()


def test_shelf_and_radial_pending_state_remains_observed(controller, capsys):
    shelf = surface("rt_shelf")
    radial = surface("rt_radial")
    controller.result = observed("active")
    controller.result.pending = "pause"
    shelf.pause_from_shelf()
    assert "ACTIVE" in capsys.readouterr().out
    text, level = shelf.describe_render_status(controller.result)
    assert "Pending pause" in text and level == "message"
    assert eval(radial.CHECK_EXPRESSION, {}) is True
    assert eval(radial.check_expression(paused=True), {}) is False


def test_radial_action_and_check_preserve_invoking_pane(controller):
    surface("rt_shelf")
    radial = surface("rt_radial")
    pane = object()
    scope = {"kwargs": {"pane": pane}}
    assert eval(radial.CHECK_EXPRESSION, scope)
    assert controller.calls[0][1]["viewer"] is pane
    controller.calls.clear()
    exec(radial.action_script("pause_from_shelf"), scope)
    assert controller.calls[0][1]["viewer"] is pane
    assert controller.calls[-1] == ("pause", controller.viewer)


def test_radial_check_tolerates_unavailable_viewer(controller):
    surface("rt_shelf")
    radial = surface("rt_radial")
    controller.failure = "Not a Solaris viewer"
    assert eval(radial.CHECK_EXPRESSION, {"kwargs": {"pane": object()}}) is False


def test_shelf_xml_render_commands_are_registered_for_lops(controller):
    surface("rt_shelf")
    root = ET.parse(EXT_ROOT / "houdini" / "rt_shelf.shelf").getroot()
    for name, helper in (("rt_toggle", "toggle_from_shelf"), ("rt_pause", "pause_from_shelf")):
        tool = root.find("tool[@name='%s']" % name)
        assert tool is not None
        assert tool.find("toolMenuContext/contextNetType").text == "LOP"
        code = tool.find("script").text
        assert helper + '(viewer=kwargs.get("pane"))' in code
        compile(code, name, "exec")
        controller.calls.clear()
        pane = object()
        exec(code, {"kwargs": {"pane": pane}})
        assert controller.calls[0][1]["viewer"] is pane
        assert controller.calls[-1][0] == ("toggle" if name == "rt_toggle" else "pause")
