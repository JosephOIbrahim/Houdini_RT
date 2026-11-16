"""HoudiniRT shelf controls for the intended Solaris viewer.

This is the readable copy of the code run by the ``rt_toggle`` shelf tool.
``rt_shelf.shelf`` holds a thin bootstrap that locates this module, imports it,
and calls :func:`toggle_from_shelf`. Keep both files in the same folder.

The primary toggle and pause actions use the process-wide render controller.
They never start the optional Python assistant. The caller's pane is passed
through to the controller so an ambiguous desktop never selects an arbitrary
viewer. Existing assistant turn helpers remain available independently.

LIMITATION - the shelf button's label and icon are static
    A shelf tool's label and icon are declared once, in the ``.shelf`` XML, and
    do not change while Houdini runs. Houdini 22.0.429 documents no per-state
    dynamic label or icon for shelf tools, so this button cannot show ON/OFF on
    its own face. The current state is reported in Houdini's status bar (and
    printed to stdout), and in the HoudiniRT Python Panel (``rt_panel.pypanel``).
    Do not add a dynamic shelf icon: there is no documented API for it.

The tool never raises. Every failure is turned into a status line and the
click is swallowed.
"""

import os
import sys
import traceback
from collections.abc import Mapping

#: Prefix on every status line this tool writes.
PREFIX = "HoudiniRT"

#: Optional explicit fallback for the extension root (the folder containing
#: ``rt_runtime``). Empty means "no baked-in machine path": set
#: ``RT_TOGGLE_ROOT`` instead, or keep this file directly beside ``rt_runtime``.
REPO_ROOT = ""

#: Severity keys used by report_status(), mapped to hou.severityType names.
_SEVERITY_NAMES = {
    "message": "Message",
    "warning": "Warning",
    "error": "Error",
}


# ---------------------------------------------------------------------------
# Locating the runtime
# ---------------------------------------------------------------------------


def extension_root():
    """Return the folder that contains the ``rt_runtime`` package.

    Resolution order:
      1. ``RT_TOGGLE_ROOT`` environment variable, if set.
      2. The parent of this file's folder, when ``rt_runtime`` is found there.
      3. ``REPO_ROOT``, when it is non-empty.

    Returns an empty string when nothing resolves. Callers must treat that as
    "runtime not importable" and report a status line, never as a path.

    A copy of this file placed somewhere else on the Houdini path therefore
    still finds the runtime via ``RT_TOGGLE_ROOT``, with no machine path baked
    into the source tree.
    """
    override = os.environ.get("RT_TOGGLE_ROOT")
    if override:
        return os.path.normpath(override)

    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    if os.path.isdir(os.path.join(parent, "rt_runtime")):
        return parent
    if REPO_ROOT:
        return os.path.normpath(REPO_ROOT)
    return ""


def ensure_runtime_importable():
    """Put the extension root on ``sys.path`` so ``import rt_runtime`` works."""
    root = extension_root()
    if not root:
        return
    if os.path.isdir(os.path.join(root, "rt_runtime")) and root not in sys.path:
        sys.path.insert(0, root)


# ---------------------------------------------------------------------------
# Formatting an ActivationResult
# ---------------------------------------------------------------------------


def _state_text(state):
    """Render a runtime state as display text (RuntimeState.ON -> 'ON')."""
    if state is None:
        return "UNKNOWN"
    value = getattr(state, "value", None)
    if value is None:
        value = getattr(state, "name", state)
    return str(value).upper()


