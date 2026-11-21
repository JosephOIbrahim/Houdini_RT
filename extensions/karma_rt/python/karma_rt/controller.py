"""KarmaRT Solaris session controller and real-time lookdev state machine.

Coordinates Houdini 22 Solaris SceneViewer leasing, zero-restart ``LOP:Custom``
USD viewport overrides, Auto-Turbo interaction/settle transitions, queued-pause
confirmation with bounded timeout, external renderer takeover handling,
Houdini 22.0.429 reference-grid guarding, and non-gating latency ledger logging.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any
import uuid

from .advisor import (
    UNKNOWN,
    LatencyLedger,
    SceneAdvisory,
    StageTelemetry,
    scan_stage_bottlenecks,
)
from .auto_turbo import (
    AutoTurboConfig,
    AutoTurboEngine,
    build_turbo_merged_overrides,
    unwrap_typed_overrides,
)
from .catalog import (
    DEFAULT_RENDERER_LABELS,
    H22Catalog,
    KARMA_CPU_ALIASES,
    KARMA_XPU_ALIASES,
    VULKAN_ALIASES,
    load_h22_catalog,
    resolve_delegate_label,
)
from .hardware import HardwareSnapshot, probe_hardware
from .preflight import PreflightReport, run_preflight as _run_preflight
from .presets import (
    FOCUS_CROP_REGIONS,
    SUPPORTED_DENOISER_ENGINES,
    SUPPORTED_RESOLUTION_SCALES,
    PresetSpec,
    build_focus_crop_override,
    build_resolution_scale_override,
    get_presets,
    strip_builtin_denoiser_if_usd_image_filters,
    validate_overrides_zero_restart,
    validate_preset_zero_restart,
)
from .reference_grid import ReferenceGridGuard
from .session_overrides import (
    HipLifecycleGuard,
    OverridePreview,
    OverrideReceipt,
    SessionOverrideEngine,
    SessionOverrideError,
)

logger = logging.getLogger(__name__)

_SUBSCRIBED_VIEWER_EVENTS: tuple[str, ...] = (
    "RendererChanged",
    "Resizing",
    "BeginResize",
    "EndResize",
    "StateEntered",
    "StateExited",
    "ViewerTerminated",
)

_ACTIVE_STATES: frozenset[str] = frozenset(
    {
        "ACTIVE_IDLE",
        "ACTIVE_TURBO_INTERACT",
        "ACTIVE_PAUSED",
        "ACTIVE_DISK_RENDER_YIELD",
    }
)

_VULKAN_ALIASES_FOLDED: frozenset[str] = frozenset(a.casefold() for a in VULKAN_ALIASES)
_KARMA_CPU_ALIASES_FOLDED: frozenset[str] = frozenset(a.casefold() for a in KARMA_CPU_ALIASES)


class KarmaSessionError(RuntimeError):
    """Raised when a Solaris KarmaRT session control operation cannot proceed."""


class SessionState:
    """Explicit state constants for ``KarmaSessionController``."""

    UNINITIALIZED: str = "UNINITIALIZED"
    PREFLIGHT_CHECKING: str = "PREFLIGHT_CHECKING"
    UNAVAILABLE: str = "UNAVAILABLE"
    READY_OFF: str = "READY_OFF"
    PREVIEW_DIFF: str = "PREVIEW_DIFF"
    APPLYING_OVERRIDE: str = "APPLYING_OVERRIDE"
    ACTIVE_IDLE: str = "ACTIVE_IDLE"
    ACTIVE_TURBO_INTERACT: str = "ACTIVE_TURBO_INTERACT"
    ACTIVE_PAUSED: str = "ACTIVE_PAUSED"
    ACTIVE_DISK_RENDER_YIELD: str = "ACTIVE_DISK_RENDER_YIELD"
    RESTORING: str = "RESTORING"
    EXTERNAL_TAKEOVER: str = "EXTERNAL_TAKEOVER"
    DEGRADED: str = "DEGRADED"


@dataclass(frozen=True)
class SessionSnapshot:
    """Immutable controller <-> UI state snapshot."""

    state: str
    viewer_name: str = ""
    lopnet_path: str = ""
    display_lop_path: str = ""
    settings_prim_path: str = ""
    observed_delegate: str = ""
    previous_renderer: str = ""
    active_preset_id: str = ""
    auto_turbo_enabled: bool = False
    turbo_engaged: bool = False
    paused: bool = False
    pending_op: str = ""
    override_active: bool = False
    final_render_untouched: bool = True
    camera_path: str = ""
    camera_locked: bool = False
    clean_overlays_active: bool = False
    denoiser_mode: str = "UNKNOWN"
    xpu_device_summary: str = "UNKNOWN"
    stage_serial: int | None = None
    last_receipt: Any = None
    last_error: str = ""
    message: str = ""
    telemetry_note: str = "UNKNOWN"
    available_delegates: tuple[str, ...] = ()
    show_render_stats: bool = False
    vram_eviction_risk: str = "UNKNOWN"
    optix_cache_ready: bool | None = None
    focus_crop: str = "full"
    resolution_scale: float = 1.0
    viewport_frozen: bool = False
    stage_pinned: bool = False
    solo_geometry_count: int = 0
    solo_lights_count: int = 0
    usd_image_filters_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["available_delegates"] = list(self.available_delegates)
        if hasattr(self.last_receipt, "to_dict") and callable(self.last_receipt.to_dict):
            payload["last_receipt"] = self.last_receipt.to_dict()
        return payload


@dataclass(slots=True)
class _PendingPause:
    """Bounded confirmation state for a queued ``setRendererPaused`` command."""

    target_paused: bool
    issued_at: float
    deadline: float
    command_dispatched: bool = True
    reason: str = "pause"


@dataclass(slots=True)
class _ViewerLease:
    """Per-Solaris-SceneViewer session lease and recorded pre-KarmaRT state."""

    viewer: Any
    viewer_key: str
    viewer_name: str
    session_id: str
    lopnet_path: str
    display_lop_path: str
    settings_prim_path: str = ""
    state: str = SessionState.ACTIVE_IDLE
    owned_delegate: str = ""
    active_preset_id: str = ""
    allow_restart: bool = False
    denoiser_mode: str = UNKNOWN
    override_active: bool = False
    active_overrides: dict[str, Any] = field(default_factory=dict)
    base_typed_overrides: dict[str, Any] = field(default_factory=dict)
    auto_turbo_enabled: bool = False
    turbo_engaged: bool = False
    paused: bool = False
    pending_pause: _PendingPause | None = None
    clean_overlays_active: bool = False
    show_render_stats: bool = False
    last_receipt: OverrideReceipt | None = None
    last_error: str = ""
    message: str = ""
    # Recorded pre-KarmaRT artist state for faithful restore():
    previous_renderer: str = ""
    previous_paused: bool | None = None
    previous_show_lights: bool | None = None
    previous_show_selection: bool | None = None
    previous_use_overrides: bool | None = None
    previous_show_procedurals: bool | None = None
    previous_show_render_time: bool | None = None
    previous_vk_settings: dict[str, bool] = field(default_factory=dict)
    previous_stage_locked: bool | None = None
    previous_panes_follow: bool | None = None
    focus_crop: str = "full"
    resolution_scale: float = 1.0
    usd_image_filters_detected: bool = False
    solo_modified: bool = False
    event_callback: Callable[..., Any] | None = None


def _assert_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise KarmaSessionError(
            "KarmaSessionController must only be invoked from the main thread "
            f"(called from {threading.current_thread().name!r})."
        )


def _same_pane(a: Any, b: Any) -> bool:
    """Native Houdini pane identity check without brittle pointer assumptions."""
    if a is b:
        return True
    if a is None or b is None:
        return False
    try:
        return bool(a == b)
    except Exception:
        return False


def _normalize_event_name(raw_event: Any) -> str:
    if raw_event is None:
        return ""
    name_attr = getattr(raw_event, "name", None)
    if callable(name_attr):
        try:
            res = str(name_attr())
            if res:
                return res.rsplit(".", 1)[-1]
        except Exception:
            pass
    elif isinstance(name_attr, str) and name_attr:
        return name_attr.rsplit(".", 1)[-1]
    text = str(raw_event).strip()
    return text.rsplit(".", 1)[-1] if text else ""


def _safe_viewer_name(viewer: Any) -> str:
    if viewer is None:
        return ""
    name_fn = getattr(viewer, "name", None)
    if callable(name_fn):
        try:
            return str(name_fn())
        except Exception:
            return ""
    return str(name_fn) if isinstance(name_fn, str) else ""


def _viewer_key(viewer: Any) -> str:
    name = _safe_viewer_name(viewer)
    pane_fn = getattr(viewer, "pane", None)
    if callable(pane_fn):
        try:
            pane = pane_fn()
            if pane is not None:
                id_fn = getattr(pane, "id", None)
                if callable(id_fn):
                    pane_id = id_fn()
                    return f"{pane_id}:{name}"
        except Exception:
            pass
    return name if name else f"viewer:{id(viewer)}"


def _is_scene_viewer_tab(tab: Any, hou_module: Any) -> bool:
    if tab is None:
        return False
    pane_tab_type = getattr(hou_module, "paneTabType", None)
    sv_enum = getattr(pane_tab_type, "SceneViewer", None) if pane_tab_type is not None else None
    type_fn = getattr(tab, "type", None)
    if sv_enum is not None and callable(type_fn):
        try:
            return bool(type_fn() == sv_enum)
        except Exception:
            return False
    return callable(getattr(tab, "isViewingSceneGraph", None)) and callable(
        getattr(tab, "curViewport", None)
    )


def _is_viewing_scene_graph(viewer: Any) -> bool:
    fn = getattr(viewer, "isViewingSceneGraph", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            return False
    return False


def _is_vulkan_label(label: str | None) -> bool:
    if not label:
        return False
    folded = str(label).strip().casefold()
    return folded in _VULKAN_ALIASES_FOLDED or "vulkan" in folded or folded.endswith(" vk")


def _delegates_match(observed: str, owned: str) -> bool:
    obs = str(observed or "").strip()
    own = str(owned or "").strip()
    if not obs or not own:
        return obs == own
    if obs.casefold() == own.casefold():
        return True
    obs_mapped = DEFAULT_RENDERER_LABELS.get(obs, obs).casefold()
    own_mapped = DEFAULT_RENDERER_LABELS.get(own, own).casefold()
    return obs_mapped == own_mapped


def _get_viewer_renderer(viewer: Any) -> str:
    for method_name in ("hydraRenderer", "currentHydraRenderer"):
        fn = getattr(viewer, method_name, None)
        if callable(fn):
            try:
                val = fn()
                if val is not None:
                    return str(val).strip()
            except Exception:
                continue
    return ""


def _set_viewer_renderer(viewer: Any, label: str) -> None:
    fn = getattr(viewer, "setHydraRenderer", None)
    if not callable(fn):
        raise KarmaSessionError("Solaris SceneViewer does not expose setHydraRenderer().")
    try:
        fn(str(label))
    except Exception as exc:
        raise KarmaSessionError(f"Failed to switch Hydra delegate to {label!r}: {exc}") from exc


def _get_viewer_paused(viewer: Any) -> bool:
    for method_name in ("isRendererPaused", "rendererPaused"):
        fn = getattr(viewer, method_name, None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                continue
    return False


def _get_use_viewport_overrides(viewer: Any, renderer: str | None = None) -> bool | None:
    for method_name in ("isUsingViewportOverrides", "usingViewportOverrides"):
        fn = getattr(viewer, method_name, None)
        if callable(fn):
            try:
                if renderer:
                    try:
                        return bool(fn(renderer=renderer))
                    except TypeError:
                        return bool(fn())
                return bool(fn())
            except Exception:
                continue
    fn = getattr(viewer, "useViewportOverrides", None)
    if callable(fn):
        try:
            return bool(fn())
        except TypeError:
            return None
        except Exception:
            return None
    return None


def _set_use_viewport_overrides(viewer: Any, enabled: bool, renderer: str | None = None) -> bool:
    for method_name in ("useViewportOverrides", "setUseViewportOverrides"):
        fn = getattr(viewer, method_name, None)
        if not callable(fn):
            continue
        try:
            if renderer:
                try:
                    fn(bool(enabled), renderer=str(renderer))
                except TypeError:
                    fn(bool(enabled))
            else:
                fn(bool(enabled))
            return True
        except Exception as exc:
            logger.debug("%s(%s) failed: %s", method_name, enabled, exc)
    return False


def _iter_viewports(viewer: Any) -> list[Any]:
    viewports: list[Any] = []
    vps_fn = getattr(viewer, "viewports", None)
    if callable(vps_fn):
        try:
            found = vps_fn()
            if found:
                viewports.extend(list(found))
        except Exception:
            pass
    if not viewports:
        cur_fn = getattr(viewer, "curViewport", None)
        if callable(cur_fn):
            try:
                vp = cur_fn()
                if vp is not None:
                    viewports.append(vp)
            except Exception:
                pass
    return viewports


def _cur_viewport_settings(viewer: Any) -> Any | None:
    cur_fn = getattr(viewer, "curViewport", None)
    if not callable(cur_fn):
        return None
    try:
        vp = cur_fn()
    except Exception:
        return None
    if vp is None:
        return None
    settings_fn = getattr(vp, "settings", None)
    if not callable(settings_fn):
        return None
    try:
        return settings_fn()
    except Exception:
        return None


def _get_show_lights(viewer: Any) -> bool | None:
    for obj in (viewer, _cur_viewport_settings(viewer)):
        if obj is None:
            continue
        for method_name in ("showLights", "isShowingLights", "displayLights"):
            fn = getattr(obj, method_name, None)
            if callable(fn):
                try:
                    return bool(fn())
                except TypeError:
                    continue
                except Exception:
                    continue
    return None


def _set_show_lights(viewer: Any, enabled: bool) -> bool:
    applied = False
    fn = getattr(viewer, "setShowLights", None)
    if callable(fn):
        try:
            fn(bool(enabled))
            applied = True
        except Exception:
            pass
    for vp in _iter_viewports(viewer):
        settings_fn = getattr(vp, "settings", None)
        if not callable(settings_fn):
            continue
        try:
            settings = settings_fn()
        except Exception:
            continue
        if settings is None:
            continue
        for setter_name in ("setShowLights", "setDisplayLights"):
            sfn = getattr(settings, setter_name, None)
            if callable(sfn):
                try:
                    sfn(bool(enabled))
                    applied = True
                    break
                except Exception:
                    continue
    return applied


def _get_show_selection(viewer: Any) -> bool | None:
    for obj in (viewer, _cur_viewport_settings(viewer)):
        if obj is None:
            continue
        for method_name in ("showSelection", "isShowingSelection", "displaySelection"):
            fn = getattr(obj, method_name, None)
            if callable(fn):
                try:
                    return bool(fn())
                except TypeError:
                    continue
                except Exception:
                    continue
    return None


def _set_show_selection(viewer: Any, enabled: bool) -> bool:
    applied = False
    fn = getattr(viewer, "setShowSelection", None)
    if callable(fn):
        try:
            fn(bool(enabled))
            applied = True
        except Exception:
            pass
    for vp in _iter_viewports(viewer):
        settings_fn = getattr(vp, "settings", None)
        if not callable(settings_fn):
            continue
        try:
            settings = settings_fn()
        except Exception:
            continue
        if settings is None:
            continue
        for setter_name in ("setShowSelection", "setDisplaySelection"):
            sfn = getattr(settings, setter_name, None)
            if callable(sfn):
                try:
                    sfn(bool(enabled))
                    applied = True
                    break
                except Exception:
                    continue
    return applied


def _get_show_render_time(viewer: Any) -> bool | None:
    for obj in (viewer, _cur_viewport_settings(viewer)):
        if obj is None:
            continue
        for method_name in ("showRenderTime", "isShowingRenderTime", "displayRenderTime"):
            fn = getattr(obj, method_name, None)
            if callable(fn):
                try:
                    return bool(fn())
                except TypeError:
                    continue
                except Exception:
                    continue
    return None


def _set_show_render_time(viewer: Any, enabled: bool) -> bool:
    applied = False
    for method_name in ("setShowRenderTime", "setShowRenderStats"):
        fn = getattr(viewer, method_name, None)
        if callable(fn):
            try:
                fn(bool(enabled))
                applied = True
            except Exception:
                pass
    for vp in _iter_viewports(viewer):
        settings_fn = getattr(vp, "settings", None)
        if not callable(settings_fn):
            continue
        try:
            settings = settings_fn()
        except Exception:
            continue
        if settings is None:
            continue
        for setter_name in ("setShowRenderTime", "setDisplayRenderTime"):
            sfn = getattr(settings, setter_name, None)
            if callable(sfn):
                try:
                    sfn(bool(enabled))
                    applied = True
                    break
                except Exception:
                    continue
    return applied


def _get_stage_locked(viewer: Any) -> bool | None:
    if viewer is None:
        return None
    for method_name in ("sceneGraphStageLocked", "isSceneGraphStageLocked"):
        fn = getattr(viewer, method_name, None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                continue
    return None


def _set_stage_locked(viewer: Any, locked: bool) -> bool:
    if viewer is None:
        return False
    fn = getattr(viewer, "setSceneGraphStageLocked", None)
    if callable(fn):
        try:
            fn(bool(locked))
            return True
        except Exception:
            return False
    return False


def _get_panes_follow_current_node(hou_module: Any) -> bool | None:
    lop_mod = getattr(hou_module, "lop", None) if hou_module is not None else None
    fn = getattr(lop_mod, "panesFollowCurrentNode", None) if lop_mod is not None else None
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            return None
    return None


def _set_panes_follow_current_node(hou_module: Any, follow: bool) -> bool:
    lop_mod = getattr(hou_module, "lop", None) if hou_module is not None else None
    fn = getattr(lop_mod, "setPanesFollowCurrentNode", None) if lop_mod is not None else None
    if callable(fn):
        try:
            fn(bool(follow))
            return True
        except Exception:
            return False
    return False


def _get_scene_graph_selection(viewer: Any) -> list[str]:
    if viewer is None:
        return []
    fn = getattr(viewer, "currentSceneGraphSelection", None)
    if not callable(fn):
        return []
    try:
        raw = fn() or ()
        return [str(p).strip() for p in raw if str(p).strip()]
    except Exception:
        return []


def _get_show_hydra_procedurals(viewer: Any) -> bool | None:
    for method_name in ("isShowingHydraProcedurals", "showHydraProcedurals"):
        fn = getattr(viewer, method_name, None)
        if callable(fn):
            try:
                return bool(fn())
            except TypeError:
                continue
            except Exception:
                continue
    return None


def _set_show_hydra_procedurals(viewer: Any, enabled: bool) -> bool:
    for method_name in ("setShowHydraProcedurals", "showHydraProcedurals"):
        fn = getattr(viewer, method_name, None)
        if callable(fn):
            try:
                fn(bool(enabled))
                return True
            except TypeError:
                continue
            except Exception:
                continue
    return False


def _capture_vk_settings(viewer: Any) -> dict[str, bool]:
    settings = _cur_viewport_settings(viewer)
    if settings is None:
        return {}
    captured: dict[str, bool] = {}
    probes = (
        ("vk_raytracing", ("useRayTracing", "isUsingRayTracing")),
        ("vk_denoising", ("useDenoising", "isUsingDenoising")),
        ("vk_fast_interactive", ("fastInteractiveSampling", "isUsingFastInteractiveSampling")),
    )
    for key, method_names in probes:
        for method_name in method_names:
            fn = getattr(settings, method_name, None)
            if callable(fn):
                try:
                    captured[key] = bool(fn())
                    break
                except Exception:
                    continue
    return captured


def _apply_vk_settings(
    viewer: Any,
    *,
    raytracing: bool | None = None,
    denoising: bool | None = None,
    fast_interactive: bool | None = None,
) -> dict[str, bool]:
    applied: dict[str, bool] = {}
    for vp in _iter_viewports(viewer):
        settings_fn = getattr(vp, "settings", None)
        if not callable(settings_fn):
            continue
        try:
            settings = settings_fn()
        except Exception:
            continue
        if settings is None:
            continue
        if raytracing is not None:
            fn = getattr(settings, "setUseRayTracing", None)
            if callable(fn):
                try:
                    fn(bool(raytracing))
                    applied["vk_raytracing"] = bool(raytracing)
                except Exception:
                    pass
        if denoising is not None:
            fn = getattr(settings, "setUseDenoising", None)
            if callable(fn):
                try:
                    fn(bool(denoising))
                    applied["vk_denoising"] = bool(denoising)
                except Exception:
                    pass
        if fast_interactive is not None:
            fn = getattr(settings, "setFastInteractiveSampling", None)
            if callable(fn):
                try:
                    fn(bool(fast_interactive))
                    applied["vk_fast_interactive"] = bool(fast_interactive)
                except Exception:
                    pass
    return applied


def _get_camera_info(viewer: Any) -> tuple[str, bool]:
    cur_fn = getattr(viewer, "curViewport", None)
    if not callable(cur_fn):
        return "", False
    try:
        vp = cur_fn()
    except Exception:
        return "", False
    if vp is None:
        return "", False

    camera_path = ""
    cam_path_fn = getattr(vp, "cameraPath", None)
    if callable(cam_path_fn):
        try:
            raw = cam_path_fn()
            if raw:
                camera_path = str(raw).strip()
        except Exception:
            pass
    if not camera_path:
        cam_fn = getattr(vp, "camera", None)
        if callable(cam_fn):
            try:
                cam = cam_fn()
                if cam is not None:
                    if isinstance(cam, str):
                        camera_path = cam.strip()
                    elif callable(getattr(cam, "path", None)):
                        camera_path = str(cam.path()).strip()
                    else:
                        camera_path = str(cam).strip()
            except Exception:
                pass

    camera_locked = False
    locked_fn = getattr(vp, "isCameraLockedToView", None)
    if callable(locked_fn):
        try:
            camera_locked = bool(locked_fn())
        except Exception:
            camera_locked = False

    return camera_path, camera_locked


def _get_stage_serial(viewer: Any, display_lop: Any = None) -> int | None:
    if viewer is not None:
        fn = getattr(viewer, "stageSerial", None)
        if callable(fn):
            try:
                val = fn()
                if val is not None and not isinstance(val, bool):
                    return int(val)
            except Exception:
                pass
    if display_lop is not None:
        for method_name in ("stageSerial", "stageGenId"):
            fn = getattr(display_lop, method_name, None)
            if callable(fn):
                try:
                    val = fn()
                    if val is not None and not isinstance(val, bool):
                        return int(val)
                except Exception:
                    continue
    return None


def _read_env_var(name: str, hou_module: Any = None) -> str | None:
    val = os.environ.get(name)
    if val is not None and val.strip() != "":
        return val.strip()
    if hou_module is not None:
        getenv_fn = getattr(hou_module, "getenv", None)
        if callable(getenv_fn):
            try:
                hval = getenv_fn(name)
                if hval is not None and str(hval).strip() != "":
                    return str(hval).strip()
            except Exception:
                pass
    return None


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().casefold() not in ("", "0", "false", "no", "off")


def _summarize_xpu_devices(hou_module: Any = None) -> tuple[str, HardwareSnapshot]:
    hw = probe_hardware()
    devices = _read_env_var("KARMA_XPU_DEVICES", hou_module)
    no_optix = _is_truthy_env(_read_env_var("KARMA_XPU_DISABLE_OPTIX_DEVICE", hou_module))
    no_embree = _is_truthy_env(_read_env_var("KARMA_XPU_DISABLE_EMBREE_DEVICE", hou_module))

    parts: list[str] = []
    if hw.gpus:
        parts.append(hw.summary_badge)
    if devices:
        parts.append(f"env={devices}")
    if no_optix:
        parts.append("OptiX=off")
    if no_embree:
        parts.append("Embree=off")
    summary = " · ".join(parts) if parts else hw.summary_badge
    return summary, hw


def _telemetry_note_from_receipt(receipt: OverrideReceipt | None) -> str:
    if receipt is None:
        return UNKNOWN
    cook_delta = int(receipt.cook_count_after) - int(receipt.cook_count_before)
    mismatch_note = f", mismatches={len(receipt.mismatches)}" if receipt.mismatches else ""
    return (
        f"apply={receipt.apply_ms:.2f}ms, written={len(receipt.written_attrs)}, "
        f"cook_delta={cook_delta}{mismatch_note}"
    )


class KarmaSessionController:
    """Real-time Solaris KarmaRT lookdev session controller."""

    def __init__(
        self,
        hou_module: Any = None,
        *,
        clock: Callable[[], float] | None = None,
        command_timeout: float = 10.0,
        ledger_path: str | os.PathLike[str] | None = None,
        auto_turbo_config: AutoTurboConfig | None = None,
    ) -> None:
        self._hou_injected: bool = hou_module is not None
        self._hou_module: Any = hou_module
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._command_timeout: float = max(0.05, float(command_timeout))

        self.ledger: LatencyLedger = LatencyLedger(ledger_path=ledger_path)
        self.engine: SessionOverrideEngine = SessionOverrideEngine(hou_module=hou_module)
        self.auto_turbo: AutoTurboEngine = AutoTurboEngine(
            config=auto_turbo_config,
            clock=self._clock,
        )

        self._catalog: H22Catalog | None = None
        self._last_preflight: PreflightReport | None = None
        self._ref_grid_guard: ReferenceGridGuard = ReferenceGridGuard(
            hou_getter=self._get_hou_optional,
            same_pane=_same_pane,
        )
        self._hip_guard: HipLifecycleGuard = HipLifecycleGuard(
            self.engine,
            hou_module=hou_module,
            teardown_hook=self._on_hip_teardown,
        )

        # Independent per-control re-entry locks so restore() and emergency_reset()
        # are NEVER blocked by any other in-flight operation.
        self._locks: dict[str, bool] = {
            "apply": False,
            "pause": False,
            "restore": False,
            "emergency": False,
            "turbo": False,
        }

        self._leases: list[_ViewerLease] = []
        self._auto_turbo_prefs: dict[str, bool] = {}
        self._show_stats_prefs: dict[str, bool] = {}
        self._focus_crop_prefs: dict[str, str] = {}
        self._res_scale_prefs: dict[str, float] = {}
        self._saved_panes_follow: bool | None = None
        self._unleased_yielded_keys: set[str] = set()
        self._listeners: list[Callable[[SessionSnapshot], Any]] = []

    def _now(self) -> float:
        return float(self._clock())

    def _get_hou_optional(self) -> Any:
        if self._hou_injected:
            return self._hou_module
        if self._hou_module is not None:
            return self._hou_module
        try:
            import hou  # type: ignore[import-not-found]

            self._hou_module = hou
            return hou
        except Exception:
            return None

    def _require_hou(self) -> Any:
        hou = self._get_hou_optional()
        if hou is None:
            raise KarmaSessionError("Houdini 'hou' module is not available.")
        return hou

    @property
    def catalog(self) -> H22Catalog:
        if self._catalog is None:
            self._catalog = load_h22_catalog(hou_module=self._get_hou_optional())
        return self._catalog

    def add_listener(self, callback: Callable[[SessionSnapshot], Any]) -> None:
        if callable(callback) and callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[SessionSnapshot], Any]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _notify_listeners(self, snapshot: SessionSnapshot) -> None:
        for listener in list(self._listeners):
            try:
                listener(snapshot)
            except Exception as exc:
                logger.debug("KarmaSessionController listener raised error: %s", exc)

    def _ensure_hip_guard_installed(self) -> None:
        if self._hip_guard.installed:
            return
        hou = self._get_hou_optional()
        if hou is None or not hasattr(hou, "hipFile"):
            return
        if not callable(getattr(hou.hipFile, "addEventCallback", None)):
            return
        try:
            self._hip_guard.install()
        except Exception as exc:
            logger.debug("HipLifecycleGuard install skipped: %s", exc)

    def _find_lease(self, viewer: Any) -> _ViewerLease | None:
        stale: list[_ViewerLease] = []
        matched: _ViewerLease | None = None
        for lease in self._leases:
            try:
                if _same_pane(lease.viewer, viewer):
                    matched = lease
                    break
            except Exception:
                stale.append(lease)
        for item in stale:
            if item in self._leases:
                self._leases.remove(item)
        return matched

    def _other_active_override_leases(
        self,
        lopnet_path: str,
        *,
        exclude: _ViewerLease | None = None,
    ) -> list[_ViewerLease]:
        return [
            lease
            for lease in self._leases
            if lease is not exclude
            and lease.lopnet_path == lopnet_path
            and lease.override_active
        ]

    def _register_hip_guard_for_lease(self, lease: _ViewerLease) -> None:
        self._ensure_hip_guard_installed()
        if not lease.lopnet_path:
            return

        def _reapply_current() -> None:
            if lease not in self._leases or not lease.override_active:
                return
            hou = self._get_hou_optional()
            if hou is None:
                return
            lopnet_node = hou.node(lease.lopnet_path)
            if lopnet_node is None:
                return
            display_node = (
                hou.node(lease.display_lop_path) if lease.display_lop_path else None
            )
            if lease.turbo_engaged:
                overrides = unwrap_typed_overrides(
                    build_turbo_merged_overrides(lease.base_typed_overrides, self.catalog)
                )
                preset_tag = f"{lease.active_preset_id}:turbo"
                allow_restart = False
            else:
                overrides = dict(lease.active_overrides)
                preset_tag = lease.active_preset_id
                allow_restart = lease.allow_restart
            if overrides:
                self.engine.apply_overrides(
                    lopnet_node,
                    overrides=overrides,
                    preset_id=preset_tag,
                    session_id=lease.session_id,
                    catalog=self.catalog,
                    display_lop=display_node,
                    allow_restart=allow_restart,
                )

        current_overrides = (
            unwrap_typed_overrides(
                build_turbo_merged_overrides(lease.base_typed_overrides, self.catalog)
            )
            if lease.turbo_engaged
            else dict(lease.active_overrides)
        )
        self._hip_guard.register_active(
            lease.lopnet_path,
            {
                "display_lop_path": lease.display_lop_path,
                "session_id": lease.session_id,
                "preset_id": lease.active_preset_id,
                "overrides": current_overrides,
                "catalog": self.catalog,
                "allow_restart": lease.allow_restart,
                "reapply": _reapply_current,
            },
        )

    def _unregister_hip_guard_if_unused(
        self,
        lopnet_path: str,
        *,
        exclude: _ViewerLease | None = None,
    ) -> None:
        if not lopnet_path:
            return
        remaining = self._other_active_override_leases(lopnet_path, exclude=exclude)
        if remaining:
            self._register_hip_guard_for_lease(remaining[0])
        else:
            self._hip_guard.unregister_active(lopnet_path)

    def _available_delegates(self, viewer: Any = None) -> tuple[str, ...]:
        hou = self._get_hou_optional()
        labels: list[str] = []
        seen: set[str] = set()

        def _add(label: str) -> None:
            cleaned = str(label).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                labels.append(cleaned)

        if hou is not None:
            lop_mod = getattr(hou, "lop", None)
            if lop_mod is not None:
                lbl_fn = getattr(lop_mod, "availableRendererLabels", None)
                if callable(lbl_fn):
                    try:
                        for lbl in lbl_fn() or ():
                            _add(str(lbl))
                    except Exception:
                        pass
                had_labels = bool(labels)
                name_fn = getattr(lop_mod, "availableRendererNames", None)
                if callable(name_fn):
                    try:
                        for nm in name_fn() or ():
                            nm_str = str(nm).strip()
                            if not had_labels:
                                mapped = (
                                    self.catalog.renderer_labels.get(nm_str)
                                    or DEFAULT_RENDERER_LABELS.get(nm_str)
                                    or nm_str
                                )
                                _add(mapped)
                    except Exception:
                        pass

        if viewer is not None:
            hr_fn = getattr(viewer, "hydraRenderers", None)
            if callable(hr_fn):
                try:
                    for entry in hr_fn() or ():
                        entry_str = str(entry).strip()
                        if not entry_str:
                            continue
                        mapped = (
                            self.catalog.renderer_labels.get(entry_str)
                            or DEFAULT_RENDERER_LABELS.get(entry_str)
                            or entry_str
                        )
                        _add(mapped)
                except Exception:
                    pass

        return tuple(labels)

    def _recommended_denoiser(self, viewer: Any = None) -> str:
        hou = self._get_hou_optional()
        optix_disabled = _is_truthy_env(_read_env_var("KARMA_XPU_DISABLE_OPTIX_DEVICE", hou))
        devices_val = (_read_env_var("KARMA_XPU_DEVICES", hou) or "").strip().casefold()
        if devices_val in ("cpu", "embree"):
            optix_disabled = True
        return "oidn" if optix_disabled else "optix"

    def list_solaris_viewers(self) -> tuple[Any, ...]:
        """Return all open Solaris SceneViewers (``isViewingSceneGraph() == True``)."""
        hou = self._get_hou_optional()
        if hou is None:
            return ()
        is_ui_fn = getattr(hou, "isUIAvailable", None)
        if callable(is_ui_fn):
            try:
                if not is_ui_fn():
                    return ()
            except Exception:
                return ()
        ui = getattr(hou, "ui", None)
        if ui is None or not callable(getattr(ui, "curDesktop", None)):
            return ()
        try:
            desktop = ui.curDesktop()
        except Exception:
            return ()
        if desktop is None or not callable(getattr(desktop, "paneTabs", None)):
            return ()

        solaris: list[Any] = []
        try:
            tabs = desktop.paneTabs() or ()
        except Exception:
            return ()

        for tab in tabs:
            if not _is_scene_viewer_tab(tab, hou):
                continue
            if not _is_viewing_scene_graph(tab):
                continue
            if not any(_same_pane(existing, tab) for existing in solaris):
                solaris.append(tab)
        return tuple(solaris)

    def resolve_viewer(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> Any:
        """Resolve an unambiguous Solaris SceneViewer or raise ``KarmaSessionError``."""
        _assert_main_thread()

        if viewer is not None:
            if callable(getattr(viewer, "isViewingSceneGraph", None)) and not _is_viewing_scene_graph(
                viewer
            ):
                raise KarmaSessionError(
                    f"SceneViewer {_safe_viewer_name(viewer)!r} is not viewing a Solaris LOP "
                    "Scene Graph (isViewingSceneGraph() is False)."
                )
            return viewer

        hou = self._require_hou()
        is_ui_fn = getattr(hou, "isUIAvailable", None)
        if callable(is_ui_fn):
            try:
                if not is_ui_fn():
                    raise KarmaSessionError("Houdini UI is unavailable (headless session).")
            except KarmaSessionError:
                raise
            except Exception as exc:
                raise KarmaSessionError(f"Failed to query hou.isUIAvailable(): {exc}") from exc

        solaris_viewers = self.list_solaris_viewers()

        if viewer_name is not None and str(viewer_name).strip():
            target_name = str(viewer_name).strip()
            for candidate in solaris_viewers:
                if _safe_viewer_name(candidate) == target_name:
                    return candidate
            # Check if a non-Solaris SceneViewer exists with that name for a clearer error.
            ui = getattr(hou, "ui", None)
            desktop = ui.curDesktop() if ui is not None and callable(getattr(ui, "curDesktop", None)) else None
            if desktop is not None and callable(getattr(desktop, "paneTabs", None)):
                try:
                    for tab in desktop.paneTabs() or ():
                        if _is_scene_viewer_tab(tab, hou) and _safe_viewer_name(tab) == target_name:
                            raise KarmaSessionError(
                                f"SceneViewer {target_name!r} is not viewing a Solaris LOP Scene Graph "
                                "(isViewingSceneGraph() is False)."
                            )
                except KarmaSessionError:
                    raise
                except Exception:
                    pass
            raise KarmaSessionError(
                f"No Solaris SceneViewer found with name {target_name!r}."
            )

        if len(solaris_viewers) == 0:
            raise KarmaSessionError(
                "No Solaris SceneViewer (isViewingSceneGraph() == True) found on the active desktop."
            )

        if len(solaris_viewers) == 1:
            return solaris_viewers[0]

        # Multiple Solaris viewers: accept an explicit cursor match, otherwise refuse ambiguity.
        ui = getattr(hou, "ui", None)
        desktop = ui.curDesktop() if ui is not None and callable(getattr(ui, "curDesktop", None)) else None
        under_cursor = None
        if desktop is not None and callable(getattr(desktop, "paneTabUnderCursor", None)):
            try:
                under_cursor = desktop.paneTabUnderCursor()
            except Exception:
                under_cursor = None

        if under_cursor is not None:
            for candidate in solaris_viewers:
                if _same_pane(candidate, under_cursor):
                    return candidate

        names = ", ".join(_safe_viewer_name(v) or "<unnamed>" for v in solaris_viewers)
        raise KarmaSessionError(
            f"Ambiguous Solaris viewer selection: {len(solaris_viewers)} Solaris SceneViewers "
            f"are open ({names}). Specify viewer or viewer_name explicitly."
        )

    def _resolve_lopnet_and_display(self, viewer: Any) -> tuple[Any, Any]:
        viewer_name = _safe_viewer_name(viewer) or "<viewer>"
        pwd_fn = getattr(viewer, "pwd", None)
        if not callable(pwd_fn):
            raise KarmaSessionError(
                f"Solaris viewer {viewer_name!r} does not expose pwd()."
            )
        try:
            pwd_node = pwd_fn()
        except Exception as exc:
            raise KarmaSessionError(
                f"Failed to query pwd() on Solaris viewer {viewer_name!r}: {exc}"
            ) from exc

        if pwd_node is None:
            raise KarmaSessionError(
                f"Solaris viewer {viewer_name!r} has no current LOP network (pwd() returned None)."
            )

        lopnet = pwd_node
        if not hasattr(lopnet, "viewportOverrides"):
            parent_fn = getattr(pwd_node, "parent", None)
            parent = parent_fn() if callable(parent_fn) else None
            if parent is not None and hasattr(parent, "viewportOverrides"):
                lopnet = parent
            elif not hasattr(lopnet, "displayNode"):
                raise KarmaSessionError(
                    f"Node {pwd_node.path()!r} in Solaris viewer {viewer_name!r} is not a LOP network."
                )

        display_fn = getattr(lopnet, "displayNode", None)
        display_lop = None
        if callable(display_fn):
            try:
                display_lop = display_fn()
            except Exception as exc:
                raise KarmaSessionError(
                    f"Failed to query displayNode() on LOP network {lopnet.path()!r}: {exc}"
                ) from exc

        if display_lop is None:
            lopnet_path = str(lopnet.path()) if callable(getattr(lopnet, "path", None)) else repr(lopnet)
            raise KarmaSessionError(
                f"LOP network {lopnet_path!r} has no display node set. "
                "Set the display flag on a LOP node before running KarmaRT."
            )

        return lopnet, display_lop

    def _subscribe_viewer_events(self, lease: _ViewerLease) -> None:
        if lease.event_callback is not None:
            return
        add_cb = getattr(lease.viewer, "addEventCallback", None)
        if not callable(add_cb):
            return

        viewer_ref = lease.viewer

        def _callback(*args: Any, **kwargs: Any) -> None:
            self._handle_viewer_event(viewer_ref, *args, **kwargs)

        try:
            add_cb(_callback)
            lease.event_callback = _callback
            return
        except TypeError:
            pass
        except Exception as exc:
            logger.debug("viewer.addEventCallback(cb) failed: %s", exc)
            return

        # Alternate signature (event_types, callback)
        hou = self._get_hou_optional()
        ev_enum = getattr(hou, "sceneViewerEvent", None) if hou is not None else None
        event_types: tuple[Any, ...] = tuple(
            getattr(ev_enum, name)
            for name in _SUBSCRIBED_VIEWER_EVENTS
            if ev_enum is not None and hasattr(ev_enum, name)
        )
        if not event_types:
            event_types = _SUBSCRIBED_VIEWER_EVENTS
        try:
            add_cb(event_types, _callback)
            lease.event_callback = _callback
        except Exception as exc:
            logger.debug("viewer.addEventCallback(events, cb) failed: %s", exc)

    def _unsubscribe_viewer_events(self, lease: _ViewerLease) -> None:
        cb = lease.event_callback
        lease.event_callback = None
        if cb is None:
            return
        rem_cb = getattr(lease.viewer, "removeEventCallback", None)
        if not callable(rem_cb):
            return
        try:
            rem_cb(cb)
        except TypeError:
            hou = self._get_hou_optional()
            ev_enum = getattr(hou, "sceneViewerEvent", None) if hou is not None else None
            event_types: tuple[Any, ...] = tuple(
                getattr(ev_enum, name)
                for name in _SUBSCRIBED_VIEWER_EVENTS
                if ev_enum is not None and hasattr(ev_enum, name)
            ) or _SUBSCRIBED_VIEWER_EVENTS
            try:
                rem_cb(event_types, cb)
            except Exception:
                pass
        except Exception:
            pass

    def _handle_viewer_event(self, viewer_ref: Any, *args: Any, **kwargs: Any) -> None:
        raw_event = kwargs.get("event_type", kwargs.get("event"))
        if raw_event is None and args:
            if len(args) >= 2 and _same_pane(args[0], viewer_ref):
                raw_event = args[1]
            else:
                raw_event = args[0]

        event_name = _normalize_event_name(raw_event)
        if not event_name:
            return
        folded = event_name.casefold()

        lease = self._find_lease(viewer_ref)
        if lease is None:
            return

        if folded in ("resizing", "beginresize", "endresize", "stateentered", "stateexited"):
            if lease.auto_turbo_enabled and lease.state in (
                SessionState.ACTIVE_IDLE,
                SessionState.ACTIVE_TURBO_INTERACT,
            ):
                self.auto_turbo.notify_interaction_event(
                    lease.viewer_key,
                    event_name,
                    now=self._now(),
                )
            return

        if folded == "viewerterminated":
            self._handle_viewer_terminated(lease)
            return

        if folded == "rendererchanged":
            if self._locks["restore"] or self._locks["apply"] or self._locks["emergency"]:
                return
            observed = _get_viewer_renderer(viewer_ref)
            if observed and lease.owned_delegate and not _delegates_match(
                observed, lease.owned_delegate
            ):
                self._handle_external_takeover(lease, observed, trigger="RendererChanged")

    def _clear_lease_overrides_if_unshared(self, lease: _ViewerLease) -> dict[str, Any]:
        if self._other_active_override_leases(lease.lopnet_path, exclude=lease):
            self._unregister_hip_guard_if_unused(lease.lopnet_path, exclude=lease)
            return {"cleared": False, "reason": "shared_with_other_viewer", "removed_count": 0}

        self._unregister_hip_guard_if_unused(lease.lopnet_path, exclude=lease)
        if not lease.lopnet_path:
            return {"cleared": True, "removed_count": 0}

        hou = self._get_hou_optional()
        lopnet_node = None
        display_node = None
        if hou is not None and callable(getattr(hou, "node", None)):
            try:
                lopnet_node = hou.node(lease.lopnet_path)
            except Exception:
                lopnet_node = None
            if lease.display_lop_path:
                try:
                    display_node = hou.node(lease.display_lop_path)
                except Exception:
                    display_node = None
        if lopnet_node is None:
            try:
                lopnet_node, display_node = self._resolve_lopnet_and_display(lease.viewer)
            except Exception:
                return {"cleared": False, "reason": "lopnet_unavailable", "removed_count": 0}

        try:
            return self.engine.clear_owned_overrides(
                lopnet_node,
                display_lop=display_node,
                session_id=None,
            )
        except Exception as exc:
            logger.debug("clear_owned_overrides failed on %s: %s", lease.lopnet_path, exc)
            return {"cleared": False, "error": str(exc), "removed_count": 0}

    def _handle_external_takeover(
        self,
        lease: _ViewerLease,
        observed_delegate: str,
        *,
        trigger: str,
    ) -> SessionSnapshot:
        """Transition through EXTERNAL_TAKEOVER, clean owned LOP:Custom specs, drop lease."""
        lease.state = SessionState.EXTERNAL_TAKEOVER
        lease.pending_pause = None
        lease.turbo_engaged = False

        clear_result = self._clear_lease_overrides_if_unshared(lease)
        lease.override_active = False

        try:
            self._ref_grid_guard.release(lease.viewer)
        except Exception:
            pass
        self.auto_turbo.reset(lease.viewer_key)
        self._unsubscribe_viewer_events(lease)
        if lease in self._leases:
            self._leases.remove(lease)

        msg = (
            f"External renderer takeover detected ({observed_delegate!r} replaced "
            f"{lease.owned_delegate!r}); KarmaRT cleaned owned LOP:Custom overrides "
            "and dropped its viewer lease without reverting your renderer."
        )
        stage_serial = _get_stage_serial(lease.viewer)
        self.ledger.record(
            event="external_takeover",
            decision=f"drop_lease_to_{observed_delegate}",
            via=trigger,
            proof=(
                f"cleared={clear_result.get('cleared')}; "
                f"removed_count={clear_result.get('removed_count', 0)}"
            ),
            stage_serial=stage_serial,
            preset_id=lease.active_preset_id,
            observed_delegate=observed_delegate,
            metrics={"previous_owned_delegate": lease.owned_delegate},
        )

        cam_path, cam_locked = _get_camera_info(lease.viewer)
        xpu_summary, hw_snap = _summarize_xpu_devices(self._get_hou_optional())
        snapshot = SessionSnapshot(
            state=SessionState.EXTERNAL_TAKEOVER,
            viewer_name=lease.viewer_name,
            lopnet_path=lease.lopnet_path,
            display_lop_path=lease.display_lop_path,
            settings_prim_path=lease.settings_prim_path,
            observed_delegate=observed_delegate,
            previous_renderer=lease.previous_renderer,
            active_preset_id="",
            auto_turbo_enabled=self._auto_turbo_prefs.get(lease.viewer_key, lease.auto_turbo_enabled),
            turbo_engaged=False,
            paused=_get_viewer_paused(lease.viewer),
            pending_op="",
            override_active=False,
            final_render_untouched=True,
            camera_path=cam_path,
            camera_locked=cam_locked,
            clean_overlays_active=False,
            denoiser_mode=lease.denoiser_mode,
            xpu_device_summary=xpu_summary,
            stage_serial=stage_serial,
            last_receipt=lease.last_receipt,
            last_error="",
            message=msg,
            telemetry_note=_telemetry_note_from_receipt(lease.last_receipt),
            available_delegates=self._available_delegates(lease.viewer),
            show_render_stats=bool(_get_show_render_time(lease.viewer)),
            vram_eviction_risk=hw_snap.vram_eviction_risk,
            optix_cache_ready=hw_snap.optix_cache_ready,
            viewport_frozen=bool(_get_stage_locked(lease.viewer)),
            stage_pinned=(_get_panes_follow_current_node(self._get_hou_optional()) is False),
        )
        self._notify_listeners(snapshot)
        return snapshot

    def _handle_viewer_terminated(self, lease: _ViewerLease) -> None:
        self._clear_lease_overrides_if_unshared(lease)
        try:
            self._ref_grid_guard.release(lease.viewer)
        except Exception:
            pass
        self.auto_turbo.reset(lease.viewer_key)
        self._unsubscribe_viewer_events(lease)
        if lease in self._leases:
            self._leases.remove(lease)

        self.ledger.record(
            event="viewer_terminated",
            decision="drop_lease_clean_overrides",
            via="ViewerTerminated",
            proof=f"viewer={lease.viewer_name}",
            preset_id=lease.active_preset_id,
            observed_delegate=lease.owned_delegate,
        )
        snapshot = SessionSnapshot(
            state=SessionState.READY_OFF,
            viewer_name=lease.viewer_name,
            lopnet_path=lease.lopnet_path,
            display_lop_path=lease.display_lop_path,
            override_active=False,
            final_render_untouched=True,
            message=f"Solaris viewer {lease.viewer_name!r} closed; KarmaRT cleaned owned overrides.",
            available_delegates=self._available_delegates(),
        )
        self._notify_listeners(snapshot)

    def _on_hip_teardown(self, event_type: Any) -> None:
        leases = list(self._leases)
        self._leases.clear()
        self._unleased_yielded_keys.clear()
        for lease in leases:
            self._unsubscribe_viewer_events(lease)
        self._ref_grid_guard.release_all()
        self.auto_turbo.reset_all()

        event_name = _normalize_event_name(event_type) or "hip_teardown"
        self.ledger.record(
            event="hip_teardown",
            decision="release_all_leases",
            via=event_name,
            proof=f"released_leases={len(leases)}",
        )
        self._notify_listeners(
            SessionSnapshot(
                state=SessionState.READY_OFF,
                override_active=False,
                final_render_untouched=True,
                message=f"Scene lifecycle event ({event_name}) released KarmaRT session overrides.",
                available_delegates=self._available_delegates(),
            )
        )

    def _reconcile_pending_pause(
        self,
        lease: _ViewerLease,
        viewer: Any,
        *,
        now: float | None = None,
    ) -> bool:
        """Check whether an in-flight pause/unpause confirmed or timed out (never replays)."""
        pending = lease.pending_pause
        if pending is None:
            return False

        ts = float(self._now() if now is None else now)
        observed_paused = _get_viewer_paused(viewer)

        if observed_paused == pending.target_paused:
            lease.pending_pause = None
            lease.paused = pending.target_paused
            lease.last_error = ""
            if pending.reason == "disk_render_yield" and pending.target_paused:
                lease.state = SessionState.ACTIVE_DISK_RENDER_YIELD
                lease.message = "Interactive viewport paused for disk render yield."
            elif pending.target_paused:
                lease.state = SessionState.ACTIVE_PAUSED
                lease.message = "Karma interactive viewport paused."
            else:
                lease.state = SessionState.ACTIVE_IDLE
                lease.message = "Karma interactive viewport resumed."

            self.ledger.record(
                event="pause_confirmed",
                decision=f"paused={pending.target_paused}",
                via="isRendererPaused",
                proof=f"confirmed_in_ms={(ts - pending.issued_at) * 1000.0:.1f}",
                stage_serial=_get_stage_serial(viewer),
                preset_id=lease.active_preset_id,
                observed_delegate=_get_viewer_renderer(viewer) or lease.owned_delegate,
            )
            return True

        if ts >= pending.deadline:
            elapsed_s = max(0.0, ts - pending.issued_at)
            target = pending.target_paused
            lease.pending_pause = None
            lease.state = SessionState.DEGRADED
            lease.last_error = (
                f"Timed out after {elapsed_s:.2f}s waiting for Houdini to confirm "
                f"setRendererPaused({target}). Command was not replayed."
            )
            lease.message = lease.last_error
            self.ledger.record(
                event="pause_timeout",
                decision="transition_degraded_no_replay",
                via="command_timeout",
                proof=f"target_paused={target}; observed_paused={observed_paused}; elapsed_s={elapsed_s:.2f}",
                stage_serial=_get_stage_serial(viewer),
                preset_id=lease.active_preset_id,
                observed_delegate=_get_viewer_renderer(viewer) or lease.owned_delegate,
            )
            return True

        return False

    def _build_snapshot(
        self,
        viewer: Any,
        *,
        lease: _ViewerLease | None = None,
        state_override: str | None = None,
        pending_op_override: str | None = None,
        message_override: str | None = None,
        error_override: str | None = None,
    ) -> SessionSnapshot:
        v_name = _safe_viewer_name(viewer)
        v_key = _viewer_key(viewer)
        observed_delegate = _get_viewer_renderer(viewer)
        available_delegates = self._available_delegates(viewer)
        cam_path, cam_locked = _get_camera_info(viewer)
        hou = self._get_hou_optional()
        xpu_summary, hw_snap = _summarize_xpu_devices(hou)
        live_frozen = bool(_get_stage_locked(viewer))
        live_panes_follow = _get_panes_follow_current_node(hou)
        live_stage_pinned = live_panes_follow is False

        lopnet_path = lease.lopnet_path if lease is not None else ""
        display_lop_path = lease.display_lop_path if lease is not None else ""
        settings_prim_path = lease.settings_prim_path if lease is not None else ""
        lopnet_node = None
        display_lop = None

        if not lopnet_path or not display_lop_path:
            try:
                lopnet_node, display_lop = self._resolve_lopnet_and_display(viewer)
                lopnet_path = str(lopnet_node.path())
                display_lop_path = str(display_lop.path())
                if not settings_prim_path:
                    try:
                        binding = self.engine.resolve_binding(lopnet_node, display_lop=display_lop)
                        settings_prim_path = binding.settings_prim_path
                    except Exception:
                        pass
            except Exception:
                pass
        elif hou is not None and callable(getattr(hou, "node", None)):
            try:
                lopnet_node = hou.node(lopnet_path)
                display_lop = hou.node(display_lop_path)
            except Exception:
                display_lop = None

        solo_geo_count = 0
        solo_light_count = 0
        if lopnet_node is not None and display_lop is not None:
            try:
                solo_state = self.engine.get_solo_state(lopnet_node, display_lop=display_lop)
                solo_geo_count = len(solo_state.get("geometry", ()))
                solo_light_count = len(solo_state.get("lights", ()))
            except Exception:
                pass

        stage_serial = _get_stage_serial(viewer, display_lop)
        live_paused = _get_viewer_paused(viewer)
        live_render_stats = _get_show_render_time(viewer)

        if lease is not None:
            pending_op = pending_op_override if pending_op_override is not None else ""
            if not pending_op and lease.pending_pause is not None:
                if lease.pending_pause.reason == "disk_render_yield":
                    pending_op = "yielding_for_disk_render"
                elif lease.pending_pause.reason == "disk_render_resume":
                    pending_op = "resuming_from_disk_render"
                else:
                    pending_op = "pausing" if lease.pending_pause.target_paused else "resuming"

            state = state_override if state_override is not None else lease.state
            return SessionSnapshot(
                state=state,
                viewer_name=v_name or lease.viewer_name,
                lopnet_path=lopnet_path,
                display_lop_path=display_lop_path,
                settings_prim_path=settings_prim_path,
                observed_delegate=observed_delegate or lease.owned_delegate,
                previous_renderer=lease.previous_renderer,
                active_preset_id=lease.active_preset_id,
                auto_turbo_enabled=lease.auto_turbo_enabled,
                turbo_engaged=lease.turbo_engaged,
                paused=live_paused if lease.pending_pause is None else lease.paused,
                pending_op=pending_op,
                override_active=lease.override_active,
                final_render_untouched=True,
                camera_path=cam_path,
                camera_locked=cam_locked,
                clean_overlays_active=lease.clean_overlays_active,
                denoiser_mode=lease.denoiser_mode,
                xpu_device_summary=xpu_summary,
                stage_serial=stage_serial,
                last_receipt=lease.last_receipt,
                last_error=error_override if error_override is not None else lease.last_error,
                message=message_override if message_override is not None else lease.message,
                telemetry_note=_telemetry_note_from_receipt(lease.last_receipt),
                available_delegates=available_delegates,
                show_render_stats=(
                    live_render_stats if live_render_stats is not None else lease.show_render_stats
                ),
                vram_eviction_risk=hw_snap.vram_eviction_risk,
                optix_cache_ready=hw_snap.optix_cache_ready,
                focus_crop=lease.focus_crop,
                resolution_scale=lease.resolution_scale,
                viewport_frozen=live_frozen,
                stage_pinned=live_stage_pinned,
                solo_geometry_count=solo_geo_count,
                solo_lights_count=solo_light_count,
                usd_image_filters_detected=lease.usd_image_filters_detected,
            )

        # Unleased viewer snapshot
        has_karma = (
            resolve_delegate_label(available_delegates, KARMA_XPU_ALIASES) is not None
            or resolve_delegate_label(available_delegates, KARMA_CPU_ALIASES) is not None
        )
        if state_override is not None:
            state = state_override
        elif v_key in self._unleased_yielded_keys and live_paused:
            state = SessionState.ACTIVE_DISK_RENDER_YIELD
        elif available_delegates and not has_karma:
            state = SessionState.UNAVAILABLE
        else:
            state = SessionState.READY_OFF

        show_lights = _get_show_lights(viewer)
        show_sel = _get_show_selection(viewer)
        clean_overlays = (show_lights is False) and (show_sel is False)

        default_msg = (
            message_override
            if message_override is not None
            else (
                "KarmaRT ready (off)."
                if state == SessionState.READY_OFF
                else (
                    "Interactive viewport yielded for disk render."
                    if state == SessionState.ACTIVE_DISK_RENDER_YIELD
                    else "No Karma Hydra delegate registered."
                )
            )
        )

        return SessionSnapshot(
            state=state,
            viewer_name=v_name,
            lopnet_path=lopnet_path,
            display_lop_path=display_lop_path,
            settings_prim_path=settings_prim_path,
            observed_delegate=observed_delegate,
            previous_renderer="",
            active_preset_id="",
            auto_turbo_enabled=self._auto_turbo_prefs.get(v_key, False),
            turbo_engaged=False,
            paused=live_paused,
            pending_op=pending_op_override or "",
            override_active=False,
            final_render_untouched=True,
            camera_path=cam_path,
            camera_locked=cam_locked,
            clean_overlays_active=clean_overlays,
            denoiser_mode=self._recommended_denoiser(viewer),
            xpu_device_summary=xpu_summary,
            stage_serial=stage_serial,
            last_receipt=None,
            last_error=error_override or "",
            message=default_msg,
            telemetry_note=UNKNOWN,
            available_delegates=available_delegates,
            show_render_stats=(
                live_render_stats
                if live_render_stats is not None
                else self._show_stats_prefs.get(v_key, False)
            ),
            vram_eviction_risk=hw_snap.vram_eviction_risk,
            optix_cache_ready=hw_snap.optix_cache_ready,
            focus_crop=self._focus_crop_prefs.get(v_key, "full"),
            resolution_scale=self._res_scale_prefs.get(v_key, 1.0),
            viewport_frozen=live_frozen,
            stage_pinned=live_stage_pinned,
            solo_geometry_count=solo_geo_count,
            solo_lights_count=solo_light_count,
        )

    def run_preflight(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> PreflightReport:
        """Run read-only Houdini 22 / Solaris / Karma preflight diagnostics."""
        _assert_main_thread()
        resolved_viewer = None
        if viewer is not None or viewer_name is not None:
            resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        else:
            try:
                resolved_viewer = self.resolve_viewer()
            except KarmaSessionError:
                resolved_viewer = None

        self._notify_listeners(
            SessionSnapshot(
                state=SessionState.PREFLIGHT_CHECKING,
                viewer_name=_safe_viewer_name(resolved_viewer),
                message="Running read-only Houdini 22 / KarmaRT preflight checks...",
                available_delegates=self._available_delegates(resolved_viewer),
            )
        )
        report = _run_preflight(
            hou_module=self._get_hou_optional(),
            viewer=resolved_viewer,
            catalog=self.catalog,
        )
        self._last_preflight = report
        return report

    def observe(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Observe live Solaris viewer and session state without mutating USD."""
        _assert_main_thread()
        try:
            resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        except KarmaSessionError as exc:
            xpu_summary, hw_snap = _summarize_xpu_devices(self._get_hou_optional())
            return SessionSnapshot(
                state=SessionState.UNAVAILABLE,
                last_error=str(exc),
                message=str(exc),
                xpu_device_summary=xpu_summary,
                vram_eviction_risk=hw_snap.vram_eviction_risk,
                optix_cache_ready=hw_snap.optix_cache_ready,
                available_delegates=self._available_delegates(),
            )

        lease = self._find_lease(resolved_viewer)
        if lease is not None:
            # Detect external delegate switch even if viewer didn't emit RendererChanged.
            observed = _get_viewer_renderer(resolved_viewer)
            if (
                observed
                and lease.owned_delegate
                and not _delegates_match(observed, lease.owned_delegate)
                and not self._locks["restore"]
                and not self._locks["apply"]
                and not self._locks["emergency"]
            ):
                return self._handle_external_takeover(lease, observed, trigger="observe_poll")

            changed = self._reconcile_pending_pause(lease, resolved_viewer)
            snapshot = self._build_snapshot(resolved_viewer, lease=lease)
            if changed:
                self._notify_listeners(snapshot)
            return snapshot

        return self._build_snapshot(resolved_viewer, lease=None)

    def _resolve_preset(
        self,
        preset_id: str,
        *,
        viewer: Any = None,
        denoiser_engine: str | None = None,
    ) -> tuple[PresetSpec, str]:
        resolved_denoiser = (
            str(denoiser_engine).strip().lower()
            if denoiser_engine is not None and str(denoiser_engine).strip()
            else self._recommended_denoiser(viewer)
        )
        if resolved_denoiser not in SUPPORTED_DENOISER_ENGINES:
            raise KarmaSessionError(
                f"Unsupported denoiser engine {denoiser_engine!r}; "
                f"expected one of {SUPPORTED_DENOISER_ENGINES}."
            )
        presets = get_presets(denoiser_engine=resolved_denoiser)
        preset = presets.get(str(preset_id).strip())
        if preset is None:
            valid_ids = ", ".join(sorted(presets.keys()))
            raise KarmaSessionError(
                f"Unknown KarmaRT preset {preset_id!r}. Valid presets: {valid_ids}."
            )
        return preset, resolved_denoiser

    def _resolve_target_delegate(
        self,
        preset: PresetSpec,
        available_delegates: tuple[str, ...],
        delegate_preference: str,
    ) -> tuple[str, str]:
        """Resolve a live Hydra delegate label and explicit fallback note (never silent)."""
        if preset.target_delegate_kind == "vulkan":
            vk_label = resolve_delegate_label(available_delegates, VULKAN_ALIASES)
            if vk_label is None:
                raise KarmaSessionError(
                    f"Houdini VK delegate is not registered for preset {preset.id!r}. "
                    f"Available delegates: {list(available_delegates)}"
                )
            return vk_label, ""

        pref = str(delegate_preference or "Karma XPU").strip()
        pref_is_cpu = pref.casefold() in _KARMA_CPU_ALIASES_FOLDED

        primary = resolve_delegate_label(available_delegates, pref)
        if primary is not None:
            return primary, ""

        fallback_aliases = KARMA_XPU_ALIASES if pref_is_cpu else KARMA_CPU_ALIASES
        fallback = resolve_delegate_label(available_delegates, fallback_aliases)
        if fallback is not None:
            warning = (
                f"Preferred delegate {pref!r} unavailable; explicitly fell back to {fallback!r}."
            )
            return fallback, warning

        raise KarmaSessionError(
            f"No Karma Hydra delegate (Karma XPU or Karma CPU) is registered for preset "
            f"{preset.id!r}. Available delegates: {list(available_delegates)}"
        )

    def _compose_typed_overrides(
        self,
        preset_usd_overrides: dict[str, tuple[str, Any]],
        *,
        lopnet: Any,
        display_lop: Any,
        focus_crop: str,
        resolution_scale: float,
    ) -> tuple[dict[str, tuple[str, Any]], bool]:
        """Compose zero-restart preset overrides + double-denoise guard + Focus Crop + Res Scale."""
        try:
            binding = self.engine.resolve_binding(lopnet, display_lop=display_lop)
            settings_path = binding.settings_prim_path
        except Exception:
            settings_path = ""

        has_usd_filters = False
        try:
            has_usd_filters = self.engine.has_usd_image_filters(display_lop, settings_path)
        except Exception:
            has_usd_filters = False

        composed = strip_builtin_denoiser_if_usd_image_filters(
            preset_usd_overrides,
            has_usd_image_filters=has_usd_filters,
        )
        if focus_crop and focus_crop != "full":
            composed.update(build_focus_crop_override(focus_crop))
        if abs(float(resolution_scale) - 1.0) >= 1e-4:
            prod_res = self.engine.read_production_resolution(display_lop, settings_path)
            if prod_res is None:
                prod_res = (1280, 720)
            composed.update(build_resolution_scale_override(prod_res, resolution_scale))
        return composed, has_usd_filters

    def preview_preset(
        self,
        preset_id: str,
        viewer: Any = None,
        viewer_name: str | None = None,
        denoiser_engine: str | None = None,
    ) -> OverridePreview:
        """Pure read-only dry-run diff preview of a preset against the active Solaris stage."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lopnet, display_lop = self._resolve_lopnet_and_display(resolved_viewer)
        preset, _ = self._resolve_preset(
            preset_id,
            viewer=resolved_viewer,
            denoiser_engine=denoiser_engine,
        )

        lease = self._find_lease(resolved_viewer)
        v_key = _viewer_key(resolved_viewer)
        focus_crop = lease.focus_crop if lease is not None else self._focus_crop_prefs.get(v_key, "full")
        res_scale = lease.resolution_scale if lease is not None else self._res_scale_prefs.get(v_key, 1.0)
        self._notify_listeners(
            self._build_snapshot(
                resolved_viewer,
                lease=lease,
                state_override=SessionState.PREVIEW_DIFF,
                message_override=f"Previewing zero-restart override diff for preset {preset.id!r}.",
            )
        )

        if preset.clears_overrides or preset.target_delegate_kind == "vulkan":
            composed = dict(preset.usd_overrides)
        else:
            composed, _ = self._compose_typed_overrides(
                preset.usd_overrides,
                lopnet=lopnet,
                display_lop=display_lop,
                focus_crop=focus_crop,
                resolution_scale=res_scale,
            )
        unwrapped = unwrap_typed_overrides(composed)
        try:
            return self.engine.preview_overrides(
                lopnet,
                overrides=unwrapped,
                preset_id=preset.id,
                catalog=self.catalog,
                display_lop=display_lop,
            )
        except SessionOverrideError as exc:
            raise KarmaSessionError(str(exc)) from exc

    def activate_preset(
        self,
        preset_id: str,
        viewer: Any = None,
        viewer_name: str | None = None,
        delegate_preference: str = "Karma XPU",
        allow_restart: bool = False,
        denoiser_engine: str | None = None,
    ) -> SessionSnapshot:
        """Activate a KarmaRT preset on a Solaris SceneViewer without mutating production USD."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)

        if self._locks["apply"]:
            return self._build_snapshot(
                resolved_viewer,
                lease=self._find_lease(resolved_viewer),
                pending_op_override="applying",
                message_override="Preset apply already in progress.",
            )

        self._locks["apply"] = True
        t0 = time.perf_counter()
        try:
            lopnet, display_lop = self._resolve_lopnet_and_display(resolved_viewer)
            preset, resolved_denoiser = self._resolve_preset(
                preset_id,
                viewer=resolved_viewer,
                denoiser_engine=denoiser_engine,
            )

            if not allow_restart:
                try:
                    validate_preset_zero_restart(preset, self.catalog)
                except ValueError as exc:
                    raise KarmaSessionError(str(exc)) from exc

            available_delegates = self._available_delegates(resolved_viewer)
            target_label, fallback_warning = self._resolve_target_delegate(
                preset,
                available_delegates,
                delegate_preference,
            )

            v_key = _viewer_key(resolved_viewer)
            v_name = _safe_viewer_name(resolved_viewer)
            lopnet_path = str(lopnet.path())
            display_lop_path = str(display_lop.path())
            self._unleased_yielded_keys.discard(v_key)

            lease = self._find_lease(resolved_viewer)
            is_new_lease = lease is None
            if lease is None:
                lease = _ViewerLease(
                    viewer=resolved_viewer,
                    viewer_key=v_key,
                    viewer_name=v_name,
                    session_id=uuid.uuid4().hex[:12],
                    lopnet_path=lopnet_path,
                    display_lop_path=display_lop_path,
                    auto_turbo_enabled=self._auto_turbo_prefs.get(v_key, False),
                    previous_renderer=_get_viewer_renderer(resolved_viewer),
                    previous_paused=_get_viewer_paused(resolved_viewer),
                    previous_show_lights=_get_show_lights(resolved_viewer),
                    previous_show_selection=_get_show_selection(resolved_viewer),
                    previous_use_overrides=_get_use_viewport_overrides(
                        resolved_viewer, target_label
                    ),
                    previous_show_procedurals=_get_show_hydra_procedurals(resolved_viewer),
                    previous_show_render_time=_get_show_render_time(resolved_viewer),
                    previous_vk_settings=_capture_vk_settings(resolved_viewer),
                    previous_stage_locked=_get_stage_locked(resolved_viewer),
                    previous_panes_follow=_get_panes_follow_current_node(self._get_hou_optional()),
                    focus_crop=self._focus_crop_prefs.get(v_key, "full"),
                    resolution_scale=self._res_scale_prefs.get(v_key, 1.0),
                )
                self._leases.append(lease)
                self._subscribe_viewer_events(lease)
            else:
                lease.lopnet_path = lopnet_path
                lease.display_lop_path = display_lop_path
                lease.viewer_name = v_name or lease.viewer_name

            lease.state = SessionState.APPLYING_OVERRIDE
            lease.pending_pause = None
            lease.turbo_engaged = False
            self.auto_turbo.reset(lease.viewer_key)
            self._notify_listeners(
                self._build_snapshot(
                    resolved_viewer,
                    lease=lease,
                    state_override=SessionState.APPLYING_OVERRIDE,
                    pending_op_override="applying",
                    message_override=f"Applying KarmaRT preset {preset.id!r} on {target_label!r}...",
                )
            )

            # Switch Hydra delegate only when different from current delegate.
            current_renderer = _get_viewer_renderer(resolved_viewer)
            if not _delegates_match(current_renderer, target_label):
                _set_viewer_renderer(resolved_viewer, target_label)
            observed_delegate = _get_viewer_renderer(resolved_viewer) or target_label

            receipt: OverrideReceipt | None = None
            settings_prim_path = ""
            has_usd_filters = False

            if preset.target_delegate_kind == "vulkan":
                _apply_vk_settings(
                    resolved_viewer,
                    raytracing=bool(preset.viewport_options.get("vk_raytracing", True)),
                    denoising=bool(preset.viewport_options.get("vk_denoising", True)),
                    fast_interactive=bool(
                        preset.viewport_options.get("vk_fast_interactive", True)
                    ),
                )
                if preset.viewport_options.get("show_hydra_procedurals") is False:
                    _set_show_hydra_procedurals(resolved_viewer, False)
                if not self._other_active_override_leases(lopnet_path, exclude=lease):
                    try:
                        self.engine.clear_owned_overrides(
                            lopnet,
                            display_lop=display_lop,
                            session_id=None,
                        )
                    except SessionOverrideError:
                        pass
                lease.override_active = False
                lease.active_overrides = {}
                lease.base_typed_overrides = {}
                lease.usd_image_filters_detected = False
                self._unregister_hip_guard_if_unused(lopnet_path, exclude=lease)
                lease.denoiser_mode = "vulkan_rt"
            else:
                _set_use_viewport_overrides(resolved_viewer, True, renderer=target_label)

                if preset.clears_overrides:
                    if not self._other_active_override_leases(lopnet_path, exclude=lease):
                        try:
                            self.engine.clear_owned_overrides(
                                lopnet,
                                display_lop=display_lop,
                                session_id=None,
                            )
                        except SessionOverrideError as exc:
                            raise KarmaSessionError(str(exc)) from exc
                    try:
                        binding = self.engine.resolve_binding(lopnet, display_lop=display_lop)
                        settings_prim_path = binding.settings_prim_path
                    except SessionOverrideError:
                        settings_prim_path = ""
                    lease.override_active = False
                    lease.active_overrides = {}
                    lease.base_typed_overrides = {}
                    lease.focus_crop = "full"
                    lease.resolution_scale = 1.0
                    lease.usd_image_filters_detected = False
                    self._focus_crop_prefs[v_key] = "full"
                    self._res_scale_prefs[v_key] = 1.0
                    self._unregister_hip_guard_if_unused(lopnet_path, exclude=lease)
                else:
                    composed_typed, has_usd_filters = self._compose_typed_overrides(
                        preset.usd_overrides,
                        lopnet=lopnet,
                        display_lop=display_lop,
                        focus_crop=lease.focus_crop,
                        resolution_scale=lease.resolution_scale,
                    )
                    if not allow_restart:
                        try:
                            validate_overrides_zero_restart(
                                composed_typed,
                                self.catalog,
                                allow_restart=False,
                            )
                        except ValueError as exc:
                            raise KarmaSessionError(str(exc)) from exc
                    unwrapped_overrides = unwrap_typed_overrides(composed_typed)
                    try:
                        receipt = self.engine.apply_overrides(
                            lopnet,
                            overrides=unwrapped_overrides,
                            preset_id=preset.id,
                            session_id=lease.session_id,
                            catalog=self.catalog,
                            display_lop=display_lop,
                            allow_restart=allow_restart,
                        )
                    except SessionOverrideError as exc:
                        raise KarmaSessionError(str(exc)) from exc

                    if not receipt.applied:
                        raise KarmaSessionError(
                            receipt.error or f"Failed to apply overrides for preset {preset.id!r}."
                        )
                    settings_prim_path = (
                        receipt.binding.settings_prim_path if receipt.binding is not None else ""
                    )
                    self.auto_turbo.mark_internal_stage_write(resolved_viewer, lease.viewer_key)
                    lease.override_active = True
                    lease.active_overrides = dict(unwrapped_overrides)
                    lease.base_typed_overrides = dict(composed_typed)
                    lease.usd_image_filters_detected = has_usd_filters
                    self._register_hip_guard_for_lease(lease)

                show_procs = preset.viewport_options.get("show_hydra_procedurals")
                if show_procs is not None:
                    _set_show_hydra_procedurals(resolved_viewer, bool(show_procs))

                if preset.viewport_options.get("show_render_purpose") is True:
                    for purpose_method in ("setShowRenderPurpose", "setDisplayRenderPurpose"):
                        pfn = getattr(resolved_viewer, purpose_method, None)
                        if callable(pfn):
                            try:
                                pfn(True)
                            except Exception:
                                pass
                            break

                lease.denoiser_mode = "usd_image_filter" if has_usd_filters else resolved_denoiser

            # Clean Lookdev Overlays & Render Stats
            clean_overlays = bool(preset.viewport_options.get("clean_overlays", False))
            if clean_overlays:
                _set_show_lights(resolved_viewer, False)
                _set_show_selection(resolved_viewer, False)
                lease.clean_overlays_active = True
            else:
                if lease.previous_show_lights is not None:
                    _set_show_lights(resolved_viewer, lease.previous_show_lights)
                if lease.previous_show_selection is not None:
                    _set_show_selection(resolved_viewer, lease.previous_show_selection)
                lease.clean_overlays_active = False

            _set_show_render_time(resolved_viewer, True)
            lease.show_render_stats = True
            self._show_stats_prefs[v_key] = True

            # Strictly gated on Houdini (22, 0, 429) inside ReferenceGridGuard.engage()
            self._ref_grid_guard.engage(resolved_viewer)

            lease.owned_delegate = observed_delegate
            lease.active_preset_id = preset.id
            lease.settings_prim_path = settings_prim_path
            lease.allow_restart = bool(allow_restart)
            lease.last_receipt = receipt
            lease.paused = _get_viewer_paused(resolved_viewer)

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            stage_serial = _get_stage_serial(resolved_viewer, display_lop)

            if receipt is not None and receipt.mismatches:
                mismatch_keys = ", ".join(sorted(receipt.mismatches.keys()))
                lease.state = SessionState.DEGRADED
                lease.last_error = f"Override readback mismatch on: {mismatch_keys}"
                lease.message = (
                    f"{preset.label} applied on {observed_delegate} with readback mismatch "
                    f"({mismatch_keys})."
                )
            else:
                lease.last_error = ""
                lease.state = (
                    SessionState.ACTIVE_PAUSED if lease.paused else SessionState.ACTIVE_IDLE
                )
                base_msg = f"{preset.label} active on {observed_delegate}."
                lease.message = f"{base_msg} {fallback_warning}".strip()

            via_str = (
                "Houdini VK"
                if preset.target_delegate_kind == "vulkan"
                else ("LOP:Custom(clear)" if preset.clears_overrides else "LOP:Custom")
            )
            proof_str = (
                f"written={len(receipt.written_attrs)}; "
                f"cook_delta={receipt.cook_count_after - receipt.cook_count_before}; "
                f"mismatches={len(receipt.mismatches)}"
                if receipt is not None
                else f"delegate={observed_delegate}; cleared_overrides={preset.clears_overrides}"
            )
            decision_str = (
                f"activate={preset.id}; delegate={observed_delegate}"
                + (f"; {fallback_warning}" if fallback_warning else "")
            )
            self.ledger.record(
                event="activate_preset",
                decision=decision_str,
                via=via_str,
                proof=proof_str,
                stage_serial=stage_serial,
                apply_ms=receipt.apply_ms if receipt is not None else elapsed_ms,
                preset_id=preset.id,
                observed_delegate=observed_delegate,
                metrics={
                    "settings_prim_path": settings_prim_path or UNKNOWN,
                    "denoiser_mode": lease.denoiser_mode,
                    "clean_overlays": lease.clean_overlays_active,
                    "reference_grid_guard_supported": self._ref_grid_guard.supported(),
                    "fallback_warning": fallback_warning or "none",
                },
            )

            snapshot = self._build_snapshot(resolved_viewer, lease=lease)
            self._notify_listeners(snapshot)
            return snapshot
        except Exception:
            if "is_new_lease" in locals() and is_new_lease and "lease" in locals() and lease in self._leases:
                self._unsubscribe_viewer_events(lease)
                self._leases.remove(lease)
            raise
        finally:
            self._locks["apply"] = False

    def toggle(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
        default_preset: str = "interactive_lookdev",
        delegate_preference: str = "Karma XPU",
    ) -> SessionSnapshot:
        """Toggle KarmaRT on (activating ``default_preset``) or off (restoring)."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)
        if lease is not None and (
            lease.state in _ACTIVE_STATES or lease.state == SessionState.DEGRADED
        ):
            return self.restore(viewer=resolved_viewer)
        return self.activate_preset(
            default_preset,
            viewer=resolved_viewer,
            delegate_preference=delegate_preference,
        )

    def _dispatch_pause_command(
        self,
        viewer: Any,
        *,
        paused: bool,
        reason: str,
        lease: _ViewerLease | None,
    ) -> SessionSnapshot:
        target = bool(paused)
        now = self._now()

        if lease is not None and lease.pending_pause is not None:
            if lease.pending_pause.target_paused == target:
                # Never replay an already-dispatched pause/resume command.
                self._reconcile_pending_pause(lease, viewer, now=now)
                snap = self._build_snapshot(viewer, lease=lease)
                self._notify_listeners(snap)
                return snap

        set_pause_fn = getattr(viewer, "setRendererPaused", None)
        if not callable(set_pause_fn):
            err = "Solaris SceneViewer does not expose setRendererPaused()."
            if lease is not None:
                lease.state = SessionState.DEGRADED
                lease.last_error = err
                lease.message = err
                snap = self._build_snapshot(viewer, lease=lease)
                self._notify_listeners(snap)
                return snap
            raise KarmaSessionError(err)

        try:
            set_pause_fn(target)
        except Exception as exc:
            err = f"setRendererPaused({target}) failed: {exc}"
            if lease is not None:
                lease.state = SessionState.DEGRADED
                lease.last_error = err
                lease.message = err
                snap = self._build_snapshot(viewer, lease=lease)
                self._notify_listeners(snap)
                return snap
            raise KarmaSessionError(err) from exc

        v_key = _viewer_key(viewer)
        if target:
            self.auto_turbo.reset(v_key)
            if lease is not None:
                lease.turbo_engaged = False

        observed_paused = _get_viewer_paused(viewer)
        if lease is not None:
            if observed_paused == target:
                lease.pending_pause = None
                lease.paused = target
                lease.last_error = ""
                if reason == "disk_render_yield" and target:
                    lease.state = SessionState.ACTIVE_DISK_RENDER_YIELD
                    lease.message = (
                        "Interactive Karma viewport paused for disk render yield "
                        "(production stage untouched)."
                    )
                elif target:
                    lease.state = SessionState.ACTIVE_PAUSED
                    lease.message = "Interactive Karma viewport paused."
                else:
                    lease.state = SessionState.ACTIVE_IDLE
                    lease.message = "Interactive Karma viewport resumed."
            else:
                lease.pending_pause = _PendingPause(
                    target_paused=target,
                    issued_at=now,
                    deadline=now + self._command_timeout,
                    command_dispatched=True,
                    reason=reason,
                )
                if reason == "disk_render_yield" and target:
                    lease.state = SessionState.ACTIVE_DISK_RENDER_YIELD
                    lease.message = "Waiting for Houdini to confirm viewport pause for disk render yield..."
                else:
                    lease.message = (
                        "Waiting for Houdini to confirm renderer pause..."
                        if target
                        else "Waiting for Houdini to confirm renderer resume..."
                    )

            self.ledger.record(
                event=reason,
                decision=f"set_renderer_paused={target}",
                via="setRendererPaused",
                proof=f"confirmed_sync={observed_paused == target}; final_render_untouched=True",
                stage_serial=_get_stage_serial(viewer),
                preset_id=lease.active_preset_id,
                observed_delegate=_get_viewer_renderer(viewer) or lease.owned_delegate,
            )
            snap = self._build_snapshot(viewer, lease=lease)
            self._notify_listeners(snap)
            return snap

        # Unleased viewer pause/yield
        if reason == "disk_render_yield" and target:
            self._unleased_yielded_keys.add(v_key)
            state_override = SessionState.ACTIVE_DISK_RENDER_YIELD
            msg = "Interactive viewport paused for disk render yield (production stage untouched)."
        else:
            if not target:
                self._unleased_yielded_keys.discard(v_key)
            state_override = SessionState.READY_OFF
            msg = f"Viewport renderer paused={observed_paused}."

        self.ledger.record(
            event=reason,
            decision=f"set_renderer_paused={target}",
            via="setRendererPaused",
            proof=f"observed_paused={observed_paused}; final_render_untouched=True",
            stage_serial=_get_stage_serial(viewer),
            observed_delegate=_get_viewer_renderer(viewer),
        )
        snap = self._build_snapshot(
            viewer,
            lease=None,
            state_override=state_override,
            message_override=msg,
        )
        self._notify_listeners(snap)
        return snap

    def set_paused(
        self,
        paused: bool,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Pause or resume the Solaris viewport Hydra delegate with bounded confirmation."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)

        if self._locks["pause"]:
            return self._build_snapshot(
                resolved_viewer,
                lease=lease,
                pending_op_override="pausing" if paused else "resuming",
            )

        self._locks["pause"] = True
        try:
            return self._dispatch_pause_command(
                resolved_viewer,
                paused=bool(paused),
                reason="pause" if paused else "resume",
                lease=lease,
            )
        finally:
            self._locks["pause"] = False

    def toggle_clean_overlays(
        self,
        enabled: bool | None = None,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Toggle Clean Lookdev Overlays (lights/selection hidden + H22.0.429 ref grid guard)."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)

        if enabled is None:
            if lease is not None:
                current = lease.clean_overlays_active
            else:
                current = (_get_show_lights(resolved_viewer) is False) and (
                    _get_show_selection(resolved_viewer) is False
                )
            target = not current
        else:
            target = bool(enabled)

        if target:
            _set_show_lights(resolved_viewer, False)
            _set_show_selection(resolved_viewer, False)
            self._ref_grid_guard.engage(resolved_viewer)
        else:
            restore_lights = (
                lease.previous_show_lights
                if lease is not None and lease.previous_show_lights is not None
                else True
            )
            restore_selection = (
                lease.previous_show_selection
                if lease is not None and lease.previous_show_selection is not None
                else True
            )
            _set_show_lights(resolved_viewer, restore_lights)
            _set_show_selection(resolved_viewer, restore_selection)
            self._ref_grid_guard.release(resolved_viewer)

        if lease is not None:
            lease.clean_overlays_active = target
            lease.message = (
                "Clean Lookdev Overlays enabled."
                if target
                else "Clean Lookdev Overlays disabled."
            )

        self.ledger.record(
            event="toggle_clean_overlays",
            decision=f"clean_overlays={target}",
            via="SceneViewer.overlays",
            proof=f"reference_grid_guard_engaged={self._ref_grid_guard.engaged_count}",
            stage_serial=_get_stage_serial(resolved_viewer),
            preset_id=lease.active_preset_id if lease is not None else UNKNOWN,
            observed_delegate=_get_viewer_renderer(resolved_viewer),
        )
        snap = self._build_snapshot(
            resolved_viewer,
            lease=lease,
            message_override=(
                "Clean Lookdev Overlays enabled."
                if target
                else "Clean Lookdev Overlays disabled."
            ),
        )
        self._notify_listeners(snap)
        return snap

    def set_show_render_stats(
        self,
        enabled: bool,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Toggle Solaris viewport render-time / stats overlay."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)
        flag = bool(enabled)
        v_key = _viewer_key(resolved_viewer)

        _set_show_render_time(resolved_viewer, flag)
        self._show_stats_prefs[v_key] = flag
        if lease is not None:
            lease.show_render_stats = flag
            lease.message = f"Viewport render stats {'enabled' if flag else 'disabled'}."

        snap = self._build_snapshot(
            resolved_viewer,
            lease=lease,
            message_override=f"Viewport render stats {'enabled' if flag else 'disabled'}.",
        )
        self._notify_listeners(snap)
        return snap

    def _reapply_lease_modifiers(
        self,
        resolved_viewer: Any,
        lease: _ViewerLease,
        *,
        event_name: str,
        decision: str,
        message: str,
    ) -> SessionSnapshot:
        """Re-compose and apply active preset + Focus Crop + Res Scale atomically in LOP:Custom."""
        lopnet, display_lop = self._resolve_lopnet_and_display(resolved_viewer)
        if not lease.active_preset_id or _is_vulkan_label(lease.owned_delegate):
            lease.message = message
            snap = self._build_snapshot(resolved_viewer, lease=lease, message_override=message)
            self._notify_listeners(snap)
            return snap

        denoiser_arg = (
            lease.denoiser_mode
            if lease.denoiser_mode in SUPPORTED_DENOISER_ENGINES
            else None
        )
        preset, _ = self._resolve_preset(
            lease.active_preset_id,
            viewer=resolved_viewer,
            denoiser_engine=denoiser_arg,
        )
        if preset.clears_overrides and lease.focus_crop == "full" and abs(lease.resolution_scale - 1.0) < 1e-4:
            self.engine.clear_owned_overrides(lopnet, display_lop=display_lop, session_id=None)
            lease.override_active = False
            lease.active_overrides = {}
            lease.base_typed_overrides = {}
            self._unregister_hip_guard_if_unused(lease.lopnet_path, exclude=lease)
            lease.message = message
            snap = self._build_snapshot(resolved_viewer, lease=lease, message_override=message)
            self._notify_listeners(snap)
            return snap

        composed_typed, has_usd_filters = self._compose_typed_overrides(
            preset.usd_overrides,
            lopnet=lopnet,
            display_lop=display_lop,
            focus_crop=lease.focus_crop,
            resolution_scale=lease.resolution_scale,
        )
        validate_overrides_zero_restart(composed_typed, self.catalog, allow_restart=False)
        unwrapped = unwrap_typed_overrides(composed_typed)
        receipt = self.engine.apply_overrides(
            lopnet,
            overrides=unwrapped,
            preset_id=lease.active_preset_id,
            session_id=lease.session_id,
            catalog=self.catalog,
            display_lop=display_lop,
            allow_restart=False,
        )
        lease.last_receipt = receipt
        lease.override_active = receipt.applied
        lease.active_overrides = dict(unwrapped)
        lease.base_typed_overrides = dict(composed_typed)
        lease.usd_image_filters_detected = has_usd_filters
        lease.turbo_engaged = False
        lease.message = message
        self.auto_turbo.mark_internal_stage_write(resolved_viewer, lease.viewer_key)
        self._register_hip_guard_for_lease(lease)

        self.ledger.record(
            event=event_name,
            decision=decision,
            via="LOP:Custom",
            proof=(
                f"written={len(receipt.written_attrs)}; "
                f"cook_delta={receipt.cook_count_after - receipt.cook_count_before}; "
                f"mismatches={len(receipt.mismatches)}"
            ),
            stage_serial=_get_stage_serial(resolved_viewer, display_lop),
            apply_ms=receipt.apply_ms,
            preset_id=lease.active_preset_id,
            observed_delegate=lease.owned_delegate,
        )
        snap = self._build_snapshot(resolved_viewer, lease=lease, message_override=message)
        self._notify_listeners(snap)
        return snap

    def set_focus_crop(
        self,
        region_id: str,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Apply zero-restart ``dataWindowNDC`` Focus Crop ('full', 'center_50', 'center_33')."""
        _assert_main_thread()
        key = str(region_id or "full").strip().lower()
        if key not in FOCUS_CROP_REGIONS:
            raise KarmaSessionError(
                f"Unsupported focus crop region {region_id!r}; expected one of {tuple(FOCUS_CROP_REGIONS)}."
            )
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        v_key = _viewer_key(resolved_viewer)
        self._focus_crop_prefs[v_key] = key
        label = FOCUS_CROP_REGIONS[key][0]
        msg = f"Focus Crop set to {label} (zero-restart dataWindowNDC)."

        lease = self._find_lease(resolved_viewer)
        if lease is not None:
            lease.focus_crop = key
            return self._reapply_lease_modifiers(
                resolved_viewer,
                lease,
                event_name="set_focus_crop",
                decision=f"focus_crop={key}",
                message=msg,
            )

        snap = self._build_snapshot(resolved_viewer, lease=None, message_override=msg)
        self._notify_listeners(snap)
        return snap

    def set_resolution_scale(
        self,
        scale: float,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Apply zero-restart session ``resolution`` scale (1.0, 0.75, 0.5, 0.25) in LOP:Custom."""
        _assert_main_thread()
        factor = float(scale)
        if factor <= 0.0 or factor > 2.0:
            raise KarmaSessionError(f"Resolution scale must be in (0.0, 2.0], got {scale!r}")
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        v_key = _viewer_key(resolved_viewer)
        self._res_scale_prefs[v_key] = factor
        pct = int(round(factor * 100))
        msg = f"Viewport Resolution Scale set to {pct}% (production resolution untouched)."

        lease = self._find_lease(resolved_viewer)
        if lease is not None:
            lease.resolution_scale = factor
            return self._reapply_lease_modifiers(
                resolved_viewer,
                lease,
                event_name="set_resolution_scale",
                decision=f"resolution_scale={factor:.2f}",
                message=msg,
            )

        snap = self._build_snapshot(resolved_viewer, lease=None, message_override=msg)
        self._notify_listeners(snap)
        return snap

    def set_viewport_frozen(
        self,
        frozen: bool,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Freeze/unfreeze Solaris viewport USD updates via ``setSceneGraphStageLocked``."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)
        flag = bool(frozen)
        ok = _set_stage_locked(resolved_viewer, flag)
        if not ok:
            raise KarmaSessionError(
                "Solaris SceneViewer does not expose setSceneGraphStageLocked()."
            )
        msg = (
            "Viewport USD sync FROZEN (edit LOP networks freely without triggering Karma re-sync)."
            if flag
            else "Viewport USD sync UNFROZEN (live Solaris updates resumed)."
        )
        if lease is not None:
            lease.message = msg
        self.ledger.record(
            event="set_viewport_frozen",
            decision=f"frozen={flag}",
            via="SceneViewer.setSceneGraphStageLocked",
            proof=f"observed_locked={_get_stage_locked(resolved_viewer)}",
            stage_serial=_get_stage_serial(resolved_viewer),
            preset_id=lease.active_preset_id if lease is not None else UNKNOWN,
            observed_delegate=_get_viewer_renderer(resolved_viewer),
        )
        snap = self._build_snapshot(resolved_viewer, lease=lease, message_override=msg)
        self._notify_listeners(snap)
        return snap

    def set_stage_pinned(
        self,
        pinned: bool,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Pin Solaris stage to display flag (``hou.lop.setPanesFollowCurrentNode(not pinned)``)."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        hou = self._require_hou()
        lease = self._find_lease(resolved_viewer)
        flag = bool(pinned)

        if self._saved_panes_follow is None:
            self._saved_panes_follow = _get_panes_follow_current_node(hou)
        ok = _set_panes_follow_current_node(hou, not flag)
        if not ok:
            raise KarmaSessionError(
                "hou.lop.setPanesFollowCurrentNode() is unavailable in this Houdini build."
            )
        msg = (
            "Stage PINNED to display flag (selecting upstream LOPs will not recompose the viewport)."
            if flag
            else "Stage unpinned (panes follow selected LOP node)."
        )
        if lease is not None:
            lease.message = msg
        self.ledger.record(
            event="set_stage_pinned",
            decision=f"stage_pinned={flag}",
            via="hou.lop.setPanesFollowCurrentNode",
            proof=f"panesFollowCurrentNode={_get_panes_follow_current_node(hou)}",
            stage_serial=_get_stage_serial(resolved_viewer),
            preset_id=lease.active_preset_id if lease is not None else UNKNOWN,
            observed_delegate=_get_viewer_renderer(resolved_viewer),
        )
        snap = self._build_snapshot(resolved_viewer, lease=lease, message_override=msg)
        self._notify_listeners(snap)
        return snap

    def solo_selected_geometry(
        self,
        prim_paths: list[str] | tuple[str, ...] | None = None,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Non-destructively solo selected USD geometry via ``LopViewportOverrides.setSoloGeometry``."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lopnet, display_lop = self._resolve_lopnet_and_display(resolved_viewer)
        targets = list(prim_paths) if prim_paths is not None else _get_scene_graph_selection(resolved_viewer)
        if not targets:
            raise KarmaSessionError(
                "No USD primitives selected in the Scene Graph. Select one or more prims to solo."
            )
        soloed = self.engine.set_solo_geometry(lopnet, targets, display_lop=display_lop)
        lease = self._find_lease(resolved_viewer)
        if lease is not None:
            lease.solo_modified = True
            self.auto_turbo.mark_internal_stage_write(resolved_viewer, lease.viewer_key)
        msg = f"Soloed {len(soloed)} geometry prim(s) non-destructively (zero USD recomposition)."
        if lease is not None:
            lease.message = msg
        self.ledger.record(
            event="solo_selected_geometry",
            decision=f"solo_geo={len(soloed)}",
            via="LopViewportOverrides.setSoloGeometry",
            proof=f"targets={targets[:4]}",
            stage_serial=_get_stage_serial(resolved_viewer, display_lop),
            preset_id=lease.active_preset_id if lease is not None else UNKNOWN,
            observed_delegate=_get_viewer_renderer(resolved_viewer),
        )
        snap = self._build_snapshot(resolved_viewer, lease=lease, message_override=msg)
        self._notify_listeners(snap)
        return snap

    def solo_selected_lights(
        self,
        prim_paths: list[str] | tuple[str, ...] | None = None,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Non-destructively solo selected USD lights via ``LopViewportOverrides.setSoloLights``."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lopnet, display_lop = self._resolve_lopnet_and_display(resolved_viewer)
        targets = list(prim_paths) if prim_paths is not None else _get_scene_graph_selection(resolved_viewer)
        if not targets:
            raise KarmaSessionError(
                "No USD lights selected in the Scene Graph. Select one or more lights to solo."
            )
        soloed = self.engine.set_solo_lights(lopnet, targets, display_lop=display_lop)
        lease = self._find_lease(resolved_viewer)
        if lease is not None:
            lease.solo_modified = True
            self.auto_turbo.mark_internal_stage_write(resolved_viewer, lease.viewer_key)
        msg = f"Soloed {len(soloed)} light prim(s) non-destructively."
        if lease is not None:
            lease.message = msg
        self.ledger.record(
            event="solo_selected_lights",
            decision=f"solo_lights={len(soloed)}",
            via="LopViewportOverrides.setSoloLights",
            proof=f"targets={targets[:4]}",
            stage_serial=_get_stage_serial(resolved_viewer, display_lop),
            preset_id=lease.active_preset_id if lease is not None else UNKNOWN,
            observed_delegate=_get_viewer_renderer(resolved_viewer),
        )
        snap = self._build_snapshot(resolved_viewer, lease=lease, message_override=msg)
        self._notify_listeners(snap)
        return snap

    def clear_solo(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Clear all viewport Solo Geometry and Solo Lights overrides."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lopnet, display_lop = self._resolve_lopnet_and_display(resolved_viewer)
        self.engine.clear_solo_overrides(lopnet, display_lop=display_lop)
        lease = self._find_lease(resolved_viewer)
        if lease is not None:
            lease.solo_modified = False
            self.auto_turbo.mark_internal_stage_write(resolved_viewer, lease.viewer_key)
            lease.message = "Cleared viewport Solo Geometry and Solo Lights overrides."
        snap = self._build_snapshot(
            resolved_viewer,
            lease=lease,
            message_override="Cleared viewport Solo Geometry and Solo Lights overrides.",
        )
        self._notify_listeners(snap)
        return snap

    def set_auto_turbo(
        self,
        enabled: bool,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Enable or disable Auto-Turbo interaction acceleration for a Solaris viewer."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        v_key = _viewer_key(resolved_viewer)
        flag = bool(enabled)
        self._auto_turbo_prefs[v_key] = flag

        lease = self._find_lease(resolved_viewer)
        if lease is not None:
            lease.auto_turbo_enabled = flag
            if not flag:
                was_engaged = lease.turbo_engaged
                self.auto_turbo.reset(lease.viewer_key)
                if was_engaged:
                    self._settle_turbo_overrides(resolved_viewer, lease)
                lease.message = "Auto-Turbo disabled."
            else:
                self.auto_turbo.reset(lease.viewer_key)
                lease.message = "Auto-Turbo enabled."

        self.ledger.record(
            event="set_auto_turbo",
            decision=f"auto_turbo={flag}",
            via="AutoTurboEngine",
            proof=f"settle_delay_ms={self.auto_turbo.config.settle_delay_ms}",
            stage_serial=_get_stage_serial(resolved_viewer),
            preset_id=lease.active_preset_id if lease is not None else UNKNOWN,
            observed_delegate=_get_viewer_renderer(resolved_viewer),
        )
        snap = self._build_snapshot(
            resolved_viewer,
            lease=lease,
            message_override=f"Auto-Turbo {'enabled' if flag else 'disabled'}.",
        )
        self._notify_listeners(snap)
        return snap

    def _settle_turbo_overrides(
        self,
        viewer: Any,
        lease: _ViewerLease,
        *,
        reason: str = "settled",
    ) -> None:
        if _is_vulkan_label(lease.owned_delegate):
            lease.turbo_engaged = False
            if lease.state == SessionState.ACTIVE_TURBO_INTERACT:
                lease.state = SessionState.ACTIVE_IDLE
            lease.message = f"Auto-Turbo settled on {lease.owned_delegate} ({reason})."
            return

        lopnet, display_lop = self._resolve_lopnet_and_display(viewer)
        if lease.active_overrides:
            receipt = self.engine.apply_overrides(
                lopnet,
                overrides=lease.active_overrides,
                preset_id=lease.active_preset_id,
                session_id=lease.session_id,
                catalog=self.catalog,
                display_lop=display_lop,
                allow_restart=lease.allow_restart,
            )
            lease.last_receipt = receipt
            lease.override_active = receipt.applied
            lease.turbo_engaged = False
            if lease.state == SessionState.ACTIVE_TURBO_INTERACT:
                lease.state = SessionState.ACTIVE_IDLE
            self.auto_turbo.mark_internal_stage_write(viewer, lease.viewer_key)
            self._register_hip_guard_for_lease(lease)
            lease.message = f"Auto-Turbo settled back to {lease.active_preset_id} ({reason})."
            self.ledger.record(
                event="auto_turbo_settle",
                decision=f"restore_preset={lease.active_preset_id}",
                via="LOP:Custom",
                proof=(
                    f"reason={reason}; written={len(receipt.written_attrs)}; "
                    f"cook_delta={receipt.cook_count_after - receipt.cook_count_before}"
                ),
                stage_serial=_get_stage_serial(viewer, display_lop),
                apply_ms=receipt.apply_ms,
                preset_id=lease.active_preset_id,
                observed_delegate=lease.owned_delegate,
            )
        else:
            if not self._other_active_override_leases(lease.lopnet_path, exclude=lease):
                self.engine.clear_owned_overrides(
                    lopnet,
                    display_lop=display_lop,
                    session_id=lease.session_id,
                )
            lease.override_active = False
            lease.turbo_engaged = False
            self._unregister_hip_guard_if_unused(lease.lopnet_path, exclude=lease)
            if lease.state == SessionState.ACTIVE_TURBO_INTERACT:
                lease.state = SessionState.ACTIVE_IDLE
            lease.message = f"Auto-Turbo settled back to {lease.active_preset_id} ({reason})."
            self.ledger.record(
                event="auto_turbo_settle",
                decision=f"clear_turbo_overrides_for={lease.active_preset_id}",
                via="LOP:Custom(clear)",
                proof=f"reason={reason}",
                stage_serial=_get_stage_serial(viewer, display_lop),
                preset_id=lease.active_preset_id,
                observed_delegate=lease.owned_delegate,
            )

    def tick_auto_turbo(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
        now: float | None = None,
    ) -> SessionSnapshot:
        """Advance the Auto-Turbo motion/settle control loop by one tick."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)

        if (
            self._locks["turbo"]
            or self._locks["apply"]
            or self._locks["restore"]
            or self._locks["emergency"]
        ):
            return self._build_snapshot(resolved_viewer, lease=lease)

        self._locks["turbo"] = True
        try:
            if lease is None:
                return self._build_snapshot(resolved_viewer, lease=None)

            ts = float(self._now() if now is None else now)

            # Check for external renderer takeover first.
            observed = _get_viewer_renderer(resolved_viewer)
            if observed and lease.owned_delegate and not _delegates_match(
                observed, lease.owned_delegate
            ):
                return self._handle_external_takeover(lease, observed, trigger="tick_auto_turbo")

            if self._reconcile_pending_pause(lease, resolved_viewer, now=ts):
                snap = self._build_snapshot(resolved_viewer, lease=lease)
                self._notify_listeners(snap)
                return snap

            if not lease.auto_turbo_enabled or lease.state not in (
                SessionState.ACTIVE_IDLE,
                SessionState.ACTIVE_TURBO_INTERACT,
            ):
                return self._build_snapshot(resolved_viewer, lease=lease)

            tick_result = self.auto_turbo.evaluate(
                resolved_viewer,
                lease.viewer_key,
                lease.owned_delegate,
                now=ts,
            )

            if tick_result.action == "none":
                return self._build_snapshot(resolved_viewer, lease=lease)

            if tick_result.action == "vk_fast":
                _apply_vk_settings(resolved_viewer, fast_interactive=True)
                lease.turbo_engaged = True
                lease.state = SessionState.ACTIVE_TURBO_INTERACT
                lease.message = f"Auto-Turbo VK fast sampling active ({tick_result.reason})."
                self.ledger.record(
                    event="auto_turbo_vk_fast",
                    decision="enable_vk_fast_interactive",
                    via="Houdini VK",
                    proof=(
                        f"reason={tick_result.reason}; "
                        f"motion={tick_result.motion_magnitude:.5f}; "
                        f"stage_delta={tick_result.stage_delta}"
                    ),
                    stage_serial=_get_stage_serial(resolved_viewer),
                    preset_id=lease.active_preset_id,
                    observed_delegate=lease.owned_delegate,
                )
                snap = self._build_snapshot(resolved_viewer, lease=lease)
                self._notify_listeners(snap)
                return snap

            if tick_result.action == "engage":
                lopnet, display_lop = self._resolve_lopnet_and_display(resolved_viewer)
                try:
                    merged_typed = build_turbo_merged_overrides(
                        lease.base_typed_overrides,
                        self.catalog,
                    )
                except ValueError as exc:
                    lease.state = SessionState.DEGRADED
                    lease.last_error = str(exc)
                    lease.message = str(exc)
                    snap = self._build_snapshot(resolved_viewer, lease=lease)
                    self._notify_listeners(snap)
                    return snap

                merged_raw = unwrap_typed_overrides(merged_typed)
                receipt = self.engine.apply_overrides(
                    lopnet,
                    overrides=merged_raw,
                    preset_id=f"{lease.active_preset_id}:turbo",
                    session_id=lease.session_id,
                    catalog=self.catalog,
                    display_lop=display_lop,
                    allow_restart=False,
                )
                lease.last_receipt = receipt
                if not receipt.applied:
                    lease.state = SessionState.DEGRADED
                    lease.last_error = receipt.error or "Auto-Turbo override apply failed."
                    lease.message = lease.last_error
                else:
                    lease.turbo_engaged = True
                    lease.override_active = True
                    lease.state = SessionState.ACTIVE_TURBO_INTERACT
                    lease.last_error = ""
                    lease.message = f"Auto-Turbo engaged ({tick_result.reason})."
                    self.auto_turbo.mark_internal_stage_write(
                        resolved_viewer, lease.viewer_key, now=ts
                    )
                    self._register_hip_guard_for_lease(lease)

                self.ledger.record(
                    event="auto_turbo_engage",
                    decision=f"engage_turbo={receipt.applied}",
                    via="LOP:Custom",
                    proof=(
                        f"reason={tick_result.reason}; "
                        f"motion={tick_result.motion_magnitude:.5f}; "
                        f"stage_delta={tick_result.stage_delta}; "
                        f"written={len(receipt.written_attrs)}; "
                        f"cook_delta={receipt.cook_count_after - receipt.cook_count_before}"
                    ),
                    stage_serial=_get_stage_serial(resolved_viewer, display_lop),
                    apply_ms=receipt.apply_ms,
                    preset_id=f"{lease.active_preset_id}:turbo",
                    observed_delegate=lease.owned_delegate,
                )
                snap = self._build_snapshot(resolved_viewer, lease=lease)
                self._notify_listeners(snap)
                return snap

            if tick_result.action == "settle":
                self._settle_turbo_overrides(
                    resolved_viewer,
                    lease,
                    reason=tick_result.reason,
                )
                snap = self._build_snapshot(resolved_viewer, lease=lease)
                self._notify_listeners(snap)
                return snap

            return self._build_snapshot(resolved_viewer, lease=lease)
        finally:
            self._locks["turbo"] = False

    def yield_for_disk_render(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Pause interactive Karma viewport for disk rendering without touching production USD."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)

        if self._locks["pause"]:
            return self._build_snapshot(
                resolved_viewer,
                lease=lease,
                pending_op_override="yielding_for_disk_render",
            )

        self._locks["pause"] = True
        try:
            return self._dispatch_pause_command(
                resolved_viewer,
                paused=True,
                reason="disk_render_yield",
                lease=lease,
            )
        finally:
            self._locks["pause"] = False

    def resume_from_disk_render(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Resume interactive Karma viewport after a disk render yield."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)

        if self._locks["pause"]:
            return self._build_snapshot(
                resolved_viewer,
                lease=lease,
                pending_op_override="resuming_from_disk_render",
            )

        self._locks["pause"] = True
        try:
            return self._dispatch_pause_command(
                resolved_viewer,
                paused=False,
                reason="disk_render_resume",
                lease=lease,
            )
        finally:
            self._locks["pause"] = False

    def unlock_and_orbit_copy(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Unlock the active viewport camera from view so shot cameras cannot drift."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        cur_vp_fn = getattr(resolved_viewer, "curViewport", None)
        vp = cur_vp_fn() if callable(cur_vp_fn) else None
        if vp is None:
            raise KarmaSessionError(
                "Solaris SceneViewer has no active viewport (curViewport() returned None)."
            )

        cam_path_before, _ = _get_camera_info(resolved_viewer)
        lock_fn = getattr(vp, "lockCameraToView", None)
        if not callable(lock_fn):
            raise KarmaSessionError("Active GeometryViewport does not expose lockCameraToView().")

        try:
            lock_fn(False)
        except Exception as exc:
            raise KarmaSessionError(f"Failed to unlock camera from view: {exc}") from exc

        _, cam_locked_after = _get_camera_info(resolved_viewer)
        lease = self._find_lease(resolved_viewer)
        msg = (
            f"Unlocked camera {cam_path_before or '<viewport>'} from view so shot camera "
            "remains protected from accidental orbit drift."
        )
        if lease is not None:
            lease.message = msg

        self.ledger.record(
            event="unlock_and_orbit_copy",
            decision="unlock_camera_from_view",
            via="curViewport().lockCameraToView(False)",
            proof=f"camera={cam_path_before or UNKNOWN}; locked_after={cam_locked_after}",
            stage_serial=_get_stage_serial(resolved_viewer),
            preset_id=lease.active_preset_id if lease is not None else UNKNOWN,
            observed_delegate=_get_viewer_renderer(resolved_viewer),
        )
        snap = self._build_snapshot(
            resolved_viewer,
            lease=lease,
            message_override=msg,
        )
        self._notify_listeners(snap)
        return snap

    def quick_snapshot(
        self,
        output_path: str | None = None,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> dict[str, Any]:
        """Capture a fast single-frame viewport preview PNG without touching ``karmarendersettings``."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        hou = self._get_hou_optional()

        if output_path is not None and str(output_path).strip():
            target_path = Path(str(output_path).strip())
        else:
            hip_dir = _read_env_var("HIP", hou)
            if hip_dir and Path(hip_dir).is_dir():
                base_dir = Path(hip_dir) / "render" / "karmart_snapshots"
            else:
                base_dir = Path(tempfile.gettempdir()) / "karmart_snapshots"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
            target_path = base_dir / f"snapshot_{stamp}.png"

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return {
                "ok": False,
                "path": str(target_path),
                "method": "none",
                "message": f"Failed to create snapshot directory {target_path.parent}: {exc}",
            }

        cur_vp_fn = getattr(resolved_viewer, "curViewport", None)
        vp = cur_vp_fn() if callable(cur_vp_fn) else None
        errors: list[str] = []

        # Method 1: Single-frame SceneViewer flipbook stash (never mutates USD render settings)
        fb_settings_fn = getattr(resolved_viewer, "flipbookSettings", None)
        fb_fn = getattr(resolved_viewer, "flipbook", None)
        if callable(fb_settings_fn) and callable(fb_fn):
            try:
                raw_settings = fb_settings_fn()
                stash_fn = getattr(raw_settings, "stash", None)
                fb_settings = stash_fn() if callable(stash_fn) else raw_settings

                frame_val = 1.0
                if hou is not None and callable(getattr(hou, "frame", None)):
                    try:
                        frame_val = float(hou.frame())
                    except Exception:
                        frame_val = 1.0

                if callable(getattr(fb_settings, "output", None)):
                    fb_settings.output(str(target_path))
                if callable(getattr(fb_settings, "outputToMPlay", None)):
                    fb_settings.outputToMPlay(False)
                if callable(getattr(fb_settings, "frameRange", None)):
                    fb_settings.frameRange((frame_val, frame_val))

                try:
                    fb_fn(vp, fb_settings)
                except TypeError:
                    fb_fn(settings=fb_settings)

                if target_path.is_file():
                    self.ledger.record(
                        event="quick_snapshot",
                        decision="viewport_preview_png",
                        via="viewer.flipbook",
                        proof=f"path={target_path}; bytes={target_path.stat().st_size}",
                        stage_serial=_get_stage_serial(resolved_viewer),
                        observed_delegate=_get_viewer_renderer(resolved_viewer),
                    )
                    return {
                        "ok": True,
                        "path": str(target_path),
                        "method": "flipbook",
                        "message": f"Saved viewport preview snapshot to {target_path}.",
                    }
                errors.append("flipbook completed without creating output PNG on disk")
            except Exception as exc:
                errors.append(f"flipbook failed: {exc}")

        # Method 2: GeometryViewport saveViewToFile / draw fallback
        if vp is not None:
            save_fn = getattr(vp, "saveViewToFile", None)
            if callable(save_fn):
                try:
                    save_fn(str(target_path))
                    if target_path.is_file():
                        self.ledger.record(
                            event="quick_snapshot",
                            decision="viewport_preview_png",
                            via="vp.saveViewToFile",
                            proof=f"path={target_path}; bytes={target_path.stat().st_size}",
                            stage_serial=_get_stage_serial(resolved_viewer),
                            observed_delegate=_get_viewer_renderer(resolved_viewer),
                        )
                        return {
                            "ok": True,
                            "path": str(target_path),
                            "method": "viewport_save",
                            "message": f"Saved viewport preview snapshot to {target_path}.",
                        }
                    errors.append("saveViewToFile completed without creating output PNG")
                except Exception as exc:
                    errors.append(f"saveViewToFile failed: {exc}")

            draw_fn = getattr(vp, "draw", None)
            if callable(draw_fn):
                try:
                    draw_fn()
                except Exception as exc:
                    errors.append(f"vp.draw failed: {exc}")

        # Method 3: hou.ui mainQtWindow grab fallback (GUI sessions only)
        if hou is not None:
            ui = getattr(hou, "ui", None)
            main_win_fn = getattr(ui, "mainQtWindow", None) if ui is not None else None
            if callable(main_win_fn):
                try:
                    win = main_win_fn()
                    grab_fn = getattr(win, "grab", None) if win is not None else None
                    if callable(grab_fn):
                        pixmap = grab_fn()
                        save_px = getattr(pixmap, "save", None) if pixmap is not None else None
                        if callable(save_px) and save_px(str(target_path), "PNG") and target_path.is_file():
                            self.ledger.record(
                                event="quick_snapshot",
                                decision="viewport_preview_png",
                                via="hou.ui.mainQtWindow.grab",
                                proof=f"path={target_path}; bytes={target_path.stat().st_size}",
                                stage_serial=_get_stage_serial(resolved_viewer),
                                observed_delegate=_get_viewer_renderer(resolved_viewer),
                            )
                            return {
                                "ok": True,
                                "path": str(target_path),
                                "method": "qt_grab",
                                "message": f"Saved Qt window preview snapshot to {target_path}.",
                            }
                except Exception as exc:
                    errors.append(f"qt_grab failed: {exc}")

        detail = "; ".join(errors) if errors else "No supported viewport capture API available."
        self.ledger.record(
            event="quick_snapshot",
            decision="snapshot_failed",
            via="none",
            proof=detail,
            stage_serial=_get_stage_serial(resolved_viewer),
            observed_delegate=_get_viewer_renderer(resolved_viewer),
        )
        return {
            "ok": False,
            "path": str(target_path),
            "method": "none",
            "message": f"Could not capture viewport snapshot: {detail}",
        }

    def scan_advisories(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> tuple[StageTelemetry, list[SceneAdvisory]]:
        """Run read-only Solaris stage bottleneck inspection for the active viewer."""
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lopnet, display_lop = self._resolve_lopnet_and_display(resolved_viewer)

        telemetry, advisories = scan_stage_bottlenecks(
            display_lop,
            lopnet=lopnet,
            hou_module=self._get_hou_optional(),
        )
        self.ledger.record(
            event="scan_advisories",
            decision=f"advisories={len(advisories)}",
            via="scan_stage_bottlenecks",
            proof=(
                f"prims={telemetry.prim_count}; truncated={telemetry.truncated}; "
                f"meshes={telemetry.mesh_count}; volumes={telemetry.volume_count}"
            ),
            stage_serial=telemetry.stage_serial,
            scan_ms=telemetry.scan_ms,
            observed_delegate=_get_viewer_renderer(resolved_viewer),
            metrics={"advisory_ids": [adv.id for adv in advisories]},
        )
        return telemetry, advisories

    def _restore_viewer_visual_state(self, lease: _ViewerLease) -> list[str]:
        errors: list[str] = []
        viewer = lease.viewer

        try:
            self._ref_grid_guard.release(viewer)
        except Exception as exc:
            errors.append(f"ref_grid_release: {exc}")

        if lease.previous_show_lights is not None:
            try:
                _set_show_lights(viewer, lease.previous_show_lights)
            except Exception as exc:
                errors.append(f"showLights: {exc}")

        if lease.previous_show_selection is not None:
            try:
                _set_show_selection(viewer, lease.previous_show_selection)
            except Exception as exc:
                errors.append(f"showSelection: {exc}")

        if lease.previous_show_render_time is not None:
            try:
                _set_show_render_time(viewer, lease.previous_show_render_time)
            except Exception as exc:
                errors.append(f"showRenderTime: {exc}")

        if lease.previous_show_procedurals is not None:
            try:
                _set_show_hydra_procedurals(viewer, lease.previous_show_procedurals)
            except Exception as exc:
                errors.append(f"showHydraProcedurals: {exc}")

        if lease.previous_vk_settings:
            try:
                _apply_vk_settings(
                    viewer,
                    raytracing=lease.previous_vk_settings.get("vk_raytracing"),
                    denoising=lease.previous_vk_settings.get("vk_denoising"),
                    fast_interactive=lease.previous_vk_settings.get("vk_fast_interactive"),
                )
            except Exception as exc:
                errors.append(f"vk_settings: {exc}")

        if lease.previous_use_overrides is not None:
            try:
                _set_use_viewport_overrides(
                    viewer,
                    lease.previous_use_overrides,
                    renderer=lease.owned_delegate or None,
                )
            except Exception as exc:
                errors.append(f"useViewportOverrides: {exc}")

        if lease.previous_paused is not None:
            try:
                if _get_viewer_paused(viewer) != lease.previous_paused:
                    set_pause_fn = getattr(viewer, "setRendererPaused", None)
                    if callable(set_pause_fn):
                        set_pause_fn(bool(lease.previous_paused))
            except Exception as exc:
                errors.append(f"setRendererPaused: {exc}")

        if lease.previous_stage_locked is not None:
            try:
                _set_stage_locked(viewer, lease.previous_stage_locked)
            except Exception as exc:
                errors.append(f"stageLocked: {exc}")

        if lease.previous_panes_follow is not None:
            try:
                _set_panes_follow_current_node(self._get_hou_optional(), lease.previous_panes_follow)
            except Exception as exc:
                errors.append(f"panesFollowCurrentNode: {exc}")

        if lease.solo_modified:
            try:
                lopnet, display_lop = self._resolve_lopnet_and_display(viewer)
                self.engine.clear_solo_overrides(lopnet, display_lop=display_lop)
            except Exception as exc:
                errors.append(f"clearSolo: {exc}")

        if lease.previous_renderer:
            try:
                current = _get_viewer_renderer(viewer)
                if not _delegates_match(current, lease.previous_renderer):
                    _set_viewer_renderer(viewer, lease.previous_renderer)
            except Exception as exc:
                errors.append(f"setHydraRenderer: {exc}")

        return errors

    def restore(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Restore a leased Solaris SceneViewer and remove KarmaRT owned LOP:Custom specs.

        Uses an independent ``restore`` lock so it is NEVER blocked by ``apply``,
        ``pause``, or ``turbo`` locks.
        """
        _assert_main_thread()
        resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        lease = self._find_lease(resolved_viewer)

        if self._locks["restore"]:
            return self._build_snapshot(
                resolved_viewer,
                lease=lease,
                state_override=SessionState.RESTORING,
                pending_op_override="restoring",
            )

        self._locks["restore"] = True
        try:
            v_key = _viewer_key(resolved_viewer)
            self._unleased_yielded_keys.discard(v_key)
            self._focus_crop_prefs[v_key] = "full"
            self._res_scale_prefs[v_key] = 1.0
            if self._saved_panes_follow is not None:
                _set_panes_follow_current_node(self._get_hou_optional(), self._saved_panes_follow)
                self._saved_panes_follow = None

            if lease is None:
                self._ref_grid_guard.release(resolved_viewer)
                self.auto_turbo.reset(v_key)
                snap = self._build_snapshot(
                    resolved_viewer,
                    lease=None,
                    state_override=SessionState.READY_OFF,
                    message_override="KarmaRT already off; viewer state unchanged.",
                )
                self._notify_listeners(snap)
                return snap

            lease.state = SessionState.RESTORING
            lease.pending_pause = None
            lease.turbo_engaged = False
            self._notify_listeners(
                self._build_snapshot(
                    resolved_viewer,
                    lease=lease,
                    state_override=SessionState.RESTORING,
                    pending_op_override="restoring",
                    message_override="Restoring Solaris viewer and clearing KarmaRT overrides...",
                )
            )

            self.auto_turbo.reset(lease.viewer_key)
            clear_result = self._clear_lease_overrides_if_unshared(lease)
            lease.override_active = False

            restore_errors = self._restore_viewer_visual_state(lease)

            self._unsubscribe_viewer_events(lease)
            if lease in self._leases:
                self._leases.remove(lease)

            err_text = "; ".join(restore_errors)
            self.ledger.record(
                event="restore",
                decision="restored_viewer_and_cleared_overrides",
                via="LOP:Custom",
                proof=(
                    f"cleared={clear_result.get('cleared')}; "
                    f"removed_count={clear_result.get('removed_count', 0)}; "
                    f"errors={len(restore_errors)}"
                ),
                stage_serial=_get_stage_serial(resolved_viewer),
                preset_id=lease.active_preset_id,
                observed_delegate=_get_viewer_renderer(resolved_viewer),
            )

            snap = self._build_snapshot(
                resolved_viewer,
                lease=None,
                state_override=SessionState.DEGRADED if restore_errors else SessionState.READY_OFF,
                error_override=err_text,
                message_override=(
                    f"Restored with warnings: {err_text}"
                    if restore_errors
                    else "KarmaRT restored viewer and cleared owned LOP:Custom overrides."
                ),
            )
            self._notify_listeners(snap)
            return snap
        finally:
            self._locks["restore"] = False

    def emergency_reset(
        self,
        viewer: Any = None,
        viewer_name: str | None = None,
    ) -> SessionSnapshot:
        """Force-clear ALL KarmaRT ``LOP:Custom`` specs, guards, and leases unconditionally.

        Uses an independent ``emergency`` lock so it is NEVER blocked by ``apply``,
        ``pause``, ``restore``, or ``turbo`` locks.
        """
        _assert_main_thread()
        if self._locks["emergency"]:
            return SessionSnapshot(
                state=SessionState.RESTORING,
                pending_op="emergency_reset",
                message="Emergency reset already in progress.",
            )

        self._locks["emergency"] = True
        try:
            hou = self._get_hou_optional()
            resolved_viewer = None
            try:
                resolved_viewer = self.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
            except KarmaSessionError:
                resolved_viewer = viewer

            # Collect every tracked LopNetwork + /stage + current viewer pwd()
            lopnet_candidates: dict[str, Any] = {}
            leases_snapshot = list(self._leases)
            self._leases.clear()
            self._unleased_yielded_keys.clear()

            for lease in leases_snapshot:
                if lease.lopnet_path:
                    lopnet_candidates.setdefault(lease.lopnet_path, None)
            for path in list(self._hip_guard.active_entries().keys()):
                lopnet_candidates.setdefault(path, None)
            lopnet_candidates.setdefault("/stage", None)

            if resolved_viewer is not None:
                try:
                    cur_lopnet, _ = self._resolve_lopnet_and_display(resolved_viewer)
                    cur_path = str(cur_lopnet.path())
                    lopnet_candidates[cur_path] = cur_lopnet
                except Exception:
                    pass

            cleared_lopnets: list[str] = []
            total_removed = 0

            for path, node_obj in list(lopnet_candidates.items()):
                target_node = node_obj
                if target_node is None and hou is not None and callable(getattr(hou, "node", None)):
                    try:
                        target_node = hou.node(path)
                    except Exception:
                        target_node = None
                self._hip_guard.unregister_active(path)
                if target_node is None:
                    continue
                try:
                    res = self.engine.clear_owned_overrides(target_node, session_id=None)
                    cleared_lopnets.append(path)
                    total_removed += int(res.get("removed_count", 0))
                except Exception as exc:
                    logger.debug("emergency_reset clear_owned_overrides skipped %s: %s", path, exc)

            # Sweep any remaining orphaned KarmaRT specs across the scene graph.
            try:
                orphan_reports = self._hip_guard.sweep_orphaned_overrides()
                for report in orphan_reports:
                    total_removed += int(report.get("removed_count", 0))
                    lpath = str(report.get("lopnet_path") or "")
                    if lpath and lpath not in cleared_lopnets:
                        cleared_lopnets.append(lpath)
            except Exception as exc:
                logger.debug("emergency_reset orphan sweep skipped: %s", exc)

            self._ref_grid_guard.release_all()
            self.auto_turbo.reset_all()
            self._focus_crop_prefs.clear()
            self._res_scale_prefs.clear()
            if self._saved_panes_follow is not None:
                _set_panes_follow_current_node(hou, self._saved_panes_follow)
                self._saved_panes_follow = None
            if resolved_viewer is not None:
                _set_stage_locked(resolved_viewer, False)
                try:
                    cur_lopnet, cur_disp = self._resolve_lopnet_and_display(resolved_viewer)
                    self.engine.clear_solo_overrides(cur_lopnet, display_lop=cur_disp)
                except Exception:
                    pass

            for lease in leases_snapshot:
                try:
                    self._restore_viewer_visual_state(lease)
                except Exception:
                    pass
                self._unsubscribe_viewer_events(lease)

            msg = (
                f"Emergency reset completed: cleared {total_removed} owned spec(s) across "
                f"{len(cleared_lopnets)} LOP network(s), released reference grid guard, "
                "and reset Auto-Turbo."
            )
            self.ledger.record(
                event="emergency_reset",
                decision="force_clear_all_owned_overrides",
                via="LOP:Custom",
                proof=f"lopnets={cleared_lopnets}; removed_count={total_removed}",
                stage_serial=_get_stage_serial(resolved_viewer) if resolved_viewer is not None else None,
                observed_delegate=_get_viewer_renderer(resolved_viewer) if resolved_viewer is not None else UNKNOWN,
            )

            if resolved_viewer is not None:
                snap = self._build_snapshot(
                    resolved_viewer,
                    lease=None,
                    state_override=SessionState.READY_OFF,
                    error_override="",
                    message_override=msg,
                )
            else:
                xpu_summary, hw_snap = _summarize_xpu_devices(hou)
                snap = SessionSnapshot(
                    state=SessionState.READY_OFF,
                    override_active=False,
                    final_render_untouched=True,
                    xpu_device_summary=xpu_summary,
                    vram_eviction_risk=hw_snap.vram_eviction_risk,
                    optix_cache_ready=hw_snap.optix_cache_ready,
                    message=msg,
                    available_delegates=self._available_delegates(),
                )
            self._notify_listeners(snap)
            return snap
        finally:
            self._locks["emergency"] = False


_CONTROLLER_SINGLETON: KarmaSessionController | None = None


def get_controller() -> KarmaSessionController:
    """Return the process-wide ``KarmaSessionController`` singleton."""
    global _CONTROLLER_SINGLETON
    if _CONTROLLER_SINGLETON is None:
        _CONTROLLER_SINGLETON = KarmaSessionController()
    return _CONTROLLER_SINGLETON


def reset_controller_for_tests() -> None:
    """Tear down and clear the singleton controller (for unit tests)."""
    global _CONTROLLER_SINGLETON
    ctrl = _CONTROLLER_SINGLETON
    _CONTROLLER_SINGLETON = None
    if ctrl is not None:
        try:
            ctrl._hip_guard.uninstall()
        except Exception:
            pass
        try:
            ctrl._ref_grid_guard.release_all()
        except Exception:
            pass
        try:
            ctrl.auto_turbo.reset_all()
        except Exception:
            pass


__all__ = [
    "KarmaSessionError",
    "SessionState",
    "SessionSnapshot",
    "KarmaSessionController",
    "get_controller",
    "reset_controller_for_tests",
]
