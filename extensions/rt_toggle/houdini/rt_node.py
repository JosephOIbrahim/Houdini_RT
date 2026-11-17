"""Native parameter controls for the HoudiniRT viewer session.

The explicit Viewer parameter pins actions to a Solaris pane. Enable requests
a direction, Pause/Resume and Refresh read the same shared render controller.
Saved parameters are not live state: loading/cooking this null never activates
a renderer. Refresh reads current native state. Assistant turn controls are
retained as optional commands and do not determine renderer state.

No node or parameter is created at import time. Call ``build()``.
"""

import os

import hou


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

NODE_NAME = "RT_TOGGLE"

ENABLE_PARM = "enable"
ENABLE_LABEL = "Render with HoudiniRT"
VIEWER_PARM = "rt_viewer"
PAUSE_PARM = "rt_pause"
RESTORE_PARM = "rt_restore"
REFRESH_PARM = "rt_refresh"

STATUS_PARM = "rt_status"
STATUS_LABEL = "RT Status"

TURN_PROMPT_PARM = "rt_prompt"
TURN_PROMPT_LABEL = "RT Prompt"
TURN_RUN_PARM = "rt_run_turn"
TURN_RUN_LABEL = "Run Turn"
TURN_CANCEL_PARM = "rt_cancel_turn"
TURN_CANCEL_LABEL = "Cancel Turn"
TURN_STATUS_PARM = "rt_turn_status"
TURN_STATUS_LABEL = "RT Turn Status"

TURN_IDLE = "IDLE"

STATUS_OFF = "OFF"
STATUS_ON = "ON"
STATUS_REFUSED = "REFUSED"
STATUS_ERROR = "ERROR"

# rt_node.py lives in <extension>/houdini/, so:
#   _MODULE_DIR = <extension>/houdini   (needed to import rt_node)
#   _EXT_ROOT   = <extension>           (needed to import rt_runtime)
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_EXT_ROOT = os.path.dirname(_MODULE_DIR)

# Re-entrancy guard for the enable callback. A module-level flag is enough
# because Houdini parm callbacks run in the main thread; it is not scene state
# and is not saved anywhere.
_TOGGLE_IN_PROGRESS = False

#: The most recent turn, so the Cancel Turn button can reach it.
_ACTIVE_TURN = {"control": None, "poller": None}

#: How long one Run Turn click follows a turn before leaving it running. A parm
#: callback has no timer, so it polls for a bounded moment like the shelf tool.
DEFAULT_TURN_POLL_S = 2.0


# ---------------------------------------------------------------------------
# Callback script template
# ---------------------------------------------------------------------------

def callback_script():
    """Return the one-line Python script stored on the ``enable`` parm.

    The callback script is saved inside the .hip as a string, so it has to be
    self-contained: it puts this module's directory and the extension root on
    ``sys.path`` before importing rt_node. Build-time absolute paths are baked
    in; moving the extension means running ``build()`` again.

    The one-line shape follows the documented recommendation in
    docs:hom/locations.md#parameter_callback_scripts ("write one line of
    Python that calls the 'real' callback function").
    """
    return _callback_call("toggle_callback")


def turn_callback_script():
    """Callback script for the Run Turn button."""
    return _callback_call("turn_callback")


def cancel_callback_script():
    """Callback script for the Cancel Turn button."""
    return _callback_call("cancel_turn_callback")


