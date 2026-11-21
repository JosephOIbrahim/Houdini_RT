"""M3 Verification: Read-Only H22 Scene Bottleneck Advisor, JSONL Ledger & Clone Preview."""
from __future__ import annotations

import os
import tempfile
import unittest

import hou
from pxr import Sdf, UsdGeom, UsdLux, UsdShade, UsdVol

from karma_rt.advisor import UNKNOWN, LatencyLedger, scan_stage_bottlenecks
from karma_rt.clone_preview import DEFAULT_CLONE_HOST, ClonePreviewManager, normalize_ipv4_host


class TestM3AdvisorAndLedger(unittest.TestCase):
    def setUp(self) -> None:
        hou.hipFile.clear(suppress_save_prompt=True)

    def test_bottleneck_advisor_detects_h22_issues_readonly(self) -> None:
        stage_net = hou.node("/stage")

        # 1. Scatter Instances LOP with Camera Mask OFF (sibling in /stage so unconnected scatter doesn't mark py_lop error-dirty)
        scatter = stage_net.createNode("scatterinstances", "scatter_plants")
        if scatter.parm("enablecameramask") is not None:
            scatter.parm("enablecameramask").set(0)

        # 2. Light without Equiangular MIS
        lgt = stage_net.createNode("light::2.0", "key_light")

        # 3. Synthetic fog volume + material-bound mesh without tangentu via Python LOP
        py_lop = stage_net.createNode("pythonscript", "inject_scene")
        py_lop.setInput(0, lgt)
        py_lop.parm("python").set(
            "\n".join(
                [
                    "from pxr import Sdf, UsdGeom, UsdShade, UsdVol",
                    "st = hou.pwd().editableStage()",
                    "UsdVol.Volume.Define(st, '/shot/vol/karmafogbox')",
                    "m = UsdGeom.Mesh.Define(st, '/shot/geo/hero_mesh')",
                    "m.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)])",
                    "m.CreateFaceVertexCountsAttr([3])",
                    "m.CreateFaceVertexIndicesAttr([0,1,2])",
                    "m.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.catmullClark)",
                    "mat = UsdShade.Material.Define(st, '/shot/MTL/hero_mat')",
                    "shd = UsdShade.Shader.Define(st, '/shot/MTL/hero_mat/legacy_principled')",
                    "shd.CreateIdAttr('principledshader::2.0')",
                    "UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)",
                    "st.DefinePrim('/shot/geo/splat_asset', 'ParticleField3DGaussianSplat')",
                    "st.DefinePrim('/Render/Products/ImageFilters', 'HoudiniImageFilterList')",
                    "rs = st.DefinePrim('/Render/rs_conflict', 'RenderSettings')",
                    "rs.CreateAttribute('karma:global:imagefilter', Sdf.ValueTypeNames.String).Set('[[\"denoise\", {\"engine\": \"optix\"}]]')",
                    "for i in range(505):",
                    "    st.DefinePrim(f'/shot/flat_crowd/item_{i:04d}', 'Xform')",
                ]
            )
        )
        py_lop.setDisplayFlag(True)
        _ = py_lop.stage(apply_viewport_overrides=True)
        cook_before = py_lop.cookCount()

        telemetry, advisories = scan_stage_bottlenecks(py_lop, lopnet=stage_net, hou_module=hou)
        self.assertEqual(py_lop.cookCount(), cook_before, "Advisor scan MUST be 100% read-only (0 recooks)!")
        self.assertGreaterEqual(telemetry.volume_count, 1)
        self.assertGreaterEqual(telemetry.light_count, 1)
        self.assertGreaterEqual(telemetry.mesh_count, 1)

        adv_ids = {a.id for a in advisories}
        self.assertIn("equiangular_mis", adv_ids, f"Missing Equiangular MIS advisory in {adv_ids}")
        self.assertIn("scatter_camera_frustum", adv_ids, f"Missing Scatter Frustum advisory in {adv_ids}")
        self.assertIn("vulkan_tangentu_parity", adv_ids, f"Missing tangentu advisory in {adv_ids}")
        self.assertIn("xpu_principled_shader_fallback", adv_ids, f"Missing Principled shader advisory in {adv_ids}")
        self.assertIn("flat_hierarchy_siblings", adv_ids, f"Missing flat hierarchy advisory in {adv_ids}")
        self.assertIn("gaussian_splat_shadows", adv_ids, f"Missing Gaussian Splat advisory in {adv_ids}")
        self.assertIn("double_denoiser_conflict", adv_ids, f"Missing double-denoiser advisory in {adv_ids}")

        # Verify restart flags on advisories
        for a in advisories:
            if a.id == "subdiv_displacement_dicing":
                self.assertTrue(a.triggers_restart)
            if a.id in ("equiangular_mis", "scatter_camera_frustum", "vulkan_tangentu_parity"):
                self.assertFalse(a.triggers_restart)

    def test_latency_ledger_honesty_and_summary_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = os.path.join(tmp, "karma_rt_ledger.jsonl")
            ledger = LatencyLedger(ledger_path=ledger_path)

            e1 = ledger.record(
                event="activate_preset",
                decision="ACTIVATE live_draft",
                via="LOP:Custom",
                proof="cookCount delta=0",
                apply_ms=3.2,
                scan_ms=None,  # Must serialize verbatim as UNKNOWN!
                preset_id="live_draft",
                observed_delegate="Karma XPU",
                metrics={"ttfp_ms": None},
            )
            self.assertEqual(e1.scan_ms, UNKNOWN)
            self.assertEqual(e1.metrics["ttfp_ms"], UNKNOWN)

            ledger.record(
                event="scan_stage",
                decision="SCAN",
                via="read-only",
                proof="3 advisories",
                apply_ms=4.8,
                scan_ms=6.5,
                preset_id="live_draft",
                observed_delegate="Karma XPU",
            )
            recent = ledger.read_recent(10)
            self.assertEqual(len(recent), 2)
            stats = ledger.summary_stats()
            self.assertEqual(stats["count"], 2)
            self.assertIsInstance(stats["apply_ms"]["median"], float)
            self.assertAlmostEqual(stats["scan_ms"]["median"], 6.5, places=3)

    def test_clone_preview_ipv4_pinning_and_honest_headless_status(self) -> None:
        self.assertEqual(DEFAULT_CLONE_HOST, "127.0.0.1")
        self.assertEqual(normalize_ipv4_host("localhost"), "127.0.0.1")
        self.assertEqual(normalize_ipv4_host("::1"), "127.0.0.1")
        mgr = ClonePreviewManager(hou_module=hou, host="localhost")
        self.assertEqual(mgr.host, "127.0.0.1")
        status = mgr.status()
        self.assertEqual(status.host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
