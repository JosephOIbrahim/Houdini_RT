"""HoudiniRT runtime ON/OFF Python Panel.

This module is the readable copy of the script embedded in rt_panel.pypanel.
The .pypanel is self-contained (the script lives in a CDATA block) so it can be
installed on its own; keep the two files in sync.

Install (inside Houdini):

    import hou
    hou.pypanel.installFile(r"<path>\\houdini\\rt_panel.pypanel")

Contract used here (as implemented by rt_runtime):

    rt_runtime.session.get_adapter() -> RuntimeAdapter
    RuntimeAdapter.target.name -> configured target name
    RuntimeAdapter.probe() -> {
        "available": bool,
        "python_version": str,
        "site_packages_exists": bool,
        "compatibility": {"safe": bool, "reasons": [str], "blockers": [str]},
        "active": bool,
    }
    RuntimeAdapter.status() -> RuntimeState (OFF / ON / ERROR)
    RuntimeAdapter.toggle() -> ActivationResult
    ActivationResult.ok / .state / .changed / .reasons / .error
    RuntimeAdapter.turn_control() -> TurnControl | None

The turn section submits one turn with ``wait_ms=0`` (202, return immediately)
and polls ``GET /turn/<id>`` on a Qt timer. It never blocks on a turn.

This widget makes no hou calls. Everything it knows about the runtime comes
from the adapter. Attribute lookups are deliberately tolerant (several alias
names per field) so a small field-name change in the adapter cannot break the
panel; the alias tuples below are the single place to adjust.
"""

import os
import sys
import traceback

try:  # Houdini 20+ ships PySide6 through hutil.Qt.
    from hutil.Qt import QtCore, QtWidgets
except Exception:  # pragma: no cover - older Houdini
    try:
        from PySide2 import QtCore, QtWidgets
    except Exception:
        from PySide import QtCore, QtGui as QtWidgets


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PANEL_TITLE = "HOUDINIRT RUNTIME"

STATE_OFF = "OFF"
STATE_ON = "ON"
STATE_REFUSED = "REFUSED"
STATE_ERROR = "ERROR"

_STATUS_COLORS = {
    STATE_ON: "#57b685",
    STATE_OFF: "#9aa0aa",
    STATE_REFUSED: "#d2a44f",
    STATE_ERROR: "#e0766f",
}

# Field-name aliases, in priority order.
_TARGET_KEYS = ("target", "target_name", "name", "runtime", "runtime_name")
_PYTHON_KEYS = (
    "python_version",
    "target_python_version",
    "target_python",
    "py_version",
    "python",
)
_VERDICT_KEYS = ("abi_verdict", "verdict", "abi_status")
_COMPAT_KEYS = ("compatibility", "compat")
_BLOCKER_KEYS = ("blockers", "abi_blockers", "problems", "issues")
_STATE_KEYS = ("state", "status", "enabled", "active", "is_on")
_ERROR_KEYS = ("error", "error_text", "message")
_REASON_KEYS = ("reasons", "reason", "messages")

# One process-wide import attempt cache. A failed import is retried on the next
# Refresh so the panel recovers if rt_runtime is added to sys.path afterwards.
_IMPORT_CACHE = {"get_adapter": None, "error": None}


# ---------------------------------------------------------------------------
# Small helpers (no Qt, safe to unit-test)
# ---------------------------------------------------------------------------

def _load_get_adapter():
    """Return rt_runtime.session.get_adapter, or None if it cannot import."""
    if _IMPORT_CACHE["get_adapter"] is not None:
        return _IMPORT_CACHE["get_adapter"]
    try:
        from rt_runtime.session import get_adapter
    except Exception as exc:
        _IMPORT_CACHE["get_adapter"] = None
        _IMPORT_CACHE["error"] = "%s: %s" % (type(exc).__name__, exc)
        return None
    _IMPORT_CACHE["get_adapter"] = get_adapter
    _IMPORT_CACHE["error"] = None
    return get_adapter


