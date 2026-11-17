"""Actual USD texture graph and HDRI inspection, without image/filesystem checks."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rt_runtime.scene_support import SceneSupportScan, load_profile
try:
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Tf
except ImportError:
    Usd = None


@unittest.skipUnless(Usd is not None, "Requires Houdini/OpenUSD Python")
class TextureSupportUsdTests(unittest.TestCase):
    def fixture(self, scalar=False):
        stage = Usd.Stage.CreateInMemory()
        mesh = UsdGeom.Mesh.Define(stage, "/World/mesh")
        mesh.CreateSubdivisionSchemeAttr("none")
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, "faceVarying").Set([(0, 0)])
        material = UsdShade.Material.Define(stage, "/material")
        shader = self.shader(stage, "surface", "ND_open_pbr_surface_surfaceshader", Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput("mtlx").ConnectToSource(shader.GetOutput("out"))
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        kind = Sdf.ValueTypeNames.Float if scalar else Sdf.ValueTypeNames.Color3f
        image = self.shader(stage, "image", "ND_image_float" if scalar else "ND_image_color3", kind)
        image.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("deliberately_absent_texture.exr"))
        shader.CreateInput("specular_roughness" if scalar else "base_color", kind).ConnectToSource(image.GetOutput("out"))
        return stage, mesh, material, shader, image

    @staticmethod
    def shader(stage, name, identifier, kind):
        shader = UsdShade.Shader.Define(stage, "/material/" + name)
        shader.CreateIdAttr(identifier)
        shader.CreateOutput("out", kind)
        return shader

    def scan(self, stage):
        profile = load_profile({"HRT_PLUGIN_ROOT": str(Path(__file__).parent / "fixtures/material_support")})
        viewer = NS(name=lambda: "texture_fixture", stage=lambda: stage,
                    isViewingSceneGraph=lambda: True, sceneGraphStageLocked=lambda: False)
        # Loading the tiny installed profile is separate from stage/asset inspection.
        with patch.object(Path, "stat", side_effect=AssertionError("UI attempted filesystem stat")), \
             patch.object(Path, "open", side_effect=AssertionError("UI attempted file open")):
            scan = SceneSupportScan(viewer, profile, hou_module=NS(frame=lambda: 1),
                                   usd_modules=(Usd, UsdGeom, Tf))
            self.addCleanup(scan.close)
            while scan.report.state == "running":
                scan.step(max_items=2)
        self.assertEqual(scan.report.state, "complete", scan.report.text())
        return scan.report

    def unsupported(self, stage):
        return [f for f in self.scan(stage).findings if f.kind == "Unsupported"]

    def test_declared_image_graph_is_assessed_without_falsely_validating_missing_asset(self):
        for scalar in (False, True):
            with self.subTest(scalar=scalar):
                stage, _, _, _, image = self.fixture(scalar)
                image.GetInput("file").GetAttr().SetColorSpace("Raw" if scalar else "srgb_texture")
                report = self.scan(stage)
                self.assertFalse([f for f in report.findings if f.kind == "Unsupported"], report.text())
                self.assertTrue(any("/material/image.inputs:file" in f.reason and "not checked" in f.reason
                                    for f in report.findings), report.text())
                self.assertNotIn("file is missing", report.text())
                self.assertEqual(report.supported_material_bindings, 0)  # Texels have not been assessed.

    def test_constant_coordinates_do_not_require_st(self):
        stage, mesh, _, _, image = self.fixture()
        mesh.GetPrim().RemoveProperty("primvars:st")
        image.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(.2, .8))
        self.assertFalse(self.unsupported(stage))

    def test_promoted_image_controls_are_unassessed_without_false_fallback(self):
        stage, _, material, _, image = self.fixture()
        interface = material.CreateInput("texture_path", Sdf.ValueTypeNames.Asset)
        interface.Set(Sdf.AssetPath("promoted.exr"))
        image.GetInput("file").ConnectToSource(interface)
        report = self.scan(stage)
        self.assertFalse([f for f in report.findings if f.kind == "Unsupported"], report.text())
        self.assertTrue(any("Interface input file" in f.reason and f.kind == "Not assessed" for f in report.findings))

    def test_ior_constant_border_requires_target_domain_but_unused_default_does_not(self):
        stage, _, _, surface, image = self.fixture(scalar=True)
        surface.GetInput("specular_roughness").DisconnectSource()
        surface.CreateInput("specular_ior", Sdf.ValueTypeNames.Float).ConnectToSource(image.GetOutput("out"))
        self.assertFalse(self.unsupported(stage))
        image.CreateInput("uaddressmode", Sdf.ValueTypeNames.String).Set("constant")
        self.assertTrue(any("border" in f.reason for f in self.unsupported(stage)))
        image.CreateInput("default", Sdf.ValueTypeNames.Float).Set(1.5)
        self.assertFalse(self.unsupported(stage))
        image.GetInput("default").Set(4.0)
        self.assertTrue(self.unsupported(stage))
        image.GetInput("uaddressmode").Set("periodic")
        self.assertFalse(self.unsupported(stage))

    def test_asset_inspection_avoids_usd_get_implicit_resolution(self):
        stage, _, _, _, image = self.fixture()
        original_attribute_get = Usd.Attribute.Get
        original_input_get = UsdShade.Input.Get
        def attribute_get(attr, *args):
            if attr.GetTypeName() == Sdf.ValueTypeNames.Asset:
                raise AssertionError("Asset Get would invoke resolution")
            return original_attribute_get(attr, *args)
        def input_get(item, *args):
            if item.GetTypeName() == Sdf.ValueTypeNames.Asset:
                raise AssertionError("Input Get would invoke asset resolution")
            return original_input_get(item, *args)
        with patch.object(Usd.Attribute, "Get", attribute_get), patch.object(UsdShade.Input, "Get", input_get):
            self.assertFalse(self.unsupported(stage))
        file = image.GetInput("file")
        file.Set(Sdf.AssetPath("frame_a.exr"), Usd.TimeCode(1))
        file.Set(Sdf.AssetPath("frame_b.exr"), Usd.TimeCode(2))
        report = self.scan(stage)
        self.assertTrue(any("Time-sampled asset" in f.reason and f.kind == "Not assessed" for f in report.findings))
        self.assertFalse([f for f in report.findings if f.kind == "Unsupported"])

    def test_missing_st_is_reported_only_for_uv_dependent_images(self):
        stage, mesh, _, _, image = self.fixture()
        mesh.GetPrim().RemoveProperty("primvars:st")
        self.assertTrue(any(f.feature == "Texture coordinates" for f in self.unsupported(stage)))

    def test_cubic_sequence_layers_and_wrong_image_signature_are_rejected(self):
        for name, kind, value in [("filtertype", "String", "cubic"), ("layer", "String", "beauty"),
                                  ("frameoffset", "Int", 2), ("file", "Asset", Sdf.AssetPath("map.<UDIM>.exr"))]:
            with self.subTest(name=name):
                stage, _, _, _, image = self.fixture()
                image.CreateInput(name, getattr(Sdf.ValueTypeNames, kind)).Set(value)
                self.assertTrue(self.unsupported(stage))
        stage, _, _, _, image = self.fixture()
        image.CreateIdAttr("ND_image_float")
        self.assertTrue(self.unsupported(stage))

    def test_uv_graph_depth_cycles_wrong_ports_and_unknown_controls_are_bounded(self):
        stage, _, _, _, image = self.fixture()
        parent = image.CreateInput("texcoord", Sdf.ValueTypeNames.Float2)
        for index in range(9):
            transform = self.shader(stage, "transform" + str(index), "ND_UsdTransform2d", Sdf.ValueTypeNames.Float2)
            parent.ConnectToSource(transform.GetOutput("out"))
            parent = transform.CreateInput("in", Sdf.ValueTypeNames.Float2)
        report = self.scan(stage)
        self.assertTrue(any("depth limit" in f.reason for f in report.findings), report.text())
        stage, _, _, _, image = self.fixture()
        transform = self.shader(stage, "cycle", "ND_UsdTransform2d", Sdf.ValueTypeNames.Float2)
        transform.CreateInput("in", Sdf.ValueTypeNames.Float2).ConnectToSource(transform.GetOutput("out"))
        image.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(transform.GetOutput("out"))
        self.assertTrue(any("cycle" in f.reason for f in self.unsupported(stage)))
        stage, _, _, surface, image = self.fixture()
        surface.GetInput("base_color").ConnectToSource(image.CreateOutput("other", Sdf.ValueTypeNames.Color3f))
        self.assertTrue(any("out output" in f.reason for f in self.unsupported(stage)))

    def test_geomprop_index_and_nonfile_color_metadata(self):
        for identifier, name, kind, value in [
                ("ND_texcoord_vector2", "index", "Int", 1),
                ("ND_geompropvalue_vector2", "geomprop", "String", "uv")]:
            with self.subTest(identifier=identifier):
                stage, _, _, _, image = self.fixture()
                uv = self.shader(stage, "uv", identifier, Sdf.ValueTypeNames.Float2)
                uv.CreateInput(name, getattr(Sdf.ValueTypeNames, kind)).Set(value)
                image.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(uv.GetOutput("out"))
                self.assertTrue(self.unsupported(stage))
        stage, _, _, _, image = self.fixture()
        image.CreateInput("default", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.1))
        image.GetInput("default").GetAttr().SetColorSpace("Raw")
        self.assertTrue(self.unsupported(stage))

    def test_native_dome_schema_reports_projection_and_asset_truthfully(self):
        stage = Usd.Stage.CreateInMemory()
        dome = stage.DefinePrim("/sky", "DomeLight_1")
        dome.CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("missing.hdr"))
        dome.CreateAttribute("inputs:texture:format", Sdf.ValueTypeNames.Token).Set("automatic")
        dome.CreateAttribute("poleAxis", Sdf.ValueTypeNames.Token).Set("Z")
        report = self.scan(stage)
        self.assertFalse([f for f in report.findings if f.kind == "Unsupported"], report.text())
        self.assertTrue(any(f.feature == "HDRI asset" and f.kind == "Not assessed" for f in report.findings))
        dome.GetAttribute("inputs:texture:format").Set("mirroredBall")
        self.assertTrue(any(f.feature == "HDRI projection" for f in self.unsupported(stage)))

    def test_textured_dome_count_limit_is_reported(self):
        stage = Usd.Stage.CreateInMemory()
        for index in range(9):
            dome = stage.DefinePrim("/sky" + str(index), "DomeLight")
            dome.CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("sky.exr"))
        report = self.scan(stage)
        self.assertEqual(len([f for f in report.findings if f.feature == "HDRI count"]), 1)

    def test_dome_nonuniform_parent_scale_is_reported(self):
        stage = Usd.Stage.CreateInMemory()
        parent = UsdGeom.Xform.Define(stage, "/rig")
        scale = parent.AddScaleOp()
        scale.Set(Gf.Vec3f(2, 1, 1))
        dome = stage.DefinePrim("/rig/sky", "DomeLight_1")
        dome.CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("sky.exr"))
        self.assertTrue(any(f.feature == "HDRI transform" for f in self.unsupported(stage)))
        scale.Set(Gf.Vec3f(2, 2, 2))
        self.assertFalse(self.unsupported(stage))
        scale.Set(Gf.Vec3f(1, 1.0000007, 1))
        self.assertFalse(self.unsupported(stage))  # Native length-relative tolerance.
        scale.Set(Gf.Vec3f(1e-16))
        self.assertTrue(any(f.feature == "HDRI transform" for f in self.unsupported(stage)))

    def test_native_uv_nodes_and_affine_chain(self):
        stage, _, _, _, image = self.fixture()
        uv = self.shader(stage, "uv", "ND_texcoord_vector2", Sdf.ValueTypeNames.Float2)
        place = self.shader(stage, "place", "ND_place2d_vector2", Sdf.ValueTypeNames.Float2)
        place.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(uv.GetOutput("out"))
        place.CreateInput("operationorder", Sdf.ValueTypeNames.Int).Set(1)
        place.CreateInput("scale", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(2, -3))
        transform = self.shader(stage, "transform", "ND_UsdTransform2d", Sdf.ValueTypeNames.Float2)
        transform.CreateInput("in", Sdf.ValueTypeNames.Float2).ConnectToSource(place.GetOutput("out"))
        transform.CreateInput("rotation", Sdf.ValueTypeNames.Float).Set(30)
        image.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(transform.GetOutput("out"))
        self.assertFalse(self.unsupported(stage))
        place.GetInput("scale").Set(Gf.Vec2f(0, 1))
        self.assertTrue(self.unsupported(stage))


if __name__ == "__main__":
    unittest.main()
