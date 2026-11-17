"""Real USD semantics: run with hython; ordinary CPython skips these cases."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rt_runtime.scene_support import CapabilityProfile, SceneSupportScan

try:
    from pxr import Usd, UsdGeom, UsdLux, Sdf, Gf, Tf
except ImportError:
    Usd = None


@unittest.skipUnless(Usd is not None, "Requires Houdini/OpenUSD Python")
class SceneSupportUsdTests(unittest.TestCase):
    def scan(self, stage):
        viewer = NS(name=lambda: "native_usd_fixture", stage=lambda: stage,
                    isViewingSceneGraph=lambda: True, sceneGraphStageLocked=lambda: False)
        profile = CapabilityProfile("test profile", "fixture-v1",
                                    frozenset({"domeLight", "rectLight", "distantLight", "diskLight", "sphereLight"}),
                                    frozenset({"color", "normal", "depth", "primId"}))
        scan = SceneSupportScan(viewer, profile, hou_module=NS(frame=lambda: 1),
                                usd_modules=(Usd, UsdGeom, Tf))
        self.addCleanup(scan.close)
        while scan.report.state == "running": scan.step(max_items=3)
        self.assertEqual(scan.report.state, "complete", scan.report.text())
        return scan

    def test_scanner_viewer_calls_exist_on_real_houdini_scene_viewer(self):
        import ast
        import inspect
        try:
            import hou
        except ImportError:
            self.skipTest("Requires actual Houdini HOM, not standalone OpenUSD")
        methods = set()
        for node in ast.walk(ast.parse(inspect.getsource(SceneSupportScan))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            direct = isinstance(receiver, ast.Name) and receiver.id == "viewer"
            stored = (isinstance(receiver, ast.Attribute) and receiver.attr == "_viewer"
                      and isinstance(receiver.value, ast.Name) and receiver.value.id == "self")
            if direct or stored:
                methods.add(node.func.attr)
        self.assertTrue(methods)
        for method in methods:
            with self.subTest(method=method, houdini=hou.applicationVersionString()):
                self.assertTrue(callable(getattr(hou.SceneViewer, method, None)),
                                "Scanner calls a method absent from the actual SceneViewer: " + method)

    def test_real_light_defaults_and_nondefault_shadow_shaping_texture(self):
        stage = Usd.Stage.CreateInMemory()
        UsdLux.RectLight.Define(stage, "/key")
        dome = UsdLux.DomeLight.Define(stage, "/sky")
        dome.CreateTextureFileAttr(Sdf.AssetPath("missing.exr"))
        UsdLux.ShapingAPI.Apply(dome.GetPrim()).CreateShapingFocusAttr(2)
        UsdLux.ShadowAPI.Apply(dome.GetPrim()).CreateShadowEnableAttr(False)
        report = self.scan(stage).report
        finite = [f for f in report.findings if f.feature == "Finite light geometry"]
        self.assertEqual([(f.path, f.kind) for f in finite], [("/key", "Limitation")])
        unsupported = [f for f in report.findings if f.kind == "Unsupported"]
        self.assertEqual({f.path for f in unsupported}, {"/sky"})
        self.assertEqual({f.feature for f in unsupported},
                         {"inputs:texture:file", "inputs:shaping:focus", "inputs:shadow:enable"})

    def test_parent_scale_sphere_point_mode_and_real_notice_invalidation(self):
        stage = Usd.Stage.CreateInMemory()
        parent = UsdGeom.Xform.Define(stage, "/rig")
        parent.AddScaleOp().Set(Gf.Vec3f(2, 1, 1))
        sphere = UsdLux.SphereLight.Define(stage, "/rig/sphere")
        sphere.CreateTreatAsPointAttr(True)
        scan = self.scan(stage)
        self.assertEqual({f.feature for f in scan.report.findings},
                         {"Finite light geometry", "Sphere transform", "treatAsPoint"})
        sphere.CreateRadiusAttr(2)
        scan.check_current()
        self.assertEqual(scan.report.state, "stale")
        self.assertIsNone(scan._notice)

    def test_mesh_interpolation_material_binding_and_aov_request(self):
        stage = Usd.Stage.CreateInMemory()
        mesh = UsdGeom.Mesh.Define(stage, "/mesh")
        mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set([Gf.Vec3f(.5)])
        mesh.GetPrim().CreateRelationship("material:binding").SetTargets([Sdf.Path("/mat")])
        var = stage.DefinePrim("/var", "RenderVar")
        var.CreateAttribute("sourceName", Sdf.ValueTypeNames.String).Set("cryptomatte")
        var.CreateAttribute("sourceType", Sdf.ValueTypeNames.Token).Set("raw")
        report = self.scan(stage).report
        self.assertEqual({f.feature for f in report.findings},
                         {"Subdivision catmullClark", "displayColor vertex", "Material binding", "RenderVar cryptomatte"})

    def test_empty_and_analytic_scenes_never_claim_image_correctness(self):
        stage = Usd.Stage.CreateInMemory()
        report = self.scan(stage).report
        self.assertIn("not a fidelity guarantee", report.text())
        UsdGeom.Sphere.Define(stage, "/analytic")
        report = self.scan(stage).report
        self.assertEqual(report.findings[0].kind, "Not assessed")


if __name__ == "__main__":
    unittest.main()