def _callback_call(function):
    return (
        "import sys; "
        "_rt_module_dir = {module!r}; "
        "_rt_ext_root = {root!r}; "
        "(_rt_module_dir in sys.path) or sys.path.insert(0, _rt_module_dir); "
        "(_rt_ext_root in sys.path) or sys.path.insert(0, _rt_ext_root); "
        "import rt_node; rt_node.{name}(kwargs)"
    ).format(module=_MODULE_DIR, root=_EXT_ROOT, name=function)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(parent=None, name=NODE_NAME):
    """Create or reuse the RT_TOGGLE node and its native render controls.

    ``parent`` may be ``/obj`` (default) or a geometry network. The node type
    is the built-in ``null``, which resolves to Object/null under /obj and
    Sop/null under a geo -- both exact type names in the 22.0.429 installed
    help (help:22.0.429/nodes.zip/obj/null.txt, .../sop/null.txt).

    Safe to run twice: an existing node of that name is reused, and the parm
    templates are replaced in place. ``setParmTemplateGroup`` preserves the
    current values of same-named parms, so a rebuild does not fire the
    callback and does not change any viewer's renderer.

    Returns the ``hou.OpNode``.
    """
    if parent is None:
        parent = hou.node("/obj")
    if parent is None:
        raise RuntimeError(
            "/obj does not exist in this session; pass parent= explicitly"
        )

    with hou.undos.group("RT_TOGGLE: build"):
        node = parent.node(name)
        if node is None:
            node = parent.createNode("null", name)

        group = node.parmTemplateGroup()

        enable_template = hou.ToggleParmTemplate(
            ENABLE_PARM,
            ENABLE_LABEL,
            default_value=False,
            help=(
                "Enable HoudiniRT in the named Solaris viewer; Off restores "
                "its previous renderer. This checkbox is a request; use "
                "Refresh to read live state after loading a scene."
            ),
            script_callback=callback_script(),
            script_callback_language=hou.scriptLanguage.Python,
        )
        if group.find(ENABLE_PARM) is None:
            group.append(enable_template)
        else:
            # find() returns a copy; replace() is required for edits to land.
            group.replace(ENABLE_PARM, enable_template)

        if group.find(STATUS_PARM) is None:
            group.append(
                hou.StringParmTemplate(
                    STATUS_PARM,
                    STATUS_LABEL,
                    1,
                    default_value=("Not checked; press Refresh",),
                    help=(
                        "Observed renderer and pause state at the last action "
                        "or Refresh. Saved text is not a live status signal."
                    ),
                )
            )

        viewer_template = hou.StringParmTemplate(
            VIEWER_PARM, "Solaris Viewer", 1, default_value=("",),
            help=("Pane name shown in the HoudiniRT panel. Empty resolves the "
                  "invoking viewer or the only Solaris viewer, then pins it."),
            script_callback=_callback_call("refresh_render_callback"),
            script_callback_language=hou.scriptLanguage.Python,
        )
        for key, template in (
            (VIEWER_PARM, viewer_template),
            (PAUSE_PARM, hou.ButtonParmTemplate(
                PAUSE_PARM, "Pause / Resume", help="Pause/resume the named viewer.",
                script_callback=_callback_call("pause_callback"),
                script_callback_language=hou.scriptLanguage.Python)),
            (RESTORE_PARM, hou.ButtonParmTemplate(
                RESTORE_PARM, "Restore Previous Renderer",
                help="Switch Off or retry an incomplete renderer, pause or reference-grid restoration.",
                script_callback=_callback_call("restore_render_callback"),
                script_callback_language=hou.scriptLanguage.Python)),
            (REFRESH_PARM, hou.ButtonParmTemplate(
                REFRESH_PARM, "Refresh Render State", help="Read native viewer state.",
                script_callback=_callback_call("refresh_render_callback"),
                script_callback_language=hou.scriptLanguage.Python)),
        ):
            if group.find(key) is None:
                group.append(template)
            else:
                group.replace(key, template)

        # -- optional assistant turn -------------------------------------
        prompt_template = hou.StringParmTemplate(
            TURN_PROMPT_PARM,
            TURN_PROMPT_LABEL,
            1,
            default_value=("",),
            help=(
                "Prompt for one turn. Run Turn submits it to the running "
                "HoudiniRT sidecar; Cancel Turn stops the most recent one."
            ),
        )
        run_template = hou.ButtonParmTemplate(
            TURN_RUN_PARM,
            TURN_RUN_LABEL,
            help=(
                "Submit one turn with wait_ms=0, then poll for up to "
                "RT_TURN_POLL_S seconds (default 2)."
            ),
            script_callback=turn_callback_script(),
            script_callback_language=hou.scriptLanguage.Python,
        )
        cancel_template = hou.ButtonParmTemplate(
            TURN_CANCEL_PARM,
            TURN_CANCEL_LABEL,
            help="Ask the running turn to stop. Cancellation is cooperative.",
            script_callback=cancel_callback_script(),
            script_callback_language=hou.scriptLanguage.Python,
        )
        turn_status_template = hou.StringParmTemplate(
            TURN_STATUS_PARM,
            TURN_STATUS_LABEL,
            1,
            default_value=(TURN_IDLE,),
            help=(
                "Read-only result of the last turn: IDLE, running <id>, "
                "complete, cancelled, timeout or an error."
            ),
        )
        for name, template in (
            (TURN_PROMPT_PARM, prompt_template),
            (TURN_RUN_PARM, run_template),
            (TURN_CANCEL_PARM, cancel_template),
            (TURN_STATUS_PARM, turn_status_template),
        ):
            if group.find(name) is None:
                group.append(template)
            else:
                group.replace(name, template)

        node.setParmTemplateGroup(group)

        # Lock the status parms so they cannot be typed over in the parameter
        # pane. lock() raises hou.PermissionError when the parm belongs to a
        # locked digital asset, so the failure is swallowed here.
        for parm_name in (STATUS_PARM, TURN_STATUS_PARM):
            parm = node.parm(parm_name)
            if parm is not None:
                try:
                    parm.lock(True)
                except Exception:
                    pass

    print("RT_TOGGLE ready at {}".format(node.path()))
    return node


