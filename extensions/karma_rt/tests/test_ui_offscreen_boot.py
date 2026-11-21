"""UI & Asset Verification: Offscreen PySide6 Panel Boot + JSON/XML Asset Validation."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

import hou

from karma_rt.controller import KarmaSessionController
from karma_rt.ui.panel import KarmaRTPanelWidget, create_panel
from karma_rt.ui.radial_menu import generate_radial_menu_xml

ROOT = Path(__file__).resolve().parents[1]


class TestOffscreenUIAndAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_package_json_and_houdini_xml_assets_valid(self) -> None:
        pkg = json.loads((ROOT / "karma_rt.json").read_text(encoding="utf-8"))
        self.assertIn("22.0", pkg["enable"])
        self.assertIn("23.0", pkg["enable"])
        self.assertNotIn("22.0.429", pkg["enable"], "Package must support ALL Houdini 22.0.* builds!")

        # Validate pypanel XML & embedded Python syntax
        pypanel_root = ET.parse(ROOT / "houdini" / "python_panels" / "karma_rt.pypanel").getroot()
        scripts = [el.text for el in pypanel_root.findall(".//script") if el.text]
        self.assertGreater(len(scripts), 0)
        for s in scripts:
            ast.parse(s)

        # Validate shelf XML & embedded Python syntax
        shelf_root = ET.parse(ROOT / "houdini" / "toolbar" / "karma_rt.shelf").getroot()
        shelf_scripts = [el.text for el in shelf_root.findall(".//script") if el.text]
        self.assertGreaterEqual(len(shelf_scripts), 8)
        for s in shelf_scripts:
            ast.parse(s)

        # Validate native Houdini 22 JSON radial menu & embedded Python syntax
        radial_text = (ROOT / "houdini" / "radialmenu" / "karma_rt_radial.radialmenu").read_text(encoding="utf-8")
        radial_doc = json.loads(radial_text)
        self.assertEqual(radial_doc["name"], "karma_rt_radial")
        self.assertEqual(radial_doc["pane"], "scene")
        self.assertEqual(radial_doc["type"], "submenu")
        self.assertEqual(len(radial_doc["items"]), 8)
        for item in radial_doc["items"].values():
            self.assertEqual(item["type"], "script_action")
            ast.parse(item["script"])
        self.assertEqual(radial_text.strip(), generate_radial_menu_xml().strip())

    def test_pyside6_panel_offscreen_boot_and_controls(self) -> None:
        hou.hipFile.clear(suppress_save_prompt=True)
        ctrl = KarmaSessionController(hou_module=hou)
        panel = create_panel(controller=ctrl)
        self.assertIsInstance(panel, KarmaRTPanelWidget)
        self.assertIn("clay_headlight", panel.preset_buttons)
        self.assertTrue(hasattr(panel, "gpu_badge"))
        self.assertTrue(hasattr(panel, "focus_crop_combo"))
        self.assertTrue(hasattr(panel, "res_scale_combo"))
        self.assertTrue(hasattr(panel, "freeze_vp_btn"))
        self.assertTrue(hasattr(panel, "pin_stage_btn"))
        self.assertTrue(hasattr(panel, "solo_geo_btn"))
        panel.show()
        self.app.processEvents()
        scroll_content = panel.findChild(QtWidgets.QWidget, "panelScrollContent")
        self.assertIsNotNone(scroll_content)
        self.assertLessEqual(
            scroll_content.minimumSizeHint().width(),
            420,
            f"Panel content min width too wide ({scroll_content.minimumSizeHint().width()}px); risks horizontal clipping!",
        )

        # Exercise Doctor, Emergency Reset, and Clean Teardown safely in headless hython
        report = panel.run_preflight_doctor()
        self.app.processEvents()
        self.assertIn("22.0", report.houdini_version)

        panel._on_emergency_reset_clicked()
        self.app.processEvents()

        panel.close()
        panel.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