def _get(obj, names, default=None):
    """Read the first present, non-None attribute or mapping key in names."""
    if obj is None:
        return default
    for name in names:
        try:
            if isinstance(obj, dict):
                if name in obj and obj[name] is not None:
                    return obj[name]
            else:
                value = getattr(obj, name)
                if value is not None:
                    return value
        except Exception:
            continue
    return default


def _as_text(value, default=""):
    if value is None:
        return default
    if isinstance(value, bool):
        return "yes" if value else "no"
    if not isinstance(value, str):
        name = getattr(value, "name", None)
        if isinstance(name, str):
            return name
    text = str(value).strip()
    return text if text else default


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item is not None]
    if isinstance(value, set):
        return [item for item in sorted(value, key=repr)]
    return [value]


def _state_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return STATE_ON if value else STATE_OFF
    text = _as_text(value).upper()
    if text in ("ON", "ENABLED", "ACTIVE", "TRUE", "1", "RUNNING"):
        return STATE_ON
    if text in ("OFF", "DISABLED", "INACTIVE", "FALSE", "0", "STOPPED"):
        return STATE_OFF
    return text


def _read_state(*objects):
    """Extract an ON/OFF token from a status object, a raw scalar, or a probe."""
    for obj in objects:
        if obj is None:
            continue
        if isinstance(obj, (bool, str)):
            text = _state_text(obj)
            if text:
                return text
            continue
        raw = _get(obj, _STATE_KEYS)
        if raw is not None:
            text = _state_text(raw)
            if text:
                return text
    return ""


def _target_name(adapter, probe):
    """Target name from adapter.target.name, with probe fallbacks."""
    if adapter is not None:
        target = _get(adapter, ("target",))
        name = _as_text(_get(target, ("name", "target_name")))
        if name:
            return name
        name = _as_text(_get(adapter, ("target_name", "name")))
        if name:
            return name
    return _as_text(_get(probe, _TARGET_KEYS), "n/a")


def _verdict(raw, blockers):
    """Normalize an ABI verdict into (text, is_unsafe)."""
    if isinstance(raw, bool):
        return ("SAFE" if raw else "UNSAFE"), (not raw)
    if raw is not None:
        text = _as_text(raw).upper()
        if "UNSAFE" in text or text in ("FAIL", "FAILED", "BLOCKED", "NO"):
            return "UNSAFE", True
        if "SAFE" in text or text in ("OK", "PASS", "PASSED", "YES"):
            return "SAFE", False
        if text:
            return text, False
    return ("UNSAFE" if blockers else "SAFE"), bool(blockers)


def _render_list(items):
    lines = []
    for item in items:
        text = _as_text(item).strip()
        if not text:
            continue
        if text.endswith(":") and len(text) < 40:
            lines.append(text)
        else:
            lines.append("  - " + text)
    return "\n".join(lines)


def _clip(text, limit=4000):
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (%d characters clipped)" % (len(text) - limit)


