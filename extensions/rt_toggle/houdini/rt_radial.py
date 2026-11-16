"""RT radial menu — integrated renderer controls at the mouse.

Creates (or rebuilds) a radial menu named ``RT`` with four actions:

* **HoudiniRT** at the top, checked when the target viewer selects HoudiniRT.
* **Pause / Resume RT** on the right, checked when that renderer is paused.
* **RT Turn** at the bottom (optional assistant).
* **RT Turn Cancel** on the left.

Radial menus are supported in the **3D viewer and the network editor**, and a
menu is assigned to a key in the Radial Menu Editor. This module only builds
the menu; it does not bind a key.

Documented API used here (22.0.429 help):

* ``hou.ui.createRadialMenu(name, label)``
* ``hou.ui.radialMenu(name)`` -> menu or ``None``
* ``hou.RadialMenu.destroy()`` / ``.save(filename)``
* ``hou.RadialSubmenu.createScriptItem(location, label=None, icon=None,
  check=None, script=None)``
* ``hou.radialItemLocation`` — Top, TopLeft, Left, BottomLeft, Bottom,
  BottomRight, Right, TopRight

``check`` is a Python expression evaluated to decide the item's ticked state;
``script`` is Python executed when the item is chosen.

No ``hou`` import happens at module import time, so the spec below can be
tested on a plain CPython.

Caveats, stated rather than hidden:

* State checks resolve the same viewer as actions. Missing imports or an
  unavailable/ambiguous viewer leave the check unticked.
* Building the menu registers it for the session. Pass ``save_path`` to write
  it to a ``.radialmenu`` file, and assign a key in the Radial Menu Editor.
* None of this is exercised in the GUI yet; see ``verification/GUI_CHECKS.md``.
"""

from __future__ import annotations

import os

MENU_NAME = "RT"
MENU_LABEL = "HoudiniRT"

#: Menu item specs: location, label, icon file, whether it carries a check.
ITEMS = (
    {
        "location": "Top",
        "label": "HoudiniRT",
        "icon": "rt_toggle.svg",
        "action": "toggle_from_shelf",
        "check": True,
    },
    {
        "location": "Right",
        "label": "Pause / Resume RT",
        "icon": "rt_toggle.svg",
        "action": "pause_from_shelf",
        "check": True,
    },
    {
        "location": "Bottom",
        "label": "RT Turn",
        "icon": "rt_turn.svg",
        "action": "turn_from_shelf",
        "check": False,
    },
    {
        "location": "Left",
        "label": "RT Turn Cancel",
        "icon": "rt_turn_cancel.svg",
        "action": "cancel_turn_from_shelf",
        "check": False,
    },
)

#: Expression for the ticked state. Self-contained: the expression namespace is
#: not documented, so it imports what it needs and tolerates a status object
#: without a ``name``.
_PANE_EXPRESSION = "globals().get('kwargs', {}).get('pane')"


def module_dir() -> str:
    """The folder holding this module (and the shelf helpers)."""
    return os.path.dirname(os.path.abspath(__file__))


def icon_path(filename: str) -> str:
    """Absolute path for one icon, honouring ``RT_TOGGLE_ROOT`` when set."""
    root = os.environ.get("RT_TOGGLE_ROOT")
    if root:
        candidate = os.path.join(root, "houdini", "icons", filename)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(module_dir(), "icons", filename)


def action_script(action: str, directory: str | None = None) -> str:
    """Python source for one item: put rt_shelf on the path, then call it."""
    folder = module_dir() if directory is None else directory
    argument = (
        "viewer=" + _PANE_EXPRESSION
        if action in ("toggle_from_shelf", "pause_from_shelf") else ""
    )
    return (
        "import sys; "
        "_rt_dir = {folder!r}; "
        "(_rt_dir in sys.path) or sys.path.insert(0, _rt_dir); "
        "import rt_shelf; rt_shelf.{action}({argument})"
    ).format(folder=folder, action=action, argument=argument)


def check_expression(paused=False):
    """Build a safe state check using the same target resolver as the action."""
    folder = module_dir()
    helper = "render_is_paused" if paused else "render_is_on"
    return (
        "(None if {folder!r} in __import__('sys').path else "
        "__import__('sys').path.insert(0, {folder!r}), "
        "__import__('rt_shelf').{helper}(viewer={pane}))[1]"
    ).format(folder=folder, helper=helper, pane=_PANE_EXPRESSION)


CHECK_EXPRESSION = check_expression()


def build(save_path: str | None = None):
    """Create or rebuild the menu and return it. Requires ``hou``.

    An existing menu with the same name is destroyed first, so calling this
    twice does not stack duplicate actions.
    """
    import hou

    existing = hou.ui.radialMenu(MENU_NAME)
    if existing is not None:
        existing.destroy()

    menu = hou.ui.createRadialMenu(MENU_NAME, MENU_LABEL)
    root = menu.root()

    for spec in ITEMS:
        location = getattr(hou.radialItemLocation, spec["location"])
        root.createScriptItem(
            location,
            label=spec["label"],
            icon=icon_path(spec["icon"]),
            check=(
                check_expression(paused=spec["action"] == "pause_from_shelf")
                if spec["check"] else None
            ),
            script=action_script(spec["action"]),
        )

    if save_path:
        menu.save(save_path)
    return menu


if __name__ == "__main__":
    build()
