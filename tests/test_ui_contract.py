"""Contract tests for the user-facing surfaces: Python panel, shelf, node recipe.

These tests are import-light and never open a GUI. They check two things:

1. Static shape. Every surface file exists where the contract says it will and
   its embedded script drives the adapter through ``get_adapter`` rather than
   reimplementing state handling.
2. Optional dynamic import. Python node/recipe modules are imported under
   ``tests/fake_hou.py`` when all their dependencies are present. The test
   skips when ``rt_runtime.adapter`` or a Qt binding is missing.

Each UI test skips with a clear reason while its surface file does not exist
yet, so the suite is useful before the panel/shelf/node agents land.

Expected shapes
---------------
* ``*.pypanel``: Houdini Python Panel XML with a ``pythonPanelDocument`` root,
  an ``interface`` element carrying ``name`` and ``label``, and a ``script``
  element whose body references ``get_adapter`` and ``toggle``/``activate``/
  ``deactivate``.
* ``*.shelf``: Houdini shelf XML with a ``shelfDocument`` root and at least one
  ``tool`` whose name or label mentions RT/toggle and whose ``scriptCode``
  references ``get_adapter``.
* Node recipe: a ``.py`` file outside ``rt_runtime/`` that references
  ``get_adapter``, defines at least one top-level function, and mentions a
  callback hook (``callback`` / ``onParm`` / similar).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import fake_hou

EXT_ROOT = Path(__file__).resolve().parents[1]
SKIP_DIR_NAMES = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".mypy_cache"}


# ---------------------------------------------------------------------------
# discovery helpers
# ---------------------------------------------------------------------------
def _walk(pattern: str) -> list[Path]:
    found = []
    for path in EXT_ROOT.rglob(pattern):
        if SKIP_DIR_NAMES.intersection(path.parts):
            continue
        if path.is_file():
            found.append(path)
    return sorted(found)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


PYPANELS = _walk("*.pypanel")
SHELVES = _walk("*.shelf")

TOGGLE_DRIVE = (".toggle(", ".activate(", ".deactivate(", "toggle()")


def _reaches_adapter(code: str, surface_path: Path) -> bool:
    """True when the embedded script, or a sibling helper it calls, drives the adapter."""
    if "get_adapter" in code:
        return True
    search_dirs = [surface_path.parent, EXT_ROOT / "houdini", EXT_ROOT]
    module_names = set(re.findall(r"([A-Za-z_]\w*)\s*\.\s*[A-Za-z_]\w*\s*\(", code))
    module_names.update(
        re.findall(r"(?:^|\n)\s*(?:from|import)\s+([A-Za-z_]\w*)", code)
    )
    for module_name in module_names:
        for directory in search_dirs:
            helper = directory / f"{module_name}.py"
            if helper.exists() and helper != surface_path and "get_adapter" in _read(helper):
                return True
    return False


def _node_candidates() -> list[Path]:
    """Python files outside rt_runtime/ that look like a node/recipe surface."""
    candidates: list[Path] = []
    for path in _walk("*.py"):
        relative = path.relative_to(EXT_ROOT).parts
        if {"tests", "verification", "rt_runtime"}.intersection(relative):
            continue
        text = _read(path)
        if "get_adapter" not in text:
            continue
        looks_like_node = bool(re.search(r"node|recipe", path.stem, re.IGNORECASE)) or (
            "ParmTemplate" in text or "parmTemplate" in text
        )
        if looks_like_node:
            candidates.append(path)
    return candidates


def _skip_without(files: list[Path], surface: str) -> None:
    if not files:
        pytest.skip(f"no {surface} surface file yet (agent still writing it)")


# ---------------------------------------------------------------------------
# fake hou self-test (always runs)
# ---------------------------------------------------------------------------
def test_fake_hou_supports_ui_contract():
    template = fake_hou.ToggleParmTemplate(
        "rt_active", "RT Active", default_value=False, help="Toggle the runtime"
    )
    assert template.name() == "rt_active"
    assert template.label() == "RT Active"
    assert template.defaultValue() is False

    group = fake_hou.ParmTemplateGroup([template])
    assert group.templates() == [template]
    assert group.find("rt_active") is template

    node = fake_hou.Node("rt_toggle_node")
    parm = node.addParm(template)
    calls: list[dict] = []
    parm.setCallback(lambda **kwargs: calls.append(kwargs))
    node.fireParmCallback("rt_active", value=True)

    assert calls, "fake parm callback did not fire"
    assert calls[0]["parm"] is parm
    assert calls[0]["node"] is node
    assert calls[0]["value"] is True

    assert fake_hou.ui.displayMessage("hello") == 0
    assert fake_hou.ui.messages == ["hello"]
    assert fake_hou.pypanel.installFile("rt_toggle.pypanel") is True
    assert fake_hou.pypanel.installed == ["rt_toggle.pypanel"]

    fake_hou.ui.clear()
    fake_hou.pypanel.clear()


# ---------------------------------------------------------------------------
# Python panel
# ---------------------------------------------------------------------------
def test_panel_file_is_installable_xml():
    _skip_without(PYPANELS, ".pypanel")
    problems: list[str] = []
    for path in PYPANELS:
        try:
            root = ET.fromstring(_read(path))
        except ET.ParseError as exc:
            problems.append(f"{path.name}: not XML ({exc})")
            continue
        if _localname(root.tag).lower() != "pythonpaneldocument":
            problems.append(
                f"{path.name}: root is <{_localname(root.tag)}>, expected <pythonPanelDocument>"
            )
            continue
        interfaces = [e for e in root.iter() if _localname(e.tag) == "interface"]
        if not interfaces:
            problems.append(f"{path.name}: no <interface> element")
            continue
        interface = interfaces[0]
        if not interface.get("name") or not interface.get("label"):
            problems.append(f"{path.name}: <interface> needs non-empty name and label")
        scripts = [e for e in root.iter() if _localname(e.tag) == "script"]
        if not scripts or not (scripts[0].text or "").strip():
            problems.append(f"{path.name}: <script> is missing or empty")
    assert not problems, "Python panel contract violations:\n  " + "\n  ".join(problems)


def test_panel_script_uses_adapter():
    _skip_without(PYPANELS, ".pypanel")
    problems: list[str] = []
    for path in PYPANELS:
        text = _read(path)
        if "get_adapter" not in text:
            problems.append(f"{path.name}: script never calls get_adapter")
        if not any(marker in text for marker in TOGGLE_DRIVE):
            problems.append(
                f"{path.name}: script never drives the adapter "
                "(expected .toggle(), .activate() or .deactivate())"
            )
    assert not problems, "Python panel contract violations:\n  " + "\n  ".join(problems)


def test_panel_script_wires_the_turn_control():
    """The panel must submit without blocking and must offer a cancel."""
    _skip_without(PYPANELS, ".pypanel")
    problems: list[str] = []
    for path in PYPANELS:
        text = _read(path)
        if "turn_control" not in text:
            problems.append(f"{path.name}: script never calls adapter.turn_control()")
        if "submit_turn(" not in text:
            problems.append(f"{path.name}: script never uses submit_turn()")
        if "poller.refresh(" not in text:
            problems.append(f"{path.name}: script never polls a submitted turn")
        if ".cancel(" not in text:
            problems.append(f"{path.name}: script has no cancel path")
    assert not problems, "Python panel turn-control contract violations:\n  " + "\n  ".join(problems)


def test_panel_readable_copy_matches_the_pypanel_script():
    """rt_panel.py and the embedded CDATA must carry the same script."""
    _skip_without(PYPANELS, ".pypanel")
    readable = EXT_ROOT / "houdini" / "rt_panel.py"
    if not readable.is_file():
        pytest.skip("houdini/rt_panel.py is absent")
    script = readable.read_text(encoding="utf-8")
    problems: list[str] = []
    for path in PYPANELS:
        if path.name != "rt_panel.pypanel":
            continue
        root = ET.fromstring(_read(path))
        scripts = [e for e in root.iter() if _localname(e.tag) == "script"]
        embedded = (scripts[0].text or "") if scripts else ""
        if embedded.strip() != script.strip():
            problems.append(f"{path.name}: embedded script differs from rt_panel.py")
    assert not problems, "Panel copies are out of sync:\n  " + "\n  ".join(problems)


def test_shelf_exposes_a_turn_tool_and_a_cancel_tool():
    """The shelf must offer the same submit/cancel pair as the panel."""
    _skip_without(SHELVES, ".shelf")
    problems: list[str] = []
    for path in SHELVES:
        text = _read(path)
        if "turn_from_shelf" not in text:
            problems.append(f"{path.name}: no tool calls turn_from_shelf")
        if "cancel_turn_from_shelf" not in text:
            problems.append(f"{path.name}: no tool calls cancel_turn_from_shelf")
    assert not problems, "Shelf turn-tool contract violations:\n  " + "\n  ".join(problems)


def _node_recipes() -> list[Path]:
    found = []
    for path in EXT_ROOT.rglob("*.py"):
        if SKIP_DIR_NAMES.intersection(path.parts):
            continue
        if path.name == "rt_node.py" and path.is_file():
            found.append(path)
    return sorted(found)


def test_node_recipe_exposes_turn_parms():
    recipes = _node_recipes()
    _skip_without(recipes, "node recipe")
    problems: list[str] = []
    for path in recipes:
        text = _read(path)
        for marker in ("rt_run_turn", "rt_cancel_turn", "turn_callback"):
            if marker not in text:
                problems.append(f"{path.name}: missing {marker}")
    assert not problems, "Node turn-parm contract violations:\n  " + "\n  ".join(problems)


def test_surface_icons_exist_and_are_referenced():
    """The three tool icons must ship and every surface must point at them."""
    icons = EXT_ROOT / "houdini" / "icons"
    expected = ("rt_toggle.svg", "rt_turn.svg", "rt_turn_cancel.svg")
    problems: list[str] = []
    for name in expected:
        path = icons / name
        if not path.is_file():
            problems.append(f"missing icon: houdini/icons/{name}")
            continue
        try:
            ET.fromstring(path.read_text(encoding="utf-8"))
        except ET.ParseError as exc:
            problems.append(f"houdini/icons/{name}: not XML ({exc})")

    shelf = EXT_ROOT / "houdini" / "rt_shelf.shelf"
    if shelf.is_file():
        text = _read(shelf)
        for name in expected:
            if f"icons/{name}" not in text:
                problems.append(f"rt_shelf.shelf does not reference icons/{name}")

    panel = EXT_ROOT / "houdini" / "rt_panel.pypanel"
    if panel.is_file() and "icons/rt_toggle.svg" not in _read(panel):
        problems.append("rt_panel.pypanel does not reference icons/rt_toggle.svg")

    assert not problems, "Icon contracts:\n  " + "\n  ".join(problems)


# ---------------------------------------------------------------------------
# shelf
# ---------------------------------------------------------------------------
def test_shelf_tool_uses_adapter():
    _skip_without(SHELVES, ".shelf")
    problems: list[str] = []
    for path in SHELVES:
        try:
            root = ET.fromstring(_read(path))
        except ET.ParseError as exc:
            problems.append(f"{path.name}: not XML ({exc})")
            continue
        if _localname(root.tag).lower() != "shelfdocument":
            problems.append(
                f"{path.name}: root is <{_localname(root.tag)}>, expected <shelfDocument>"
            )
            continue
        tools = [e for e in root.iter() if _localname(e.tag) == "tool"]
        if not tools:
            problems.append(f"{path.name}: no <tool> element")
            continue
        matching = [
            tool
            for tool in tools
            if re.search(
                r"rt|toggle",
                f"{tool.get('name', '')} {tool.get('label', '')}",
                re.IGNORECASE,
            )
        ]
        if not matching:
            problems.append(
                f"{path.name}: no tool whose name/label mentions RT or toggle"
            )
            continue
        for tool in matching:
            scripts = [
                e
                for e in tool.iter()
                if _localname(e.tag).lower() in {"scriptcode", "script"}
            ]
            code = "\n".join((e.text or "") for e in scripts)
            if not scripts or not code.strip():
                problems.append(f"{path.name}: tool {tool.get('name')!r} has no script code")
            elif not _reaches_adapter(code, path):
                problems.append(
                    f"{path.name}: tool {tool.get('name')!r} script neither calls "
                    "get_adapter nor delegates to a sibling module that does"
                )
    assert not problems, "Shelf contract violations:\n  " + "\n  ".join(problems)


# ---------------------------------------------------------------------------
# node recipe
# ---------------------------------------------------------------------------
def test_node_recipe_exposes_entry_point():
    candidates = _node_candidates()
    _skip_without(candidates, "node/recipe")
    problems: list[str] = []
    for path in candidates:
        text = _read(path)
        if not re.search(r"(?m)^\s*def\s+\w+\s*\(", text):
            problems.append(f"{path.name}: no top-level function entry point")
        if not any(marker in text for marker in TOGGLE_DRIVE):
            problems.append(
                f"{path.name}: never drives the adapter "
                "(expected .toggle(), .activate() or .deactivate())"
            )
    assert not problems, "Node recipe contract violations:\n  " + "\n  ".join(problems)


def test_node_recipe_registers_callback():
    candidates = _node_candidates()
    _skip_without(candidates, "node/recipe")
    combined = "\n".join(_read(path) for path in candidates)
    assert re.search(r"callback|onparm|parmchange|parm_changed", combined, re.IGNORECASE), (
        "node recipe never mentions a parm callback hook, so the "
        "'node callback fires' claim cannot hold"
    )


# ---------------------------------------------------------------------------
# optional dynamic import under fake hou
# ---------------------------------------------------------------------------
def test_python_surfaces_import_with_fake_hou():
    candidates = _node_candidates()
    _skip_without(candidates, "node/recipe")

    try:
        import rt_runtime.adapter  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("rt_runtime.adapter is not implemented yet")

    loaded: list[str] = []
    fake_hou.install()
    try:
        for index, path in enumerate(candidates):
            spec = importlib.util.spec_from_file_location(
                f"_rt_surface_{index}", str(path)
            )
            if spec is None or spec.loader is None:
                pytest.fail(f"cannot create an import spec for {path}")
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except ModuleNotFoundError as exc:
                missing = exc.name or ""
                if (
                    missing.startswith("hou.")
                    or missing.startswith("PySide")
                    or missing.startswith("PyQt")
                    or missing == "hutil"
                    or missing.startswith("hutil.")
                ):
                    pytest.skip(
                        f"{path.name} needs a richer Houdini/Qt environment: {missing}"
                    )
                raise
            public_callables = [
                name
                for name, value in vars(module).items()
                if not name.startswith("_") and callable(value)
            ]
            assert public_callables, f"{path.name}: no public callable entry point"
            loaded.append(path.name)
    finally:
        fake_hou.uninstall()

    assert loaded, "no node/recipe module could be imported"
