"""Safe 1-click artist actions for Houdini 22 shelves, hotkeys, and radial menus.

All public entrypoints catch ``KarmaSessionError`` cleanly and report non-blocking
status messages via ``hou.ui.setStatusMessage`` whenever Houdini's GUI is available.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any


class _FallbackSessionState:
    """Fallback constants matching ``karma_rt.controller.SessionState`` verbatim."""

    UNINITIALIZED = "UNINITIALIZED"
    PREFLIGHT_CHECKING = "PREFLIGHT_CHECKING"
    UNAVAILABLE = "UNAVAILABLE"
    READY_OFF = "READY_OFF"
    PREVIEW_DIFF = "PREVIEW_DIFF"
    APPLYING_OVERRIDE = "APPLYING_OVERRIDE"
    ACTIVE_IDLE = "ACTIVE_IDLE"
    ACTIVE_TURBO_INTERACT = "ACTIVE_TURBO_INTERACT"
    ACTIVE_PAUSED = "ACTIVE_PAUSED"
    ACTIVE_DISK_RENDER_YIELD = "ACTIVE_DISK_RENDER_YIELD"
    RESTORING = "RESTORING"
    EXTERNAL_TAKEOVER = "EXTERNAL_TAKEOVER"
    DEGRADED = "DEGRADED"


class _FallbackKarmaSessionError(RuntimeError):
    """Fallback error type used only when ``karma_rt.controller`` is not yet loaded."""


def _controller_module() -> Any | None:
    existing = sys.modules.get("karma_rt.controller")
    if existing is not None:
        return existing
    try:
        return importlib.import_module("karma_rt.controller")
    except ImportError:
        return None


def _session_state_cls() -> Any:
    mod = _controller_module()
    if mod is not None and hasattr(mod, "SessionState"):
        return mod.SessionState
    return _FallbackSessionState


def _session_error_types() -> tuple[type[BaseException], ...]:
    mod = _controller_module()
    err_cls = getattr(mod, "KarmaSessionError", None) if mod is not None else None
    if isinstance(err_cls, type) and issubclass(err_cls, BaseException):
        return (err_cls, _FallbackKarmaSessionError)
    return (_FallbackKarmaSessionError,)


def _resolve_controller(controller: Any = None) -> Any:
    if controller is not None:
        return controller
    mod = _controller_module()
    if mod is None or not hasattr(mod, "get_controller"):
        raise _FallbackKarmaSessionError(
            "karma_rt.controller.get_controller() is unavailable."
        )
    return mod.get_controller()


def _import_hou(hou_module: Any = None) -> Any | None:
    if hou_module is not None:
        return hou_module
    try:
        import hou  # type: ignore[import-not-found]

        return hou
    except Exception:
        return None


def _is_ui_available(hou_mod: Any | None) -> bool:
    if hou_mod is None:
        return False
    is_ui = getattr(hou_mod, "isUIAvailable", None)
    if not callable(is_ui):
        return False
    try:
        return bool(is_ui()) and getattr(hou_mod, "ui", None) is not None
    except Exception:
        return False


def _report_status(
    message: str,
    *,
    severity: str = "message",
    hou_module: Any = None,
) -> None:
    """Emit a non-blocking Houdini status bar message when GUI is available."""
    text = str(message).strip()
    if not text:
        return
    hou_mod = _import_hou(hou_module)
    if not _is_ui_available(hou_mod):
        return
    ui_mod = getattr(hou_mod, "ui", None)
    set_status = getattr(ui_mod, "setStatusMessage", None)
    if not callable(set_status):
        return

    sev_enum = getattr(hou_mod, "severityType", None)
    sev_value: Any = None
    if sev_enum is not None:
        mapping = {
            "error": getattr(sev_enum, "Error", None),
            "warning": getattr(sev_enum, "Warning", None),
            "important": getattr(sev_enum, "ImportantMessage", None),
            "message": getattr(sev_enum, "Message", None),
        }
        sev_value = mapping.get(severity.lower())

    full_message = text if text.startswith("Karma RT") else f"Karma RT: {text}"
    try:
        if sev_value is not None:
            set_status(full_message, severity=sev_value)
        else:
            set_status(full_message)
    except Exception:
        pass


def _format_snapshot_status(snapshot: Any, fallback: str) -> str:
    if snapshot is None:
        return fallback
    message = str(getattr(snapshot, "message", "") or "").strip()
    if message:
        return message
    delegate = str(getattr(snapshot, "observed_delegate", "") or "").strip()
    state = str(getattr(snapshot, "state", "") or "").strip()
    preset = str(getattr(snapshot, "active_preset_id", "") or "").strip()
    parts = [p for p in (delegate, preset, state) if p]
    return " · ".join(parts) if parts else fallback


def toggle_karma_rt(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    default_preset: str = "interactive_lookdev",
    delegate_preference: str = "Karma XPU",
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Toggle Karma RT session overrides on or off cleanly."""
    try:
        ctrl = _resolve_controller(controller)
        snapshot = ctrl.toggle(
            viewer=viewer,
            viewer_name=viewer_name,
            default_preset=default_preset,
            delegate_preference=delegate_preference,
        )
        _report_status(
            _format_snapshot_status(snapshot, "Toggled Karma RT session."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def _apply_preset(
    preset_id: str,
    *,
    delegate_preference: str,
    viewer: Any = None,
    viewer_name: str | None = None,
    allow_restart: bool = False,
    denoiser_engine: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    try:
        ctrl = _resolve_controller(controller)
        snapshot = ctrl.activate_preset(
            preset_id,
            viewer=viewer,
            viewer_name=viewer_name,
            delegate_preference=delegate_preference,
            allow_restart=allow_restart,
            denoiser_engine=denoiser_engine,
        )
        _report_status(
            _format_snapshot_status(snapshot, f"Applied preset '{preset_id}'."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def apply_live_draft(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    delegate_preference: str = "Karma XPU",
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Apply the zero-restart Live Draft (Max FPS) lookdev preset."""
    return _apply_preset(
        "live_draft",
        delegate_preference=delegate_preference,
        viewer=viewer,
        viewer_name=viewer_name,
        controller=controller,
        hou_module=hou_module,
    )


def apply_interactive_lookdev(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    delegate_preference: str = "Karma XPU",
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Apply the balanced Interactive Lookdev + Denoise preset."""
    return _apply_preset(
        "interactive_lookdev",
        delegate_preference=delegate_preference,
        viewer=viewer,
        viewer_name=viewer_name,
        controller=controller,
        hou_module=hou_module,
    )


def apply_final_parity(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    delegate_preference: str = "Karma XPU",
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Apply Final Parity (clears session overrides and matches production Karma)."""
    return _apply_preset(
        "final_parity",
        delegate_preference=delegate_preference,
        viewer=viewer,
        viewer_name=viewer_name,
        controller=controller,
        hou_module=hou_module,
    )


def apply_vulkan_layout(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    delegate_preference: str = "Houdini VK",
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Switch to the Houdini VK RT Layout preset."""
    return _apply_preset(
        "vulkan_layout",
        delegate_preference=delegate_preference,
        viewer=viewer,
        viewer_name=viewer_name,
        controller=controller,
        hou_module=hou_module,
    )


def apply_clay_headlight(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    delegate_preference: str = "Karma XPU",
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Apply the zero-restart Clay / Headlight AO silhouette lookdev preset."""
    return _apply_preset(
        "clay_headlight",
        delegate_preference=delegate_preference,
        viewer=viewer,
        viewer_name=viewer_name,
        controller=controller,
        hou_module=hou_module,
    )


def cycle_focus_crop(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Cycle zero-restart dataWindowNDC Focus Crop (full -> center_50 -> center_33 -> full)."""
    order = ("full", "center_50", "center_33")
    try:
        ctrl = _resolve_controller(controller)
        current = ctrl.observe(viewer=viewer, viewer_name=viewer_name)
        cur_crop = str(getattr(current, "focus_crop", "full") or "full")
        next_crop = order[(order.index(cur_crop) + 1) % len(order)] if cur_crop in order else "center_50"
        snapshot = ctrl.set_focus_crop(next_crop, viewer=viewer, viewer_name=viewer_name)
        _report_status(
            _format_snapshot_status(snapshot, f"Focus Crop: {next_crop}."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def toggle_freeze_viewport(
    *,
    frozen: bool | None = None,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Toggle Solaris viewport USD stage freeze (setSceneGraphStageLocked)."""
    try:
        ctrl = _resolve_controller(controller)
        target = frozen
        if target is None:
            current = ctrl.observe(viewer=viewer, viewer_name=viewer_name)
            target = not bool(getattr(current, "viewport_frozen", False))
        snapshot = ctrl.set_viewport_frozen(bool(target), viewer=viewer, viewer_name=viewer_name)
        _report_status(
            _format_snapshot_status(snapshot, f"Viewport Frozen: {target}."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def toggle_pin_stage(
    *,
    pinned: bool | None = None,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Toggle Solaris Stage Pin (hou.lop.setPanesFollowCurrentNode)."""
    try:
        ctrl = _resolve_controller(controller)
        target = pinned
        if target is None:
            current = ctrl.observe(viewer=viewer, viewer_name=viewer_name)
            target = not bool(getattr(current, "stage_pinned", False))
        snapshot = ctrl.set_stage_pinned(bool(target), viewer=viewer, viewer_name=viewer_name)
        _report_status(
            _format_snapshot_status(snapshot, f"Stage Pinned: {target}."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def solo_selected_geo(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Non-destructively solo selected Scene Graph geometry."""
    try:
        ctrl = _resolve_controller(controller)
        snapshot = ctrl.solo_selected_geometry(viewer=viewer, viewer_name=viewer_name)
        _report_status(
            _format_snapshot_status(snapshot, "Soloed selected geometry."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def clear_solo(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Clear viewport Solo Geometry and Solo Lights overrides."""
    try:
        ctrl = _resolve_controller(controller)
        snapshot = ctrl.clear_solo(viewer=viewer, viewer_name=viewer_name)
        _report_status(
            _format_snapshot_status(snapshot, "Cleared viewport solo."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def toggle_auto_turbo(
    *,
    enabled: bool | None = None,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Toggle Auto-Turbo on camera tumble / scene edits."""
    try:
        ctrl = _resolve_controller(controller)
        target = enabled
        if target is None:
            current = ctrl.observe(viewer=viewer, viewer_name=viewer_name)
            target = not bool(getattr(current, "auto_turbo_enabled", False))
        snapshot = ctrl.set_auto_turbo(
            bool(target),
            viewer=viewer,
            viewer_name=viewer_name,
        )
        state_txt = "ON" if bool(getattr(snapshot, "auto_turbo_enabled", target)) else "OFF"
        _report_status(
            _format_snapshot_status(snapshot, f"Auto-Turbo {state_txt}."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def toggle_clean_overlays(
    *,
    enabled: bool | None = None,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Toggle Clean Lookdev Overlays on the Solaris SceneViewer."""
    try:
        ctrl = _resolve_controller(controller)
        snapshot = ctrl.toggle_clean_overlays(
            enabled=enabled,
            viewer=viewer,
            viewer_name=viewer_name,
        )
        state_txt = "ON" if bool(getattr(snapshot, "clean_overlays_active", False)) else "OFF"
        _report_status(
            _format_snapshot_status(snapshot, f"Clean Overlays {state_txt}."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def toggle_pause(
    *,
    paused: bool | None = None,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Toggle viewport Hydra render pause state."""
    try:
        ctrl = _resolve_controller(controller)
        target = paused
        if target is None:
            current = ctrl.observe(viewer=viewer, viewer_name=viewer_name)
            target = not bool(getattr(current, "paused", False))
        snapshot = ctrl.set_paused(
            bool(target),
            viewer=viewer,
            viewer_name=viewer_name,
        )
        state_txt = "Paused" if bool(getattr(snapshot, "paused", target)) else "Resumed"
        _report_status(
            _format_snapshot_status(snapshot, f"Viewport render {state_txt}."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def yield_for_disk_render(
    *,
    resume_if_yielded: bool = True,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Yield interactive Karma VRAM for disk rendering (or resume if already yielded)."""
    try:
        ctrl = _resolve_controller(controller)
        state_cls = _session_state_cls()
        if resume_if_yielded:
            current = ctrl.observe(viewer=viewer, viewer_name=viewer_name)
            if getattr(current, "state", "") == state_cls.ACTIVE_DISK_RENDER_YIELD:
                snapshot = ctrl.resume_from_disk_render(
                    viewer=viewer,
                    viewer_name=viewer_name,
                )
                _report_status(
                    _format_snapshot_status(snapshot, "Resumed from Disk Render Yield."),
                    hou_module=hou_module,
                )
                return snapshot

        snapshot = ctrl.yield_for_disk_render(
            viewer=viewer,
            viewer_name=viewer_name,
        )
        _report_status(
            _format_snapshot_status(snapshot, "Yielded viewport for Disk Render."),
            severity="important",
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def quick_snapshot(
    *,
    output_path: str | None = None,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> dict[str, Any] | None:
    """Capture a quick lookdev viewport snapshot."""
    try:
        ctrl = _resolve_controller(controller)
        result = ctrl.quick_snapshot(
            output_path=output_path,
            viewer=viewer,
            viewer_name=viewer_name,
        )
        saved_path = ""
        if isinstance(result, dict):
            saved_path = str(
                result.get("output_path")
                or result.get("path")
                or result.get("message")
                or ""
            ).strip()
        msg = f"Snapshot saved: {saved_path}" if saved_path else "Viewport snapshot captured."
        _report_status(msg, hou_module=hou_module)
        return result
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def restore_production(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Remove Karma RT session overrides and restore the prior renderer cleanly."""
    try:
        ctrl = _resolve_controller(controller)
        snapshot = ctrl.restore(viewer=viewer, viewer_name=viewer_name)
        _report_status(
            _format_snapshot_status(snapshot, "Restored production viewport state."),
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


def emergency_reset(
    *,
    viewer: Any = None,
    viewer_name: str | None = None,
    controller: Any = None,
    hou_module: Any = None,
) -> Any | None:
    """Force-clear all Karma RT session state, overrides, overlays, and timers."""
    try:
        ctrl = _resolve_controller(controller)
        snapshot = ctrl.emergency_reset(viewer=viewer, viewer_name=viewer_name)
        _report_status(
            _format_snapshot_status(snapshot, "Emergency reset completed."),
            severity="important",
            hou_module=hou_module,
        )
        return snapshot
    except _session_error_types() as exc:
        _report_status(str(exc), severity="error", hou_module=hou_module)
        return None


def open_panel(
    *,
    interface_name: str = "karma_rt_panel",
    hou_module: Any = None,
) -> Any | None:
    """Focus an open Karma RT Python Panel tab or open a floating panel window."""
    try:
        hou_mod = _import_hou(hou_module)
        if not _is_ui_available(hou_mod):
            return None

        ui_mod = hou_mod.ui
        desktop = ui_mod.curDesktop()
        pane_tab_type = getattr(hou_mod, "paneTabType", None)
        pypanel_type = getattr(pane_tab_type, "PythonPanel", None)

        # 1. Focus an existing Karma RT Python Panel pane tab if already open.
        if desktop is not None and hasattr(desktop, "paneTabs"):
            for tab in desktop.paneTabs():
                try:
                    if pypanel_type is not None and tab.type() != pypanel_type:
                        continue
                    iface = tab.activeInterface()
                    if iface is not None and iface.name() == interface_name:
                        tab.setIsCurrentTab()
                        _report_status("Focused Karma RT panel.", hou_module=hou_mod)
                        return tab
                except Exception:
                    continue

        # 2. Otherwise open a floating Python Panel tab with the registered interface.
        pypanel_mod = getattr(hou_mod, "pypanel", None)
        interfaces = pypanel_mod.interfaces() if pypanel_mod is not None else {}
        target_iface = interfaces.get(interface_name) if isinstance(interfaces, dict) else None

        if desktop is not None and pypanel_type is not None:
            pane_tab = desktop.createFloatingPaneTab(pypanel_type, size=(520, 760))
            if pane_tab is not None:
                if target_iface is not None and hasattr(pane_tab, "setActiveInterface"):
                    pane_tab.setActiveInterface(target_iface)
                    _report_status("Opened Karma RT panel.", hou_module=hou_mod)
                    return pane_tab
                _report_status(
                    f"Python Panel interface '{interface_name}' is not installed in HOUDINI_PANEL_PATH.",
                    severity="warning",
                    hou_module=hou_mod,
                )
                return pane_tab

        _report_status(
            "Unable to open Karma RT panel in current Houdini desktop.",
            severity="warning",
            hou_module=hou_mod,
        )
        return None
    except _session_error_types() as exc:
        _report_status(str(exc), severity="warning", hou_module=hou_module)
        return None


__all__ = [
    "apply_clay_headlight",
    "apply_final_parity",
    "apply_interactive_lookdev",
    "apply_live_draft",
    "apply_vulkan_layout",
    "clear_solo",
    "cycle_focus_crop",
    "emergency_reset",
    "open_panel",
    "quick_snapshot",
    "restore_production",
    "solo_selected_geo",
    "toggle_auto_turbo",
    "toggle_clean_overlays",
    "toggle_freeze_viewport",
    "toggle_karma_rt",
    "toggle_pause",
    "toggle_pin_stage",
    "yield_for_disk_render",
]
