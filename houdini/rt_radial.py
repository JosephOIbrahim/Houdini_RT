"""RT radial menu — runtime controls at the mouse.

Creates (or rebuilds) a radial menu named ``RT`` with three actions:

* **RT Toggle** at the top, with a check that reflects the sidecar state.
* **RT Turn** on the right.
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

* The check expression imports ``rt_runtime``; if the package is not on
  ``sys.path`` (for example a copy install without the package JSON), the
  expression cannot evaluate.
* Building the menu registers it for the session. Pass ``save_path`` to write
  it to a ``.radialmenu`` file, and assign a key in the Radial Menu Editor.
* None of this is exercised in the GUI yet; see ``verification/GUI_CHECKS.md``.
"""

from __future__ import annotations

import os

MENU_NAME = "RT"
MENU_LABEL = "RT Runtime"

#: Menu item specs: location, label, icon file, whether it carries a check.
ITEMS = (
    {
        "location": "Top",
        "label": "RT Toggle",
        "icon": "rt_toggle.svg",
        "action": "toggle_from_shelf",
        "check": True,
    },
    {
        "location": "Right",
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
CHECK_EXPRESSION = (
    "getattr(__import__('rt_runtime').session.get_adapter().status(), "
    "'name', '') == 'ON'"
)


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
    return (
        "import sys; "
        "_rt_dir = {folder!r}; "
        "(_rt_dir in sys.path) or sys.path.insert(0, _rt_dir); "
        "import rt_shelf; rt_shelf.{action}()"
    ).format(folder=folder, action=action)


def runtime_is_on() -> bool:
    """Convenience for a GUI check; never raises."""
    try:
        from rt_runtime import session

        state = session.get_adapter().status()
    except Exception:
        return False
    return str(getattr(state, "name", "")).upper() == "ON"


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
            check=CHECK_EXPRESSION if spec["check"] else None,
            script=action_script(spec["action"]),
        )

    if save_path:
        menu.save(save_path)
    return menu


if __name__ == "__main__":
    build()
