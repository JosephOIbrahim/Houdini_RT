"""Run only as a Python LOP cook script in the fresh GUI validation scene.

The driver creates spare controls: tx(float), hidden(toggle), blue(toggle),
insert(toggle), aspect(float). Stable paths and unchanged local geometry make
transform, visibility and color deltas individually observable.
"""
import hou
from pxr import Gf, UsdGeom, UsdLux, UsdRender, Sdf

node = hou.pwd()
tx = node.evalParm('tx')
hidden = bool(node.evalParm('hidden'))
blue = bool(node.evalParm('blue'))
insert = bool(node.evalParm('insert'))
aspect = node.evalParm('aspect')
stage = node.editableStage()


def quad(path, center, color, visible=True, half=0.55):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([(-half, -half, 0), (half, -half, 0),
                          (half, half, 0), (-half, half, 0)])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    mesh.GetDisplayColorPrimvar().SetInterpolation(UsdGeom.Tokens.constant)
    mesh.CreateVisibilityAttr(UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible)
    mesh.AddTranslateOp().Set(Gf.Vec3d(*center))


quad('/World/red', (-1.25 + tx, 0, 0), (0.65, 0.04, 0.04), not hidden)
quad('/World/green', (1.25, 0, 0), (0.04, 0.04, 0.65) if blue else (0.04, 0.65, 0.04))
if insert:
    quad('/World/inserted', (0, 1.0, 0), (0.65, 0.04, 0.65), half=0.3)

dome = UsdLux.DomeLight.Define(stage, '/World/dome')
dome.CreateIntensityAttr(1.0)
dome.CreateColorAttr(Gf.Vec3f(1, 1, 1))

camera = UsdGeom.Camera.Define(stage, '/Camera')
camera.CreateProjectionAttr(UsdGeom.Tokens.orthographic)
camera.CreateHorizontalApertureAttr(60.0)
camera.CreateVerticalApertureAttr(60.0 / aspect)
camera.AddTranslateOp().Set(Gf.Vec3d(0, 0, 5))
settings = UsdRender.Settings.Define(stage, '/Render/settings')
settings.CreateCameraRel().SetTargets(['/Camera'])
settings.GetPrim().CreateAttribute('houdinirt:seed', Sdf.ValueTypeNames.Int).Set(101)
settings.GetPrim().CreateAttribute('houdinirt:spp', Sdf.ValueTypeNames.Int).Set(1024)
settings.GetPrim().CreateAttribute('houdinirt:bounces', Sdf.ValueTypeNames.Int).Set(4)
# GUI sampling also comes from the explicit child-only HOUDINIRT_S3_CONFIG.
# Python LOP's edit target is not the stage root; do not set root metadata here.
