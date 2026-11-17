"""CPU-only H22 USD AOV fixtures; expected geometric values live in judge.py."""
import hashlib
import json
from pathlib import Path
import sys
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdRender

VARS = [('color','color4f','color4f',Sdf.ValueTypeNames.Float4,Gf.Vec4f(0)),
        ('normal','normal3f','float3',Sdf.ValueTypeNames.Normal3f,Gf.Vec3f(-1)),
        ('Neye','normal3f','float3',Sdf.ValueTypeNames.Normal3f,Gf.Vec3f(-1)),
        ('depth','float','float',Sdf.ValueTypeNames.Float,1.),
        ('cameraDepth','float','float',Sdf.ValueTypeNames.Float,0.),
        ('primId','int','int',Sdf.ValueTypeNames.Int,-1),
        ('elementId','int','int',Sdf.ValueTypeNames.Int,-1),
        ('instanceId','int','int',Sdf.ValueTypeNames.Int,-1),
        ('albedo','color3f','color3f',Sdf.ValueTypeNames.Color3f,Gf.Vec3f(0))]


def attr(prim, name, kind, value):
    prim.CreateAttribute(name,kind,custom=True).Set(value)


def make(out, name, rotated=False, invalid=None, indexed=False):
    path=out/(name+'.usda'); stage=Usd.Stage.CreateNew(str(path))
    stage.SetMetadata('renderSettingsPrimPath','/Render/settings')
    UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.y); UsdGeom.SetStageMetersPerUnit(stage,1.)
    mesh=UsdGeom.Mesh.Define(stage,'/World/Plane')
    mesh.CreatePointsAttr([(-1,-.75,0),(-.25,-.75,0),(-.25,.75,0),(-1,.75,0),
                          (.25,-.75,0),(1,-.75,0),(1,.75,0),(.25,.75,0)])
    mesh.CreateFaceVertexCountsAttr([4,4]); mesh.CreateFaceVertexIndicesAttr(list(range(8)))
    mesh.CreateSubdivisionSchemeAttr('none')
    mesh.CreateNormalsAttr([(.6,0,.8),(-.6,0,.8)]); mesh.SetNormalsInterpolation('uniform')
    if indexed:
        mesh.CreateNormalsAttr([(0,1,0),(0,1,0)])
        authored=UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('normals',Sdf.ValueTypeNames.Normal3fArray,'faceVarying')
        authored.Set([(.6,0,.8),(-.6,0,.8)]); authored.SetIndices([0,0,0,0,1,1,1,1])
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('displayColor',Sdf.ValueTypeNames.Color3fArray,'constant').Set([(.2,.4,.6)])
    rotation=Gf.Matrix4d(0,0,-1,0, 0,1,0,0, 1,0,0,0, 0,0,0,1)
    if rotated: UsdGeom.Xformable(mesh).AddTransformOp().Set(rotation)
    camera=UsdGeom.Camera.Define(stage,'/World/Camera')
    camera.CreateProjectionAttr('orthographic'); camera.CreateHorizontalApertureAttr(30.)
    camera.CreateVerticalApertureAttr(30.); camera.CreateClippingRangeAttr((.1,10.))
    view=Gf.Matrix4d(rotation if rotated else Gf.Matrix4d(1))
    view.SetTranslateOnly(Gf.Vec3d(3,0,0) if rotated else Gf.Vec3d(0,0,3))
    UsdGeom.Xformable(camera).AddTransformOp().Set(view)
    # An explicit zero-energy source suppresses husk's automatic camera headlight.
    UsdLux.DomeLight.Define(stage,'/World/UnlitDome').CreateIntensityAttr(0.)
    settings=UsdRender.Settings.Define(stage,'/Render/settings')
    settings.CreateResolutionAttr(Gf.Vec2i(48,48)); settings.CreateCameraRel().SetTargets([camera.GetPath()])
    attr(settings.GetPrim(),'houdinirt:spp',Sdf.ValueTypeNames.Int,16)
    attr(settings.GetPrim(),'houdinirt:bounces',Sdf.ValueTypeNames.Int,0)
    attr(settings.GetPrim(),'houdinirt:seed',Sdf.ValueTypeNames.Int,193)
    product=UsdRender.Product.Define(stage,'/Render/product')
    product.CreateProductTypeAttr('raster'); product.CreateProductNameAttr(str(out/(name+'.exr')))
    attr(product.GetPrim(),'driver:parameters:husk:OpenEXR:multipart',Sdf.ValueTypeNames.Bool,False)
    attr(product.GetPrim(),'driver:parameters:husk:OpenEXR:compression',Sdf.ValueTypeNames.String,'zip')
    selected=VARS if invalid is None else [VARS[0]]
    if invalid=='name': selected=selected+[('unsupported_aov','float','float',Sdf.ValueTypeNames.Float,17.)]
    if invalid=='format': selected=selected+[('normal','normal3f','int',Sdf.ValueTypeNames.Int,17)]
    paths=[]
    for source,dtype,fmt,clear_type,clear in selected:
        variable=UsdRender.Var.Define(stage,'/Render/Vars/'+source); prim=variable.GetPrim()
        variable.CreateSourceNameAttr(source); variable.CreateSourceTypeAttr('raw'); variable.CreateDataTypeAttr(dtype)
        attr(prim,'driver:parameters:aov:husk:name',Sdf.ValueTypeNames.String,source)
        attr(prim,'driver:parameters:aov:husk:channel_prefix',Sdf.ValueTypeNames.String,'beauty' if source=='color' else source)
        attr(prim,'driver:parameters:aov:husk:format',Sdf.ValueTypeNames.Token,fmt)
        attr(prim,'driver:parameters:aov:husk:multiSampled',Sdf.ValueTypeNames.Bool,False)
        attr(prim,'driver:parameters:aov:husk:clearValue',clear_type,clear)
        attr(prim,'driver:parameters:aov:husk:source_colorspace',Sdf.ValueTypeNames.String,'raw')
        attr(prim,'driver:parameters:aov:husk:output_colorspace',Sdf.ValueTypeNames.String,'raw')
        paths.append(variable.GetPath())
    product.CreateOrderedVarsRel().SetTargets(paths); settings.CreateProductsRel().SetTargets([product.GetPath()])
    stage.GetRootLayer().Save()
    return {'name':name,'rotated':rotated,'invalid':invalid,'indexed':indexed,'stage':str(path),
            'stage_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'image':str(out/(name+'.exr'))}


if __name__=='__main__':
    out=Path(sys.argv[1]).resolve(); out.mkdir(parents=True,exist_ok=False)
    cases=[make(out,'identity'),make(out,'rotated',True),make(out,'indexed_precedence',indexed=True),
           make(out,'unsupported_name',invalid='name'),make(out,'unsupported_format',invalid='format')]
    (out/'manifest.json').write_text(json.dumps({'scope':'Actual husk raw RenderVar delivery, typed EXR and strict rejection','cases':cases},indent=2)+'\n')
    print(json.dumps({'cases':len(cases),'output':str(out)}))
