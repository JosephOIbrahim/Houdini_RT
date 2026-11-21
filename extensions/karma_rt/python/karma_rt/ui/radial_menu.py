"""Houdini 22 native JSON Solaris viewport radial menu generator for KarmaRT."""
from __future__ import annotations

import json
from typing import Any

# Compass indices match Houdini's native scene-pane radial menu slot order
# clockwise from North: 0=N, 1=NE, 2=E, 3=SE, 4=S, 5=SW, 6=W, 7=NW.
RADIAL_ITEMS: tuple[dict[str, str], ...] = (
    {
        "index": "0",
        "location": "N",
        "slot": "top",
        "name": "karma_rt_toggle",
        "label": "Toggle Karma RT",
        "icon": "ROP_karma",
        "script": "from karma_rt.ui.shelf_actions import toggle_karma_rt\ntoggle_karma_rt()",
    },
    {
        "index": "1",
        "location": "NE",
        "slot": "top_right",
        "name": "karma_rt_autoturbo",
        "label": "Auto-Turbo",
        "icon": "BUTTONS_play",
        "script": "from karma_rt.ui.shelf_actions import toggle_auto_turbo\ntoggle_auto_turbo()",
    },
    {
        "index": "2",
        "location": "E",
        "slot": "right",
        "name": "karma_rt_draft",
        "label": "Live Draft",
        "icon": "LOP_karmarendersettings",
        "script": "from karma_rt.ui.shelf_actions import apply_live_draft\napply_live_draft()",
    },
    {
        "index": "3",
        "location": "SE",
        "slot": "bottom_right",
        "name": "karma_rt_snapshot",
        "label": "Quick Snapshot",
        "icon": "BUTTONS_capture",
        "script": "from karma_rt.ui.shelf_actions import quick_snapshot\nquick_snapshot()",
    },
    {
        "index": "4",
        "location": "S",
        "slot": "bottom",
        "name": "karma_rt_lookdev",
        "label": "Interactive Lookdev",
        "icon": "ROP_karma",
        "script": "from karma_rt.ui.shelf_actions import apply_interactive_lookdev\napply_interactive_lookdev()",
    },
    {
        "index": "5",
        "location": "SW",
        "slot": "bottom_left",
        "name": "karma_rt_restore",
        "label": "Restore Production",
        "icon": "BUTTONS_reload",
        "script": "from karma_rt.ui.shelf_actions import restore_production\nrestore_production()",
    },
    {
        "index": "6",
        "location": "W",
        "slot": "left",
        "name": "karma_rt_final",
        "label": "Final Parity",
        "icon": "LOP_renderpass",
        "script": "from karma_rt.ui.shelf_actions import apply_final_parity\napply_final_parity()",
    },
    {
        "index": "7",
        "location": "NW",
        "slot": "top_left",
        "name": "karma_rt_overlays",
        "label": "Clean Overlays",
        "icon": "VIEW_display_guides",
        "script": "from karma_rt.ui.shelf_actions import toggle_clean_overlays\ntoggle_clean_overlays()",
    },
)


def build_radial_menu_dict() -> dict[str, Any]:
    """Return Houdini 22's native JSON structure for ``karma_rt_radial.radialmenu``."""
    items: dict[str, dict[str, str]] = {}
    for entry in RADIAL_ITEMS:
        items[entry["index"]] = {
            "label": entry["label"],
            "type": "script_action",
            "icon": entry["icon"],
            "script": entry["script"],
        }
    return {
        "fileversion": "22.0.504",
        "name": "karma_rt_radial",
        "categories": "Solaris",
        "pane": "scene",
        "label": "Karma RT",
        "type": "submenu",
        "items": items,
    }


def generate_radial_menu_json() -> str:
    """Serialize the native Houdini 22 ``.radialmenu`` JSON document."""
    return json.dumps(build_radial_menu_dict(), indent=2) + "\n"


# Backward-compatible alias retained for callers importing the earlier name.
generate_radial_menu_xml = generate_radial_menu_json