def _field(result, name, default=None):
    """Read one result field, accepting either an object or a mapping."""
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _reason_list(value):
    """Accept None, a string, or any iterable of reasons; return a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value)]


def describe_result(result):
    """Return ``(one_line_text, severity_key)`` for an ActivationResult.

    severity_key is one of "message", "warning", "error". The formatting is
    tolerant of small field-name or type changes in the runtime: every field is
    read through _field() with a default, so an object or a mapping both work.
    """
    ok = bool(_field(result, "ok", False))
    state = _state_text(_field(result, "state", None))
    changed = bool(_field(result, "changed", False))
    reasons = _reason_list(_field(result, "reasons", None))
    error = _field(result, "error", None)

    if error:
        return f"{PREFIX}: {state} - toggle failed: {error}", "error"
    if not ok:
        detail = "; ".join(reasons) if reasons else "refused"
        return f"{PREFIX}: {state} - refused: {detail}", "warning"
    if changed:
        return f"{PREFIX}: {state} - toggled", "message"
    detail = reasons[0] if reasons else "no change"
    return f"{PREFIX}: {state} - {detail}", "message"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report_status(text, level="message"):
    """Write one line to Houdini's status bar (when hou is available) and stdout.

    Neither surface may raise: a missing ``hou`` module (plain Python), a
    non-UI hython session, or a closed status bar all degrade to stdout only.
    """
    try:
        print(text)
    except Exception:
        pass

    try:
        import hou
    except Exception:
        return

    try:
        severity = getattr(
            getattr(hou, "severityType", None),
            _SEVERITY_NAMES.get(level, "Message"),
            None,
        )
        if severity is None:
            hou.ui.setStatusMessage(text)
        else:
            hou.ui.setStatusMessage(text, severity)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _render_action(action, viewer=None, viewer_name=None):
    """Execute a shared render command on the caller's target; never raises."""
    try:
        ensure_runtime_importable()
        from rt_runtime.render_session import get_render_controller

        controller = get_render_controller()
        target = controller.resolve_viewer(viewer=viewer, viewer_name=viewer_name)
        result = getattr(controller, action)(target)
    except Exception as exc:
        report_status(f"{PREFIX}: {exc}", "error")
        return None

    text, level = describe_render_status(result)
    report_status(text, level)
    return result


def describe_render_status(result):
    """Report observed renderer state, not a worker heartbeat or convergence."""
    state = str(result.state).upper()
    target = result.viewer_name or "no viewer"
    renderer = result.renderer or "none"
    detail = result.error or result.message
    pending = getattr(result, "pending", "")
    if pending:
        detail = f"Pending {pending}: {detail}"
    text = f"{PREFIX}: {state} | {target} | {renderer}"
    if detail:
        text += f" | {detail}"
    level = "error" if result.error else (
        "warning" if result.state == "unavailable" else "message"
    )
    return text, level


def toggle_from_shelf(viewer=None, viewer_name=None):
    """Enable HoudiniRT or restore the renderer in the intended viewer."""
    return _render_action("toggle", viewer, viewer_name)


def pause_from_shelf(viewer=None, viewer_name=None):
    """Pause/resume HoudiniRT in the intended viewer."""
    return _render_action("toggle_pause", viewer, viewer_name)


def refresh_from_shelf(viewer=None, viewer_name=None):
    """Read the intended viewer without changing it."""
    return _render_action("status", viewer, viewer_name)


def render_is_on(viewer=None):
    """Read-only radial-menu check; unavailable/ambiguous targets are unticked."""
    try:
        ensure_runtime_importable()
        from rt_runtime.render_session import get_render_controller
        controller = get_render_controller()
        return controller.status(controller.resolve_viewer(viewer=viewer)).state in (
            "active", "paused"
        )
    except Exception:
        return False


def render_is_paused(viewer=None):
    try:
        ensure_runtime_importable()
        from rt_runtime.render_session import get_render_controller
        controller = get_render_controller()
        return controller.status(controller.resolve_viewer(viewer=viewer)).state == "paused"
    except Exception:
        return False


if __name__ == "__main__":
    toggle_from_shelf()


# ---------------------------------------------------------------------------
# One turn, from the shelf
# ---------------------------------------------------------------------------

#: Prefix on every turn status line.
TURN_PREFIX = "HoudiniRT turn"

#: How long one click follows a turn before leaving it running. The panel polls
#: on a Qt timer; a shelf click has no timer, so it polls for a bounded moment
#: and then reports the request id for the cancel tool.
DEFAULT_TURN_POLL_S = 2.0

#: The most recent turn, so ``rt_turn_cancel`` can reach it.
_ACTIVE_TURN = {"control": None, "poller": None}


def _read_turn_prompt():
    """Prompt text from ``RT_TURN_PROMPT`` or a Houdini input box; '' if none."""
    env = os.environ.get("RT_TURN_PROMPT")
    if env and env.strip():
        return env.strip()
    try:
        import hou
    except Exception:
        return ""
    try:
        value = hou.ui.readInput("HoudiniRT turn prompt", buttons=("OK", "Cancel"))
    except Exception:
        return ""
    # readInput returns (button_index, text) on current builds. Only OK submits.
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        if value[0] != 0:
            return ""
        return str(value[1] or "").strip()
    return ""


