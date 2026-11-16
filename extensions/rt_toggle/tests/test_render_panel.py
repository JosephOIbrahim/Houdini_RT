"""Behavioral panel presenter tests without Qt; real widget smoke is separate."""
import ast
from pathlib import Path
from types import SimpleNamespace

from rt_runtime.render_session import RenderStatus


class Field:
    def __init__(self):
        self.text = ""
        self.enabled = True

    def setText(self, value):
        self.text = value

    def setEnabled(self, value):
        self.enabled = value


def panel(controller, viewer):
    # Execute the actual presenter/action methods, without importing a Qt ABI.
    path = Path(__file__).parents[1] / "houdini" / "rt_panel.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RT_TogglePanel")
    cls.bases = [ast.Name(id="object", ctx=ast.Load())]
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    namespace = {}
    exec(compile(module, str(path), "exec"), namespace)
    instance = object.__new__(namespace["RT_TogglePanel"])
    instance._render_controller = controller
    instance._viewer = viewer
    instance._viewer_list = (viewer,) if viewer else ()
    instance._last_action_error = ""
    for name in ("_renderer_label", "_render_state_label", "_render_toggle",
                 "_render_pause", "_render_message", "_render_error"):
        setattr(instance, name, Field())
    return instance


def test_actions_keep_the_selected_viewer_and_observe_native_result():
    target = object()
    calls = []
    active = RenderStatus("active", "target", "HoudiniRT", "Karma CPU", True)
    paused = RenderStatus("paused", "target", "HoudiniRT", "Karma CPU", True)
    controller = SimpleNamespace(
        toggle=lambda v: calls.append(("toggle", v)) or active,
        toggle_pause=lambda v: calls.append(("pause", v)) or paused,
    )
    ui = panel(controller, target)
    ui._on_render_toggle()
    assert calls == [("toggle", target)]
    assert ui._renderer_label.text == "HoudiniRT"
    assert ui._render_state_label.text == "Active (unpaused)"
    ui._on_render_pause()
    assert calls[-1] == ("pause", target)
    assert ui._render_pause.text == "Resume"


def test_readback_tracks_external_renderer_change_without_mutating_it():
    states = iter([
        RenderStatus("paused", "view", "HoudiniRT", "Karma CPU", True),
        RenderStatus("off", "view", "Karma XPU"),
    ])
    ui = panel(SimpleNamespace(status=lambda v: next(states)), object())
    ui._refresh_render_state()
    assert ui._render_pause.enabled
    ui._refresh_render_state()
    assert ui._renderer_label.text == "Karma XPU"
    assert not ui._render_pause.enabled
    assert ui._render_toggle.text == "Render with HoudiniRT"


def test_unowned_active_renderer_cannot_invent_an_off_target():
    ui = panel(None, object())
    ui._refresh_render_state(RenderStatus("active", "view", "HoudiniRT"))
    assert not ui._render_toggle.enabled
    assert ui._render_pause.enabled
    assert "no previous renderer" in ui._render_message.text


def test_failed_action_stays_visible_during_poll_and_can_retry_restoration():
    observed = RenderStatus("off", "view", "Karma CPU", "Karma CPU", True)
    result = RenderStatus("off", "view", "Karma CPU", "Karma CPU", True, error="Pause restore failed")
    ui = panel(SimpleNamespace(toggle=lambda v: result, status=lambda v: observed), object())
    ui._on_render_toggle()
    ui._refresh_render_state()
    assert ui._render_error.text == "Pause restore failed"
    assert ui._render_toggle.enabled
    assert "restore previous" in ui._render_toggle.text


def test_no_target_never_calls_controller():
    ui = panel(SimpleNamespace(toggle=lambda v: (_ for _ in ()).throw(AssertionError())), None)
    ui._on_render_toggle()
    assert not ui._render_toggle.enabled
    assert not ui._render_pause.enabled
    assert ui._render_state_label.text == "No target viewer"


def test_pending_pause_keeps_actual_state_and_disables_duplicate_actions():
    ui = panel(None, object())
    ui._refresh_render_state(RenderStatus(
        "active", "view", "HoudiniRT", "Karma CPU", True, pending="pause"))
    assert ui._render_state_label.text == "Active (unpaused)"
    assert ui._render_pause.text == "Pausing…"
    assert not ui._render_pause.enabled
    assert not ui._render_toggle.enabled
    assert "Waiting for Houdini" in ui._render_message.text
    ui._refresh_render_state(RenderStatus("paused", "view", "HoudiniRT", "Karma CPU", True))
    assert ui._render_state_label.text == "Paused"
    assert ui._render_pause.text == "Resume"
    assert ui._render_toggle.enabled


def test_pending_restore_keeps_actual_renderer_until_confirmation():
    ui = panel(None, object())
    ui._refresh_render_state(RenderStatus(
        "off", "view", "Karma CPU", "Karma CPU", True, pending="restore"))
    assert ui._renderer_label.text == "Karma CPU"
    assert ui._render_toggle.text == "Restoring previous renderer…"
    assert not ui._render_toggle.enabled
    ui._refresh_render_state(RenderStatus(
        "off", "view", "Karma CPU", "Karma CPU", True, error="Restore timed out"))
    assert ui._render_error.text == "Restore timed out"
    assert ui._render_toggle.enabled
