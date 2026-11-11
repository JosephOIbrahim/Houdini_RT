"""HoudiniRT shelf tool - runtime ON/OFF toggle.

This is the readable copy of the code run by the ``rt_toggle`` shelf tool.
``rt_shelf.shelf`` holds a thin bootstrap that locates this module, imports it,
and calls :func:`toggle_from_shelf`. Keep both files in the same folder.

Contract used here (fixed by rt_runtime):

    rt_runtime.session.get_adapter() -> adapter
    adapter.toggle() -> ActivationResult(ok, state, changed, reasons, error)

The adapter decides whether activation is allowed (including the Python
version compatibility guard). This module adds no rules of its own; it reports
whatever the adapter returns.

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


def toggle_from_shelf():
    """Toggle the HoudiniRT runtime and report the outcome.

    Returns the ActivationResult on success, or None when the runtime could not
    be imported or the adapter raised. Never raises.
    """
    try:
        ensure_runtime_importable()
        from rt_runtime import session

        adapter = session.get_adapter()
        result = adapter.toggle()
    except Exception:
        detail = traceback.format_exc(limit=1).strip().splitlines()[-1]
        report_status(f"{PREFIX}: toggle failed - {detail}", "error")
        traceback.print_exc()
        return None

    try:
        text, level = describe_result(result)
    except Exception:
        text, level = f"{PREFIX}: toggle returned an unrecognized result", "warning"
    report_status(text, level)
    return result


if __name__ == "__main__":
    toggle_from_shelf()