def _poll_to_end(poller, deadline_s):
    """Poll until terminal or the deadline passes; never sleeps past it."""
    import time

    end = time.monotonic() + max(0.0, float(deadline_s))
    while not poller.done and time.monotonic() < end:
        remaining = max(0.0, end - time.monotonic())
        poller.refresh(timeout=remaining)
        if not poller.done:
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
    return poller


def describe_turn(poller):
    """Return ``(one_line_text, severity_key)`` for a ``TurnPoller``."""
    if poller is None:
        return f"{TURN_PREFIX}: no turn", "warning"
    if poller.error:
        return f"{TURN_PREFIX}: ERROR - {poller.error}", "error"

    state = poller.state
    if state == "complete":
        result = poller.result
        text = result.get("text") if isinstance(result, Mapping) else None
        if text:
            return f"{TURN_PREFIX}: complete - {text}", "message"
        return f"{TURN_PREFIX}: complete", "message"
    if state in ("cancelled", "timeout"):
        return f"{TURN_PREFIX}: {state}", "warning"
    if state == "error":
        kind = (poller.document or {}).get("error_kind") or "error"
        return f"{TURN_PREFIX}: error - {kind}", "error"
    return f"{TURN_PREFIX}: {state} {poller.request_id}", "message"


def turn_from_shelf(prompt=None, wait_s=None):
    """Submit one turn and follow it briefly. Never raises.

    Uses ``wait_ms=0`` like the panel, so admission returns at once. With no
    event loop to poll from, this click then polls for ``RT_TURN_POLL_S``
    seconds (default 2.0) and leaves the turn running if it takes longer; the
    cancel tool can still reach it.
    """
    try:
        ensure_runtime_importable()
        from rt_runtime import session

        control = session.get_adapter().turn_control()
    except Exception:
        detail = traceback.format_exc(limit=1).strip().splitlines()[-1]
        report_status(f"{TURN_PREFIX}: failed - {detail}", "error")
        return None

    if control is None:
        report_status(
            f"{TURN_PREFIX}: optional assistant is not connected; renderer controls remain available",
            "warning",
        )
        return None

    text = _read_turn_prompt() if prompt is None else str(prompt).strip()
    if not text:
        report_status(f"{TURN_PREFIX}: no prompt given", "warning")
        return None

    try:
        from rt_runtime.turn_control import submit_turn
    except Exception:
        report_status(f"{TURN_PREFIX}: rt_runtime.turn_control not importable", "error")
        return None

    code, poller = submit_turn(control, text)
    body = poller.document
    if code not in (None, 200, 202):
        message = body.get("error") or body.get("message") or f"HTTP {code}"
        report_status(f"{TURN_PREFIX}: rejected - {message}", "error")
        return None

    _ACTIVE_TURN["control"] = control
    _ACTIVE_TURN["poller"] = poller

    try:
        limit = float(os.environ.get("RT_TURN_POLL_S", DEFAULT_TURN_POLL_S))
    except (TypeError, ValueError):
        limit = DEFAULT_TURN_POLL_S
    _poll_to_end(poller, limit if wait_s is None else wait_s)

    if poller.done:
        text, level = describe_turn(poller)
    else:
        text = f"{TURN_PREFIX}: running {poller.request_id} - use RT Turn Cancel to stop it"
        level = "message"
    report_status(text, level)
    return poller


def cancel_turn_from_shelf():
    """Cancel the most recent turn, if it is still running. Never raises."""
    poller = _ACTIVE_TURN.get("poller")
    control = _ACTIVE_TURN.get("control")
    if poller is None or control is None or poller.done:
        report_status(f"{TURN_PREFIX}: no running turn", "message")
        return None
    try:
        code, body = control.cancel(poller.request_id)
    except Exception:
        detail = traceback.format_exc(limit=1).strip().splitlines()[-1]
        report_status(f"{TURN_PREFIX}: cancel failed - {detail}", "error")
        return None
    if code in (200, 202):
        report_status(f"{TURN_PREFIX}: cancelling {poller.request_id}", "message")
    else:
        report_status(f"{TURN_PREFIX}: cancel rejected - HTTP {code}", "warning")
    return poller