# ---------------------------------------------------------------------------
# Parm callback
# ---------------------------------------------------------------------------

def _render_target(node, kwargs):
    from rt_runtime.render_session import get_render_controller

    controller = get_render_controller()
    parm = node.parm(VIEWER_PARM)
    name = str(parm.eval()).strip() if parm is not None else ""
    viewer = controller.resolve_viewer(
        viewer=None if name else kwargs.get("pane"), viewer_name=name or None
    )
    # Pin automatic resolution so a later Off cannot target a different pane.
    if parm is not None and not name:
        parm.set(viewer.name())
    return controller, viewer


def _show_render_status(node, result):
    enable = node.parm(ENABLE_PARM)
    if enable is not None and result.state != "error":
        _restore_enable(enable, result.active)
    detail = result.error or result.message
    pending = getattr(result, "pending", "")
    if pending:
        detail = "Pending {}: {}".format(pending, detail)
    _set_status(node, "{} | {} | {} | {}".format(
        result.state.upper(), result.viewer_name, result.renderer or "none", detail
    ))


def _render_callback(kwargs, action):
    """Read/apply renderer state only on an explicit parameter action."""
    global _TOGGLE_IN_PROGRESS
    if _TOGGLE_IN_PROGRESS:
        return
    _TOGGLE_IN_PROGRESS = True
    try:
        node = kwargs.get("node") or hou.pwd()
        if node is None:
            return
        enable = node.parm(ENABLE_PARM)
        requested = bool(enable.eval()) if enable is not None else False
        try:
            controller, viewer = _render_target(node, kwargs)
            if action == "enable":
                result = (controller.activate(viewer) if requested
                          else controller.deactivate(viewer))
            elif action == "pause":
                result = controller.toggle_pause(viewer)
            elif action == "restore":
                result = controller.deactivate(viewer)
            else:
                result = controller.status(viewer)
            if result.state == "error" and action == "enable" and enable is not None:
                _restore_enable(enable, not requested)
            _show_render_status(node, result)
        except Exception as exc:
            # No native observation was available. Revert the user request
            # and explicitly mark state unknown rather than claiming OFF.
            if action == "enable" and enable is not None:
                _restore_enable(enable, not requested)
            _set_status(node, "UNKNOWN | {}".format(exc))
    finally:
        _TOGGLE_IN_PROGRESS = False


def toggle_callback(kwargs):
    """Checked requests activate; unchecked requests restore, never inversion."""
    _render_callback(kwargs, "enable")


def pause_callback(kwargs):
    _render_callback(kwargs, "pause")


def refresh_render_callback(kwargs):
    _render_callback(kwargs, "status")


def restore_render_callback(kwargs):
    _render_callback(kwargs, "restore")


# ---------------------------------------------------------------------------
# Turn callbacks
# ---------------------------------------------------------------------------

def turn_callback(kwargs):
    """Handle a press of Run Turn: submit one turn, poll briefly, report.

    ``wait_ms=0`` returns at once, like the panel. A parm callback has no
    timer to poll from, so this follows the turn for ``RT_TURN_POLL_S``
    seconds (default 2.0) and leaves it running if it takes longer; Cancel
    Turn can still reach it.
    """
    node = kwargs.get("node")
    if node is None:
        node = hou.pwd()
    if node is None:
        return

    prompt_parm = node.parm(TURN_PROMPT_PARM)
    text = "" if prompt_parm is None else str(prompt_parm.eval()).strip()
    if not text:
        _set_parm_text(node, TURN_STATUS_PARM, "ERROR | no prompt given")
        return

    try:
        from rt_runtime import session

        control = session.get_adapter().turn_control()
    except Exception as exc:
        _set_parm_text(node, TURN_STATUS_PARM, "ERROR | {}".format(exc))
        return
    if control is None:
        _set_parm_text(
            node,
            TURN_STATUS_PARM,
            "ERROR | optional assistant is not connected; renderer controls remain available",
        )
        return

    try:
        from rt_runtime.turn_control import submit_turn
    except Exception as exc:
        _set_parm_text(
            node,
            TURN_STATUS_PARM,
            "ERROR | rt_runtime.turn_control not importable: {!r}".format(exc),
        )
        return

    code, poller = submit_turn(control, text)
    body = poller.document
    if code not in (None, 200, 202):
        message = body.get("error") or body.get("message") or "HTTP {}".format(code)
        _set_parm_text(
            node, TURN_STATUS_PARM, "ERROR | rejected: {}".format(message)
        )
        return

    _ACTIVE_TURN["control"] = control
    _ACTIVE_TURN["poller"] = poller

    try:
        limit = float(os.environ.get("RT_TURN_POLL_S", DEFAULT_TURN_POLL_S))
    except (TypeError, ValueError):
        limit = DEFAULT_TURN_POLL_S
    _poll_to_end(poller, limit)
    _set_parm_text(node, TURN_STATUS_PARM, _turn_text(poller))