def _exc_text(limit=600):
    exc_type, exc_value = sys.exc_info()[:2]
    if exc_type is None:
        return "unknown error"
    return _clip("%s: %s" % (exc_type.__name__, exc_value), limit)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class RT_TogglePanel(QtWidgets.QWidget):
    """Large ON/OFF switch plus the runtime's probe readout."""

    def __init__(self, parent=None):
        super(RT_TogglePanel, self).__init__(parent)
        self._state = STATE_OFF
        self._busy = False
        self._adapter_ok = False
        self._turn_control = None
        self._turn_poller = None
        self._turn_timer = None
        self._turn_live = os.environ.get("RT_SIDECAR_LIVE_MODEL") == "1"
        self._build_ui()
        self._refresh()

    # -- construction -------------------------------------------------------

    def _build_ui(self):
        self.setObjectName("rt_toggle_panel")
        self.setMinimumWidth(300)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(8)

        header = QtWidgets.QLabel(PANEL_TITLE)
        header_font = header.font()
        header_font.setPointSize(15)
        header_font.setBold(True)
        header.setFont(header_font)

        subtitle = QtWidgets.QLabel("Runtime activation")
        subtitle_font = subtitle.font()
        subtitle_font.setPointSize(10)
        subtitle.setFont(subtitle_font)
        subtitle.setStyleSheet("color: #8a8f9a;")

        self._toggle_btn = QtWidgets.QPushButton("TURN ON")
        self._toggle_btn.setObjectName("rt_toggle_button")
        self._toggle_btn.setMinimumHeight(64)
        self._toggle_btn.setToolTip("Turn the HoudiniRT runtime on or off.")
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self._caption("STATUS"))
        self._status_lbl = QtWidgets.QLabel(STATE_OFF)
        self._status_lbl.setObjectName("rt_status")
        status_row.addWidget(self._status_lbl)
        status_row.addStretch(1)

        self._target_lbl = QtWidgets.QLabel("n/a")
        self._target_lbl.setWordWrap(True)
        self._python_lbl = QtWidgets.QLabel("n/a")
        self._abi_lbl = QtWidgets.QLabel("n/a")

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 6, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(4)
        form.addRow(self._caption("TARGET"), self._target_lbl)
        form.addRow(self._caption("PYTHON"), self._python_lbl)
        form.addRow(self._caption("ABI"), self._abi_lbl)

        self._error_lbl = QtWidgets.QLabel("(none)")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setObjectName("rt_error")
        try:
            self._error_lbl.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse
            )
        except Exception:
            pass

        self._details_lbl = QtWidgets.QLabel("(none)")
        self._details_lbl.setWordWrap(True)
        self._details_lbl.setObjectName("rt_details")
        try:
            self._details_lbl.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse
            )
        except Exception:
            pass

        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._refresh_btn.setObjectName("rt_refresh_button")
        self._refresh_btn.setToolTip(
            "Re-probe the runtime and update every readout."
        )
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)

        # -- one turn at a time ------------------------------------------
        self._turn_input = QtWidgets.QLineEdit()
        self._turn_input.setObjectName("rt_turn_prompt")
        self._turn_input.setPlaceholderText("Prompt for one turn")
        self._turn_input.returnPressed.connect(self._on_run_turn_clicked)

        self._turn_run_btn = QtWidgets.QPushButton(self._turn_run_text())
        self._turn_run_btn.setObjectName("rt_turn_run_button")
        self._turn_run_btn.setToolTip(
            "Submit one turn to the sidecar. Returns immediately; the panel "
            "polls until the turn ends."
        )
        self._turn_run_btn.clicked.connect(self._on_run_turn_clicked)

        self._turn_cancel_btn = QtWidgets.QPushButton("Cancel turn")
        self._turn_cancel_btn.setObjectName("rt_turn_cancel_button")
        self._turn_cancel_btn.setToolTip(
            "Ask the running turn to stop. The sidecar cancels cooperatively."
        )
        self._turn_cancel_btn.setEnabled(False)
        self._turn_cancel_btn.clicked.connect(self._on_cancel_turn_clicked)

        turn_row = QtWidgets.QHBoxLayout()
        turn_row.setSpacing(8)
        turn_row.addWidget(self._turn_run_btn)
        turn_row.addWidget(self._turn_cancel_btn)

        self._turn_lbl = QtWidgets.QLabel("(idle)")
        self._turn_lbl.setWordWrap(True)
        self._turn_lbl.setObjectName("rt_turn_status")
        try:
            self._turn_lbl.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse
            )
        except Exception:
            pass

        outer.addWidget(header)
        outer.addWidget(subtitle)
        outer.addSpacing(6)
        outer.addWidget(self._toggle_btn)
        outer.addLayout(status_row)
        outer.addLayout(form)
        outer.addSpacing(6)
        outer.addWidget(self._caption("LAST ERROR"))
        outer.addWidget(self._error_lbl)
        outer.addWidget(self._caption("BLOCKERS / REASONS"))
        outer.addWidget(self._details_lbl)
        outer.addSpacing(6)
        outer.addWidget(self._caption("TURN"))
        outer.addWidget(self._turn_input)
        outer.addLayout(turn_row)
        outer.addWidget(self._turn_lbl)
        outer.addSpacing(6)
        outer.addWidget(self._refresh_btn)
        outer.addStretch(1)

        self._apply_toggle_style(False)
        self._apply_status_style(STATE_OFF)

    def _caption(self, text):
        label = QtWidgets.QLabel(text)
        font = label.font()
        font.setPointSize(9)
        font.setBold(True)
        label.setFont(font)
        label.setStyleSheet("color: #8a8f9a;")
        return label

    # -- styling ------------------------------------------------------------

    def _apply_toggle_style(self, on):
        colors = {
            "bg": "#1f4d33" if on else "#3a3f4b",
            "fg": "#d9f2e3" if on else "#e8ebf0",
            "border": "#2f7a51" if on else "#555b69",
            "hover": "#266140" if on else "#444a58",
            "pressed": "#1a422b" if on else "#303540",
        }
        self._toggle_btn.setStyleSheet(
            "QPushButton {"
            " border-radius: 6px;"
            " padding: 14px 18px;"
            " font-size: 18px;"
            " font-weight: 600;"
            " border: 1px solid %(border)s;"
            " background-color: %(bg)s;"
            " color: %(fg)s;"
            "}"
            "QPushButton:hover { background-color: %(hover)s; }"
            "QPushButton:pressed { background-color: %(pressed)s; }"
            "QPushButton:disabled {"
            " color: #7a7f88;"
            " background-color: #2a2d34;"
            " border-color: #3a3e46;"
            "}" % colors
        )

    def _apply_status_style(self, state):
        color = _STATUS_COLORS.get(state, _STATUS_COLORS[STATE_ERROR])
        self._status_lbl.setStyleSheet(
            "color: %s; font-size: 13px; font-weight: 600;" % color
        )

    # -- state plumbing -----------------------------------------------------

    def _set_status(self, state):
        if state in (STATE_ON, STATE_OFF):
            self._state = state
        self._status_lbl.setText(state)
        self._apply_status_style(state)
        on = self._state == STATE_ON
        self._toggle_btn.setText("TURN OFF" if on else "TURN ON")
        self._apply_toggle_style(on)

    def _set_busy(self, busy):
        self._busy = busy
        try:
            self._toggle_btn.setEnabled((not busy) and self._adapter_ok)
            self._refresh_btn.setEnabled(not busy)
            self._refresh_turn_buttons()
            if busy:
                self._toggle_btn.setText("WORKING...")
            else:
                on = self._state == STATE_ON
                self._toggle_btn.setText("TURN OFF" if on else "TURN ON")
        except Exception:
            # The panel can be torn down while a slot is finishing; a dead
            # widget must not raise out of the slot.
            pass

    def _import_message(self):
        message = "rt_runtime not on sys.path"
        detail = _IMPORT_CACHE.get("error")
        if detail:
            message += "\n" + detail
        return message

    # -- refresh ------------------------------------------------------------

    def _refresh(self):
        """Probe the runtime and repaint every readout. Never raises."""
        try:
            self._set_busy(True)
            get_adapter = _load_get_adapter()
            if get_adapter is None:
                self._adapter_ok = False
                self._target_lbl.setText("n/a")
                self._python_lbl.setText("n/a")
                self._abi_lbl.setText("n/a")
                self._set_status(STATE_OFF)
                self._error_lbl.setText(self._import_message())
                self._details_lbl.setText(
                    "Put the rt_runtime package on sys.path, then press Refresh."
                )
                return

            try:
                adapter = get_adapter()
            except Exception:
                self._adapter_ok = False
                self._set_status(STATE_ERROR)
                self._error_lbl.setText("get_adapter() failed: " + _exc_text())
                self._details_lbl.setText(_clip(traceback.format_exc()))
                return

            self._adapter_ok = True
            probe = adapter.probe()
            status_obj, status_error = self._call_status(adapter)
            self._render_probe(probe, status_obj, status_error, adapter)
        except Exception:
            self._adapter_ok = False
            self._set_status(STATE_ERROR)
            self._error_lbl.setText("probe failed: " + _exc_text())
            self._details_lbl.setText(_clip(traceback.format_exc()))
        finally:
            self._set_busy(False)

    def _call_status(self, adapter):
        try:
            return adapter.status(), ""
        except Exception:
            return None, _exc_text()

    def _render_probe(self, probe, status_obj, status_error, adapter=None):
        target = _target_name(adapter, probe)
        python_version = _as_text(_get(probe, _PYTHON_KEYS), "n/a")

        # rt_runtime.probe() nests the compatibility report under
        # "compatibility"; the flat fallbacks keep this working if that changes.
        compat = _get(probe, _COMPAT_KEYS)
        verdict_raw = None
        blockers = []
        reasons = []
        if compat is not None:
            verdict_raw = _get(compat, ("safe", "verdict", "status", "is_safe", "ok"))
            blockers = _as_list(_get(compat, _BLOCKER_KEYS))
            reasons = _as_list(_get(compat, _REASON_KEYS))
        if verdict_raw is None:
            verdict_raw = _get(probe, _VERDICT_KEYS)
        if not blockers:
            blockers = _as_list(_get(probe, _BLOCKER_KEYS))
        if not reasons:
            reasons = _as_list(_get(probe, _REASON_KEYS))
        if verdict_raw is not None and not isinstance(verdict_raw, (str, bool)):
            # Another nesting shape, e.g. probe.abi.verdict.
            blockers = blockers or _as_list(_get(verdict_raw, _BLOCKER_KEYS))
            reasons = reasons or _as_list(_get(verdict_raw, _REASON_KEYS))
            verdict_raw = _get(
                verdict_raw, ("verdict", "status", "safe", "is_safe", "ok")
            )
        if verdict_raw is None:
            verdict_raw = _get(probe, ("abi_safe", "safe", "is_safe"))
        verdict_text, unsafe = _verdict(verdict_raw, blockers)
        if unsafe:
            if blockers:
                abi_text = "UNSAFE: %d blocker%s" % (
                    len(blockers),
                    "" if len(blockers) == 1 else "s",
                )
            else:
                abi_text = "UNSAFE"
        else:
            abi_text = verdict_text or "n/a"

        state = _read_state(status_obj, probe)
        if state not in (STATE_ON, STATE_OFF, STATE_REFUSED, STATE_ERROR):
            state = self._state  # keep the last known state if probe is silent

        self._target_lbl.setText(target)
        self._python_lbl.setText(python_version)
        self._abi_lbl.setText(abi_text)
        self._set_status(state)

        errors = []
        if status_error:
            errors.append("status(): " + status_error)
        probe_error = _get(probe, _ERROR_KEYS)
        if probe_error:
            errors.append(_as_text(probe_error))
        self._error_lbl.setText("\n".join(errors) if errors else "(none)")

        details = []
        if _get(probe, ("available",)) is False:
            details.append(
                "Target unavailable: the home or site-packages path is missing."
            )
        if blockers:
            details.append("ABI blockers:")
            details.extend(blockers)
        if reasons:
            details.append("Reasons:")
            details.extend(reasons)
        self._details_lbl.setText(_render_list(details) if details else "(none)")

    # -- slots --------------------------------------------------------------

    def _on_toggle_clicked(self):
        """Call adapter.toggle() and repaint from its ActivationResult."""
        if self._busy:
            return
        self._set_busy(True)
        try:
            get_adapter = _load_get_adapter()
            if get_adapter is None:
                self._adapter_ok = False
                self._set_status(STATE_OFF)
                self._error_lbl.setText(self._import_message())
                return

            try:
                adapter = get_adapter()
            except Exception:
                self._adapter_ok = False
                self._set_status(STATE_ERROR)
                self._error_lbl.setText("get_adapter() failed: " + _exc_text())
                self._details_lbl.setText(_clip(traceback.format_exc()))
                return

            self._adapter_ok = True
            result = adapter.toggle()
            self._apply_activation(result)
        except Exception:
            self._set_status(STATE_ERROR)
            self._error_lbl.setText("toggle failed: " + _exc_text())
            self._details_lbl.setText(_clip(traceback.format_exc()))
        finally:
            self._set_busy(False)

    def _on_refresh_clicked(self):
        if self._busy:
            return
        try:
            self._refresh()
        except Exception:
            self._set_status(STATE_ERROR)
            self._error_lbl.setText("refresh failed: " + _exc_text())
            self._details_lbl.setText(_clip(traceback.format_exc()))
            self._set_busy(False)

    # -- turn slots ---------------------------------------------------------

    TURN_POLL_MS = 250

    def _turn_run_text(self):
        return "Run live turn" if self._turn_live else "Run turn (offline)"

    def _set_turn_status(self, text, color=None):
        try:
            self._turn_lbl.setText(_clip(text, 400))
            self._turn_lbl.setStyleSheet(
                "color: %s;" % color if color else ""
            )
        except Exception:
            # The panel can be torn down mid-poll; a dead widget must not raise.
            pass

    def _refresh_turn_buttons(self):
        try:
            running = self._turn_poller is not None and not self._turn_poller.done
            self._turn_cancel_btn.setEnabled(bool(running))
            self._turn_run_btn.setEnabled((not self._busy) and (not running))
        except Exception:
            pass

    def _stop_turn_timer(self):
        try:
            if self._turn_timer is not None:
                self._turn_timer.stop()
        except Exception:
            pass

    def _ensure_turn_timer(self):
        if self._turn_timer is None:
            timer = QtCore.QTimer(self)
            timer.setInterval(self.TURN_POLL_MS)
            timer.timeout.connect(self._on_turn_tick)
            self._turn_timer = timer
        self._turn_timer.start()

    def _on_run_turn_clicked(self):
        """Submit one turn and start polling. Never blocks on the turn itself."""
        if self._turn_poller is not None and not self._turn_poller.done:
            return
        try:
            prompt = self._turn_input.text().strip()
        except Exception:
            prompt = ""
        if not prompt:
            self._set_turn_status(
                "Type a prompt first.", _STATUS_COLORS[STATE_ERROR]
            )
            return

        get_adapter = _load_get_adapter()
        if get_adapter is None:
            self._set_turn_status(
                self._import_message(), _STATUS_COLORS[STATE_ERROR]
            )
            return
        try:
            control = get_adapter().turn_control()
        except Exception:
            self._set_turn_status(
                "turn_control() failed: " + _exc_text(),
                _STATUS_COLORS[STATE_ERROR],
            )
            return
        if control is None:
            self._set_turn_status(
                "Sidecar is not running. Turn the runtime ON first.",
                _STATUS_COLORS[STATE_REFUSED],
            )
            return

        try:
            from rt_runtime.turn_control import submit_turn
        except Exception:
            self._set_turn_status(
                "rt_runtime.turn_control is not importable on this runtime.",
                _STATUS_COLORS[STATE_ERROR],
            )
            return

        code, poller = submit_turn(control, prompt)
        body = poller.document
        if code not in (None, 200, 202):
            message = _get(body, _ERROR_KEYS) or _get(body, _REASON_KEYS) or (
                "HTTP %d" % code
            )
            self._set_turn_status(
                "Submit rejected: " + _as_text(message),
                _STATUS_COLORS[STATE_ERROR],
            )
            return

        self._turn_control = control
        self._turn_poller = poller
        self._render_turn()
        if not self._turn_poller.done:
            self._ensure_turn_timer()

    def _on_cancel_turn_clicked(self):
        poller = self._turn_poller
        if poller is None or poller.done:
            return
        try:
            code, _body = self._turn_control.cancel(poller.request_id)
        except Exception:
            self._set_turn_status(
                "cancel failed: " + _exc_text(), _STATUS_COLORS[STATE_ERROR]
            )
            return
        if code in (200, 202):
            self._set_turn_status(
                "cancelling %s" % poller.request_id,
                _STATUS_COLORS[STATE_REFUSED],
            )
        else:
            self._set_turn_status(
                "Cancel rejected: HTTP %d" % code, _STATUS_COLORS[STATE_ERROR]
            )

    def _on_turn_tick(self):
        poller = self._turn_poller
        if poller is None:
            self._stop_turn_timer()
            return
        try:
            poller.refresh()
        except Exception:
            # refresh() does not raise by contract; a dead poller must not
            # escape a timer slot either.
            pass
        self._render_turn()
        if poller.done:
            self._stop_turn_timer()

    def _render_turn(self):
        poller = self._turn_poller
        if poller is None:
            self._set_turn_status("(idle)")
            self._refresh_turn_buttons()
            return

        state = poller.state
        if poller.error:
            self._set_turn_status(
                "ERROR: " + poller.error, _STATUS_COLORS[STATE_ERROR]
            )
        elif state == "complete":
            text = _as_text(_get(poller.result, ("text", "message")), "")
            if text:
                self._set_turn_status(
                    "complete: " + text, _STATUS_COLORS[STATE_ON]
                )
            else:
                self._set_turn_status(
                    "complete %s" % poller.request_id, _STATUS_COLORS[STATE_ON]
                )
        elif state in ("cancelled", "timeout"):
            self._set_turn_status(state, _STATUS_COLORS[STATE_REFUSED])
        elif state == "error":
            kind = poller.document.get("error_kind") or "error"
            detail = _as_text(_get(poller.result, ("error",)), "")
            self._set_turn_status(
                "error: %s%s" % (kind, (": " + detail) if detail else ""),
                _STATUS_COLORS[STATE_ERROR],
            )
        else:
            self._set_turn_status(
                "%s %s" % (state, poller.request_id)
            )
        self._refresh_turn_buttons()

    def _apply_activation(self, result):
        if result is None:
            self._set_status(STATE_ERROR)
            self._error_lbl.setText("toggle() returned no result")
            self._details_lbl.setText("(none)")
            return

        ok = _get(result, ("ok", "success"))
        error = _get(result, _ERROR_KEYS)
        reasons = _as_list(_get(result, _REASON_KEYS))
        changed = _get(result, ("changed",))
        state_raw = _get(result, _STATE_KEYS)

        if isinstance(result, bool):
            ok, state_raw = result, None
        elif isinstance(result, str):
            state_raw = result

        if error:
            state = STATE_ERROR
        elif ok is False:
            # RuntimeState.ERROR means the adapter itself failed; any other
            # unsuccessful result is a deliberate refusal.
            state = STATE_ERROR if _state_text(state_raw) == STATE_ERROR else STATE_REFUSED
        else:
            state = _state_text(state_raw)
            if state not in (STATE_ON, STATE_OFF):
                # ok but no explicit state: assume it flipped.
                state = STATE_OFF if self._state == STATE_ON else STATE_ON

        self._set_status(state)

        if error:
            self._error_lbl.setText(_clip(_as_text(error)))
        else:
            self._error_lbl.setText("(none)")

        details = []
        if reasons:
            details.append("Reasons:")
            details.extend(reasons)
        if changed is False and state in (STATE_ON, STATE_OFF):
            details.append("No change; runtime was already %s." % state)
        self._details_lbl.setText(_render_list(details) if details else "(none)")


# ---------------------------------------------------------------------------
# Python Panel hooks
# ---------------------------------------------------------------------------

def onCreateInterface():
    """Build a fresh widget for each panel instance."""
    try:
        return RT_TogglePanel()
    except Exception:
        label = QtWidgets.QLabel(
            "RT Toggle failed to build:\n" + _clip(traceback.format_exc())
        )
        label.setWordWrap(True)
        return label
