"""Static tests for the RT radial menu module.

No Houdini: ``rt_radial`` imports ``hou`` only inside ``build()``, so the spec
and the generated scripts can be inspected on plain CPython.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

EXT_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = EXT_ROOT / "houdini" / "rt_radial.py"


def _load():
    if not MODULE_PATH.is_file():
        import pytest

        pytest.skip("houdini/rt_radial.py is absent")
    spec = importlib.util.spec_from_file_location("rt_radial_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["rt_radial_under_test"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_menu_has_three_items_at_distinct_locations():
    menu = _load()

    assert menu.MENU_NAME == "RT"
    assert len(menu.ITEMS) == 3

    locations = [item["location"] for item in menu.ITEMS]
    assert len(set(locations)) == 3, locations
    assert set(locations) == {"Top", "Right", "Left"}

    labels = {item["label"] for item in menu.ITEMS}
    assert labels == {"RT Toggle", "RT Turn", "RT Turn Cancel"}


def test_each_item_has_a_shipped_icon():
    menu = _load()

    for item in menu.ITEMS:
        icon = EXT_ROOT / "houdini" / "icons" / item["icon"]
        assert icon.is_file(), f"missing icon {icon}"
        assert menu.icon_path(item["icon"]).endswith(item["icon"])


def test_only_toggle_carries_a_check():
    menu = _load()

    checked = [item["label"] for item in menu.ITEMS if item["check"]]
    assert checked == ["RT Toggle"]

    # The check must be an expression, not a statement: compile it in eval mode.
    compile(menu.CHECK_EXPRESSION, "<check>", "eval")
    assert "rt_runtime" in menu.CHECK_EXPRESSION
    assert "'ON'" in menu.CHECK_EXPRESSION


def test_action_scripts_compile_and_call_the_shelf_helpers():
    menu = _load()

    for item in menu.ITEMS:
        source = menu.action_script(item["action"])
        compile(source, "<radial action>", "exec")
        assert "import rt_shelf" in source
        assert f"rt_shelf.{item['action']}()" in source
        assert "sys.path.insert" in source


def test_build_uses_the_documented_radial_api():
    source = MODULE_PATH.read_text(encoding="utf-8")

    for marker in (
        "hou.ui.createRadialMenu",
        "hou.ui.radialMenu",
        "createScriptItem",
        "radialItemLocation",
        "menu.root()",
    ):
        assert marker in source, f"missing {marker}"


def test_module_does_not_import_hou_at_module_level():
    source = MODULE_PATH.read_text(encoding="utf-8")

    top_level = [
        line
        for line in source.splitlines()
        if line.startswith(("import hou", "from hou"))
    ]
    assert not top_level, top_level
