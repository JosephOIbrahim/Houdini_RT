"""Actual USD binding/terminal semantics, runnable headlessly with Houdini hython."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rt_runtime.scene_support import SceneSupportScan, load_profile

try:
    from pxr import Usd, UsdGeom, UsdShade, UsdLux, Sdf, Gf, Tf
except ImportError:
    Usd = None

PROFILE_ROOT = Path(__file__).parent / "fixtures" / "material_support"


@unittest.skipUnless(Usd is not None, "Requires Houdini/OpenUSD Python")
class MaterialSupportUsdTests(unittest.TestCase):
    def fixture(self):
        stage = Usd.Stage.CreateInMemory()
        mesh = UsdGeom.Mesh.Define(stage, "/World/mesh")
        mesh.CreateSubdivisionSchemeAttr("none")
        material = UsdShade.Material.Define(stage, "/materials/openpbr")
        shader = UsdShade.Shader.Define(stage, "/materials/openpbr/surface")
        shader.CreateIdAttr("ND_open_pbr_surface_surfaceshader")
        shader.CreateOutput("out", Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput("mtlx").ConnectToSource(shader.ConnectableAPI(), "out")
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        return stage, mesh, material, shader

    def scan(self, stage):
        viewer = NS(name=lambda: "material_fixture", stage=lambda: stage,
                    isViewingSceneGraph=lambda: True, sceneGraphStageLocked=lambda: False)
        profile = load_profile({"HRT_PLUGIN_ROOT": str(PROFILE_ROOT)})
        scan = SceneSupportScan(viewer, profile, hou_module=NS(frame=lambda: 1),
                                usd_modules=(Usd, UsdGeom, Tf))
        self.addCleanup(scan.close)
        while scan.report.state == "running":
            scan.step(max_items=3)
        self.assertEqual(scan.report.state, "complete", scan.report.text())
        return scan

    def test_native_default_material_is_supported_without_authored_inputs(self):
        stage, mesh, material, shader = self.fixture()
        report = self.scan(stage).report
        self.assertEqual(report.findings, [])
        self.assertEqual((report.material_bindings, report.supported_material_bindings), (1, 1))
        self.assertIn("OpenPBR", report.text())

    def test_supported_values_and_float_storage_defaults(self):
        stage, mesh, material, shader = self.fixture()
        shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.2, .4, .7))
        shader.CreateInput("base_metalness", Sdf.ValueTypeNames.Float).Set(.75)
        shader.CreateInput("specular_ior", Sdf.ValueTypeNames.Double).Set(2.1)
        shader.CreateInput("subsurface_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.8))
        shader.CreateInput("coat_ior", Sdf.ValueTypeNames.Float).Set(1.6)
        self.assertEqual(self.scan(stage).report.findings, [])

    def test_range_nonfinite_unknown_and_explicit_normal_inputs_fall_back(self):
        for name, kind, value in [("specular_ior", "Float", .9), ("base_weight", "Float", float("nan")),
                                  ("unknown", "Float", 1), ("base_weight", "Bool", True),
                                  ("geometry_normal", "Vector3f", (0, 0, 1))]:
            with self.subTest(name=name, value=value):
                stage, mesh, material, shader = self.fixture()
                shader.CreateInput(name, getattr(Sdf.ValueTypeNames, kind)).Set(value)
                report = self.scan(stage).report
                self.assertEqual(report.supported_material_bindings, 0)
                self.assertTrue(any("inputs:" + name in f.reason and "whole-mesh" in f.reason
                                    for f in report.findings), report.text())

    def test_nondefault_inactive_lobe_and_connected_constant_are_unsupported(self):
        stage, mesh, material, shader = self.fixture()
        shader.CreateInput("coat_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.5))
        interface = material.CreateInput("roughness", Sdf.ValueTypeNames.Float)
        interface.Set(.4)
        shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).ConnectToSource(interface)
        report = self.scan(stage).report
        self.assertEqual(len(report.findings), 2)
        self.assertTrue(any("coat_color" in f.reason for f in report.findings))
        self.assertTrue(any("Connected" in f.reason for f in report.findings))
        interface = next(f for f in report.findings if "specular_roughness" in f.reason)
        self.assertEqual(interface.kind, "Not assessed")
        self.assertNotIn("uses whole-mesh", interface.reason)

    def test_shader_output_connection_causes_material_fallback(self):
        stage, mesh, material, shader = self.fixture()
        constant = UsdShade.Shader.Define(stage, "/materials/openpbr/constant")
        constant.CreateIdAttr("ND_constant_color3")
        output = constant.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
        shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(output)
        report = self.scan(stage).report
        self.assertEqual(report.supported_material_bindings, 0)
        self.assertTrue(any(f.kind == "Unsupported" and "uses whole-mesh" in f.reason
                            for f in report.findings), report.text())

    def test_unbound_graphs_and_unused_universal_preview_do_not_cause_findings(self):
        stage, mesh, material, shader = self.fixture()
        unused = UsdShade.Shader.Define(stage, "/materials/unused_image")
        unused.CreateIdAttr("ND_image_color3")
        preview = UsdShade.Shader.Define(stage, "/materials/openpbr/preview")
        preview.CreateIdAttr("UsdPreviewSurface")
        material.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(), "surface")
        self.assertEqual(self.scan(stage).report.findings, [])

    def test_full_purpose_and_inherited_bindings_are_resolved(self):
        stage, mesh, material, shader = self.fixture()
        bad = UsdShade.Material.Define(stage, "/materials/unsupported")
        preview = UsdShade.Shader.Define(stage, "/materials/unsupported/surface")
        preview.CreateIdAttr("UsdPreviewSurface")
        bad.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(), "surface")
        api = UsdShade.MaterialBindingAPI(mesh.GetPrim())
        api.Bind(bad, materialPurpose="preview")
        self.assertEqual(self.scan(stage).report.findings, [])
        api.Bind(bad, materialPurpose="full")
        self.assertTrue(any("UsdPreviewSurface" in f.reason for f in self.scan(stage).report.findings))
        api.UnbindAllBindings()
        UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath("/World")).Bind(material, materialPurpose="full")
        report = self.scan(stage).report
        self.assertEqual(report.supported_material_bindings, 1)
        self.assertEqual(report.findings, [])

    def test_subset_material_forces_whole_mesh_fallback_even_when_material_matches(self):
        stage, mesh, material, shader = self.fixture()
        subset = UsdGeom.Subset.Define(stage, "/World/mesh/faces")
        subset.CreateElementTypeAttr("face")
        subset.CreateIndicesAttr([])
        self.assertEqual(self.scan(stage).report.findings, [])
        subset.CreateFamilyNameAttr("materialBind")
        inherited = self.scan(stage).report
        self.assertEqual(inherited.supported_material_bindings, 0)
        self.assertTrue(any("/World/mesh/faces" in f.reason for f in inherited.findings))
        UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material)
        report = self.scan(stage).report
        self.assertEqual(report.supported_material_bindings, 0)
        self.assertTrue(any("/World/mesh/faces" in f.reason and "whole mesh" in f.reason for f in report.findings))

    def test_default_zero_displacement_is_allowed_active_displacement_and_volume_are_not(self):
        stage, mesh, material, shader = self.fixture()
        displacement = UsdShade.Shader.Define(stage, "/materials/openpbr/displacement")
        displacement.CreateIdAttr("ND_displacement_float")
        material.CreateDisplacementOutput("mtlx").ConnectToSource(displacement.ConnectableAPI(), "out")
        self.assertEqual(self.scan(stage).report.findings, [])
        displacement.CreateInput("scale", Sdf.ValueTypeNames.Float).Set(-2)
        self.assertEqual(self.scan(stage).report.findings, [])
        displacement.CreateInput("displacement", Sdf.ValueTypeNames.Float).Set(.1)
        displacement.GetInput("scale").Set(0)
        report = self.scan(stage).report
        self.assertTrue(any("nonzero displacement" in f.reason for f in report.findings))
        displacement.GetInput("displacement").Set(0)
        material.CreateVolumeOutput("mtlx").ConnectToSource(shader.ConnectableAPI(), "out")
        self.assertTrue(any("Volume shading" in f.reason for f in self.scan(stage).report.findings))

    def test_many_subsets_are_inspected_as_individual_bounded_steps(self):
        stage, mesh, material, shader = self.fixture()
        for i in range(130):
            subset = UsdGeom.Subset.Define(stage, "/World/mesh/faces%d" % i)
            subset.CreateElementTypeAttr("face")
            subset.CreateFamilyNameAttr("materialBind")
        viewer = NS(name=lambda:"bounded", stage=lambda:stage, isViewingSceneGraph=lambda:True,
                    sceneGraphStageLocked=lambda:False)
        scan = SceneSupportScan(viewer, load_profile({"HRT_PLUGIN_ROOT":str(PROFILE_ROOT)}),
                                hou_module=NS(frame=lambda:1), usd_modules=(Usd,UsdGeom,Tf), max_prims=10)
        self.addCleanup(scan.close)
        while scan.report.material_bindings == 0:
            scan.step(max_items=1)
        self.assertEqual(scan.report.supported_material_bindings, 1)
        self.assertEqual(scan.report.findings, [])  # No eager child traversal within the mesh step.
        scan.step(max_items=1)
        self.assertEqual(scan.report.supported_material_bindings, 0)
        self.assertEqual(len(scan.report.findings), 1)
        while scan.report.state == "running":
            before = len(scan.report.findings)
            scan.step(max_items=1)
            self.assertLessEqual(len(scan.report.findings)-before, 1)
        self.assertEqual(scan.report.state, "partial")
        self.assertEqual(scan.report.inspected, 10)
        self.assertEqual(scan.report.supported_material_bindings, 0)

    def test_material_edit_invalidates_completed_report(self):
        stage, mesh, material, shader = self.fixture()
        scan = self.scan(stage)
        shader.CreateInput("base_weight", Sdf.ValueTypeNames.Float).Set(.5)
        scan.check_current()
        self.assertEqual(scan.report.state, "stale")

    def test_non_out_port_and_input_terminal_are_not_reported_as_supported(self):
        for output, port in [(True, "wrong"), (False, "out")]:
            with self.subTest(output=output, port=port):
                stage, mesh, material, shader = self.fixture()
                source = (shader.CreateOutput if output else shader.CreateInput)(port, Sdf.ValueTypeNames.Token)
                material.GetSurfaceOutput("mtlx").ConnectToSource(source)
                report = self.scan(stage).report
                self.assertEqual(report.supported_material_bindings, 0)
                self.assertTrue(report.findings, report.text())

    def test_explicit_color_spaces_fall_back_even_when_named_linear(self):
        for tag in ("sRGB", "lin_rec709", "custom"):
            with self.subTest(tag=tag):
                stage, mesh, material, shader = self.fixture()
                color = shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f)
                color.Set(Gf.Vec3f(.4))
                color.GetAttr().SetColorSpace(tag)
                report = self.scan(stage).report
                self.assertEqual(report.supported_material_bindings, 0)
                self.assertTrue(any("colorSpace" in f.reason and "inputs:base_color" in f.reason
                                    for f in report.findings), report.text())

    def test_separate_light_lobe_controls_are_reported_without_material_fallback(self):
        stage, mesh, material, shader = self.fixture()
        light = UsdLux.DistantLight.Define(stage, "/key")
        light.CreateDiffuseAttr(.5)
        light.CreateSpecularAttr(0)
        report = self.scan(stage).report
        self.assertEqual(report.supported_material_bindings, 1)
        self.assertEqual({f.feature for f in report.findings}, {"inputs:diffuse", "inputs:specular"})
        self.assertTrue(all(f.path == "/key" and f.kind == "Limitation" for f in report.findings))

    def test_non_out_displacement_port_falls_back(self):
        stage, mesh, material, shader = self.fixture()
        displacement = UsdShade.Shader.Define(stage, "/materials/openpbr/displacement")
        displacement.CreateIdAttr("ND_displacement_float")
        port = displacement.CreateOutput("wrong", Sdf.ValueTypeNames.Float)
        material.CreateDisplacementOutput("mtlx").ConnectToSource(port)
        report = self.scan(stage).report
        self.assertEqual(report.supported_material_bindings, 0)
        self.assertTrue(any("Displacement terminal" in f.reason for f in report.findings))

    def test_dangling_and_inactive_surface_terminals_fail_closed(self):
        for inactive in (False, True):
            with self.subTest(inactive=inactive):
                stage, mesh, material, shader = self.fixture()
                if inactive:
                    shader.GetPrim().SetActive(False)
                else:
                    material.GetSurfaceOutput("mtlx").GetAttr().SetConnections(
                        [Sdf.Path("/materials/missing.outputs:out")])
                report = self.scan(stage).report
                self.assertEqual(report.supported_material_bindings, 0)
                self.assertTrue(report.findings, report.text())


if __name__ == "__main__":
    unittest.main()
