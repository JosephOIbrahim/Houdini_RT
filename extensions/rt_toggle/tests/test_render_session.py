"""Behavioral renderer-session tests; no Houdini, Qt, network or sidecar."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from rt_runtime.render_session import RenderController, RenderSessionError


class Viewer:
    def __init__(self, name="persp1", renderer="Karma CPU", paused=False):
        self.label = name
        self.current = renderer
        self.paused = paused
        self.available = ["Houdini GL", "Karma CPU", "Karma XPU", "HoudiniRT"]
        self.lop = True
        self.closed = False
        self.calls = []
        self.select_mode = "normal"
        self.pause_mode = "normal"

    def _valid(self):
        if self.closed:
            raise RuntimeError("Pane has been deleted")

    def type(self):
        self._valid()
        return "SceneViewer"

    def name(self):
        self._valid()
        return self.label

    def isViewingSceneGraph(self):
        self._valid()
        return self.lop

    def hydraRenderers(self):
        self._valid()
        return tuple(self.available)

    def currentHydraRenderer(self):
        self._valid()
        return self.current

    def isRendererPaused(self):
        self._valid()
        return self.paused

    def setHydraRenderer(self, renderer):
        self._valid()
        self.calls.append(("renderer", renderer))
        if self.select_mode == "raise_before":
            raise RuntimeError("Selection failed")
        if self.select_mode == "silent":
            return
        self.current = renderer
        if self.select_mode == "raise_after":
            raise RuntimeError("Selection partly completed")

    def setRendererPaused(self, paused):
        self._valid()
        self.calls.append(("paused", paused))
        if self.pause_mode == "raise":
            raise RuntimeError("Pause change failed")
        if self.pause_mode != "silent":
            self.paused = paused


class UI:
    def __init__(self, *panes):
        self.panes = list(panes)
        self.cursor = None
        self.calls = 0

    def paneTabs(self):
        self.calls += 1
        return tuple(self.panes)

    def paneTabUnderCursor(self):
        self.calls += 1
        return self.cursor


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_controller(*panes, **options):
    ui = UI(*panes)
    hou = SimpleNamespace(ui=ui, paneTabType=SimpleNamespace(SceneViewer="SceneViewer"))
    return RenderController(hou, **options), ui


def test_explicit_viewer_wins_over_cursor_and_name():
    first, second = Viewer("one"), Viewer("two")
    controller, ui = make_controller(first, second)
    ui.cursor = second
    assert controller.resolve_viewer(first, "two") is first
    assert controller.resolve_viewer(viewer_name="two") is second
    assert controller.resolve_viewer() is second


def test_ambiguous_target_never_selects_first_viewer():
    first, second = Viewer("one"), Viewer("two")
    controller, _ = make_controller(first, second)
    with pytest.raises(RenderSessionError, match="more than one"):
        controller.resolve_viewer()
    result = controller.activate()
    assert result.state == "error"
    assert first.calls == second.calls == []


def test_sole_lop_viewer_selected_even_with_non_viewer_cursor():
    sop, lop = Viewer("sop"), Viewer("lop")
    sop.lop = False
    controller, ui = make_controller(sop, lop, SimpleNamespace(type=lambda: "NetworkEditor"))
    ui.cursor = sop
    assert controller.viewers() == (lop,)
    assert controller.resolve_viewer() is lop
    with pytest.raises(RenderSessionError, match="not viewing Solaris"):
        controller.resolve_viewer(sop)
    assert controller.activate(sop).error
    assert not lop.calls


def test_missing_or_repeated_name_requires_choice():
    first, second = Viewer("same"), Viewer("same")
    controller, _ = make_controller(first, second)
    for name in ("same", "missing"):
        with pytest.raises(RenderSessionError, match="not unique or available"):
            controller.resolve_viewer(viewer_name=name)


def test_no_viewer_has_actionable_error():
    controller, _ = make_controller()
    assert "Open a Scene Viewer" in controller.status().error


@pytest.mark.parametrize("prior_paused", [False, True])
def test_on_pause_resume_off_restores_original_state(prior_paused):
    viewer = Viewer(paused=prior_paused)
    controller, _ = make_controller(viewer)
    result = controller.activate(viewer)
    assert result.state == "active" and result.active and result.owned
    assert result.previous_renderer == "Karma CPU"
    assert viewer.current == "HoudiniRT" and not viewer.paused
    assert controller.set_paused(viewer, True).state == "paused"
    assert controller.toggle_pause(viewer).state == "active"
    result = controller.deactivate(viewer)
    assert result.state == "off" and not result.active and not result.owned
    assert viewer.current == "Karma CPU" and viewer.paused is prior_paused


def test_repeat_on_preserves_pause_and_original_renderer():
    viewer = Viewer()
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    controller.set_paused(viewer, True)
    before = list(viewer.calls)
    result = controller.activate(viewer)
    assert result.paused and result.previous_renderer == "Karma CPU"
    assert viewer.calls == before
    controller.deactivate(viewer)
    assert viewer.current == "Karma CPU"


def test_two_viewers_with_same_name_have_independent_ownership():
    first, second = Viewer("same"), Viewer("same", "Karma XPU", True)
    controller, _ = make_controller(first, second)
    controller.activate(first)
    controller.activate(second)
    controller.deactivate(first)
    assert first.current == "Karma CPU"
    assert second.current == "HoudiniRT"
    assert controller.status(second).owned
    controller.deactivate(second)
    assert second.current == "Karma XPU" and second.paused


def test_external_renderer_switch_relinquishes_ownership():
    viewer = Viewer()
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.current = "Karma XPU"
    before = list(viewer.calls)
    assert not controller.status(viewer).owned
    assert not controller.deactivate(viewer).error
    assert viewer.calls == before
    controller.activate(viewer)
    assert controller.status(viewer).previous_renderer == "Karma XPU"
    controller.deactivate(viewer)
    assert viewer.current == "Karma XPU"


@pytest.mark.parametrize("alias", ["HoudiniRT", "HdRTRendererPlugin"])
def test_selects_only_discovered_exact_renderer_alias(alias):
    viewer = Viewer()
    viewer.available = ["Karma CPU", alias]
    controller, _ = make_controller(viewer)
    assert controller.activate(viewer).active
    assert viewer.current == alias


def test_unavailable_renderer_does_not_mutate_native_state():
    viewer = Viewer()
    viewer.available = ["Karma CPU", "MyHoudiniRTExperiment"]
    controller, _ = make_controller(viewer)
    result = controller.activate(viewer)
    assert result.state == "unavailable" and result.error
    assert result.renderer == "Karma CPU" and not result.owned
    assert not viewer.calls


@pytest.mark.parametrize("mode", ["raise_before", "silent"])
def test_failed_selection_does_not_create_false_on_or_ownership(mode):
    viewer = Viewer()
    viewer.select_mode = mode
    controller, _ = make_controller(viewer)
    result = controller.activate(viewer)
    assert result.state == "off" and result.error
    assert not result.active and not result.owned


def test_selection_that_throws_after_switch_keeps_recovery():
    viewer = Viewer()
    viewer.select_mode = "raise_after"
    controller, _ = make_controller(viewer)
    result = controller.activate(viewer)
    assert result.active and result.error and result.owned
    viewer.select_mode = "normal"
    assert not controller.deactivate(viewer).error
    assert viewer.current == "Karma CPU"


@pytest.mark.parametrize("mode", ["raise", "silent"])
def test_failed_resume_keeps_observed_pause_and_restore_record(mode):
    viewer = Viewer(paused=True)
    viewer.pause_mode = mode
    clock = Clock()
    controller, _ = make_controller(viewer, clock=clock)
    result = controller.activate(viewer)
    if mode == "silent":
        assert result.pending == "enable" and not result.error
        clock.now = 10
        result = controller.status(viewer)
    assert result.active and result.paused and result.error and result.owned
    viewer.pause_mode = "normal"
    assert not controller.deactivate(viewer).error
    assert viewer.current == "Karma CPU" and viewer.paused


@pytest.mark.parametrize("mode", ["raise_before", "silent"])
def test_failed_restore_can_be_retried(mode):
    viewer = Viewer()
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.select_mode = mode
    result = controller.deactivate(viewer)
    assert result.active and result.owned and result.error
    viewer.select_mode = "normal"
    result = controller.toggle(viewer)
    assert not result.active and not result.owned and not result.error
    assert viewer.current == "Karma CPU"


def test_partial_restore_keeps_original_pause_for_retry():
    viewer = Viewer(paused=True)
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.pause_mode = "raise"
    result = controller.deactivate(viewer)
    assert result.state == "off" and result.owned and result.error
    assert viewer.current == "Karma CPU" and not viewer.paused
    assert result.previous_renderer == "Karma CPU"
    assert controller.activate(viewer).error  # cannot overwrite pending record
    viewer.pause_mode = "normal"
    result = controller.toggle(viewer)
    assert not result.error and not result.owned and viewer.paused
    assert viewer.current == "Karma CPU"


def test_throw_after_restoring_renderer_keeps_recovery():
    viewer = Viewer(paused=True)
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.select_mode = "raise_after"
    result = controller.deactivate(viewer)
    assert result.owned and result.error and viewer.current == "Karma CPU"
    viewer.select_mode = "normal"
    assert not controller.deactivate(viewer).error
    assert viewer.paused and not controller.status(viewer).owned


def test_already_selected_unowned_renderer_is_observed_without_invented_restore():
    viewer = Viewer(renderer="HoudiniRT", paused=True)
    controller, _ = make_controller(viewer)
    result = controller.activate(viewer)
    assert result.active and result.paused and not result.owned
    result = controller.deactivate(viewer)
    assert result.active and "no previous renderer" in result.error
    assert not viewer.calls
    assert controller.set_paused(viewer, False).state == "active"
    assert not controller.status(viewer).owned


def test_unavailable_previous_renderer_retains_recovery_without_fallback():
    viewer = Viewer()
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.available.remove("Karma CPU")
    before = list(viewer.calls)
    result = controller.deactivate(viewer)
    assert result.active and result.owned and "unavailable" in result.error
    assert viewer.calls == before
    viewer.available.append("Karma CPU")
    assert not controller.deactivate(viewer).error


def test_external_choice_during_partial_restore_is_not_overwritten():
    viewer = Viewer(paused=True)
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.pause_mode = "raise"
    assert controller.deactivate(viewer).owned
    viewer.current = "Karma XPU"
    before = list(viewer.calls)
    result = controller.deactivate(viewer)
    assert not result.owned and not result.error
    assert viewer.current == "Karma XPU" and viewer.calls == before


def test_closed_target_does_not_fall_back_to_other_viewer():
    closed, other = Viewer("old"), Viewer("new")
    controller, _ = make_controller(closed, other)
    controller.activate(closed)
    closed.closed = True
    result = controller.deactivate(closed)
    assert result.state == "error" and "closed" in result.error
    assert other.calls == []


def test_recreated_pane_with_same_name_cannot_inherit_previous_renderer():
    original = Viewer("same")
    controller, ui = make_controller(original)
    controller.activate(original)
    original.closed = True
    replacement = Viewer("same", "HoudiniRT")
    ui.panes = [replacement]
    result = controller.deactivate(replacement)
    assert result.error and not result.owned and replacement.calls == []


def test_selected_lop_target_retained_when_another_viewer_becomes_cursor():
    original, other = Viewer("original"), Viewer("other")
    controller, ui = make_controller(original, other)
    target = controller.resolve_viewer(original)
    controller.activate(target)
    ui.cursor = other
    controller.deactivate(target)
    assert original.current == "Karma CPU" and not other.calls


@pytest.mark.parametrize("action", ["status", "activate", "deactivate", "toggle", "toggle_pause"])
def test_worker_thread_never_calls_hom(action):
    viewer = Viewer()
    controller, ui = make_controller(viewer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(getattr(controller, action), viewer).result()
    assert "main thread" in result.error
    assert ui.calls == 0 and viewer.calls == []


def test_status_is_immutable_observation_and_not_convergence_claim():
    viewer = Viewer(renderer="HoudiniRT")
    controller, _ = make_controller(viewer)
    result = controller.status(viewer)
    assert result.state == "active"
    assert "selected" in result.message and "converged" not in result.message
    with pytest.raises(FrozenInstanceError):
        result.state = "off"
    viewer.paused = True
    assert controller.status(viewer).state == "paused"
    assert result.state == "active"  # snapshots do not mutate under observers


def test_pause_rejects_other_renderers_without_changing_them():
    viewer = Viewer()
    controller, _ = make_controller(viewer)
    result = controller.set_paused(viewer, True)
    assert result.state == "off" and result.error and not viewer.calls


def test_unknown_previous_renderer_is_not_claimed_as_restorable():
    viewer = Viewer(renderer="")
    controller, _ = make_controller(viewer)
    result = controller.activate(viewer)
    assert result.error and not result.owned and not viewer.calls


def test_native_query_may_return_plugin_id_for_discovered_display_name():
    class NativeAliasViewer(Viewer):
        def setHydraRenderer(self, renderer):
            super().setHydraRenderer(renderer)
            if renderer == "HoudiniRT":
                self.current = "HdRTRendererPlugin"

    viewer = NativeAliasViewer()
    controller, _ = make_controller(viewer)
    result = controller.activate(viewer)
    assert result.active and result.owned and not result.error
    assert controller.deactivate(viewer).renderer == "Karma CPU"


def test_pending_restore_can_finish_when_previous_renderer_is_already_selected():
    viewer = Viewer(paused=True)
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.pause_mode = "raise"
    assert controller.deactivate(viewer).owned
    viewer.available.remove("Karma CPU")
    viewer.pause_mode = "normal"
    result = controller.deactivate(viewer)
    assert not result.error and not result.owned and viewer.paused


def test_new_python_wrapper_for_same_native_pane_keeps_ownership():
    class Wrapper:
        def __init__(self, native):
            self.native = native

        def __getattr__(self, name):
            return getattr(self.native, name)

        def __eq__(self, other):
            return isinstance(other, Wrapper) and self.native is other.native

    native = Viewer()
    first, second = Wrapper(native), Wrapper(native)
    controller, _ = make_controller(first)
    assert controller.activate(first).owned
    assert controller.status(second).owned
    assert not controller.deactivate(second).error
    assert native.current == "Karma CPU"


def test_silent_pause_failure_is_reported_from_observed_state():
    viewer = Viewer()
    clock = Clock()
    controller, _ = make_controller(viewer, clock=clock)
    controller.activate(viewer)
    viewer.pause_mode = "silent"
    result = controller.set_paused(viewer, True)
    assert result.busy and result.pending == "pause" and not result.error
    clock.now = 10
    result = controller.status(viewer)
    assert result.state == "active" and result.error and not result.paused
    assert not result.busy


def test_session_singleton_has_no_hom_requirement_until_used(monkeypatch):
    from rt_runtime import render_session

    monkeypatch.setattr(render_session, "_CONTROLLER", None)
    first = render_session.get_render_controller()
    assert render_session.get_render_controller() is first
    assert first._hou_module is None


@pytest.mark.parametrize("initial,requested,operation", [(False, True, "pause"), (True, False, "resume")])
def test_deferred_pause_is_observed_without_optimistic_state(initial, requested, operation):
    viewer = Viewer(renderer="HoudiniRT", paused=initial)
    viewer.pause_mode = "silent"
    clock = Clock()
    controller, _ = make_controller(viewer, clock=clock)
    result = controller.set_paused(viewer, requested)
    assert result.pending == operation and result.busy and not result.error
    assert result.paused is initial and not result.owned
    before = list(viewer.calls)
    clock.now = 2
    assert controller.status(viewer).busy
    viewer.paused = requested  # Houdini applies the queued request later.
    result = controller.status(viewer)
    assert result.paused is requested and not result.busy and not result.error
    assert viewer.calls == before


@pytest.mark.parametrize("action", ["activate", "deactivate", "toggle", "toggle_pause", "set_paused"])
def test_actions_while_pending_do_not_submit_conflicting_requests(action):
    viewer = Viewer()
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.pause_mode = "silent"
    controller.set_paused(viewer, True)
    before = list(viewer.calls)
    result = getattr(controller, action)(viewer)
    assert result.pending == "pause" and result.active and not result.paused
    assert viewer.calls == before


def test_deferred_enable_keeps_previous_pause_and_ownership():
    viewer = Viewer(paused=True)
    viewer.pause_mode = "silent"
    controller, _ = make_controller(viewer)
    result = controller.activate(viewer)
    assert result.pending == "enable" and result.paused and result.owned
    assert result.previous_renderer == "Karma CPU" and not result.error
    viewer.paused = False
    result = controller.status(viewer)
    assert result.state == "active" and result.owned and not result.busy
    viewer.pause_mode = "normal"
    assert not controller.deactivate(viewer).owned
    assert viewer.paused


def test_deferred_restore_keeps_recovery_until_previous_pause_confirmed():
    viewer = Viewer(paused=True)
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.pause_mode = "silent"
    result = controller.deactivate(viewer)
    assert result.state == "off" and result.owned and result.pending == "restore"
    before = list(viewer.calls)
    assert controller.toggle(viewer).pending == "restore"
    assert viewer.calls == before
    viewer.paused = True
    result = controller.status(viewer)
    assert result.state == "off" and not result.owned and not result.busy
    assert not result.error and viewer.calls == before


def test_timeout_is_latched_until_next_action_and_can_be_retried():
    viewer = Viewer()
    clock = Clock()
    controller, _ = make_controller(viewer, clock=clock)
    controller.activate(viewer)
    viewer.pause_mode = "silent"
    controller.set_paused(viewer, True)
    clock.now = 9.9
    assert controller.status(viewer).busy
    clock.now = 10
    result = controller.status(viewer)
    assert not result.busy and result.error and result.owned
    assert controller.status(viewer).error == result.error
    viewer.pause_mode = "normal"
    result = controller.set_paused(viewer, True)
    assert result.paused and not result.error and not result.busy
    assert not controller.status(viewer).error


def test_restore_timeout_preserves_recovery_for_retry_without_reenabling():
    viewer = Viewer(paused=True)
    clock = Clock()
    controller, _ = make_controller(viewer, clock=clock)
    controller.activate(viewer)
    viewer.pause_mode = "silent"
    controller.deactivate(viewer)
    clock.now = 10
    result = controller.status(viewer)
    assert result.state == "off" and result.owned and result.error and not result.busy
    assert controller.activate(viewer).error and viewer.current == "Karma CPU"
    viewer.pause_mode = "normal"
    result = controller.toggle(viewer)
    assert not result.owned and not result.error and viewer.paused


@pytest.mark.parametrize("restoring", [False, True])
def test_external_switch_cancels_deferred_command_and_recovery(restoring):
    viewer = Viewer(paused=restoring)
    controller, _ = make_controller(viewer)
    controller.activate(viewer)
    viewer.pause_mode = "silent"
    if restoring:
        controller.deactivate(viewer)
    else:
        controller.set_paused(viewer, True)
    viewer.current = "Karma XPU"
    before = list(viewer.calls)
    result = controller.status(viewer)
    assert not result.busy and not result.owned and not result.error
    assert not controller.deactivate(viewer).error
    assert viewer.calls == before


def test_independent_panes_can_have_different_pending_commands():
    first, second = Viewer("same"), Viewer("same", "HoudiniRT", True)
    first.pause_mode = second.pause_mode = "silent"
    controller, _ = make_controller(first, second)
    controller.activate(first)
    assert controller.set_paused(first, True).pending == "pause"
    assert controller.set_paused(second, False).pending == "resume"
    first.paused = True
    assert not controller.status(first).busy
    assert controller.status(second).pending == "resume"
    assert controller.status(first).owned and not controller.status(second).owned


def test_deferred_readback_failure_is_bounded_and_preserves_recovery():
    viewer = Viewer()
    clock = Clock()
    controller, _ = make_controller(viewer, clock=clock)
    controller.activate(viewer)
    viewer.pause_mode = "silent"
    controller.set_paused(viewer, True)
    viewer.closed = True
    result = controller.status(viewer)
    assert result.state == "error" and result.owned and result.busy
    clock.now = 10
    result = controller.status(viewer)
    assert result.state == "error" and result.owned and not result.busy
    viewer.closed = False
    viewer.pause_mode = "normal"
    assert not controller.deactivate(viewer).owned


def test_success_at_deadline_is_accepted_before_timeout():
    viewer = Viewer(renderer="HoudiniRT")
    viewer.pause_mode = "silent"
    clock = Clock()
    controller, _ = make_controller(viewer, clock=clock)
    controller.set_paused(viewer, True)
    clock.now = 10
    viewer.paused = True
    result = controller.status(viewer)
    assert result.paused and not result.busy and not result.error
