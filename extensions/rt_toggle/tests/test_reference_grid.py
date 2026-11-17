"""Scoped H22.0.429 display ownership; fake HOM only, no Qt or native render."""
from types import SimpleNamespace

import pytest

from rt_runtime.render_session import RenderController
from test_render_session import Clock, UI, Viewer


class Deleted(RuntimeError):
    pass


class Plane:
    def __init__(self, viewer, visible=True):
        self.viewer, self.visible = viewer, visible
        self.calls = []
        self.modes = {False: "normal", True: "normal"}
        self.read_failures = 0
        self.fail_read_after_set = False
        self.spacing, self.orientation = 1.0, "XZ"

    def isVisible(self):
        self.viewer._valid()
        if self.read_failures:
            self.read_failures -= 1
            raise RuntimeError("Grid read failed")
        return self.visible

    def setIsVisible(self, visible):
        self.viewer._valid()
        self.calls.append(visible)
        mode = self.modes[visible]
        if mode == "raise_before":
            raise RuntimeError("Grid setter failed before change")
        if mode != "silent":
            self.visible = visible
            if self.viewer.ui.deferred:
                self.viewer.ui.broadcasts.append((self.viewer, visible))
            else:
                for viewport in self.viewer.splits:
                    viewport.grid = visible
        if self.fail_read_after_set:
            self.read_failures += 1
        if mode == "raise_after":
            raise RuntimeError("Grid setter failed after change")


