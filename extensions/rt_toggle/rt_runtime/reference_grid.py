"""Session-only reference-plane visibility ownership for Houdini 22.0.429.

No renderer selection, camera, grid geometry or saved preference is changed here.
The guard is applied once; a subsequently visible plane is an artist override.
"""
from dataclasses import dataclass, field
import threading


@dataclass
class _Lease:
    viewer: object
    plane: object
    hidden: bool = False
    releasing: bool = False
    viewports: list = field(default_factory=list)
    phase: str = ""
    posted: object = None
    epoch: int = 0
    error: str = ""


class ReferenceGridGuard:
    def __init__(self, hou_getter, same_pane, renderer_names, clock, report_error, departed, clear_error):
        self._hou = hou_getter
        self._same = same_pane
        self._renderers = renderer_names
        self._clock = clock
        self._report = report_error
        self._departed = departed
        self._clear_error = clear_error
        self._leases = []
        self._callback = self._poll
        self._registered = False
        self._last_poll = float('-inf')
        self._observer_owner = None

    def _find(self, viewer):
        return next((r for r in self._leases if self._same(r.viewer, viewer)), None)

    def _supported(self):
        try:
            return tuple(self._hou().applicationVersion()) == (22, 0, 429)
        except Exception:
            return False

    def _deleted(self, exc):
        kind = getattr(self._hou(), 'ObjectWasDeleted', None)
        return isinstance(kind, type) and isinstance(exc, kind)

    def _message(self, exc):
        return 'Reference grid visibility: ' + (str(exc) or type(exc).__name__)

    def _start_observer(self, viewer):
        # Query actual registration, including after an earlier partial failure.
        ui = self._hou().ui
        self._registered = self._callback in ui.eventLoopCallbacks()
        if self._registered:
            return
        self._observer_owner = viewer
        # Registration is attempted before any display mutation. If HOM raises
        # after registering, removal is still attempted so no orphan remains.
        try:
            ui.addEventLoopCallback(self._callback)
            self._registered = self._callback in ui.eventLoopCallbacks()
            if not self._registered:
                raise RuntimeError('Houdini did not register the reference grid observer.')
        except Exception:
            self._registered = True  # uncertain until removal/readback below
            self._stop_observer()
            raise

    def _stop_observer(self):
        if self._leases or not self._registered:
            return
        try:
            ui = self._hou().ui
            if self._callback in ui.eventLoopCallbacks():
                try:
                    ui.removeEventLoopCallback(self._callback)
                except Exception:
                    if self._callback in ui.eventLoopCallbacks():
                        raise
        except Exception as exc:
            self._report(self._observer_owner, self._message(exc))
            return  # Keep the callback identity and retry on its next tick.
        self._registered = False
        self._observer_owner = None

    def _drop(self, lease):
        self._clear_fault(lease)
        lease.epoch += 1
        if lease.posted is not None:
            try:
                self._hou().ui.removePostedEventCallback(lease.posted)
            except Exception as exc:
                self._report(lease.viewer, self._message(exc))
            lease.posted = None
        self._leases = [r for r in self._leases if r is not lease]
        self._stop_observer()

    @staticmethod
    def _capture(viewer):
        return [(v, bool(v.settings().displayOrthoGrid())) for v in viewer.viewports()]

    def _post(self, lease):
        if lease.posted is not None or not lease.phase:
            return
        lease.epoch += 1
        epoch = lease.epoch

        def finish():
            if self._find(lease.viewer) is not lease or lease.epoch != epoch:
                return
            lease.posted = None
            self._complete(lease)

        lease.posted = finish
        try:
            # H22 broadcasts the parent's visibility into every split only on
            # redraw. Preserve the individual flags after that native update.
            self._hou().ui.postRedrawFence()
            self._hou().ui.postEventCallback(finish)
        except Exception:
            lease.epoch += 1  # Any partially queued callback is now harmless.
            lease.posted = None
            raise

    def _complete(self, lease):
        try:
            self._hou()  # Enforce the same main-thread boundary as the controller.
            applied = lease.phase == "restore"
            if bool(lease.plane.isVisible()) != applied:
                self._drop(lease)  # Artist changed the parent; honor its broadcast.
                return
            remaining, error = [], None
            for viewport, original in lease.viewports:
                try:
                    settings = viewport.settings()
                    current = bool(settings.displayOrthoGrid())
                    if current == original or current != applied:
                        continue  # Already right or a subsequent artist change.
                    setter_error = None
                    try:
                        settings.setDisplayOrthoGrid(original)
                    except Exception as exc:
                        setter_error = exc
                    if bool(settings.displayOrthoGrid()) != original:
                        raise setter_error or RuntimeError('Houdini did not restore a split grid. Retry Off.')
                except Exception as exc:
                    if not self._deleted(exc):
                        remaining.append((viewport, original))
                        error = exc
            lease.viewports = remaining
            if error is not None:
                raise error
            self._clear_fault(lease)
            if applied:
                self._drop(lease)
            else:
                lease.phase = ""
                self.observe(lease.viewer)
        except Exception as exc:
            self._handle_error(lease, exc)

    def _handle_error(self, lease, exc):
        if self._deleted(exc):
            self._departed(lease.viewer, "", True)
            self._drop(lease)
        else:
            lease.error = self._message(exc)
            self._report(lease.viewer, lease.error)

    def _clear_fault(self, lease):
        if lease.error:
            self._clear_error(lease.viewer, lease.error)
            lease.error = ""

    def acquire(self, viewer):
        if not self._supported() or self._find(viewer):
            return
        plane = viewer.referencePlane()
        if not bool(plane.isVisible()):
            return  # Nothing changed means nothing to restore or observe.
        viewports = self._capture(viewer)
        self._start_observer(viewer)
        lease = _Lease(viewer, plane, viewports=viewports)
        self._leases.append(lease)
        self._transition(lease, False)

    def _transition(self, lease, visible):
        lease.phase = "restore" if visible else "hide"
        error = None
        try:
            lease.plane.setIsVisible(visible)
        except Exception as exc:
            error = exc
        try:
            actual = bool(lease.plane.isVisible())
            read_confirmed = True
        except Exception as exc:
            actual, error = visible, error or exc  # preserve uncertain side effects
            read_confirmed = False
        lease.hidden = not actual
        if actual != visible:
            lease.phase = ""
            if not visible:
                self._drop(lease)
            raise error or RuntimeError('Houdini did not change reference grid visibility. Retry Off.')
        self._post(lease)
        if error is not None and not (visible and read_confirmed):
            raise RuntimeError(self._message(error)) from error

    def hidden(self, viewer):
        lease = self._find(viewer)
        return bool(lease and lease.hidden and not lease.releasing)

    def cleanup_pending(self, viewer):
        lease = self._find(viewer)
        return bool(lease and lease.releasing)

    def pending(self, viewer):
        lease = self._find(viewer)
        if lease and lease.posted is not None:
            return "restore" if lease.releasing else "enable"
        return ""

    def retry(self, viewer):
        lease = self._find(viewer)
        if lease and lease.phase and lease.posted is None:
            try:
                self._post(lease)
            except Exception as exc:
                self._handle_error(lease, exc)

    def needs_retry(self, viewer):
        lease = self._find(viewer)
        return bool(lease and lease.phase and lease.posted is None)

    def observe(self, viewer, renderer=None):
        """Reconcile an existing lease; never acquire or reapply one here."""
        lease = self._find(viewer)
        if lease is None:
            self._stop_observer()
            return
        try:
            if renderer is None:
                try:
                    renderer = (viewer.currentHydraRenderer()
                                if viewer.isViewingSceneGraph() else "")
                except Exception as exc:
                    if not lease.releasing or self._deleted(exc):
                        raise
            if renderer is not None and (renderer not in self._renderers or lease.releasing):
                lease.releasing = True
                self._departed(viewer, renderer, False)
            if lease.phase:
                return  # Its fence callback owns this transition's readback.
            if bool(lease.plane.isVisible()):
                # Artist explicitly showed the parent. Its native broadcast
                # is their choice too; do not reinstate an earlier split vector.
                self._drop(lease)
                return
            lease.hidden = True
            if not lease.releasing:
                return
            # Capture *now*, preserving per-split artist edits made while active.
            lease.viewports = self._capture(viewer)
            self._transition(lease, True)
        except Exception as exc:
            self._handle_error(lease, exc)

    def _poll(self):
        # Houdini owns this callback and invokes it on its application thread.
        if threading.current_thread() is not threading.main_thread():
            return
        now = self._clock()
        if now - self._last_poll < 0.25:
            return
        self._last_poll = now
        for lease in tuple(self._leases):
            self.observe(lease.viewer)
            self.retry(lease.viewer)
        self._stop_observer()
