"""Progressive-disclosure PySide6 Python Panel for Houdini 22 KarmaRT.

Designed for Houdini 22 Solaris artists and TDs:
- Works identically under live Houdini 22 GUI and headless ``hython -offscreen``
  (``QT_QPA_PLATFORM=offscreen``, ``hou.isUIAvailable() == False``).
- Never blocks the Houdini UI thread or opens modal dialogs.
- Strictly reflects observed ``SessionSnapshot`` state with zero fabricated status.
- Cleans up listeners and Auto-Turbo ``QTimer`` deterministically on close/destroy.
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from karma_rt.presets import (
    FOCUS_CROP_REGIONS,
    SUPPORTED_RESOLUTION_SCALES,
    get_presets,
)


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


def _get_default_controller() -> Any:
    mod = _controller_module()
    if mod is None or not hasattr(mod, "get_controller"):
        raise _FallbackKarmaSessionError(
            "karma_rt.controller.get_controller() is not available."
        )
    return mod.get_controller()


_PRESET_BUTTON_SPECS: tuple[tuple[str, str, str], ...] = (
    ("live_draft", "Live Draft\n(9 spp · Max FPS)", "Karma XPU"),
    ("interactive_lookdev", "Interactive Lookdev\n(36 spp + Denoise)", "Karma XPU"),
    ("final_parity", "Final Parity\n(Full Karma 1:1)", "Karma XPU"),
    ("vulkan_layout", "Vulkan RT Layout\n(Houdini VK)", "Houdini VK"),
    ("clay_headlight", "Clay / Headlight AO (60x Silhouette)", "Karma XPU"),
)

_STATE_LABELS: dict[str, tuple[str, str]] = {
    "UNINITIALIZED": ("OFF", "off"),
    "PREFLIGHT_CHECKING": ("CHECKING", "active"),
    "UNAVAILABLE": ("UNAVAILABLE", "degraded"),
    "READY_OFF": ("OFF", "off"),
    "PREVIEW_DIFF": ("PREVIEW", "active"),
    "APPLYING_OVERRIDE": ("APPLYING", "active"),
    "ACTIVE_IDLE": ("ACTIVE", "active"),
    "ACTIVE_TURBO_INTERACT": ("TURBO", "turbo"),
    "ACTIVE_PAUSED": ("PAUSED", "paused"),
    "ACTIVE_DISK_RENDER_YIELD": ("DISK YIELD", "yield"),
    "RESTORING": ("RESTORING", "active"),
    "EXTERNAL_TAKEOVER": ("EXTERNAL", "paused"),
    "DEGRADED": ("DEGRADED", "degraded"),
}

_PANEL_STYLESHEET = """
QWidget#KarmaRTPanelWidget {
    background-color: #232323;
    color: #e6e6e6;
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 12px;
}
QScrollArea#panelScrollArea, QWidget#panelScrollContent {
    background-color: #232323;
    border: none;
}
QFrame#hudCard, QFrame#tierCard, QFrame#drawerBody {
    background-color: #2c2c2c;
    border: 1px solid #3f3f3f;
    border-radius: 5px;
}
QLabel#panelTitle {
    color: #f4f4f5;
    font-size: 14px;
    font-weight: 700;
}
QLabel#metaLabel, QLabel#subtleLabel {
    color: #a1a1aa;
    font-size: 11px;
}
QLabel#sectionHeader {
    color: #d4d4d8;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.4px;
}
QLabel#statusPill {
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
    border: 1px solid #52525b;
    background-color: #333338;
    color: #e4e4e7;
}
QLabel#statusPill[pillTone="active"] {
    background-color: #123347;
    border: 1px solid #38bdf8;
    color: #bae6fd;
}
QLabel#statusPill[pillTone="turbo"] {
    background-color: #103b4a;
    border: 1px solid #22d3ee;
    color: #a5f3fc;
}
QLabel#statusPill[pillTone="vulkan"] {
    background-color: #2c1e4a;
    border: 1px solid #a78bfa;
    color: #ede9fe;
}
QLabel#statusPill[pillTone="paused"] {
    background-color: #422b08;
    border: 1px solid #f59e0b;
    color: #fde68a;
}
QLabel#statusPill[pillTone="yield"] {
    background-color: #45260b;
    border: 1px solid #fb923c;
    color: #fed7aa;
}
QLabel#statusPill[pillTone="degraded"] {
    background-color: #4a151b;
    border: 1px solid #ef4444;
    color: #fecaca;
}
QLabel#statusPill[pillTone="off"] {
    background-color: #323236;
    border: 1px solid #52525b;
    color: #d4d4d8;
}
QLabel#overrideBadge {
    background-color: #103a28;
    border: 1px solid #22c55e;
    border-radius: 4px;
    color: #86efac;
    font-size: 11px;
    font-weight: 700;
    padding: 5px 8px;
}
QLabel#overrideBadge[safetyTone="warn"] {
    background-color: #422b08;
    border: 1px solid #f59e0b;
    color: #fde68a;
}
QLabel#gpuBadge {
    background-color: #252830;
    border: 1px solid #3f4654;
    border-radius: 4px;
    color: #cbd5e1;
    font-size: 11px;
    font-weight: 600;
    padding: 4px 8px;
}
QLabel#gpuBadge[vramTone="ok"] {
    background-color: #162b26;
    border: 1px solid #2dd4bf;
    color: #99f6e4;
}
QLabel#gpuBadge[vramTone="warn"] {
    background-color: #422b08;
    border: 1px solid #f59e0b;
    color: #fde68a;
}
QLabel#gpuBadge[vramTone="critical"] {
    background-color: #4a151b;
    border: 1px solid #ef4444;
    color: #fecaca;
}
QPushButton[miniToggle="true"] {
    padding: 4px 6px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton[miniToggle="true"]:checked {
    background-color: #15425c;
    border: 1px solid #38bdf8;
    color: #e0f2fe;
}
QFrame#cameraLockBar {
    background-color: #422b08;
    border: 1px solid #f59e0b;
    border-radius: 4px;
}
QLabel#cameraLockLabel {
    color: #fde68a;
    font-size: 11px;
    font-weight: 700;
}
QPushButton#unlockOrbitBtn {
    background-color: #b45309;
    border: 1px solid #fbbf24;
    border-radius: 4px;
    color: #fffbeb;
    font-size: 11px;
    font-weight: 700;
    padding: 3px 9px;
}
QPushButton#unlockOrbitBtn:hover {
    background-color: #d97706;
}
QLabel#feedbackLabel {
    background-color: #26262a;
    border: 1px solid #3f3f46;
    border-radius: 4px;
    color: #d4d4d8;
    font-size: 11px;
    padding: 4px 8px;
}
QLabel#feedbackLabel[feedbackTone="error"] {
    background-color: #451419;
    border: 1px solid #ef4444;
    color: #fecaca;
}
QLabel#feedbackLabel[feedbackTone="warn"] {
    background-color: #422b08;
    border: 1px solid #f59e0b;
    color: #fde68a;
}
QLabel#feedbackLabel[feedbackTone="ok"] {
    background-color: #103a28;
    border: 1px solid #22c55e;
    color: #bbf7d0;
}
QPushButton {
    background-color: #38383d;
    border: 1px solid #52525b;
    border-radius: 4px;
    color: #f4f4f5;
    padding: 5px 8px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #44444b;
    border-color: #71717a;
}
QPushButton:pressed {
    background-color: #2f2f34;
}
QPushButton:disabled {
    background-color: #2b2b2f;
    border-color: #3a3a40;
    color: #71717a;
}
QPushButton[presetButton="true"] {
    min-height: 36px;
    font-size: 12px;
    text-align: center;
}
QPushButton[presetButton="true"]:checked {
    background-color: #15425c;
    border: 1px solid #38bdf8;
    color: #e0f2fe;
}
QPushButton#restoreBtn {
    background-color: #164e39;
    border: 1px solid #22c55e;
    color: #dcfce7;
    font-weight: 700;
}
QPushButton#restoreBtn:hover {
    background-color: #1b6147;
}
QPushButton#emergencyResetBtn {
    background-color: #5b1820;
    border: 1px solid #ef4444;
    color: #fee2e2;
    font-weight: 700;
}
QPushButton#emergencyResetBtn:hover {
    background-color: #781f29;
}
QPushButton#applyVerifiedBtn {
    background-color: #15425c;
    border: 1px solid #38bdf8;
    color: #e0f2fe;
    font-weight: 700;
}
QPushButton#applyVerifiedBtn:hover {
    background-color: #1a5475;
}
QPushButton#drawerToggle {
    background-color: #303035;
    border: 1px solid #45454c;
    border-radius: 4px;
    color: #e4e4e7;
    font-weight: 700;
    padding: 6px 10px;
    text-align: left;
}
QPushButton#drawerToggle:hover {
    background-color: #3a3a40;
}
QComboBox {
    background-color: #333338;
    border: 1px solid #52525b;
    border-radius: 4px;
    color: #f4f4f5;
    font-size: 12px;
    padding: 3px 7px;
}
QComboBox QAbstractItemView {
    background-color: #2b2b30;
    color: #f4f4f5;
    font-size: 12px;
    selection-background-color: #15425c;
}
QCheckBox {
    color: #e4e4e7;
    font-size: 12px;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
}
QTableWidget {
    background-color: #222226;
    alternate-background-color: #28282d;
    border: 1px solid #3f3f46;
    color: #e4e4e7;
    gridline-color: #38383f;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
}
QHeaderView::section {
    background-color: #303036;
    color: #d4d4d8;
    border: 1px solid #3f3f46;
    padding: 4px 6px;
    font-weight: 700;
    font-size: 11px;
}
QListWidget, QPlainTextEdit {
    background-color: #202024;
    border: 1px solid #3f3f46;
    border-radius: 4px;
    color: #e4e4e7;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
}
"""


def _repolish(widget: QtWidgets.QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def _extract_viewer_name(viewer: Any) -> str:
    if viewer is None:
        return ""
    if isinstance(viewer, str):
        return viewer.strip()
    name_attr = getattr(viewer, "name", None)
    if callable(name_attr):
        try:
            return str(name_attr()).strip()
        except Exception:
            return ""
    if isinstance(name_attr, str):
        return name_attr.strip()
    return str(viewer).strip()


def _format_diff_value(value: Any) -> tuple[str, str]:
    """Return ``(compact_display, full_tooltip)`` for a USD attribute value."""
    if value is None:
        return "—", "<unset / default>"
    raw = str(value)
    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list) and parsed:
                # pixeloracle: ["variance", {...}]
                if isinstance(parsed[0], str) and len(parsed) >= 2 and isinstance(parsed[1], dict):
                    cfg = parsed[1]
                    mode = parsed[0]
                    var = cfg.get("variance")
                    minr = cfg.get("minrays")
                    return f"{mode}(min={minr}, var={var})", raw
                # imagefilter: [["denoise", {"engine": "optix", ...}]]
                if isinstance(parsed[0], list) and len(parsed[0]) >= 2 and isinstance(parsed[0][1], dict):
                    flt_name = parsed[0][0]
                    cfg = parsed[0][1]
                    eng = cfg.get("engine", "")
                    aovs = ",".join(str(x) for x in (cfg.get("aovs") or ()))
                    return f"{flt_name}:{eng}({aovs})", raw
        except Exception:
            pass
    if len(raw) > 34:
        return f"{raw[:31]}...", raw
    return raw, raw


class _CollapsibleDrawer(QtWidgets.QWidget):
    """Lightweight progressive-disclosure drawer (collapsed by default)."""

    toggled = QtCore.Signal(bool)

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = False,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._badge = ""

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self.toggle_button = QtWidgets.QPushButton(self)
        self.toggle_button.setObjectName("drawerToggle")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.toggle_button.toggled.connect(self._handle_toggled)
        outer.addWidget(self.toggle_button)

        self.body = QtWidgets.QFrame(self)
        self.body.setObjectName("drawerBody")
        self.body.setVisible(expanded)
        self.content_layout = QtWidgets.QVBoxLayout(self.body)
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.content_layout.setSpacing(8)
        outer.addWidget(self.body)

        self._update_title()

    def _update_title(self) -> None:
        arrow = "▼" if self.toggle_button.isChecked() else "▶"
        suffix = f"  [{self._badge}]" if self._badge else ""
        self.toggle_button.setText(f"{arrow}  {self._title}{suffix}")

    def _handle_toggled(self, checked: bool) -> None:
        self.body.setVisible(checked)
        self._update_title()
        self.toggled.emit(checked)

    def is_expanded(self) -> bool:
        return bool(self.toggle_button.isChecked())

    def set_expanded(self, expanded: bool) -> None:
        self.toggle_button.setChecked(bool(expanded))

    def set_badge(self, text: str) -> None:
        self._badge = text.strip()
        self._update_title()


class KarmaRTPanelWidget(QtWidgets.QWidget):
    """Progressive-disclosure PySide6 Python Panel for Houdini 22 KarmaRT."""

    def __init__(
        self,
        controller: Any = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("KarmaRTPanelWidget")
        self.setWindowTitle("Karma RT")
        self.setMinimumWidth(330)
        self.setStyleSheet(_PANEL_STYLESHEET)

        self._controller: Any = (
            controller if controller is not None else _get_default_controller()
        )
        self._snapshot: Any | None = None
        self._last_preview: Any | None = None
        self._preset_specs = get_presets()
        self._auto_turbo_enabled: bool = False
        self._ticking_auto_turbo: bool = False
        self._cleaned_up: bool = False
        self._listener_registered: bool = False

        # Non-blocking Auto-Turbo QTimer (runs ONLY when enabled AND visible).
        poll_ms = 60
        auto_turbo_obj = getattr(self._controller, "auto_turbo", None)
        cfg = getattr(auto_turbo_obj, "config", None)
        cfg_poll = getattr(cfg, "poll_interval_ms", None)
        if isinstance(cfg_poll, int) and cfg_poll > 0:
            poll_ms = cfg_poll
        self._auto_turbo_timer = QtCore.QTimer(self)
        self._auto_turbo_timer.setSingleShot(False)
        self._auto_turbo_timer.setInterval(poll_ms)
        self._auto_turbo_timer.timeout.connect(self._on_auto_turbo_tick)

        self._build_ui()
        self._register_listener()

        # Guard cleanup if Qt destroys the C++ object directly.
        controller_ref = self._controller
        listener_ref = self._on_controller_snapshot
        timer_ref = self._auto_turbo_timer

        def _on_destroyed(_obj: Any = None) -> None:
            try:
                timer_ref.stop()
            except Exception:
                pass
            try:
                controller_ref.remove_listener(listener_ref)
            except Exception:
                pass

        self.destroyed.connect(_on_destroyed)

        self.refresh_viewers()
        self.refresh_state()

    @property
    def controller(self) -> Any:
        return self._controller

    @property
    def snapshot(self) -> Any | None:
        return self._snapshot

    @property
    def auto_turbo_timer(self) -> QtCore.QTimer:
        return self._auto_turbo_timer

    def _register_listener(self) -> None:
        add_fn = getattr(self._controller, "add_listener", None)
        if callable(add_fn) and not self._listener_registered:
            try:
                add_fn(self._on_controller_snapshot)
                self._listener_registered = True
            except Exception:
                self._listener_registered = False

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setObjectName("panelScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        root_layout.addWidget(scroll)

        content = QtWidgets.QWidget(scroll)
        content.setObjectName("panelScrollContent")
        content.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        scroll.setWidget(content)

        main_layout = QtWidgets.QVBoxLayout(content)
        main_layout.setContentsMargins(8, 8, 8, 10)
        main_layout.setSpacing(8)

        # ------------------------------------------------------------------
        # Top Truthful Status Strip (Mini-HUD)
        # ------------------------------------------------------------------
        hud_card = QtWidgets.QFrame(content)
        hud_card.setObjectName("hudCard")
        hud_layout = QtWidgets.QVBoxLayout(hud_card)
        hud_layout.setContentsMargins(9, 9, 9, 9)
        hud_layout.setSpacing(6)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(6)
        title_lbl = QtWidgets.QLabel("Karma RT v2.0", hud_card)
        title_lbl.setObjectName("panelTitle")
        header_row.addWidget(title_lbl, 0)
        header_row.addStretch(1)

        self.status_pill = QtWidgets.QLabel("OFF", hud_card)
        self.status_pill.setObjectName("statusPill")
        self.status_pill.setProperty("pillTone", "off")
        self.status_pill.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(self.status_pill, 0)
        hud_layout.addLayout(header_row)

        self.gpu_badge = QtWidgets.QLabel("GPU: Probing...", hud_card)
        self.gpu_badge.setObjectName("gpuBadge")
        self.gpu_badge.setProperty("vramTone", "neutral")
        self.gpu_badge.setWordWrap(True)
        hud_layout.addWidget(self.gpu_badge)

        self.meta_label = QtWidgets.QLabel("LOP: — · Settings: — · Denoiser: UNKNOWN", hud_card)
        self.meta_label.setObjectName("metaLabel")
        self.meta_label.setWordWrap(True)
        hud_layout.addWidget(self.meta_label)

        # Emerald safety badge (visible strictly when override_active is True)
        self.override_badge = QtWidgets.QLabel(
            "VIEWPORT OVERRIDE ACTIVE · FINAL RENDER UNTOUCHED",
            hud_card,
        )
        self.override_badge.setObjectName("overrideBadge")
        self.override_badge.setProperty("safetyTone", "ok")
        self.override_badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.override_badge.setWordWrap(True)
        self.override_badge.setVisible(False)
        hud_layout.addWidget(self.override_badge)

        # Amber Camera Lock warning bar (visible strictly when camera_locked is True)
        self.camera_lock_bar = QtWidgets.QFrame(hud_card)
        self.camera_lock_bar.setObjectName("cameraLockBar")
        cam_row = QtWidgets.QHBoxLayout(self.camera_lock_bar)
        cam_row.setContentsMargins(8, 5, 8, 5)
        cam_row.setSpacing(8)
        self.camera_lock_label = QtWidgets.QLabel("CAM LOCKED:", self.camera_lock_bar)
        self.camera_lock_label.setObjectName("cameraLockLabel")
        self.camera_lock_label.setWordWrap(True)
        cam_row.addWidget(self.camera_lock_label, 1)

        self.unlock_orbit_btn = QtWidgets.QPushButton("Unlock Orbit", self.camera_lock_bar)
        self.unlock_orbit_btn.setObjectName("unlockOrbitBtn")
        self.unlock_orbit_btn.setToolTip(
            "Unlock camera and switch viewport to an interactive orbit copy."
        )
        self.unlock_orbit_btn.clicked.connect(self._on_unlock_orbit_clicked)
        cam_row.addWidget(self.unlock_orbit_btn, 0)
        self.camera_lock_bar.setVisible(False)
        hud_layout.addWidget(self.camera_lock_bar)

        # Viewer selector row
        viewer_row = QtWidgets.QHBoxLayout()
        viewer_row.setSpacing(6)
        viewer_lbl = QtWidgets.QLabel("Viewer:", hud_card)
        viewer_lbl.setObjectName("subtleLabel")
        viewer_row.addWidget(viewer_lbl)

        self.viewer_combo = QtWidgets.QComboBox(hud_card)
        self.viewer_combo.setMinimumContentsLength(10)
        self.viewer_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.viewer_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.viewer_combo.currentIndexChanged.connect(self._on_viewer_changed)
        viewer_row.addWidget(self.viewer_combo, 1)

        self.refresh_btn = QtWidgets.QPushButton("Refresh", hud_card)
        self.refresh_btn.setToolTip("Refresh Solaris Scene Viewer list and observed render state.")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        viewer_row.addWidget(self.refresh_btn, 0)
        hud_layout.addLayout(viewer_row)

        self.feedback_label = QtWidgets.QLabel("", hud_card)
        self.feedback_label.setObjectName("feedbackLabel")
        self.feedback_label.setProperty("feedbackTone", "info")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setVisible(False)
        hud_layout.addWidget(self.feedback_label)

        main_layout.addWidget(hud_card)

        # ------------------------------------------------------------------
        # Tier 0 — Default Artist Controls
        # ------------------------------------------------------------------
        tier0_card = QtWidgets.QFrame(content)
        tier0_card.setObjectName("tierCard")
        tier0_layout = QtWidgets.QVBoxLayout(tier0_card)
        tier0_layout.setContentsMargins(10, 10, 10, 10)
        tier0_layout.setSpacing(8)

        presets_header = QtWidgets.QLabel("LOOKDEV PRESETS (ZERO-RESTART)", tier0_card)
        presets_header.setObjectName("sectionHeader")
        tier0_layout.addWidget(presets_header)

        preset_grid = QtWidgets.QGridLayout()
        preset_grid.setSpacing(6)
        self.preset_buttons: dict[str, QtWidgets.QPushButton] = {}
        for idx, (preset_id, label, _default_delegate) in enumerate(_PRESET_BUTTON_SPECS):
            btn = QtWidgets.QPushButton(label, tier0_card)
            btn.setCheckable(True)
            btn.setProperty("presetButton", True)
            btn.setMinimumWidth(0)
            spec = self._preset_specs.get(preset_id)
            if spec is not None:
                btn.setToolTip(spec.description)
            btn.clicked.connect(
                lambda _checked=False, pid=preset_id: self._on_preset_clicked(pid)
            )
            self.preset_buttons[preset_id] = btn
            if preset_id == "clay_headlight":
                btn.setMinimumHeight(30)
                preset_grid.addWidget(btn, 2, 0, 1, 2)
            else:
                preset_grid.addWidget(btn, idx // 2, idx % 2)
        tier0_layout.addLayout(preset_grid)

        # Speed Multipliers (Focus Crop & Session Resolution Scale — zero-restart)
        speed_row = QtWidgets.QHBoxLayout()
        speed_row.setSpacing(6)

        crop_lbl = QtWidgets.QLabel("Crop:", tier0_card)
        crop_lbl.setObjectName("subtleLabel")
        speed_row.addWidget(crop_lbl, 0)

        self.focus_crop_combo = QtWidgets.QComboBox(tier0_card)
        self.focus_crop_combo.setMinimumContentsLength(8)
        self.focus_crop_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        for crop_key, (crop_label, _ndc) in FOCUS_CROP_REGIONS.items():
            self.focus_crop_combo.addItem(crop_label, crop_key)
        self.focus_crop_combo.setToolTip(
            "Zero-restart dataWindowNDC Focus Crop (Center 50% = 4x fewer pixels, Center 33% = 9x fewer)."
        )
        self.focus_crop_combo.currentIndexChanged.connect(self._on_focus_crop_changed)
        speed_row.addWidget(self.focus_crop_combo, 1)

        scale_lbl = QtWidgets.QLabel("Res:", tier0_card)
        scale_lbl.setObjectName("subtleLabel")
        speed_row.addWidget(scale_lbl, 0)

        self.res_scale_combo = QtWidgets.QComboBox(tier0_card)
        for scale_val in SUPPORTED_RESOLUTION_SCALES:
            pct = int(round(scale_val * 100))
            self.res_scale_combo.addItem(f"{pct}%", float(scale_val))
        self.res_scale_combo.setToolTip(
            "Zero-restart viewport resolution multiplier on LOP:Custom (production resolution untouched)."
        )
        self.res_scale_combo.currentIndexChanged.connect(self._on_res_scale_changed)
        speed_row.addWidget(self.res_scale_combo, 0)
        tier0_layout.addLayout(speed_row)

        # Solaris Stage Acceleration Strip (Freeze VP, Pin Stage, Solo Selected)
        stage_row = QtWidgets.QHBoxLayout()
        stage_row.setSpacing(5)

        self.freeze_vp_btn = QtWidgets.QPushButton("Freeze", tier0_card)
        self.freeze_vp_btn.setCheckable(True)
        self.freeze_vp_btn.setProperty("miniToggle", True)
        self.freeze_vp_btn.setMinimumWidth(0)
        self.freeze_vp_btn.setToolTip(
            "Freeze viewport USD updates (setSceneGraphStageLocked) while editing upstream LOPs."
        )
        self.freeze_vp_btn.clicked.connect(self._on_freeze_vp_clicked)
        stage_row.addWidget(self.freeze_vp_btn, 1)

        self.pin_stage_btn = QtWidgets.QPushButton("Pin", tier0_card)
        self.pin_stage_btn.setCheckable(True)
        self.pin_stage_btn.setProperty("miniToggle", True)
        self.pin_stage_btn.setMinimumWidth(0)
        self.pin_stage_btn.setToolTip(
            "Pin stage to Display Flag (panesFollowCurrentNode=False) so selecting upstream LOPs never recomposes."
        )
        self.pin_stage_btn.clicked.connect(self._on_pin_stage_clicked)
        stage_row.addWidget(self.pin_stage_btn, 1)

        self.solo_geo_btn = QtWidgets.QPushButton("Solo G", tier0_card)
        self.solo_geo_btn.setProperty("miniToggle", True)
        self.solo_geo_btn.setMinimumWidth(0)
        self.solo_geo_btn.setToolTip(
            "Non-destructively solo selected Scene Graph geometry (zero USD recomposition)."
        )
        self.solo_geo_btn.clicked.connect(self._on_solo_geo_clicked)
        stage_row.addWidget(self.solo_geo_btn, 1)

        self.solo_lights_btn = QtWidgets.QPushButton("Solo L", tier0_card)
        self.solo_lights_btn.setProperty("miniToggle", True)
        self.solo_lights_btn.setMinimumWidth(0)
        self.solo_lights_btn.setToolTip(
            "Non-destructively solo selected Scene Graph lights."
        )
        self.solo_lights_btn.clicked.connect(self._on_solo_lights_clicked)
        stage_row.addWidget(self.solo_lights_btn, 1)

        self.unsolo_btn = QtWidgets.QPushButton("Clear", tier0_card)
        self.unsolo_btn.setProperty("miniToggle", True)
        self.unsolo_btn.setMinimumWidth(0)
        self.unsolo_btn.setToolTip("Clear viewport Solo Geometry and Solo Lights overrides.")
        self.unsolo_btn.clicked.connect(self._on_unsolo_clicked)
        stage_row.addWidget(self.unsolo_btn, 1)
        tier0_layout.addLayout(stage_row)

        # Engine selector + live toggles
        engine_row = QtWidgets.QHBoxLayout()
        engine_row.setSpacing(6)
        engine_lbl = QtWidgets.QLabel("Engine:", tier0_card)
        engine_lbl.setObjectName("subtleLabel")
        engine_row.addWidget(engine_lbl)

        self.engine_combo = QtWidgets.QComboBox(tier0_card)
        self.engine_combo.setMinimumContentsLength(6)
        self.engine_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.engine_combo.addItem("Karma XPU", "Karma XPU")
        self.engine_combo.addItem("Karma CPU", "Karma CPU")
        self.engine_combo.setToolTip(
            "Preferred Karma Hydra delegate for Live Draft, Interactive Lookdev, and Final Parity."
        )
        engine_row.addWidget(self.engine_combo, 1)
        tier0_layout.addLayout(engine_row)

        self.auto_turbo_checkbox = QtWidgets.QCheckBox(
            "Auto-Turbo on Tumble/Edit", tier0_card
        )
        self.auto_turbo_checkbox.setToolTip(
            "Temporarily lowers IPR proxy resolution and sample percentage during camera motion or edits."
        )
        self.auto_turbo_checkbox.toggled.connect(self._on_auto_turbo_toggled)
        tier0_layout.addWidget(self.auto_turbo_checkbox)

        toggles_row = QtWidgets.QHBoxLayout()
        toggles_row.setSpacing(10)
        self.clean_overlays_checkbox = QtWidgets.QCheckBox(
            "Clean Overlays", tier0_card
        )
        self.clean_overlays_checkbox.setToolTip(
            "Hide viewport guides, HUD clutter, and H22.0.429 reference grid during lookdev."
        )
        self.clean_overlays_checkbox.toggled.connect(self._on_clean_overlays_toggled)
        toggles_row.addWidget(self.clean_overlays_checkbox)

        self.render_stats_checkbox = QtWidgets.QCheckBox(
            "Karma Stats HUD", tier0_card
        )
        self.render_stats_checkbox.setToolTip(
            "Toggle Houdini's native Karma render statistics viewport overlay."
        )
        self.render_stats_checkbox.toggled.connect(self._on_render_stats_toggled)
        toggles_row.addWidget(self.render_stats_checkbox)
        tier0_layout.addLayout(toggles_row)

        # Direct Action Row
        actions_header = QtWidgets.QLabel("SESSION CONTROLS", tier0_card)
        actions_header.setObjectName("sectionHeader")
        tier0_layout.addWidget(actions_header)

        direct_row = QtWidgets.QHBoxLayout()
        direct_row.setSpacing(6)

        self.pause_btn = QtWidgets.QPushButton("Pause", tier0_card)
        self.pause_btn.setToolTip("Pause or resume interactive Hydra viewport rendering.")
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        direct_row.addWidget(self.pause_btn, 1)

        self.snapshot_btn = QtWidgets.QPushButton("Quick Snapshot", tier0_card)
        self.snapshot_btn.setToolTip("Capture a quick Solaris viewport snapshot.")
        self.snapshot_btn.clicked.connect(self._on_snapshot_clicked)
        direct_row.addWidget(self.snapshot_btn, 1)

        self.disk_yield_btn = QtWidgets.QPushButton("Disk Yield", tier0_card)
        self.disk_yield_btn.setToolTip("Yield Karma XPU VRAM for USD Render ROP Disk Render")
        self.disk_yield_btn.setToolTip(
            "Pause/release interactive Karma GPU memory for disk renders and resume cleanly afterwards."
        )
        self.disk_yield_btn.clicked.connect(self._on_disk_yield_clicked)
        direct_row.addWidget(self.disk_yield_btn, 1)
        tier0_layout.addLayout(direct_row)

        # Always-enabled high-contrast safety row
        safety_row = QtWidgets.QHBoxLayout()
        safety_row.setSpacing(6)

        self.restore_btn = QtWidgets.QPushButton("Restore Production", tier0_card)
        self.restore_btn.setObjectName("restoreBtn")
        self.restore_btn.setEnabled(True)
        self.restore_btn.setToolTip(
            "Remove Karma RT LOP:Custom overrides and restore the prior renderer cleanly."
        )
        self.restore_btn.clicked.connect(self._on_restore_clicked)
        safety_row.addWidget(self.restore_btn, 1)

        self.emergency_reset_btn = QtWidgets.QPushButton("Emergency Reset", tier0_card)
        self.emergency_reset_btn.setObjectName("emergencyResetBtn")
        self.emergency_reset_btn.setEnabled(True)
        self.emergency_reset_btn.setToolTip(
            "Force-clear all Karma RT session overrides, overlays, Auto-Turbo timers, and locks."
        )
        self.emergency_reset_btn.clicked.connect(self._on_emergency_reset_clicked)
        safety_row.addWidget(self.emergency_reset_btn, 1)
        tier0_layout.addLayout(safety_row)

        main_layout.addWidget(tier0_card)

        # ------------------------------------------------------------------
        # Tier 1 — Collapsible Preview Diff & H22 Scene Advisor Drawer
        # ------------------------------------------------------------------
        self.tier1_drawer = _CollapsibleDrawer(
            "Preview Diff && H22 Scene Advisor",
            expanded=False,
            parent=content,
        )
        t1 = self.tier1_drawer.content_layout

        diff_header_row = QtWidgets.QHBoxLayout()
        diff_header_row.setSpacing(6)
        self.preview_preset_combo = QtWidgets.QComboBox(self.tier1_drawer.body)
        for preset_id, _label, _delegate in _PRESET_BUTTON_SPECS:
            spec = self._preset_specs.get(preset_id)
            combo_label = spec.label if spec is not None else preset_id
            self.preview_preset_combo.addItem(combo_label, preset_id)
        self.preview_preset_combo.setCurrentIndex(1)  # interactive_lookdev default
        diff_header_row.addWidget(self.preview_preset_combo, 1)

        self.preview_diff_btn = QtWidgets.QPushButton(
            "Preview Diff (Dry-Run)", self.tier1_drawer.body
        )
        self.preview_diff_btn.clicked.connect(self._on_preview_diff_clicked)
        diff_header_row.addWidget(self.preview_diff_btn)

        self.apply_verified_btn = QtWidgets.QPushButton(
            "Apply Verified", self.tier1_drawer.body
        )
        self.apply_verified_btn.setObjectName("applyVerifiedBtn")
        self.apply_verified_btn.setEnabled(False)
        self.apply_verified_btn.clicked.connect(self._on_apply_verified_clicked)
        diff_header_row.addWidget(self.apply_verified_btn)
        t1.addLayout(diff_header_row)

        self.preview_summary_label = QtWidgets.QLabel(
            "Run a dry-run preview to inspect exact LOP:Custom attribute diffs before applying.",
            self.tier1_drawer.body,
        )
        self.preview_summary_label.setObjectName("subtleLabel")
        self.preview_summary_label.setWordWrap(True)
        t1.addWidget(self.preview_summary_label)

        self.diff_table = QtWidgets.QTableWidget(0, 4, self.tier1_drawer.body)
        self.diff_table.setHorizontalHeaderLabels(
            ["USD Attribute", "Production", "Viewport Session", "Restart Risk"]
        )
        self.diff_table.setAlternatingRowColors(True)
        self.diff_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.diff_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.diff_table.verticalHeader().setVisible(False)
        self.diff_table.setMinimumHeight(140)
        self.diff_table.setMaximumHeight(220)
        hdr = self.diff_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        t1.addWidget(self.diff_table)

        advisor_row = QtWidgets.QHBoxLayout()
        advisor_row.setSpacing(6)
        advisor_title = QtWidgets.QLabel("H22 SCENE BOTTLENECK ADVISOR", self.tier1_drawer.body)
        advisor_title.setObjectName("sectionHeader")
        advisor_row.addWidget(advisor_title, 1)

        self.scan_advisories_btn = QtWidgets.QPushButton(
            "Scan Scene Bottlenecks", self.tier1_drawer.body
        )
        self.scan_advisories_btn.clicked.connect(self._on_scan_advisories_clicked)
        advisor_row.addWidget(self.scan_advisories_btn)
        t1.addLayout(advisor_row)

        self.telemetry_summary_label = QtWidgets.QLabel(
            "Read-only scan checks Fog Equiangular MIS, scatterinstances Frustum Mask, "
            "Displaced Subdivs, missing tangentu, Principled/VEX fallbacks, flat >500-sibling "
            "hierarchies, Gaussian Splats, Light Tree, Double-Denoise, OptiX cache, and VRAM.",
            self.tier1_drawer.body,
        )
        self.telemetry_summary_label.setObjectName("subtleLabel")
        self.telemetry_summary_label.setWordWrap(True)
        t1.addWidget(self.telemetry_summary_label)

        self.advisories_list = QtWidgets.QListWidget(self.tier1_drawer.body)
        self.advisories_list.setWordWrap(True)
        self.advisories_list.setMinimumHeight(130)
        self.advisories_list.setMaximumHeight(220)
        t1.addWidget(self.advisories_list)

        main_layout.addWidget(self.tier1_drawer)

        # ------------------------------------------------------------------
        # Tier 2 — Collapsible TD Diagnostics Drawer
        # ------------------------------------------------------------------
        self.tier2_drawer = _CollapsibleDrawer(
            "TD Diagnostics (Preflight && Ledger)",
            expanded=False,
            parent=content,
        )
        t2 = self.tier2_drawer.content_layout

        preflight_row = QtWidgets.QHBoxLayout()
        preflight_row.setSpacing(6)
        preflight_lbl = QtWidgets.QLabel("PREFLIGHT DOCTOR", self.tier2_drawer.body)
        preflight_lbl.setObjectName("sectionHeader")
        preflight_row.addWidget(preflight_lbl, 1)

        self.preflight_btn = QtWidgets.QPushButton(
            "Run Preflight Doctor", self.tier2_drawer.body
        )
        self.preflight_btn.clicked.connect(self._on_preflight_clicked)
        preflight_row.addWidget(self.preflight_btn)
        t2.addLayout(preflight_row)

        self.preflight_output = QtWidgets.QPlainTextEdit(self.tier2_drawer.body)
        self.preflight_output.setReadOnly(True)
        self.preflight_output.setMinimumHeight(130)
        self.preflight_output.setMaximumHeight(220)
        self.preflight_output.setPlaceholderText(
            "Click 'Run Preflight Doctor' to inspect Houdini 22 build, USD version, "
            "Hydra delegates, denoiser, 22.0.429 grid guard, and env vars."
        )
        t2.addWidget(self.preflight_output)

        ledger_row = QtWidgets.QHBoxLayout()
        ledger_row.setSpacing(6)
        ledger_lbl = QtWidgets.QLabel("LATENCY LEDGER (JSONL)", self.tier2_drawer.body)
        ledger_lbl.setObjectName("sectionHeader")
        ledger_row.addWidget(ledger_lbl, 1)

        self.refresh_ledger_btn = QtWidgets.QPushButton(
            "Refresh Ledger", self.tier2_drawer.body
        )
        self.refresh_ledger_btn.clicked.connect(self._on_refresh_ledger_clicked)
        ledger_row.addWidget(self.refresh_ledger_btn)
        t2.addLayout(ledger_row)

        self.ledger_stats_label = QtWidgets.QLabel(
            "Entries: 0 | apply_ms median=UNKNOWN p90=UNKNOWN | scan_ms median=UNKNOWN p90=UNKNOWN",
            self.tier2_drawer.body,
        )
        self.ledger_stats_label.setObjectName("subtleLabel")
        self.ledger_stats_label.setWordWrap(True)
        t2.addWidget(self.ledger_stats_label)

        self.ledger_output = QtWidgets.QPlainTextEdit(self.tier2_drawer.body)
        self.ledger_output.setReadOnly(True)
        self.ledger_output.setMinimumHeight(120)
        self.ledger_output.setMaximumHeight(200)
        self.ledger_output.setPlaceholderText(
            "Click 'Refresh Ledger' to load recent DECISION · VIA · PROOF telemetry entries."
        )
        t2.addWidget(self.ledger_output)

        main_layout.addWidget(self.tier2_drawer)
        main_layout.addStretch(1)

    # ------------------------------------------------------------------
    # Public helpers & state sync
    # ------------------------------------------------------------------

    def selected_viewer_name(self) -> str | None:
        data = self.viewer_combo.currentData()
        if isinstance(data, str) and data.strip():
            return data.strip()
        return None

    def selected_engine(self) -> str:
        data = self.engine_combo.currentData()
        return str(data) if data else "Karma XPU"

    def set_tier1_expanded(self, expanded: bool) -> None:
        self.tier1_drawer.set_expanded(expanded)

    def set_tier2_expanded(self, expanded: bool) -> None:
        self.tier2_drawer.set_expanded(expanded)

    def _set_feedback(self, text: str, tone: str = "info") -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            self.feedback_label.setText("")
            self.feedback_label.setVisible(False)
            return
        self.feedback_label.setText(cleaned)
        self.feedback_label.setProperty("feedbackTone", tone)
        _repolish(self.feedback_label)
        self.feedback_label.setVisible(True)

    def refresh_viewers(self) -> tuple[str, ...]:
        """Populate the Solaris Scene Viewer dropdown from ``controller.list_solaris_viewers()``."""
        current_name = self.selected_viewer_name()
        names: list[str] = []
        try:
            viewers = self._controller.list_solaris_viewers() or ()
            for viewer in viewers:
                vname = _extract_viewer_name(viewer)
                if vname and vname not in names:
                    names.append(vname)
        except Exception:
            names = []

        blocker = QtCore.QSignalBlocker(self.viewer_combo)
        try:
            self.viewer_combo.clear()
            if not names:
                self.viewer_combo.addItem("(No Solaris Scene Viewer)", None)
            else:
                for vname in names:
                    self.viewer_combo.addItem(vname, vname)
                if current_name and current_name in names:
                    self.viewer_combo.setCurrentIndex(names.index(current_name))
        finally:
            del blocker

        return tuple(names)

    def refresh_state(self) -> Any | None:
        """Observe live viewer state from the controller and update the HUD."""
        try:
            snapshot = self._controller.observe(viewer_name=self.selected_viewer_name())
            self.apply_snapshot(snapshot)
            return snapshot
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
            return None
        except Exception as exc:
            self._set_feedback(f"Observe failed: {exc}", tone="error")
            return None

    def _on_controller_snapshot(self, snapshot: Any) -> None:
        if self._cleaned_up:
            return
        try:
            self.apply_snapshot(snapshot)
        except RuntimeError:
            # Widget already destroyed on C++ side.
            self.cleanup()

    def apply_snapshot(self, snapshot: Any) -> None:
        """Update every HUD indicator and control strictly from ``SessionSnapshot``."""
        if snapshot is None or self._cleaned_up:
            return

        self._snapshot = snapshot
        state_cls = _session_state_cls()
        state = str(getattr(snapshot, "state", state_cls.UNINITIALIZED) or state_cls.UNINITIALIZED)
        observed_delegate = str(getattr(snapshot, "observed_delegate", "") or "").strip()
        active_preset = str(getattr(snapshot, "active_preset_id", "") or "").strip()
        override_active = bool(getattr(snapshot, "override_active", False))
        final_untouched = bool(getattr(snapshot, "final_render_untouched", True))
        camera_locked = bool(getattr(snapshot, "camera_locked", False))
        camera_path = str(getattr(snapshot, "camera_path", "") or "").strip()
        auto_turbo_enabled = bool(getattr(snapshot, "auto_turbo_enabled", False))
        turbo_engaged = bool(getattr(snapshot, "turbo_engaged", False))
        paused = bool(getattr(snapshot, "paused", False))
        clean_overlays = bool(getattr(snapshot, "clean_overlays_active", False))
        show_stats = bool(getattr(snapshot, "show_render_stats", False))
        lopnet_path = str(getattr(snapshot, "lopnet_path", "") or "").strip() or "—"
        settings_prim = str(getattr(snapshot, "settings_prim_path", "") or "").strip() or "—"
        denoiser_mode = str(getattr(snapshot, "denoiser_mode", "UNKNOWN") or "UNKNOWN")
        xpu_summary = str(getattr(snapshot, "xpu_device_summary", "UNKNOWN") or "UNKNOWN")
        last_error = str(getattr(snapshot, "last_error", "") or "").strip()
        message = str(getattr(snapshot, "message", "") or "").strip()
        viewer_name = str(getattr(snapshot, "viewer_name", "") or "").strip()

        # Sync viewer combobox if controller observed a named viewer not yet listed.
        if viewer_name:
            blocker = QtCore.QSignalBlocker(self.viewer_combo)
            try:
                existing_idx = self.viewer_combo.findData(viewer_name)
                if existing_idx >= 0:
                    self.viewer_combo.setCurrentIndex(existing_idx)
                else:
                    if self.viewer_combo.count() == 1 and self.viewer_combo.itemData(0) is None:
                        self.viewer_combo.clear()
                    self.viewer_combo.addItem(viewer_name, viewer_name)
                    self.viewer_combo.setCurrentIndex(self.viewer_combo.count() - 1)
            finally:
                del blocker

        # 1. Delegate & State pill (truthful observed values only)
        short_state, pill_tone = _STATE_LABELS.get(state, (state, "off"))
        if turbo_engaged and state == state_cls.ACTIVE_TURBO_INTERACT:
            short_state = "TURBO"
            pill_tone = "turbo"
        elif "vk" in observed_delegate.lower() and pill_tone == "active":
            pill_tone = "vulkan"

        pill_text = f"{observed_delegate} · {short_state}" if observed_delegate else short_state
        self.status_pill.setText(pill_text)
        self.status_pill.setProperty("pillTone", pill_tone)
        _repolish(self.status_pill)

        # 1b. Live GPU / VRAM / Driver / OptiX Health Badge
        vram_risk = str(getattr(snapshot, "vram_eviction_risk", "UNKNOWN") or "UNKNOWN").upper()
        if vram_risk == "CRITICAL":
            vram_tone = "critical"
        elif vram_risk == "WARN":
            vram_tone = "warn"
        elif vram_risk == "OK":
            vram_tone = "ok"
        else:
            vram_tone = "neutral"
        self.gpu_badge.setText(f"GPU: {xpu_summary}")
        self.gpu_badge.setProperty("vramTone", vram_tone)
        _repolish(self.gpu_badge)

        # 2. Metadata summary line
        usd_flt_tag = (
            " (USD ImageFilter)"
            if bool(getattr(snapshot, "usd_image_filters_detected", False))
            else ""
        )
        self.meta_label.setText(
            f"LOP: {lopnet_path}  ·  Settings: {settings_prim}  ·  "
            f"Denoiser: {denoiser_mode}{usd_flt_tag}"
        )

        # 3. Emerald safety badge
        self.override_badge.setVisible(override_active)
        if final_untouched:
            self.override_badge.setText("VIEWPORT OVERRIDE ACTIVE · FINAL RENDER UNTOUCHED")
            self.override_badge.setProperty("safetyTone", "ok")
        else:
            self.override_badge.setText("VIEWPORT OVERRIDE ACTIVE · CHECK FINAL RENDER")
            self.override_badge.setProperty("safetyTone", "warn")
        _repolish(self.override_badge)

        # 4. Amber Camera Lock warning bar
        self.camera_lock_bar.setVisible(camera_locked)
        if camera_locked:
            cam_display = camera_path if camera_path else "<locked viewport camera>"
            self.camera_lock_label.setText(f"CAM LOCKED: {cam_display}")

        # 5. Preset active check highlights
        is_active_session = state in (
            state_cls.ACTIVE_IDLE,
            state_cls.ACTIVE_TURBO_INTERACT,
            state_cls.ACTIVE_PAUSED,
            state_cls.ACTIVE_DISK_RENDER_YIELD,
        )
        for pid, btn in self.preset_buttons.items():
            blocker = QtCore.QSignalBlocker(btn)
            try:
                btn.setChecked(bool(is_active_session and active_preset == pid))
            finally:
                del blocker

        # 6. Checkboxes synced with signal blockers (zero loop risk)
        for checkbox, checked_val in (
            (self.auto_turbo_checkbox, auto_turbo_enabled),
            (self.clean_overlays_checkbox, clean_overlays),
            (self.render_stats_checkbox, show_stats),
        ):
            blocker = QtCore.QSignalBlocker(checkbox)
            try:
                checkbox.setChecked(checked_val)
            finally:
                del blocker

        # 6b. Sync Focus Crop, Res Scale, Freeze VP, Pin Stage, and Solo badges
        focus_crop = str(getattr(snapshot, "focus_crop", "full") or "full")
        blocker = QtCore.QSignalBlocker(self.focus_crop_combo)
        try:
            idx = self.focus_crop_combo.findData(focus_crop)
            if idx >= 0:
                self.focus_crop_combo.setCurrentIndex(idx)
        finally:
            del blocker

        res_scale = float(getattr(snapshot, "resolution_scale", 1.0) or 1.0)
        blocker = QtCore.QSignalBlocker(self.res_scale_combo)
        try:
            for i in range(self.res_scale_combo.count()):
                item_val = self.res_scale_combo.itemData(i)
                if item_val is not None and abs(float(item_val) - res_scale) < 1e-3:
                    self.res_scale_combo.setCurrentIndex(i)
                    break
        finally:
            del blocker

        vp_frozen = bool(getattr(snapshot, "viewport_frozen", False))
        blocker = QtCore.QSignalBlocker(self.freeze_vp_btn)
        try:
            self.freeze_vp_btn.setChecked(vp_frozen)
            self.freeze_vp_btn.setText("Frozen" if vp_frozen else "Freeze")
        finally:
            del blocker

        stage_pinned = bool(getattr(snapshot, "stage_pinned", False))
        blocker = QtCore.QSignalBlocker(self.pin_stage_btn)
        try:
            self.pin_stage_btn.setChecked(stage_pinned)
            self.pin_stage_btn.setText("Pinned" if stage_pinned else "Pin")
        finally:
            del blocker

        solo_geo_n = int(getattr(snapshot, "solo_geometry_count", 0) or 0)
        solo_lgt_n = int(getattr(snapshot, "solo_lights_count", 0) or 0)
        self.solo_geo_btn.setText(f"G({solo_geo_n})" if solo_geo_n > 0 else "Solo G")
        self.solo_lights_btn.setText(f"L({solo_lgt_n})" if solo_lgt_n > 0 else "Solo L")
        self.unsolo_btn.setEnabled(solo_geo_n > 0 or solo_lgt_n > 0)

        # 7. Pause & Disk Render Yield dynamic button text
        self.pause_btn.setText("Resume" if paused else "Pause")
        if state == state_cls.ACTIVE_DISK_RENDER_YIELD:
            self.disk_yield_btn.setText("Resume Render")
        else:
            self.disk_yield_btn.setText("Disk Yield")

        # 8. Safety buttons are ALWAYS enabled regardless of state
        self.restore_btn.setEnabled(True)
        self.emergency_reset_btn.setEnabled(True)

        # 9. Sync non-blocking Auto-Turbo QTimer
        self._auto_turbo_enabled = auto_turbo_enabled
        self._sync_auto_turbo_timer()

        # 10. Feedback banner
        if last_error:
            self._set_feedback(last_error, tone="error")
        elif message:
            tone = "warn" if state in (state_cls.DEGRADED, state_cls.UNAVAILABLE) else "info"
            self._set_feedback(message, tone=tone)
        else:
            self._set_feedback("")

    # ------------------------------------------------------------------
    # Auto-Turbo QTimer management & Qt lifecycle hooks
    # ------------------------------------------------------------------

    def _sync_auto_turbo_timer(self) -> None:
        if self._cleaned_up:
            if self._auto_turbo_timer.isActive():
                self._auto_turbo_timer.stop()
            return
        should_run = bool(self._auto_turbo_enabled and self.isVisible())
        if should_run and not self._auto_turbo_timer.isActive():
            self._auto_turbo_timer.start()
        elif not should_run and self._auto_turbo_timer.isActive():
            self._auto_turbo_timer.stop()

    def _on_auto_turbo_tick(self) -> None:
        if self._cleaned_up or not self._auto_turbo_enabled or not self.isVisible():
            self._auto_turbo_timer.stop()
            return
        if self._ticking_auto_turbo:
            return
        self._ticking_auto_turbo = True
        try:
            snapshot = self._controller.tick_auto_turbo(
                viewer_name=self.selected_viewer_name()
            )
            if snapshot is not None:
                self.apply_snapshot(snapshot)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
        except Exception as exc:
            self._set_feedback(f"Auto-Turbo tick error: {exc}", tone="error")
        finally:
            self._ticking_auto_turbo = False

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._sync_auto_turbo_timer()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        super().hideEvent(event)
        self._sync_auto_turbo_timer()

    def cleanup(self) -> None:
        """Stop timers and disconnect controller listeners cleanly (idempotent)."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        try:
            if self._auto_turbo_timer.isActive():
                self._auto_turbo_timer.stop()
        except RuntimeError:
            pass
        if self._listener_registered and self._controller is not None:
            try:
                self._controller.remove_listener(self._on_controller_snapshot)
            except Exception:
                pass
            self._listener_registered = False

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.cleanup()
        super().closeEvent(event)

    def deleteLater(self) -> None:
        self.cleanup()
        super().deleteLater()

    # ------------------------------------------------------------------
    # Tier 0 UI action handlers
    # ------------------------------------------------------------------

    def _on_viewer_changed(self, _index: int) -> None:
        self.refresh_state()

    def _on_refresh_clicked(self) -> None:
        self.refresh_viewers()
        self.refresh_state()

    def _on_unlock_orbit_clicked(self) -> None:
        try:
            snap = self._controller.unlock_and_orbit_copy(
                viewer_name=self.selected_viewer_name()
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_preset_clicked(self, preset_id: str) -> None:
        delegate_pref = (
            "Houdini VK" if preset_id == "vulkan_layout" else self.selected_engine()
        )
        # Keep Tier 1 dry-run preset selector synced for artist convenience.
        combo_idx = self.preview_preset_combo.findData(preset_id)
        if combo_idx >= 0:
            self.preview_preset_combo.setCurrentIndex(combo_idx)

        try:
            snap = self._controller.activate_preset(
                preset_id,
                viewer_name=self.selected_viewer_name(),
                delegate_preference=delegate_pref,
                allow_restart=False,
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
            if self._snapshot is not None:
                self.apply_snapshot(self._snapshot)

    def _on_auto_turbo_toggled(self, checked: bool) -> None:
        try:
            snap = self._controller.set_auto_turbo(
                bool(checked),
                viewer_name=self.selected_viewer_name(),
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
            blocker = QtCore.QSignalBlocker(self.auto_turbo_checkbox)
            try:
                self.auto_turbo_checkbox.setChecked(self._auto_turbo_enabled)
            finally:
                del blocker

    def _on_clean_overlays_toggled(self, checked: bool) -> None:
        try:
            snap = self._controller.toggle_clean_overlays(
                enabled=bool(checked),
                viewer_name=self.selected_viewer_name(),
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
            if self._snapshot is not None:
                self.apply_snapshot(self._snapshot)

    def _on_render_stats_toggled(self, checked: bool) -> None:
        try:
            snap = self._controller.set_show_render_stats(
                bool(checked),
                viewer_name=self.selected_viewer_name(),
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
            if self._snapshot is not None:
                self.apply_snapshot(self._snapshot)

    def _on_pause_clicked(self) -> None:
        currently_paused = bool(getattr(self._snapshot, "paused", False))
        try:
            snap = self._controller.set_paused(
                not currently_paused,
                viewer_name=self.selected_viewer_name(),
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_snapshot_clicked(self) -> None:
        try:
            result = self._controller.quick_snapshot(
                viewer_name=self.selected_viewer_name()
            )
            if isinstance(result, dict):
                saved = str(
                    result.get("output_path")
                    or result.get("path")
                    or result.get("message")
                    or "Snapshot captured."
                )
                ok = bool(result.get("ok", True))
                self._set_feedback(
                    f"Snapshot: {saved}" if ok else f"Snapshot warning: {saved}",
                    tone="ok" if ok else "warn",
                )
            else:
                self._set_feedback("Viewport snapshot captured.", tone="ok")
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_disk_yield_clicked(self) -> None:
        state_cls = _session_state_cls()
        current_state = str(getattr(self._snapshot, "state", ""))
        try:
            if current_state == state_cls.ACTIVE_DISK_RENDER_YIELD:
                snap = self._controller.resume_from_disk_render(
                    viewer_name=self.selected_viewer_name()
                )
            else:
                snap = self._controller.yield_for_disk_render(
                    viewer_name=self.selected_viewer_name()
                )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_restore_clicked(self) -> None:
        try:
            snap = self._controller.restore(viewer_name=self.selected_viewer_name())
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_emergency_reset_clicked(self) -> None:
        try:
            snap = self._controller.emergency_reset(
                viewer_name=self.selected_viewer_name()
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="error")

    def _on_focus_crop_changed(self, _index: int) -> None:
        crop_key = str(self.focus_crop_combo.currentData() or "full")
        try:
            snap = self._controller.set_focus_crop(
                crop_key,
                viewer_name=self.selected_viewer_name(),
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_res_scale_changed(self, _index: int) -> None:
        scale_val = self.res_scale_combo.currentData()
        if scale_val is None:
            return
        try:
            snap = self._controller.set_resolution_scale(
                float(scale_val),
                viewer_name=self.selected_viewer_name(),
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_freeze_vp_clicked(self, checked: bool) -> None:
        try:
            snap = self._controller.set_viewport_frozen(
                bool(checked),
                viewer_name=self.selected_viewer_name(),
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
            if self._snapshot is not None:
                self.apply_snapshot(self._snapshot)

    def _on_pin_stage_clicked(self, checked: bool) -> None:
        try:
            snap = self._controller.set_stage_pinned(
                bool(checked),
                viewer_name=self.selected_viewer_name(),
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
            if self._snapshot is not None:
                self.apply_snapshot(self._snapshot)

    def _on_solo_geo_clicked(self) -> None:
        try:
            snap = self._controller.solo_selected_geometry(
                viewer_name=self.selected_viewer_name()
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_solo_lights_clicked(self) -> None:
        try:
            snap = self._controller.solo_selected_lights(
                viewer_name=self.selected_viewer_name()
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    def _on_unsolo_clicked(self) -> None:
        try:
            snap = self._controller.clear_solo(
                viewer_name=self.selected_viewer_name()
            )
            self.apply_snapshot(snap)
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")

    # ------------------------------------------------------------------
    # Tier 1: Preview Diff (Dry-Run) & H22 Scene Advisor
    # ------------------------------------------------------------------

    def _on_preview_diff_clicked(self) -> None:
        self.run_preview_diff()

    def run_preview_diff(self, preset_id: str | None = None) -> Any | None:
        """Execute a read-only ``preview_preset`` dry-run and populate the diff table."""
        target_preset = preset_id or str(
            self.preview_preset_combo.currentData() or "interactive_lookdev"
        )
        try:
            preview = self._controller.preview_preset(
                target_preset,
                viewer_name=self.selected_viewer_name(),
            )
        except _session_error_types() as exc:
            self._last_preview = None
            self.apply_verified_btn.setEnabled(False)
            self.preview_summary_label.setText(f"Preview unavailable: {exc}")
            self._set_feedback(str(exc), tone="warn")
            return None

        self._last_preview = preview
        diffs = list(getattr(preview, "diffs", ()) or ())
        binding = getattr(preview, "binding", None)
        warnings = list(getattr(preview, "warnings", ()) or ())
        valid = bool(getattr(preview, "valid", True))
        reason = str(getattr(preview, "reason", "") or "").strip()

        self.diff_table.setRowCount(len(diffs))
        restart_count = 0
        for row, diff in enumerate(diffs):
            attr_name = str(getattr(diff, "usd_attr", ""))
            sdf_type = str(getattr(diff, "sdf_type", ""))
            diff_status = str(getattr(diff, "status", ""))
            triggers_restart = bool(getattr(diff, "triggers_restart", False))
            if triggers_restart:
                restart_count += 1

            prod_short, prod_full = _format_diff_value(getattr(diff, "production_value", None))
            prop_short, prop_full = _format_diff_value(getattr(diff, "proposed_value", None))

            attr_item = QtWidgets.QTableWidgetItem(attr_name)
            attr_item.setToolTip(f"{attr_name} ({sdf_type}) · status: {diff_status}")

            prod_item = QtWidgets.QTableWidgetItem(prod_short)
            prod_item.setToolTip(prod_full)

            sess_item = QtWidgets.QTableWidgetItem(prop_short)
            sess_item.setToolTip(f"{prop_full} (status: {diff_status})")

            risk_label = "Restarts Karma" if triggers_restart else "Zero-Restart"
            risk_item = QtWidgets.QTableWidgetItem(risk_label)
            risk_font = risk_item.font()
            risk_font.setBold(triggers_restart)
            risk_item.setFont(risk_font)
            risk_item.setForeground(
                QtGui.QColor("#fbbf24" if triggers_restart else "#4ade80")
            )

            self.diff_table.setItem(row, 0, attr_item)
            self.diff_table.setItem(row, 1, prod_item)
            self.diff_table.setItem(row, 2, sess_item)
            self.diff_table.setItem(row, 3, risk_item)

        settings_path = getattr(binding, "settings_prim_path", "—") if binding else "—"
        binding_src = getattr(binding, "source", "—") if binding else "—"
        status_note = (
            f"VERIFIED ZERO-RESTART ({len(diffs)} attrs)"
            if valid and restart_count == 0
            else f"BLOCKED ({restart_count} restart attr(s)): {reason}"
        )
        summary_lines = [
            f"Preset: {target_preset} · Target: {settings_path} ({binding_src}) · {status_note}"
        ]
        if warnings:
            summary_lines.append("Warnings: " + " | ".join(warnings))
        self.preview_summary_label.setText("\n".join(summary_lines))
        self.apply_verified_btn.setEnabled(valid)
        self.tier1_drawer.set_badge(f"{len(diffs)} diff")
        return preview

    def _on_apply_verified_clicked(self) -> None:
        self.apply_verified_preview()

    def apply_verified_preview(self) -> Any | None:
        """Apply the currently verified dry-run preview without triggering restarts."""
        if self._last_preview is None or not bool(getattr(self._last_preview, "valid", False)):
            self._set_feedback("Run a valid Preview Diff before applying.", tone="warn")
            return None

        preset_id = str(getattr(self._last_preview, "preset_id", "interactive_lookdev"))
        delegate_pref = (
            "Houdini VK" if preset_id == "vulkan_layout" else self.selected_engine()
        )
        try:
            snap = self._controller.activate_preset(
                preset_id,
                viewer_name=self.selected_viewer_name(),
                delegate_preference=delegate_pref,
                allow_restart=False,
            )
            self.apply_snapshot(snap)
            return snap
        except _session_error_types() as exc:
            self._set_feedback(str(exc), tone="warn")
            return None

    def _on_scan_advisories_clicked(self) -> None:
        self.run_scene_scan()

    def run_scene_scan(self) -> tuple[Any, list[Any]] | None:
        """Run read-only H22 stage bottleneck analysis and populate the advisories list."""
        try:
            telemetry, advisories = self._controller.scan_advisories(
                viewer_name=self.selected_viewer_name()
            )
        except _session_error_types() as exc:
            self.telemetry_summary_label.setText(f"Scene scan unavailable: {exc}")
            self._set_feedback(str(exc), tone="warn")
            return None

        prim_count = getattr(telemetry, "prim_count", 0)
        truncated = bool(getattr(telemetry, "truncated", False))
        mesh_count = getattr(telemetry, "mesh_count", 0)
        subdiv_count = getattr(telemetry, "subdiv_count", 0)
        volume_count = getattr(telemetry, "volume_count", 0)
        light_count = getattr(telemetry, "light_count", 0)
        instancer_count = getattr(telemetry, "instancer_count", 0)
        scan_ms = getattr(telemetry, "scan_ms", 0.0)
        trunc_note = " (truncated)" if truncated else ""

        self.telemetry_summary_label.setText(
            f"Prims: {prim_count}{trunc_note} | Meshes: {mesh_count} (Subdiv: {subdiv_count}) | "
            f"Volumes: {volume_count} | Lights: {light_count} | Instancers: {instancer_count} | "
            f"Scan: {float(scan_ms):.1f} ms"
        )

        self.advisories_list.clear()
        if not advisories:
            item = QtWidgets.QListWidgetItem(
                "[OK] No Karma/Solaris bottlenecks detected on the active stage."
            )
            item.setForeground(QtGui.QColor("#86efac"))
            self.advisories_list.addItem(item)
            self.tier1_drawer.set_badge("0 advisories")
            return telemetry, list(advisories)

        for adv in advisories:
            sev = str(getattr(adv, "severity", "info")).upper()
            title = str(getattr(adv, "title", "Advisory"))
            mechanism = str(getattr(adv, "mechanism", ""))
            recommendation = str(getattr(adv, "recommendation", ""))
            triggers_restart = bool(getattr(adv, "triggers_restart", False))
            target_paths = list(getattr(adv, "target_paths", ()) or ())
            risk_tag = "Restarts Karma" if triggers_restart else "Zero-Restart"

            lines = [f"[{sev}] {title} ({risk_tag})"]
            if mechanism:
                lines.append(f"  Cause: {mechanism}")
            if recommendation:
                lines.append(f"  Action: {recommendation}")
            if target_paths:
                sample = ", ".join(target_paths[:3])
                extra = f" (+{len(target_paths) - 3} more)" if len(target_paths) > 3 else ""
                lines.append(f"  Targets: {sample}{extra}")

            item = QtWidgets.QListWidgetItem("\n".join(lines))
            if triggers_restart or sev in ("WARNING", "WARN", "ERROR", "CRITICAL"):
                item.setForeground(QtGui.QColor("#fde68a"))
            else:
                item.setForeground(QtGui.QColor("#bae6fd"))
            self.advisories_list.addItem(item)

        self.tier1_drawer.set_badge(f"{len(advisories)} advisories")
        return telemetry, list(advisories)

    # ------------------------------------------------------------------
    # Tier 2: TD Diagnostics (Preflight Doctor & Latency Ledger)
    # ------------------------------------------------------------------

    def _on_preflight_clicked(self) -> None:
        self.run_preflight_doctor()

    def run_preflight_doctor(self) -> Any | None:
        """Run read-only Houdini 22 preflight checks and render structured diagnostics."""
        try:
            report = self._controller.run_preflight(
                viewer_name=self.selected_viewer_name()
            )
        except _session_error_types() as exc:
            self.preflight_output.setPlainText(f"Preflight failed: {exc}")
            self._set_feedback(str(exc), tone="warn")
            return None

        status = str(getattr(report, "status", "UNKNOWN"))
        h_ver = getattr(report, "houdini_version", None) or "UNKNOWN"
        py_ver = getattr(report, "python_version", None) or "UNKNOWN"
        usd_ver = getattr(report, "usd_version", None) or "UNKNOWN"
        delegates = list(getattr(report, "available_delegates", ()) or ())
        denoiser = getattr(report, "recommended_denoiser", "UNKNOWN")
        grid_guard = bool(getattr(report, "reference_grid_guard_needed", False))
        checks = list(getattr(report, "checks", ()) or ())
        env_diag = dict(getattr(report, "env_diagnostics", {}) or {})
        warnings = list(getattr(report, "warnings", ()) or ())

        hw = getattr(report, "hardware", None)
        hw_badge = getattr(hw, "summary_badge", "UNKNOWN") if hw is not None else "UNKNOWN"
        panes_follow = getattr(report, "panes_follow_current_node", None)

        lines: list[str] = [
            f"Status:        {status}",
            f"Houdini Build: {h_ver}",
            f"Python:        {py_ver}",
            f"OpenUSD:       {usd_ver}",
            f"Hardware:      {hw_badge}",
            f"Delegates:     {', '.join(delegates) if delegates else 'None detected'}",
            (
                f"Capabilities:  XPU={bool(getattr(report, 'karma_xpu_available', False))}  "
                f"CPU={bool(getattr(report, 'karma_cpu_available', False))}  "
                f"VK={bool(getattr(report, 'vulkan_available', False))}"
            ),
            f"Denoiser:      {denoiser}",
            f"Stage Follow:  {panes_follow}",
            f"22.0.429 Grid: {'GUARD REQUIRED' if grid_guard else 'Not required'}",
            "",
            "Checks:",
        ]
        for chk in checks:
            chk_status = str(getattr(chk, "status", "INFO")).upper()
            chk_name = str(getattr(chk, "name", ""))
            chk_detail = str(getattr(chk, "detail", ""))
            lines.append(f"  [{chk_status:<4}] {chk_name}: {chk_detail}")

        if env_diag:
            lines.append("")
            lines.append("Environment Diagnostics:")
            for key, val in env_diag.items():
                lines.append(f"  {key} = {val if val is not None else 'UNSET'}")

        if warnings:
            lines.append("")
            lines.append("Warnings:")
            for warn in warnings:
                lines.append(f"  - {warn}")

        self.preflight_output.setPlainText("\n".join(lines))
        self.tier2_drawer.set_badge(status)
        return report

    def _on_refresh_ledger_clicked(self) -> None:
        self.refresh_ledger()

    def refresh_ledger(self, limit: int = 15) -> dict[str, Any]:
        """Load JSONL LatencyLedger stats and recent DECISION · VIA · PROOF entries."""
        ledger = getattr(self._controller, "ledger", None)
        if ledger is None:
            self.ledger_stats_label.setText("Ledger unavailable on controller.")
            self.ledger_output.setPlainText("No LatencyLedger attached.")
            return {}

        try:
            stats = dict(ledger.summary_stats() or {})
            recent = list(ledger.read_recent(limit=limit) or [])
        except Exception as exc:
            self.ledger_stats_label.setText(f"Ledger read error: {exc}")
            self.ledger_output.setPlainText("")
            return {}

        count = stats.get("count", 0)
        apply_med = stats.get("apply_ms_median", "UNKNOWN")
        apply_p90 = stats.get("apply_ms_p90", "UNKNOWN")
        scan_med = stats.get("scan_ms_median", "UNKNOWN")
        scan_p90 = stats.get("scan_ms_p90", "UNKNOWN")
        ledger_path = getattr(ledger, "ledger_path", "")

        self.ledger_stats_label.setText(
            f"Entries: {count} | apply_ms median={apply_med} p90={apply_p90} | "
            f"scan_ms median={scan_med} p90={scan_p90}"
        )

        if not recent:
            self.ledger_output.setPlainText(
                f"No ledger entries recorded yet.\nPath: {ledger_path}"
            )
            return stats

        lines: list[str] = [f"Path: {ledger_path}", ""]
        for entry in reversed(recent):
            ts = str(entry.get("timestamp_utc", "UNKNOWN"))
            decision = str(entry.get("decision", "UNKNOWN"))
            via = str(entry.get("via", "UNKNOWN"))
            proof = str(entry.get("proof", "UNKNOWN"))
            event = str(entry.get("event", "UNKNOWN"))
            preset = str(entry.get("preset_id", "UNKNOWN"))
            delegate = str(entry.get("observed_delegate", "UNKNOWN"))
            apply_ms = entry.get("apply_ms", "UNKNOWN")
            scan_ms = entry.get("scan_ms", "UNKNOWN")
            lines.append(
                f"{ts} | {decision} · {via} · {proof}\n"
                f"  event={event} preset={preset} delegate={delegate} "
                f"apply_ms={apply_ms} scan_ms={scan_ms}"
            )

        self.ledger_output.setPlainText("\n".join(lines))
        return stats


def create_panel(
    controller: Any = None,
    parent: QtWidgets.QWidget | None = None,
) -> KarmaRTPanelWidget:
    """Instantiate and return a KarmaRT Houdini 22 PySide6 panel widget."""
    return KarmaRTPanelWidget(controller=controller, parent=parent)


__all__ = [
    "KarmaRTPanelWidget",
    "create_panel",
]