def cancel_turn_callback(kwargs):
    """Handle a press of Cancel Turn. Never raises."""
    node = kwargs.get("node")
    if node is None:
        node = hou.pwd()
    if node is None:
        return

    poller = _ACTIVE_TURN.get("poller")
    control = _ACTIVE_TURN.get("control")
    if poller is None or control is None or poller.done:
        _set_parm_text(node, TURN_STATUS_PARM, "IDLE | no running turn")
        return
    try:
        code, _body = control.cancel(poller.request_id)
    except Exception as exc:
        _set_parm_text(
            node, TURN_STATUS_PARM, "ERROR | cancel failed: {!r}".format(exc)
        )
        return
    if code in (200, 202):
        _set_parm_text(
            node, TURN_STATUS_PARM, "CANCELLING | {}".format(poller.request_id)
        )
    else:
        _set_parm_text(
            node, TURN_STATUS_PARM, "ERROR | cancel rejected: HTTP {}".format(code)
        )


def _poll_to_end(poller, deadline_s):
    """Poll until terminal or the deadline passes. Never sleeps past it."""
    import time

    end = time.monotonic() + max(0.0, float(deadline_s))
    while not poller.done and time.monotonic() < end:
        remaining = max(0.0, end - time.monotonic())
        poller.refresh(timeout=remaining)
        if not poller.done:
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
    return poller


def _turn_text(poller):
    """One parm line for a TurnPoller."""
    if poller.error:
        return "ERROR | {}".format(poller.error)
    state = poller.state
    if state == "complete":
        result = poller.result
        text = result.get("text") if isinstance(result, dict) else None
        if text:
            return "COMPLETE | {}".format(text)
        return "COMPLETE | {}".format(poller.request_id)
    if state == "error":
        kind = (poller.document or {}).get("error_kind") or "error"
        return "ERROR | {}".format(kind)
    if state in ("cancelled", "timeout"):
        return "{} | {}".format(state.upper(), poller.request_id)
    return "RUNNING | {}".format(poller.request_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _refuse(node, parm, requested_on, token, detail):
    """Write REFUSED/ERROR and put the checkbox back to its previous value."""
    _set_status(node, "{} | {}".format(token, detail))
    _restore_enable(parm, not requested_on)


def _restore_enable(parm, value):
    """Set the enable parm back without letting the failure escape."""
    try:
        parm.set(1 if value else 0)
    except Exception:
        # A locked digital-asset parm can refuse the write; the status parm
        # still carries the verdict.
        pass


def _set_status(node, text):
    """Write ``text`` into the read-only toggle status parm."""
    _set_parm_text(node, STATUS_PARM, text)


def _set_parm_text(node, parm_name, text):
    """Write ``text`` into a read-only string parm.

    The parm is locked in the UI. The docs do not state whether Parm.set()
    is refused on a locked parm, so this unlocks first when it can, writes,
    then re-locks. On a locked digital asset, ``lock(False)`` raises
    hou.PermissionError; the plain write is attempted instead.
    """
    status_parm = node.parm(parm_name)
    if status_parm is None:
        return

    was_locked = status_parm.isLocked()
    if was_locked:
        try:
            status_parm.lock(False)
        except Exception:
            try:
                status_parm.set(text)
            except Exception:
                pass
            return

    try:
        status_parm.set(text)
    except Exception:
        pass
    finally:
        if was_locked:
            try:
                status_parm.lock(True)
            except Exception:
                pass


def _status_from_result(result):
    """Read the result's state as one of the display tokens."""
    state = getattr(result, "state", None)
    value = getattr(state, "value", None)
    if value is None:
        value = getattr(state, "name", state)
    text = str(value).upper()
    if text == STATUS_ON:
        return STATUS_ON
    if text == STATUS_OFF:
        return STATUS_OFF
    return STATUS_ERROR


def _result_reason(result):
    """Turn ActivationResult.reasons / .error into one readable string."""
    reasons = result.reasons
    if reasons:
        if isinstance(reasons, str):
            return reasons
        return "; ".join(str(reason) for reason in reasons)
    error = result.error
    if error:
        return str(error)
    return "no reason given"


def _result_detail(result):
    """Format the non-boolean parts of ActivationResult for the status tail."""
    return "state={!r} changed={!r}".format(result.state, result.changed)
