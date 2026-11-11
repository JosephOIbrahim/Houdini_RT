"""rt_node.py - Houdini-side on/off node for the HoudiniRT runtime.

Builds a small null node named ``RT_TOGGLE`` (an Object/null under ``/obj``,
a Sop/null when the parent is a geometry network) carrying:

  * a toggle parm   ``enable``     labeled "RT Runtime"
  * a string parm   ``rt_status``  labeled "RT Status" (read-only in the UI)
  * a Python parm callback on ``enable`` that calls
    ``rt_runtime.session.get_adapter().toggle()`` and writes the outcome into
    ``rt_status``.

Fixed contract (from the HoudiniRT scaffold):
    rt_runtime.session.get_adapter() -> adapter
    adapter.toggle()                 -> ActivationResult(ok, state, changed,
                                                         reasons, error)

Documented vs unverified (checked against the local SideFX corpus):
  * ``hou.ToggleParmTemplate``, ``hou.StringParmTemplate``,
    ``hou.ParmTemplateGroup`` (``append`` / ``find`` / ``replace``),
    ``OpNode.parmTemplateGroup`` / ``OpNode.setParmTemplateGroup``,
    ``Parm.lock`` / ``Parm.isLocked`` and the ``script_callback`` /
    ``script_callback_language`` constructor arguments are documented
    (docs:hom/hou/*.md).
  * The callback ``kwargs`` keys (``parm``, ``node``, ``script_value`` ...)
    are documented in docs:hom/locations.md#parameter_callback_scripts.
  * NOT documented locally: whether ``Parm.set()`` still works on a *locked*
    parm. ``_set_status`` therefore tries to unlock, write, and re-lock, and
    falls back to a plain write when unlocking is refused (locked digital
    asset). Treat that as the live-check item.
  * NOT documented locally: whether a programmatic ``Parm.set()`` re-enters
    the callback synchronously. ``_TOGGLE_IN_PROGRESS`` blocks the common
    (synchronous) case; a deferred callback could still fire a second
    ``toggle()``. Also a live-check item.
  * ``ActivationResult.state``'s exact type is not fixed by the contract; it
    is only formatted with ``repr()`` into the diagnostic tail of the status
    string, never compared.

No node or parm is created at import time. Call ``build()``.
"""

import os

import hou


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

NODE_NAME = "RT_TOGGLE"

ENABLE_PARM = "enable"
ENABLE_LABEL = "RT Runtime"

STATUS_PARM = "rt_status"
STATUS_LABEL = "RT Status"

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
    return (
        "import sys; "
        "_rt_module_dir = {module!r}; "
        "_rt_ext_root = {root!r}; "
        "(_rt_module_dir in sys.path) or sys.path.insert(0, _rt_module_dir); "
        "(_rt_ext_root in sys.path) or sys.path.insert(0, _rt_ext_root); "
        "import rt_node; rt_node.toggle_callback(kwargs)"
    ).format(module=_MODULE_DIR, root=_EXT_ROOT)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(parent=None, name=NODE_NAME):
    """Create (or reuse) the RT_TOGGLE node and its two spare parms.

    ``parent`` may be ``/obj`` (default) or a geometry network. The node type
    is the built-in ``null``, which resolves to Object/null under /obj and
    Sop/null under a geo -- both exact type names in the 22.0.429 installed
    help (help:22.0.429/nodes.zip/obj/null.txt, .../sop/null.txt).

    Safe to run twice: an existing node of that name is reused, and the parm
    templates are replaced in place. ``setParmTemplateGroup`` preserves the
    current values of same-named parms, so a rebuild does not fire the
    callback and does not flip the runtime.

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
                "Turn the HoudiniRT runtime on or off. Calls "
                "rt_runtime.session.get_adapter().toggle() and reports the "
                "outcome in RT Status."
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
                    default_value=(STATUS_OFF,),
                    help=(
                        "Read-only result of the last toggle. Starts with one "
                        "of OFF, ON, REFUSED or ERROR; anything after the "
                        "separator is diagnostic detail."
                    ),
                )
            )

        node.setParmTemplateGroup(group)

        # Lock the status parm so it cannot be typed over in the parameter
        # pane. lock() raises hou.PermissionError when the parm belongs to a
        # locked digital asset, so the failure is swallowed here.
        status_parm = node.parm(STATUS_PARM)
        if status_parm is not None:
            try:
                status_parm.lock(True)
            except Exception:
                pass

    print("RT_TOGGLE ready at {}".format(node.path()))
    return node


# ---------------------------------------------------------------------------
# Parm callback
# ---------------------------------------------------------------------------

def toggle_callback(kwargs):
    """Handle a change of the ``enable`` toggle.

    ``kwargs`` is the documented callback dictionary
    (docs:hom/locations.md#parameter_callback_scripts). ``kwargs["node"]`` is
    the node holding the parm and ``kwargs["parm"]`` is the parm itself.

    The checkbox is treated as the *requested direction*: checked asks the
    runtime to come on, unchecked asks it to go off. ``adapter.toggle()`` is
    called either way (the fixed contract only offers ``toggle()``). On
    REFUSED / ERROR the checkbox is put back where it was, so the UI does not
    claim a state the runtime did not grant.
    """
    global _TOGGLE_IN_PROGRESS

    if _TOGGLE_IN_PROGRESS:
        # The revert below sets ``enable``; that change re-enters here.
        return

    _TOGGLE_IN_PROGRESS = True
    try:
        node = kwargs.get("node")
        parm = kwargs.get("parm")
        if node is None:
            node = hou.pwd()
        if node is None or parm is None:
            return

        requested_on = bool(parm.eval())

        try:
            from rt_runtime import session
        except Exception as exc:
            _refuse(
                node,
                parm,
                requested_on,
                STATUS_ERROR,
                "rt_runtime not importable ({}); expected it under {}".format(
                    exc, _EXT_ROOT
                ),
            )
            return

        try:
            adapter = session.get_adapter()
            result = adapter.toggle()
            ok = bool(result.ok)
            reason = _result_reason(result)
            detail = _result_detail(result)
        except Exception as exc:
            _refuse(node, parm, requested_on, STATUS_ERROR, repr(exc))
            return

        if not ok:
            _refuse(node, parm, requested_on, STATUS_REFUSED, reason)
            return

        token = STATUS_ON if requested_on else STATUS_OFF
        _set_status(node, "{} | {}".format(token, detail))
    finally:
        _TOGGLE_IN_PROGRESS = False


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
    """Write ``text`` into the read-only status parm.

    The parm is locked in the UI. The docs do not state whether Parm.set()
    is refused on a locked parm, so this unlocks first when it can, writes,
    then re-locks. On a locked digital asset, ``lock(False)`` raises
    hou.PermissionError; the plain write is attempted instead.
    """
    status_parm = node.parm(STATUS_PARM)
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
