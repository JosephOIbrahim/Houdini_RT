"""Auto-Turbo real-time camera/edit lookahead and settle control engine for KarmaRT.

Tracks per-viewer camera transforms (``curViewport().viewTransform()``), USD stage
serial deltas (``viewer.stageSerial()``), and Solaris viewport interaction events
(``Resizing``, ``BeginResize``, ``StateEntered``) to engage zero-restart IPR
overrides during active interaction and restore settled preset quality after
``settle_delay_ms`` of quiet.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
import math
import time
from typing import Any

from .catalog import H22Catalog, VULKAN_ALIASES, load_h22_catalog
from .presets import AUTO_TURBO_OVERRIDES, validate_overrides_zero_restart

_LOOKAHEAD_START_EVENTS: frozenset[str] = frozenset(
    {
        "resizing",
        "beginresize",
        "stateentered",
    }
)

_LOOKAHEAD_END_EVENTS: frozenset[str] = frozenset(
    {
        "endresize",
        "stateexited",
    }
)

_KNOWN_SDF_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "bool",
        "int",
        "int2",
        "int3",
        "int4",
        "float",
        "float2",
        "float3",
        "float4",
        "double",
        "double2",
        "double3",
        "double4",
        "string",
        "token",
        "asset",
        "bool[]",
        "int[]",
        "float[]",
        "double[]",
        "string[]",
        "token[]",
    }
)

_VULKAN_ALIAS_FOLDED: frozenset[str] = frozenset(alias.casefold() for alias in VULKAN_ALIASES)


@dataclass(frozen=True, slots=True)
class AutoTurboConfig:
    """Tuning parameters for the KarmaRT Auto-Turbo control loop."""

    settle_delay_ms: int = 300
    poll_interval_ms: int = 60
    transform_epsilon: float = 1e-4
    enable_vk_fast_sampling: bool = True
    hybrid_vk_handoff: bool = False
    vk_handoff_cooldown_ms: int = 1200

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TurboTickResult:
    """Single evaluation result from ``AutoTurboEngine.evaluate``."""

    action: str
    reason: str
    motion_magnitude: float = 0.0
    stage_delta: bool = False
    elapsed_quiet_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _ViewerTurboState:
    """Per-viewer camera, stage-serial, and event lookahead tracking state."""

    last_transform: tuple[float, ...] | None = None
    last_stage_serial: int | None = None
    last_motion_ts: float | None = None
    pending_event_impulse: bool = False
    event_reason: str = ""
    engaged: bool = False
    engaged_mode: str = ""
    last_vk_handoff_ts: float | None = None
    suppress_stage_echo_until: float = 0.0
    pending_self_serial_echoes: int = 0


def _normalize_event_name(event_name: Any) -> str:
    if event_name is None:
        return ""
    name_attr = getattr(event_name, "name", None)
    if callable(name_attr):
        try:
            resolved = str(name_attr())
            if resolved:
                return resolved.rsplit(".", 1)[-1]
        except Exception:
            pass
    elif isinstance(name_attr, str) and name_attr:
        return name_attr.rsplit(".", 1)[-1]
    text = str(event_name).strip()
    if not text:
        return ""
    return text.rsplit(".", 1)[-1]


def _is_vulkan_delegate(active_delegate: str | None) -> bool:
    if not active_delegate:
        return False
    folded = str(active_delegate).strip().casefold()
    if not folded:
        return False
    if folded in _VULKAN_ALIAS_FOLDED:
        return True
    return "vulkan" in folded or folded.endswith(" vk")


def _extract_matrix16(raw_transform: Any) -> tuple[float, ...] | None:
    """Convert a ``hou.Matrix4`` or 16-float / 4x4 sequence into a flat tuple."""
    if raw_transform is None:
        return None

    candidate = raw_transform
    as_tuple_fn = getattr(raw_transform, "asTuple", None)
    if callable(as_tuple_fn):
        try:
            candidate = as_tuple_fn()
        except Exception:
            return None
    else:
        as_tuple_of_tuples_fn = getattr(raw_transform, "asTupleOfTuples", None)
        if callable(as_tuple_of_tuples_fn):
            try:
                candidate = as_tuple_of_tuples_fn()
            except Exception:
                return None

    if isinstance(candidate, (str, bytes)):
        return None

    flat: list[float] = []
    if isinstance(candidate, Sequence):
        for item in candidate:
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                for sub in item:
                    try:
                        val = float(sub)
                    except (TypeError, ValueError):
                        return None
                    if math.isnan(val) or math.isinf(val):
                        return None
                    flat.append(val)
            else:
                try:
                    val = float(item)
                except (TypeError, ValueError):
                    return None
                if math.isnan(val) or math.isinf(val):
                    return None
                flat.append(val)
    else:
        return None

    if len(flat) != 16:
        return None
    return tuple(flat)


def _sample_view_transform(viewer: Any) -> tuple[float, ...] | None:
    if viewer is None:
        return None
    cur_vp_fn = getattr(viewer, "curViewport", None)
    if not callable(cur_vp_fn):
        return None
    try:
        vp = cur_vp_fn()
    except Exception:
        return None
    if vp is None:
        return None
    vt_fn = getattr(vp, "viewTransform", None)
    if not callable(vt_fn):
        return None
    try:
        raw = vt_fn()
    except Exception:
        return None
    return _extract_matrix16(raw)


def _sample_stage_serial(viewer: Any) -> int | None:
    if viewer is None:
        return None
    serial_fn = getattr(viewer, "stageSerial", None)
    if not callable(serial_fn):
        return None
    try:
        raw = serial_fn()
    except Exception:
        return None
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def unwrap_typed_overrides(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    """Unwrap ``(sdf_type, value)`` preset tuples into raw values for ``SessionOverrideEngine``."""
    if not overrides:
        return {}
    unwrapped: dict[str, Any] = {}
    for attr_name, raw_entry in overrides.items():
        if (
            isinstance(raw_entry, tuple)
            and len(raw_entry) == 2
            and isinstance(raw_entry[0], str)
            and raw_entry[0].strip().lower() in _KNOWN_SDF_TYPE_NAMES
        ):
            unwrapped[str(attr_name)] = raw_entry[1]
        else:
            unwrapped[str(attr_name)] = raw_entry
    return unwrapped


def build_turbo_merged_overrides(
    base_overrides: Mapping[str, Any] | None,
    catalog: H22Catalog | None = None,
) -> dict[str, Any]:
    """Merge ``AUTO_TURBO_OVERRIDES`` onto ``base_overrides`` with strict zero-restart validation.

    Raises ``ValueError`` if any merged attribute triggers a Karma Hydra delegate restart.
    """
    active_catalog = catalog if catalog is not None else load_h22_catalog()
    merged: dict[str, Any] = dict(base_overrides) if base_overrides is not None else {}
    merged.update(AUTO_TURBO_OVERRIDES)
    validate_overrides_zero_restart(merged, active_catalog, allow_restart=False)
    return merged


class AutoTurboEngine:
    """Stateful per-viewer camera/edit motion detector and settle timer."""

    def __init__(
        self,
        config: AutoTurboConfig | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config: AutoTurboConfig = config if config is not None else AutoTurboConfig()
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._states: dict[str, _ViewerTurboState] = {}

    def _now(self, now: float | None = None) -> float:
        return float(self._clock() if now is None else now)

    def _state_for(self, viewer_key: str) -> _ViewerTurboState:
        key = str(viewer_key)
        state = self._states.get(key)
        if state is None:
            state = _ViewerTurboState()
            self._states[key] = state
        return state

    def is_engaged(self, viewer_key: str) -> bool:
        state = self._states.get(str(viewer_key))
        return bool(state.engaged) if state is not None else False

    def can_vk_handoff(self, viewer_key: str, now: float | None = None) -> bool:
        """Return True if optional hybrid Vulkan handoff is enabled and past cooldown."""
        if not self.config.hybrid_vk_handoff:
            return False
        state = self._states.get(str(viewer_key))
        if state is None or state.last_vk_handoff_ts is None:
            return True
        elapsed_ms = (self._now(now) - state.last_vk_handoff_ts) * 1000.0
        return elapsed_ms >= float(self.config.vk_handoff_cooldown_ms)

    def mark_vk_handoff(self, viewer_key: str, now: float | None = None) -> None:
        state = self._state_for(viewer_key)
        state.last_vk_handoff_ts = self._now(now)

    def mark_internal_stage_write(
        self,
        viewer: Any,
        viewer_key: str,
        *,
        echo_window_ms: float = 2000.0,
        now: float | None = None,
    ) -> None:
        """Record a controller-authored ``LOP:Custom`` write so its async stageSerial echo never re-triggers Auto-Turbo."""
        state = self._state_for(viewer_key)
        ts = self._now(now)
        current_serial = _sample_stage_serial(viewer)
        if current_serial is not None:
            if state.last_stage_serial is not None and current_serial != state.last_stage_serial:
                # Synchronous bump already observed; absorb immediately.
                state.last_stage_serial = current_serial
            else:
                # SceneViewer may sync LOP:Custom asynchronously on a later UI paint tick.
                if state.last_stage_serial is None:
                    state.last_stage_serial = current_serial
                state.pending_self_serial_echoes += 1
        else:
            state.pending_self_serial_echoes += 1
        state.suppress_stage_echo_until = ts + max(0.0, float(echo_window_ms)) / 1000.0

    def notify_interaction_event(
        self,
        viewer_key: str,
        event_name: Any,
        now: float | None = None,
    ) -> None:
        """Record a Solaris SceneViewer interaction lookahead impulse."""
        norm = _normalize_event_name(event_name)
        if not norm:
            return
        folded = norm.casefold()
        ts = self._now(now)
        state = self._state_for(viewer_key)

        if folded in _LOOKAHEAD_START_EVENTS:
            state.last_motion_ts = ts
            state.pending_event_impulse = True
            state.event_reason = f"event:{norm}"
        elif folded in _LOOKAHEAD_END_EVENTS:
            state.pending_event_impulse = False

    def evaluate(
        self,
        viewer: Any,
        viewer_key: str,
        active_delegate: str,
        now: float | None = None,
    ) -> TurboTickResult:
        """Evaluate camera transform delta, stage serial delta, and settle window."""
        ts = self._now(now)
        state = self._state_for(viewer_key)

        current_transform = _sample_view_transform(viewer)
        motion_magnitude = 0.0
        camera_moved = False
        if current_transform is not None:
            if state.last_transform is not None and len(state.last_transform) == len(
                current_transform
            ):
                motion_magnitude = max(
                    abs(curr - prev)
                    for curr, prev in zip(current_transform, state.last_transform)
                )
                camera_moved = motion_magnitude > float(self.config.transform_epsilon)
            state.last_transform = current_transform

        current_serial = _sample_stage_serial(viewer)
        stage_delta = False
        if ts > state.suppress_stage_echo_until:
            state.pending_self_serial_echoes = 0
        if current_serial is not None:
            if state.last_stage_serial is not None and current_serial != state.last_stage_serial:
                if state.pending_self_serial_echoes > 0 and ts <= state.suppress_stage_echo_until:
                    state.pending_self_serial_echoes -= 1
                else:
                    stage_delta = True
            state.last_stage_serial = current_serial

        event_impulse = False
        event_reason = ""
        if state.pending_event_impulse:
            state.pending_event_impulse = False
            event_age_ms = (
                (ts - state.last_motion_ts) * 1000.0 if state.last_motion_ts is not None else 0.0
            )
            if event_age_ms < float(max(self.config.settle_delay_ms, 1)):
                event_impulse = True
                event_reason = state.event_reason or "event:interaction"

        reasons: list[str] = []
        if camera_moved:
            reasons.append("camera_motion")
        if stage_delta:
            reasons.append("stage_edit")
        if event_impulse:
            reasons.append(event_reason)

        has_activity = bool(reasons)
        is_vulkan = _is_vulkan_delegate(active_delegate)

        if has_activity:
            if camera_moved or stage_delta or state.last_motion_ts is None:
                state.last_motion_ts = ts
            elapsed_quiet_ms = max(0.0, (ts - state.last_motion_ts) * 1000.0)
            reason_text = "+".join(reasons)

            if not state.engaged:
                if is_vulkan:
                    if not self.config.enable_vk_fast_sampling:
                        return TurboTickResult(
                            action="none",
                            reason="vk_fast_sampling_disabled",
                            motion_magnitude=motion_magnitude,
                            stage_delta=stage_delta,
                            elapsed_quiet_ms=elapsed_quiet_ms,
                        )
                    state.engaged = True
                    state.engaged_mode = "vk_fast"
                    return TurboTickResult(
                        action="vk_fast",
                        reason=reason_text,
                        motion_magnitude=motion_magnitude,
                        stage_delta=stage_delta,
                        elapsed_quiet_ms=elapsed_quiet_ms,
                    )

                state.engaged = True
                state.engaged_mode = "karma_turbo"
                return TurboTickResult(
                    action="engage",
                    reason=reason_text,
                    motion_magnitude=motion_magnitude,
                    stage_delta=stage_delta,
                    elapsed_quiet_ms=elapsed_quiet_ms,
                )

            return TurboTickResult(
                action="none",
                reason=f"holding:{reason_text}",
                motion_magnitude=motion_magnitude,
                stage_delta=stage_delta,
                elapsed_quiet_ms=elapsed_quiet_ms,
            )

        elapsed_quiet_ms = (
            max(0.0, (ts - state.last_motion_ts) * 1000.0)
            if state.last_motion_ts is not None
            else 0.0
        )

        if state.engaged and elapsed_quiet_ms >= float(self.config.settle_delay_ms):
            prev_mode = state.engaged_mode
            state.engaged = False
            state.engaged_mode = ""
            suffix = f"({prev_mode})" if prev_mode else ""
            return TurboTickResult(
                action="settle",
                reason=f"settled_after_{elapsed_quiet_ms:.1f}ms{suffix}",
                motion_magnitude=motion_magnitude,
                stage_delta=stage_delta,
                elapsed_quiet_ms=elapsed_quiet_ms,
            )

        return TurboTickResult(
            action="none",
            reason="engaged_waiting_settle" if state.engaged else "idle",
            motion_magnitude=motion_magnitude,
            stage_delta=stage_delta,
            elapsed_quiet_ms=elapsed_quiet_ms,
        )

    def reset(self, viewer_key: str) -> None:
        """Clear all per-viewer motion/settle tracking cleanly."""
        self._states.pop(str(viewer_key), None)

    def reset_all(self) -> None:
        """Clear tracking state for all viewers."""
        self._states.clear()


__all__ = [
    "AutoTurboConfig",
    "TurboTickResult",
    "AutoTurboEngine",
    "build_turbo_merged_overrides",
    "unwrap_typed_overrides",
]
