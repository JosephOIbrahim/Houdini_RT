"""Observed, per-viewer HoudiniRT controls. No sidecar or persistent scene state.

All public controller operations run on Houdini's main application thread.
``state == 'active'`` means the native LOP renderer is selected and unpaused;
the ``active`` property includes paused selection. HOM does not expose
first-frame, convergence, or GPU-error evidence through these APIs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import threading
import time
from typing import Any

from .reference_grid import ReferenceGridGuard

RENDERER_NAMES = ("HoudiniRT", "HdRTRendererPlugin")


class RenderSessionError(RuntimeError):
    """A viewer cannot be safely selected or controlled."""


@dataclass(frozen=True)
class RenderStatus:
    state: str
    viewer_name: str = ""
    renderer: str = ""
    previous_renderer: str = ""
    owned: bool = False
    message: str = ""
    error: str = ""
    pending: str = ""
    cleanup_pending: bool = False

    @property
    def busy(self) -> bool:
        return bool(self.pending)

    @property
    def active(self) -> bool:
        """True when HoudiniRT is selected, including while paused."""
        return self.renderer in RENDERER_NAMES

    @property
    def paused(self) -> bool:
        return self.state == "paused"

    @property
    def can_restore(self) -> bool:
        return self.owned or self.cleanup_pending


@dataclass
class _Ownership:
    viewer: Any
    previous_renderer: str
    previous_paused: bool
    renderer: str
    restoring: bool = False


@dataclass
class _Pending:
    viewer: Any
    renderer: str
    paused: bool
    operation: str
    deadline: float


class RenderController:
    """Session-only ownership shared by all controls, with injectable HOM."""

    def __init__(self, hou_module=None, *, clock=None, command_timeout=10.0):
        self._hou_module = hou_module
        self._ownership: list[_Ownership] = []
        self._pending: list[_Pending] = []
        self._errors: list[tuple[Any, str]] = []
        self._clock = clock or time.monotonic
        self._command_timeout = max(0.1, float(command_timeout))
        self._grid = ReferenceGridGuard(self._hou, self._same_pane, RENDERER_NAMES,
                                        self._clock, self._set_error, self._grid_departed,
                                        self._clear_grid_error)

    @staticmethod
    def _main_thread():
        if threading.current_thread() is not threading.main_thread():
            raise RenderSessionError("Renderer controls must run on Houdini's main thread.")

    def _hou(self):
        self._main_thread()
        if self._hou_module is None:
            try:
                import hou
            except ImportError as exc:
                raise RenderSessionError("Open these controls inside Houdini.") from exc
            self._hou_module = hou
        return self._hou_module

    @staticmethod
    def _same_pane(first, second):
        # Native HOM wrappers may be recreated around the same underlying pane.
        # Names are deliberately excluded: a newly opened pane can reuse one.
        try:
            return first is second or bool(first == second)
        except Exception:
            return first is second

    def _record(self, viewer):
        return next((r for r in self._ownership if self._same_pane(r.viewer, viewer)), None)

    def _forget(self, record):
        self._ownership = [r for r in self._ownership if r is not record]

    def _command(self, viewer):
        return next((p for p in self._pending if self._same_pane(p.viewer, viewer)), None)

    def _drop_command(self, command):
        self._pending = [p for p in self._pending if p is not command]

    def _clear_error(self, viewer):
        self._errors = [(p, e) for p, e in self._errors if not self._same_pane(p, viewer)]

    def _set_error(self, viewer, error):
        self._clear_error(viewer)
        self._errors.append((viewer, error))

    def _error(self, viewer):
        return next((e for p, e in self._errors if self._same_pane(p, viewer)), "")

    def _clear_grid_error(self, viewer, error):
        if self._error(viewer) == error:
            self._clear_error(viewer)

    def _grid_departed(self, viewer, renderer, deleted):
        """The display observer can notice takeover even with no panel open."""
        record, command = self._record(viewer), self._command(viewer)
        if not deleted and record and record.restoring and renderer == record.previous_renderer:
            return  # Keep the pending restoration of the previous pause state.
        if command:
            self._drop_command(command)
        if record:
            self._forget(record)
        if record or command or deleted:
            self._clear_error(viewer)

    @staticmethod
    def _renderer_matches(actual, expected):
        return actual == expected or (actual in RENDERER_NAMES and expected in RENDERER_NAMES)

    def _request_pause(self, viewer, paused, operation):
        # HOM queues pause changes. Keep the requested state separate from the
        # native observation, and let later main-thread status polls settle it.
        command = _Pending(viewer, viewer.currentHydraRenderer(), paused,
                           operation, self._clock() + self._command_timeout)
        self._pending.append(command)
        try:
            if bool(viewer.isRendererPaused()) != paused:
                viewer.setRendererPaused(paused)
        except Exception:
            self._drop_command(command)
            raise
        return self._observe(viewer)

    def _is_lop_viewer(self, pane):
        try:
            return (pane.type() == self._hou().paneTabType.SceneViewer
                    and bool(pane.isViewingSceneGraph()))
        except Exception:
            return False

    def viewers(self) -> tuple:
        """All visible LOP Scene Viewers, including floating panes."""
        hou = self._hou()
        return tuple(p for p in hou.ui.paneTabs() if self._is_lop_viewer(p))

    def resolve_viewer(self, viewer=None, viewer_name=None):
        """Resolve explicit target, cursor LOP viewer, or the sole LOP viewer.

        Ambiguity is an error: a button never changes an arbitrary first pane.
        An explicit invalid/closed pane never falls back to another viewer.
        """
        hou = self._hou()
        if viewer is not None:
            if not self._is_lop_viewer(viewer):
                raise RenderSessionError("The target viewer is closed or is not viewing Solaris/LOPs.")
            return viewer
        candidates = self.viewers()
        if viewer_name:
            matches = [p for p in candidates if p.name() == viewer_name]
            if len(matches) == 1:
                return matches[0]
            raise RenderSessionError(f"Choose an available Solaris viewer; {viewer_name!r} is not unique or available.")
        cursor = hou.ui.paneTabUnderCursor()
        if cursor is not None and self._is_lop_viewer(cursor):
            return cursor
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise RenderSessionError("Open a Scene Viewer in Solaris/LOPs to use HoudiniRT.")
        raise RenderSessionError("Choose the target Solaris viewer; more than one is open.")

    def _observe(self, viewer):
        name = viewer.name()
        renderer = viewer.currentHydraRenderer()
        paused = bool(viewer.isRendererPaused())
        available = tuple(viewer.hydraRenderers())
        record = self._record(viewer)
        command = self._command(viewer)
        if command and not self._renderer_matches(renderer, command.renderer):
            # A native menu or another tool took over this pane. Never finish
            # our old command against the artist's new renderer selection.
            self._drop_command(command)
            command = None
            self._forget(record)
            self._clear_error(viewer)
            record = None
        if record and renderer not in RENDERER_NAMES:
            # While restoring, the native renderer can already be back but
            # restoring its prior pause state may still need another attempt.
            if not (record.restoring and renderer == record.previous_renderer):
                self._forget(record)
                self._clear_error(viewer)
                record = None
        if command:
            if paused == command.paused:
                self._drop_command(command)
                if command.operation == "restore":
                    self._forget(record)
                    record = None
                self._clear_error(viewer)
                command = None
            elif self._clock() >= command.deadline:
                requested = "pause" if command.paused else "resume"
                self._set_error(viewer, f"Houdini did not confirm the requested {requested} "
                                f"within {self._command_timeout:g} seconds. "
                                "The displayed state is the observed state; retry the action.")
                self._drop_command(command)
                command = None
        self._grid.observe(viewer, renderer)
        record = self._record(viewer)
        command = self._command(viewer)
        if renderer in RENDERER_NAMES:
            state = "paused" if paused else "active"
            message = "HoudiniRT is paused." if paused else "HoudiniRT is selected; rendering is not paused."
        elif any(r in available for r in RENDERER_NAMES):
            state, message = "off", f"Current renderer: {renderer}."
        else:
            state, message = "unavailable", "HoudiniRT is not available in this viewer. Check its renderer package."
        if command:
            message += {"enable": " Waiting for HoudiniRT to resume.",
                        "pause": " Waiting for Houdini to pause.",
                        "resume": " Waiting for Houdini to resume.",
                        "restore": " Restoring the previous renderer's pause state."}[command.operation]
        elif record and record.restoring:
            message += " Previous renderer restoration is pending; switch Off again to retry."
        cleanup = self._grid.cleanup_pending(viewer)
        display_pending = self._grid.pending(viewer)
        if display_pending:
            message += " Waiting for redraw to preserve the split viewport grid settings."
        elif cleanup:
            message += " Reference grid restoration is pending; switch Off again to retry."
        if self._grid.hidden(viewer):
            message += " Reference grid temporarily hidden for responsive camera edits; restored on Off."
        return RenderStatus(state, name, renderer,
                            record.previous_renderer if record else "",
                            record is not None, message, self._error(viewer),
                            command.operation if command else display_pending, cleanup)

    def status(self, viewer=None) -> RenderStatus:
        """Read native state; never infer ON from service health or a checkbox."""
        try:
            viewer = self.resolve_viewer(viewer)
            return self._observe(viewer)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            record = command = None
            if viewer is not None and threading.current_thread() is threading.main_thread():
                self._grid.observe(viewer)
                record, command = self._record(viewer), self._command(viewer)
                # Keep recovery after a transient failed read. A command whose
                # state cannot be observed is still bounded by its deadline.
                if command and self._clock() >= command.deadline:
                    self._drop_command(command)
                    command = None
                self._set_error(viewer, error)
            return RenderStatus("error", previous_renderer=record.previous_renderer if record else "",
                                owned=record is not None, message=error, error=error,
                                pending=command.operation if command else self._grid.pending(viewer),
                                cleanup_pending=self._grid.cleanup_pending(viewer))

    def _failure(self, viewer, exc):
        text = str(exc) or type(exc).__name__
        observed = self.status(viewer)
        if viewer is not None and threading.current_thread() is threading.main_thread():
            self._set_error(viewer, text)
        return replace(observed, message=text, error=text)

    @staticmethod
    def _renderer_name(viewer):
        available = tuple(viewer.hydraRenderers())
        for renderer in RENDERER_NAMES:
            if renderer in available:
                return renderer
        raise RenderSessionError("HoudiniRT is not available in this viewer. Check its renderer package.")

    def activate(self, viewer=None) -> RenderStatus:
        try:
            viewer = self.resolve_viewer(viewer)
            observed = self._observe(viewer)
            if observed.busy:
                return observed
            self._clear_error(viewer)
            observed = replace(observed, error="")
            if observed.active:
                return observed  # preserve pause and ownership on repeat clicks
            if observed.can_restore:
                raise RenderSessionError("Complete the pending Off restoration before enabling HoudiniRT again.")
            if not observed.renderer:
                raise RenderSessionError("The viewer has no restorable previous renderer.")
            renderer = self._renderer_name(viewer)
            record = _Ownership(viewer, observed.renderer,
                                bool(viewer.isRendererPaused()), renderer)
            self._ownership.append(record)
            viewer.setHydraRenderer(renderer)
            if viewer.currentHydraRenderer() not in RENDERER_NAMES:
                raise RenderSessionError("Houdini did not select the requested HoudiniRT renderer.")
            self._grid.acquire(viewer)
            return self._request_pause(viewer, False, "enable")
        except Exception as exc:
            return self._failure(viewer, exc)

    def deactivate(self, viewer=None) -> RenderStatus:
        try:
            viewer = self.resolve_viewer(viewer)
            retrying_display = self._grid.needs_retry(viewer)
            self._grid.retry(viewer)
            observed = self._observe(viewer)
            if observed.busy and not (retrying_display and self._command(viewer) is None):
                return observed
            self._clear_error(viewer)
            observed = replace(observed, error="")
            record = self._record(viewer)
            if record is None:
                if observed.cleanup_pending:
                    self._grid.observe(viewer)
                    return self._observe(viewer)
                if observed.active:
                    raise RenderSessionError(
                        "HoudiniRT was enabled outside these controls; no previous renderer was recorded. "
                        "Choose a renderer from the viewer's renderer menu.")
                return observed
            if (viewer.currentHydraRenderer() != record.previous_renderer
                    and record.previous_renderer not in tuple(viewer.hydraRenderers())):
                raise RenderSessionError(
                    f"Previous renderer {record.previous_renderer!r} is unavailable. "
                    "Restore its package or choose a renderer in the viewer.")
            record.restoring = True
            if viewer.currentHydraRenderer() != record.previous_renderer:
                viewer.setHydraRenderer(record.previous_renderer)
            if viewer.currentHydraRenderer() != record.previous_renderer:
                raise RenderSessionError("Houdini did not restore the previous renderer. Switch Off again to retry.")
            return self._request_pause(viewer, record.previous_paused, "restore")
        except Exception as exc:
            return self._failure(viewer, exc)

    def toggle(self, viewer=None) -> RenderStatus:
        try:
            viewer = self.resolve_viewer(viewer)
            # Polling can finish cleanup. Preserve the button's restoration
            # intent instead of immediately enabling a fresh session afterward.
            restoring = self._record(viewer) is not None or self._grid.cleanup_pending(viewer)
            current = self._observe(viewer)
            if current.busy:
                return current
            if current.active or current.can_restore or restoring:
                return self.deactivate(viewer)
            return self.activate(viewer)
        except Exception as exc:
            return self._failure(viewer, exc)

    def set_paused(self, viewer=None, paused=True) -> RenderStatus:
        try:
            viewer = self.resolve_viewer(viewer)
            observed = self._observe(viewer)
            if observed.busy:
                return observed
            self._clear_error(viewer)
            if not observed.active:
                raise RenderSessionError("Enable HoudiniRT in this viewer before using Pause or Resume.")
            requested = bool(paused)
            return self._request_pause(viewer, requested, "pause" if requested else "resume")
        except Exception as exc:
            return self._failure(viewer, exc)

    def toggle_pause(self, viewer=None) -> RenderStatus:
        try:
            viewer = self.resolve_viewer(viewer)
            current = self._observe(viewer)
            if current.busy:
                return current
            return self.set_paused(viewer, not current.paused)
        except Exception as exc:
            return self._failure(viewer, exc)


# An explicit module reload must not orphan the live display observer/leases.
_CONTROLLER = globals().get("_CONTROLLER")
_CONTROLLER_LOCK = threading.Lock()


def get_render_controller() -> RenderController:
    """The session owner survives panel reload and is shared by every surface."""
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        if _CONTROLLER is None:
            _CONTROLLER = RenderController()
        return _CONTROLLER