class GridViewer(Viewer):
    def __init__(self, *args, visible=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.plane = Plane(self, visible)
        self.renderer_read_error = False
        self.splits = [GridViewport()]
        self.ui = None

    def _valid(self):
        if self.closed:
            raise Deleted("Pane has been deleted")

    def referencePlane(self):
        self._valid()
        return self.plane

    def viewports(self):
        self._valid()
        return tuple(self.splits)

    def currentHydraRenderer(self):
        if self.renderer_read_error:
            raise RuntimeError("Renderer read failed")
        return super().currentHydraRenderer()


class GridViewport:
    def __init__(self, grid=True):
        self.grid = grid
        self.closed = False
        self.mode = "normal"
        self.calls = []

    def settings(self):
        if self.closed:
            raise Deleted("Viewport was deleted")
        return self

    def displayOrthoGrid(self):
        return self.grid

    def setDisplayOrthoGrid(self, visible):
        self.calls.append(visible)
        if self.mode == "raise_before":
            raise RuntimeError("Split grid setter failed")
        if self.mode != "silent":
            self.grid = visible
        if self.mode == "raise_after":
            raise RuntimeError("Split grid setter partly completed")


class GridUI(UI):
    def __init__(self, *panes):
        super().__init__(*panes)
        self.callbacks = []
        self.add_mode = self.remove_mode = "normal"
        self.deferred = False
        self.posted = []
        self.broadcasts = []
        self.post_mode = "normal"
        self.fences = 0

    def eventLoopCallbacks(self):
        return tuple(self.callbacks)

    def addEventLoopCallback(self, callback):
        if self.add_mode == "raise_before":
            raise RuntimeError("Register failed before change")
        if self.add_mode != "silent":
            self.callbacks.append(callback)
        if self.add_mode == "raise_after":
            raise RuntimeError("Register failed after change")

    def removeEventLoopCallback(self, callback):
        if self.remove_mode == "raise_before" or callback not in self.callbacks:
            raise RuntimeError("Unregister failed")
        self.callbacks.remove(callback)
        if self.remove_mode == "raise_after":
            raise RuntimeError("Unregister failed after change")

    def postRedrawFence(self):
        self.fences += 1

    def postEventCallback(self, callback):
        if self.post_mode == "raise_before":
            raise RuntimeError("Post failed")
        if self.deferred:
            self.posted.append(callback)
        else:
            callback()
        if self.post_mode == "raise_after":
            raise RuntimeError("Post partly completed")

    def removePostedEventCallback(self, callback):
        if callback in self.posted:
            self.posted.remove(callback)

    def redraw(self):
        broadcasts, self.broadcasts = self.broadcasts, []
        for viewer, visible in broadcasts:
            for viewport in viewer.splits:
                viewport.grid = visible

    def flush_one(self):
        self.redraw()
        if self.posted:
            self.posted.pop(0)()


def setup(*viewers, version=(22, 0, 429), deferred=False):
    ui, clock = GridUI(*viewers), Clock()
    ui.deferred = deferred
    for viewer in viewers:
        viewer.ui = ui
    hou = SimpleNamespace(ui=ui, applicationVersion=lambda: version,
                          ObjectWasDeleted=Deleted,
                          paneTabType=SimpleNamespace(SceneViewer="SceneViewer"))
    return RenderController(hou, clock=clock), ui, clock


def tick(ui, clock):
    clock.now += 0.3
    for callback in tuple(ui.callbacks):
        callback()


def test_owned_enable_hides_once_and_off_restores_only_visibility():
    viewer = GridViewer()
    controller, ui, _ = setup(viewer)
    active = controller.activate(viewer)
    assert active.active and active.owned and not active.error
    assert "Reference grid temporarily hidden" in active.message
    assert viewer.plane.calls == [False] and len(ui.callbacks) == 1
    viewer.plane.spacing, viewer.plane.orientation = 0.1, "YZ"
    assert controller.activate(viewer).active
    controller.status(viewer)
    assert viewer.plane.calls == [False]
    off = controller.deactivate(viewer)
    assert off.state == "off" and not off.can_restore and not off.error
    assert viewer.plane.calls == [False, True]
    assert (viewer.plane.spacing, viewer.plane.orientation) == (0.1, "YZ")
    assert not ui.callbacks


@pytest.mark.parametrize("version", [(22, 0, 428), (22, 0, 430), (21, 0, 429)])
def test_guard_is_exact_build_scoped(version):
    viewer = GridViewer()
    controller, ui, _ = setup(viewer, version=version)
    assert controller.activate(viewer).active
    assert not viewer.plane.calls and not ui.callbacks


@pytest.mark.parametrize("original_hidden,already_active", [(True, False), (False, True)])
def test_no_display_ownership_for_unchanged_or_unowned_renderer(original_hidden, already_active):
    viewer = GridViewer(visible=not original_hidden,
                        renderer="HoudiniRT" if already_active else "Karma CPU")
    controller, ui, _ = setup(viewer)
    assert controller.activate(viewer).active
    assert not viewer.plane.calls and not ui.callbacks


def test_artist_visible_override_is_never_reapplied():
    viewer = GridViewer()
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    viewer.plane.visible = True  # Native viewer menu, not our controls.
    tick(ui, clock)
    assert not ui.callbacks
    viewer.plane.visible = False  # Artist later chooses hidden themselves.
    controller.activate(viewer)
    controller.deactivate(viewer)
    assert viewer.plane.calls == [False] and not viewer.plane.visible


def test_external_takeover_without_panel_restores_and_invalidates_renderer_ownership():
    viewer = GridViewer()
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    calls = list(viewer.calls)
    viewer.current = "Karma XPU"
    tick(ui, clock)
    assert viewer.plane.visible and not ui.callbacks
    assert viewer.calls == calls
    viewer.current = "HoudiniRT"  # Another tool enables it before any status poll.
    result = controller.status(viewer)
    assert result.active and not result.owned
    assert controller.deactivate(viewer).error
    assert viewer.calls == calls


def test_hidden_desktop_pane_is_not_deleted_and_rename_does_not_retarget():
    viewer, unrelated = GridViewer("same"), GridViewer("same")
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    ui.panes = [unrelated]
    viewer.label = "renamed"
    tick(ui, clock)
    assert not viewer.plane.visible and len(ui.callbacks) == 1
    viewer.current = "Karma XPU"
    tick(ui, clock)
    assert viewer.plane.visible and not unrelated.plane.calls


@pytest.mark.parametrize("deleted", [False, True])
def test_context_departure_and_deletion_retire_lease_and_ownership(deleted):
    viewer = GridViewer()
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    if deleted:
        viewer.closed = True
    else:
        viewer.lop = False
    tick(ui, clock)
    assert not ui.callbacks and not controller._ownership and not controller._grid._leases
    assert viewer.plane.calls == ([False] if deleted else [False, True])


@pytest.mark.parametrize("mode", ["raise_before", "silent"])
def test_renderer_activation_failure_does_not_hide_grid(mode):
    viewer = GridViewer()
    viewer.select_mode = mode
    controller, ui, _ = setup(viewer)
    assert controller.activate(viewer).error
    assert not viewer.plane.calls and not ui.callbacks


@pytest.mark.parametrize("mode", ["raise_before", "silent", "raise_after"])
def test_hide_setter_failure_reports_actual_state_and_preserves_needed_cleanup(mode):
    viewer = GridViewer()
    viewer.plane.modes[False] = mode
    controller, ui, _ = setup(viewer)
    result = controller.activate(viewer)
    assert result.active and result.owned and result.error
    assert bool(ui.callbacks) == (mode == "raise_after")
    assert viewer.plane.visible == (mode != "raise_after")
    off = controller.deactivate(viewer)
    assert off.state == "off" and not off.can_restore and viewer.plane.visible
    assert not ui.callbacks


def test_hide_readback_failure_retains_cleanup_until_observable():
    viewer = GridViewer()
    viewer.plane.fail_read_after_set = True
    controller, ui, _ = setup(viewer)
    result = controller.activate(viewer)
    assert result.error and result.owned and not viewer.plane.visible
    assert ui.callbacks
    viewer.plane.fail_read_after_set = False
    assert not controller.deactivate(viewer).can_restore
    assert viewer.plane.visible and not ui.callbacks


def test_failed_renderer_off_keeps_guard_until_renderer_actually_changes():
    viewer = GridViewer()
    controller, ui, _ = setup(viewer)
    controller.activate(viewer)
    viewer.select_mode = "silent"
    failed = controller.deactivate(viewer)
    assert failed.error and failed.owned and failed.active
    assert not viewer.plane.visible and len(ui.callbacks) == 1
    viewer.select_mode = "normal"
    assert controller.deactivate(viewer).state == "off"
    assert viewer.plane.visible and not ui.callbacks


@pytest.mark.parametrize("mode", ["raise_before", "silent"])
def test_restore_failure_is_display_only_and_toggle_retry_does_not_reenable(mode):
    viewer = GridViewer()
    controller, ui, _ = setup(viewer)
    controller.activate(viewer)
    viewer.plane.modes[True] = mode
    failed = controller.deactivate(viewer)
    assert failed.state == "off" and not failed.owned
    assert failed.cleanup_pending and failed.can_restore and failed.error
    calls = list(viewer.calls)
    viewer.plane.modes[True] = "normal"
    retried = controller.toggle(viewer)
    assert retried.state == "off" and not retried.can_restore
    assert viewer.calls == calls and viewer.plane.visible and not ui.callbacks


def test_restore_partial_exception_with_positive_readback_is_success():
    viewer = GridViewer()
    controller, ui, _ = setup(viewer)
    controller.activate(viewer)
    viewer.plane.modes[True] = "raise_after"
    result = controller.deactivate(viewer)
    assert result.state == "off" and not result.error and not result.can_restore
    assert viewer.plane.visible and not ui.callbacks


def test_known_cleanup_survives_unknown_renderer_readback():
    viewer = GridViewer()
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    viewer.plane.modes[True] = "silent"
    controller.deactivate(viewer)
    viewer.renderer_read_error = True
    assert controller.status(viewer).cleanup_pending
    viewer.plane.modes[True] = "normal"
    tick(ui, clock)
    assert viewer.plane.visible and not ui.callbacks
    assert not controller._ownership and not controller._grid._leases


@pytest.mark.parametrize("mode", ["raise_before", "raise_after", "silent"])
def test_failed_registration_never_hides_and_later_activation_registers(mode):
    viewer = GridViewer()
    controller, ui, _ = setup(viewer)
    ui.add_mode = mode
    failed = controller.activate(viewer)
    assert failed.error and failed.active and not viewer.plane.calls
    assert not ui.callbacks
    controller.deactivate(viewer)
    ui.add_mode = "normal"
    assert not controller.activate(viewer).error
    assert viewer.plane.calls == [False] and len(ui.callbacks) == 1


def test_unregister_failure_retains_callback_identity_then_retries_without_display_changes():
    viewer = GridViewer()
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    ui.remove_mode = "raise_before"
    result = controller.deactivate(viewer)
    assert result.state == "off" and result.error and not result.can_restore
    assert viewer.plane.visible and len(ui.callbacks) == 1
    calls = list(viewer.plane.calls)
    ui.remove_mode = "normal"
    tick(ui, clock)
    assert not ui.callbacks and viewer.plane.calls == calls


def test_remove_raises_after_actual_success_does_not_leave_false_registration():
    viewer = GridViewer()
    controller, ui, _ = setup(viewer)
    controller.activate(viewer)
    ui.remove_mode = "raise_after"
    assert not controller.deactivate(viewer).error
    assert not ui.callbacks and not controller._grid._registered


def test_prior_paused_restore_can_settle_after_display_lease_finishes():
    viewer = GridViewer(paused=True)
    controller, ui, clock = setup(viewer)
    assert not controller.activate(viewer).paused
    viewer.pause_mode = "silent"
    result = controller.deactivate(viewer)
    assert result.pending == "restore" and result.owned
    assert viewer.plane.visible and not ui.callbacks
    viewer.paused = True
    settled = controller.status(viewer)
    assert settled.state == "off" and not settled.can_restore and not settled.busy


def test_observer_does_not_lose_owned_pause_restore_when_grid_restore_retries():
    viewer = GridViewer(paused=True)
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    viewer.pause_mode = "silent"
    viewer.plane.modes[True] = "silent"
    failed = controller.deactivate(viewer)
    assert failed.owned and failed.cleanup_pending and failed.pending == "restore"
    tick(ui, clock)
    assert controller._record(viewer) and controller._command(viewer)
    viewer.paused = True
    viewer.plane.modes[True] = "normal"
    result = controller.status(viewer)
    assert not result.can_restore and not result.busy and not ui.callbacks


def test_two_viewers_have_independent_visibility_and_one_observer():
    first, second = GridViewer("one"), GridViewer("two")
    controller, ui, clock = setup(first, second)
    controller.activate(first)
    controller.activate(second)
    assert len(ui.callbacks) == 1
    first.current = "Karma XPU"
    tick(ui, clock)
    assert first.plane.visible and not second.plane.visible and len(ui.callbacks) == 1
    controller.deactivate(second)
    assert second.plane.visible and not ui.callbacks


def test_transient_renderer_query_failure_does_not_discard_live_display_recovery():
    viewer = GridViewer()
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    viewer.renderer_read_error = True
    tick(ui, clock)
    assert not viewer.plane.visible and len(ui.callbacks) == 1
    assert controller.status(viewer).owned
    viewer.renderer_read_error = False
    viewer.current = "Karma XPU"
    tick(ui, clock)
    assert viewer.plane.visible and not ui.callbacks and not controller._ownership


def test_deletion_then_new_pane_with_same_name_cannot_inherit_lease():
    viewer = GridViewer("same")
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    viewer.closed = True
    replacement = GridViewer("same")
    ui.panes = [replacement]
    tick(ui, clock)
    assert not controller.status(replacement).can_restore
    assert replacement.plane.visible and not replacement.plane.calls and not ui.callbacks


def test_observer_and_controller_never_access_hom_on_worker_thread():
    from concurrent.futures import ThreadPoolExecutor
    viewer = GridViewer()
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    viewer.current = "Karma XPU"
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(controller.status, viewer).result().error
        pool.submit(tick, ui, clock).result()
    assert not viewer.plane.visible and len(ui.callbacks) == 1
    tick(ui, clock)
    assert viewer.plane.visible and not ui.callbacks


def test_module_reexecution_keeps_existing_singleton_and_live_callback():
    import runpy
    import rt_runtime.render_session as session
    viewer = GridViewer()
    controller, ui, clock = setup(viewer)
    controller.activate(viewer)
    namespace = runpy.run_path(session.__file__, init_globals={"_CONTROLLER": controller},
                              run_name="rt_runtime.render_session")
    assert namespace["get_render_controller"]() is controller
    viewer.current = "Karma XPU"
    tick(ui, clock)
    assert viewer.plane.visible and not ui.callbacks


def mixed_viewer():
    viewer = GridViewer()
    viewer.splits = [GridViewport(v) for v in (True, False, True, True)]
    return viewer


def grids(viewer):
    return tuple(v.grid for v in viewer.splits)


def test_deferred_hide_preserves_mixed_split_vector_after_native_broadcast():
    viewer = mixed_viewer()
    controller, ui, _ = setup(viewer, deferred=True)
    active = controller.activate(viewer)
    assert active.active and active.busy and active.pending == "enable"
    assert not viewer.plane.visible and grids(viewer) == (True, False, True, True)
    ui.redraw()
    assert grids(viewer) == (False, False, False, False)
    ui.flush_one()
    assert grids(viewer) == (True, False, True, True)
    assert not controller.status(viewer).busy and len(ui.callbacks) == 1


def test_deferred_off_preserves_current_artist_vector_and_stays_pending_until_readback():
    viewer = mixed_viewer()
    controller, ui, _ = setup(viewer, deferred=True)
    controller.activate(viewer)
    ui.flush_one()
    viewer.splits[0].grid = False  # Artist edit during the active session.
    off = controller.deactivate(viewer)
    assert off.state == "off" and not off.owned
    assert off.cleanup_pending and off.pending == "restore" and off.busy
    ui.redraw()
    assert grids(viewer) == (True, True, True, True)
    ui.flush_one()
    result = controller.status(viewer)
    assert grids(viewer) == (False, False, True, True)
    assert not result.can_restore and not result.busy and not ui.callbacks


def test_external_takeover_during_hide_serializes_both_preservation_phases():
    viewer = mixed_viewer()
    controller, ui, clock = setup(viewer, deferred=True)
    controller.activate(viewer)
    viewer.current = "Karma XPU"
    tick(ui, clock)
    assert controller.status(viewer).pending == "restore"
    assert not controller._ownership and not viewer.plane.visible
    ui.flush_one()  # Complete hide preservation, then begin parent restoration.
    assert viewer.plane.visible and len(ui.posted) == 1
    assert grids(viewer) == (True, False, True, True)
    ui.flush_one()
    assert grids(viewer) == (True, False, True, True)
    assert not ui.callbacks and not controller.status(viewer).can_restore
    assert viewer.calls == [("renderer", "HoudiniRT")]


def test_manual_parent_show_relinquishes_and_keeps_artist_native_broadcast():
    viewer = mixed_viewer()
    controller, ui, clock = setup(viewer, deferred=True)
    controller.activate(viewer)
    ui.flush_one()
    viewer.plane.setIsVisible(True)
    ui.redraw()
    tick(ui, clock)
    assert grids(viewer) == (True, True, True, True) and not ui.callbacks
    controller.deactivate(viewer)
    assert grids(viewer) == (True, True, True, True)


def test_artist_split_override_after_broadcast_is_not_overwritten():
    viewer = mixed_viewer()
    controller, ui, _ = setup(viewer, deferred=True)
    controller.activate(viewer)
    ui.redraw()
    viewer.splits[1].grid = True  # Distinct from the broadcast False.
    ui.flush_one()
    assert grids(viewer) == (True, True, True, True)


def test_rapid_repeat_actions_do_not_issue_parent_or_renderer_changes_while_pending():
    viewer = mixed_viewer()
    controller, ui, _ = setup(viewer, deferred=True)
    controller.activate(viewer)
    assert controller.deactivate(viewer).busy
    assert controller.toggle(viewer).busy
    assert controller.toggle_pause(viewer).busy
    assert viewer.plane.calls == [False]
    assert viewer.calls == [("renderer", "HoudiniRT")] and len(ui.posted) == 1


@pytest.mark.parametrize("mode", ["raise_before", "silent"])
def test_split_restore_failure_retains_cleanup_and_retry_never_reenables(mode):
    viewer = mixed_viewer()
    controller, ui, _ = setup(viewer, deferred=True)
    controller.activate(viewer)
    ui.flush_one()
    viewer.splits[1].mode = mode
    controller.deactivate(viewer)
    ui.flush_one()
    failed = controller.status(viewer)
    assert failed.state == "off" and failed.error and failed.cleanup_pending
    assert not failed.busy and not failed.owned
    calls = list(viewer.calls)
    viewer.splits[1].mode = "normal"
    retry = controller.toggle(viewer)
    assert retry.state == "off" and retry.busy and retry.cleanup_pending
    ui.flush_one()
    assert grids(viewer) == (True, False, True, True)
    assert viewer.calls == calls and not ui.callbacks
    assert not controller.status(viewer).error


def test_partial_split_setter_success_uses_readback():
    viewer = mixed_viewer()
    viewer.splits[0].mode = "raise_after"
    controller, ui, _ = setup(viewer, deferred=True)
    controller.activate(viewer)
    ui.flush_one()
    result = controller.status(viewer)
    assert not result.error and not result.busy
    assert grids(viewer) == (True, False, True, True)


@pytest.mark.parametrize("mode", ["raise_before", "raise_after"])
def test_post_failure_retries_with_stale_callback_invalidated(mode):
    viewer = mixed_viewer()
    controller, ui, clock = setup(viewer, deferred=True)
    ui.post_mode = mode
    failed = controller.activate(viewer)
    assert failed.error and failed.owned
    stale = list(ui.posted)
    ui.post_mode = "normal"
    tick(ui, clock)
    assert controller.status(viewer).busy
    while ui.posted:
        ui.flush_one()
    assert grids(viewer) == (True, False, True, True)
    calls = [list(v.calls) for v in viewer.splits]
    for callback in stale:
        callback()
    assert [v.calls for v in viewer.splits] == calls


def test_closed_split_and_new_same_named_replacement_are_not_old_restore_targets():
    viewer = mixed_viewer()
    controller, ui, _ = setup(viewer, deferred=True)
    controller.activate(viewer)
    old = viewer.splits[0]
    old.closed = True
    replacement = GridViewport(False)
    viewer.splits[0] = replacement
    ui.flush_one()
    assert replacement.grid is False and not replacement.calls and not old.calls
    assert not controller.status(viewer).busy


def test_manual_parent_show_during_hide_invalidates_pending_preservation():
    viewer = mixed_viewer()
    controller, ui, _ = setup(viewer, deferred=True)
    controller.activate(viewer)
    viewer.plane.setIsVisible(True)
    ui.flush_one()
    assert grids(viewer) == (True, True, True, True)
    assert not controller.status(viewer).busy and not ui.callbacks


def test_pane_deletion_while_posted_discards_all_recovery_without_retargeting():
    viewer = mixed_viewer()
    controller, ui, clock = setup(viewer, deferred=True)
    controller.activate(viewer)
    stale = ui.posted[0]
    viewer.closed = True
    tick(ui, clock)
    assert not ui.posted and not ui.callbacks and not controller._ownership
    stale()
    assert not controller._grid._leases


def test_permanent_hide_split_failure_does_not_block_native_renderer_off():
    viewer = mixed_viewer()
    viewer.splits[0].mode = "silent"
    controller, ui, _ = setup(viewer, deferred=True)
    controller.activate(viewer)
    ui.flush_one()
    assert controller.status(viewer).error
    off = controller.deactivate(viewer)
    assert viewer.current == "Karma CPU" and off.state == "off"
    assert off.cleanup_pending and not off.owned
    ui.flush_one()  # Persistent split failure, but renderer remains Off.
    failed = controller.status(viewer)
    assert failed.state == "off" and failed.cleanup_pending and failed.error
    viewer.splits[0].mode = "normal"
    controller.deactivate(viewer)
    ui.flush_one()  # Hide preservation now succeeds, schedules parent restore.
    ui.flush_one()  # Parent restore preserves the original mixed vector.
    assert grids(viewer) == (True, False, True, True)
    result = controller.status(viewer)
    assert result.state == "off" and not result.can_restore and not result.error


def test_successful_split_retry_does_not_clear_unrelated_renderer_error():
    viewer = mixed_viewer()
    controller, ui, clock = setup(viewer, deferred=True)
    controller.activate(viewer)
    ui.flush_one()
    viewer.splits[1].mode = "silent"
    controller.deactivate(viewer)
    ui.flush_one()
    assert controller.status(viewer).error
    controller._set_error(viewer, "Unrelated renderer failure")
    viewer.splits[1].mode = "normal"
    tick(ui, clock)
    ui.flush_one()
    assert controller.status(viewer).error == "Unrelated renderer failure"
    assert not ui.callbacks


def test_external_hrt_reselection_during_off_cleanup_discards_old_pause_ownership():
    viewer = mixed_viewer()
    viewer.paused = True
    controller, ui, clock = setup(viewer, deferred=True)
    controller.activate(viewer)
    ui.flush_one()
    viewer.pause_mode = "silent"
    assert controller.deactivate(viewer).pending == "restore"
    viewer.current = "HoudiniRT"  # Another tool's fresh session while our Off settles.
    tick(ui, clock)
    assert not controller._ownership and not controller._pending
    ui.flush_one()
    result = controller.status(viewer)
    assert result.active and not result.owned and not result.can_restore
