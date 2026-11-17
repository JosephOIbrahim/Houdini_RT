"""Run with installed H22 hython. Author independent semantic USD light fixtures."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdRender, UsdShade
import numpy as np

HERE = Path(__file__).resolve().parent
SELECTED = ["distant_delta", "distant_oblique", "distant_cone_12", "distant_normalized_12", "rect", "rect_normalized", "rect_scaled_normalized", "disk", "disk_normalized", "disk_scaled_normalized", "sphere", "sphere_normalized", "sphere_scaled_normalized", "mixed", "distant_tilted_normal"]


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def material(stage, path, color):
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/surface")
    shader.CreateIdAttr("ND_open_pbr_surface_surfaceshader")
    shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    for name, value in (("base_weight", 1.), ("base_diffuse_roughness", 0.), ("specular_weight", 0.), ("coat_weight", 0.), ("fuzz_weight", 0.)):
        shader.CreateInput(name, Sdf.ValueTypeNames.Float).Set(value)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(shader.CreateOutput("out", Sdf.ValueTypeNames.Token))
    return mat


def make(path, case, seed):
    stage = Usd.Stage.CreateNew(str(path)); UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y); UsdGeom.SetStageMetersPerUnit(stage, 1.)
    mesh = UsdGeom.Mesh.Define(stage, "/World/Receiver")
    mesh.CreatePointsAttr([(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)])
    mesh.CreateFaceVertexCountsAttr([4]); mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant).Set([Gf.Vec3f(*case["albedo"])])
    if case["normal"] != [0, 0, 1]:
        mesh.CreateNormalsAttr([Gf.Vec3f(*case["normal"])]); mesh.SetNormalsInterpolation(UsdGeom.Tokens.constant)
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material(stage, "/World/Materials/Receiver", case["albedo"]))
    camera = UsdGeom.Camera.Define(stage, "/World/Camera")
    camera.CreateProjectionAttr(UsdGeom.Tokens.orthographic)
    camera.CreateHorizontalApertureAttr(20 * case["extent"])
    camera.CreateVerticalApertureAttr(20 * case["extent"] * case["height"] / case["width"])
    camera.CreateClippingRangeAttr(Gf.Vec2f(.1, 10))
    camera.AddTranslateOp().Set(Gf.Vec3d(0, 0, 1))
    authored = []
    for i, light in enumerate(case["lights"]):
        kind = light["type"]; path_light = "/World/Light" + str(i)
        typ = {"rect": UsdLux.RectLight, "disk": UsdLux.DiskLight, "sphere": UsdLux.SphereLight, "distant": UsdLux.DistantLight}[kind]
        emitter = typ.Define(stage, path_light)
        api = UsdLux.LightAPI(emitter.GetPrim())
        api.CreateIntensityAttr(light["intensity"]); api.CreateExposureAttr(light["exposure"])
        api.CreateColorAttr(Gf.Vec3f(*light["color"])); api.CreateNormalizeAttr(light["normalize"])
        if case["name"] == "mixed" and kind != "distant":
            # Karma defaults both switches off. The physical oracle and HRT use
            # opaque finite-emitter geometry; explicitly match that convention.
            emitter.GetPrim().CreateAttribute("inputs:karma:light:renderlightgeo", Sdf.ValueTypeNames.Bool).Set(True)
            emitter.GetPrim().CreateAttribute("inputs:karma:light:lightgeocastsshadow", Sdf.ValueTypeNames.Bool).Set(True)
        if kind in ("rect", "disk"):
            # Author real scale so USD/HDK normalization cannot accidentally use local area.
            u, v = np.asarray(light["u"]), np.asarray(light["v"])
            lu, lv = float(np.linalg.norm(u)), float(np.linalg.norm(v))
            if kind == "rect":
                emitter.CreateWidthAttr(1.4); emitter.CreateHeightAttr(.9)
                xx, yy = u / .7, v / .45
            else:
                emitter.CreateRadiusAttr(.7); xx, yy = u / .7, v / .7
            zz = -np.asarray(light["normal"])
            rows = [list(xx) + [0.], list(yy) + [0.], list(zz) + [0.], light["position"] + [1.]]
            emitter.AddTransformOp().Set(Gf.Matrix4d(*[float(x) for row in rows for x in row]))
        elif kind == "sphere":
            emitter.CreateRadiusAttr(.55)
            emitter.AddTranslateOp().Set(Gf.Vec3d(*light["position"]))
            scale = light["radius"] / .55
            emitter.AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))
        else:
            emitter.CreateAngleAttr(light["angle"])
            direction = Gf.Vec3d(*light["normal"])
            rotation = Gf.Rotation(Gf.Vec3d(0, 0, -1), direction)
            transform = Gf.Matrix4d().SetRotate(rotation)
            transform.SetTranslateOnly(Gf.Vec3d(*light["position"]))
            emitter.AddTransformOp().Set(transform)
        authored.append({"path": path_light, "type": kind, "karma_physical_geometry_explicit": case["name"] == "mixed" and kind != "distant"})
    settings = UsdRender.Settings.Define(stage, "/Render/settings")
    settings.CreateCameraRel().SetTargets([camera.GetPath()]); settings.CreateResolutionAttr(Gf.Vec2i(case["width"], case["height"]))
    for name, typ, value in (
        ("houdinirt:seed", Sdf.ValueTypeNames.Int, seed), ("houdinirt:spp", Sdf.ValueTypeNames.Int, 4096), ("houdinirt:bounces", Sdf.ValueTypeNames.Int, 0),
        ("karma:global:randomseed", Sdf.ValueTypeNames.Int, seed),
        ("karma:global:convergence_mode", Sdf.ValueTypeNames.Token, "Path Traced"), ("karma:global:pathtracedsamples", Sdf.ValueTypeNames.Int, 4096),
        ("karma:global:pixelfilter", Sdf.ValueTypeNames.String, "box"), ("karma:global:pixelfiltersize", Sdf.ValueTypeNames.Float, 2.),
        ("karma:global:imagefilter", Sdf.ValueTypeNames.String, "[]"), ("karma:object:diffuselimit", Sdf.ValueTypeNames.Float, 0.),
        ("karma:global:colorlimit", Sdf.ValueTypeNames.Float, 1e6), ("karma:global:colorlimitindirect", Sdf.ValueTypeNames.Float, 1e6),
    ): settings.GetPrim().CreateAttribute(name, typ).Set(value)
    stage.SetMetadata("renderSettingsPrimPath", "/Render/settings")
    stage.GetRootLayer().Save()
    return authored


def main():
    out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=False)
    frozen = HERE / "frozen"
    contract = json.loads((frozen / "contract.json").read_text())
    cases = {case["name"]: case for case in contract["cases"]}
    manifest = {"schema": 1, "scope": "Supplementary USD/HDK light translation and independent Karma comparison", "created_utc": datetime.now(timezone.utc).isoformat(), "generator_sha256": sha(Path(__file__)), "oracle_contract_sha256": sha(frozen / "contract.json"), "spp": 4096, "cases": [],
                "tolerances": {"each_renderer_analytic_channel_mean_relative": .015, "each_renderer_analytic_rgb_relative_rmse": .06, "candidate_karma_channel_mean_relative": .02, "alpha_max_abs": 1e-6},
                "statistics": {"seed_count": 3, "SE_multiplier": 5, "base_relative_floor": .001, "quadrature_safety_multiplier": 3, "candidate_karma_relative_floor": .003},
                "oracle_convergence": json.loads((frozen / "oracle_convergence.json").read_text()),
                "pixel_orientation": "EXR top-down; oracle bottom-up; always flip oracle vertically, never select best orientation"}
    for name in SELECTED:
        for seed in cases[name]["seeds"]:
            path = out / (name + "_" + str(seed) + ".usda"); lights = make(path, cases[name], seed)
            manifest["cases"].append({"name": name, "seed": seed, "stage": str(path), "stage_sha256": sha(path), "lights": lights, "oracle": str(frozen / (name + ".npy")), "oracle_sha256": sha(frozen / (name + ".npy"))})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "generated", "cases": len(manifest["cases"]), "manifest": str(out / "manifest.json")}))


if __name__ == "__main__": main()
