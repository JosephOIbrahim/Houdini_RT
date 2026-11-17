"""H22 CPU-only: native-material ingress and independently rendered Karma fixtures."""
from pathlib import Path
import hashlib
import json
import sys
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdRender,UsdShade

HERE=Path(__file__).resolve().parent
NATIVE=HERE/'native_openpbr.usda'
NATIVE_SHA='3bdf54d721ad1790208b68ab12169b54626daa15a9b987168c316bc6b2fde94d'
SEEDS=(101,107,113)
FALLBACK=(.05,.6,.05)
MODELS={
    'lambert_dome':dict(base_color=(.15,.45,.8),specular_weight=0),
    'dielectric_dome':dict(base_color=(.8,.8,.8)),
    'metal_dome':dict(base_color=(.8,.3,.07),base_metalness=1),
    'weighted_dome':dict(base_color=(.15,.45,.8),base_weight=.35,specular_weight=0),
    'default_rect':dict(),
    'smooth_metal_rect':dict(base_color=(.8,.3,.07),base_metalness=1,specular_roughness=0),
    'rough_metal_rect':dict(base_color=(.8,.3,.07),base_metalness=1,specular_roughness=.5),
    'mixed_rect':dict(base_color=(.2,.6,.8),base_metalness=.45,specular_roughness=.3),
    'ior_rect':dict(specular_ior=2.5),
    'tint_rect':dict(base_color=(.3,.2,.1),specular_color=(.2,.8,.5)),
    'default_distant':dict(),
    'mixed_lights':dict(base_color=(.2,.6,.8),base_metalness=.45,specular_roughness=.2)}
DEFAULTS=dict(base_color=(.8,.8,.8),base_weight=1.,base_metalness=0.,specular_color=(1,1,1),
              specular_weight=1.,specular_roughness=.3,specular_ior=1.5,base_diffuse_roughness=0.)


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def attribute(prim,name,kind,value):prim.CreateAttribute(name,kind,custom=True).Set(value)


def make(path,name,seed,negative=None,aov=False):
    stage=Usd.Stage.CreateNew(str(path));UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.y);UsdGeom.SetStageMetersPerUnit(stage,1.)
    native=Sdf.Layer.FindOrOpen(str(NATIVE));Sdf.CopySpec(native,'/materials',stage.GetRootLayer(),'/materials')
    model=dict(DEFAULTS);model.update(MODELS.get(name,{}))
    shader=UsdShade.Shader(stage.GetPrimAtPath('/materials/openpbr/surface'))
    for key,value in model.items():
        color=isinstance(value,tuple)
        shader.CreateInput(key,Sdf.ValueTypeNames.Color3f if color else Sdf.ValueTypeNames.Float).Set(Gf.Vec3f(*value) if color else value)
    mesh=UsdGeom.Mesh.Define(stage,'/World/Receiver')
    mesh.CreatePointsAttr([(-3,-3,0),(3,-3,0),(3,3,0),(-3,3,0)])
    mesh.CreateFaceVertexCountsAttr([4]);mesh.CreateFaceVertexIndicesAttr([0,1,2,3]);mesh.CreateSubdivisionSchemeAttr('none')
    mesh.CreateDisplayColorPrimvar('constant').Set([Gf.Vec3f(*FALLBACK)])
    binding=UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
    binding.Bind(UsdShade.Material(stage.GetPrimAtPath('/materials/openpbr')))
    if negative=='unbound':binding.UnbindAllBindings()
    elif negative=='missing':stage.RemovePrim('/materials/openpbr')
    elif negative=='emission':shader.CreateInput('emission_luminance',Sdf.ValueTypeNames.Float).Set(2.)
    elif negative=='colorspace':shader.GetInput('base_color').GetAttr().SetColorSpace('srgb_texture')
    elif negative=='texture':
        texture=UsdShade.Shader.Define(stage,'/materials/openpbr/unsupported_texture')
        texture.CreateIdAttr('ND_image_color3');texture.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('not_loaded_by_bounded_profile.exr'))
        shader.GetInput('base_color').ConnectToSource(texture.CreateOutput('out',Sdf.ValueTypeNames.Color3f))
    dome=UsdLux.DomeLight.Define(stage,'/Lights/Dome');dome.CreateIntensityAttr(1. if name.endswith('dome') or negative or aov else .1 if name=='mixed_lights' else 0.)
    if name.endswith('rect') or name=='mixed_lights':
        light=UsdLux.RectLight.Define(stage,'/Lights/Rect');light.CreateWidthAttr(.8);light.CreateHeightAttr(.7)
        light.CreateIntensityAttr(4.);light.CreateNormalizeAttr(False);light.AddTranslateOp().Set(Gf.Vec3d(-.35,0,2))
        attribute(light.GetPrim(),'inputs:karma:light:renderlightgeo',Sdf.ValueTypeNames.Bool,True)
        attribute(light.GetPrim(),'inputs:karma:light:lightgeocastsshadow',Sdf.ValueTypeNames.Bool,True)
    if name.endswith('distant') or name=='mixed_lights':
        light=UsdLux.DistantLight.Define(stage,'/Lights/Distant');light.CreateIntensityAttr(2.);light.CreateAngleAttr(0.)
        light.AddRotateYOp().Set(35.)
    camera=UsdGeom.Camera.Define(stage,'/Camera');camera.CreateProjectionAttr('orthographic')
    camera.CreateHorizontalApertureAttr(24.);camera.CreateVerticalApertureAttr(18.);camera.CreateClippingRangeAttr((.1,10.))
    camera.AddTranslateOp().Set(Gf.Vec3d(0,0,1))
    settings=UsdRender.Settings.Define(stage,'/Render/settings');settings.CreateCameraRel().SetTargets([camera.GetPath()])
    settings.CreateResolutionAttr(Gf.Vec2i(128,96));stage.SetMetadata('renderSettingsPrimPath','/Render/settings')
    for key,kind,value in (
        ('houdinirt:spp',Sdf.ValueTypeNames.Int,4096),('houdinirt:bounces',Sdf.ValueTypeNames.Int,0),('houdinirt:seed',Sdf.ValueTypeNames.Int,seed),
        ('karma:global:randomseed',Sdf.ValueTypeNames.Int,seed),('karma:global:convergence_mode',Sdf.ValueTypeNames.Token,'Path Traced'),
        ('karma:global:pathtracedsamples',Sdf.ValueTypeNames.Int,4096),('karma:global:pixelfilter',Sdf.ValueTypeNames.String,'box'),
        ('karma:global:pixelfiltersize',Sdf.ValueTypeNames.Float,2.),('karma:global:imagefilter',Sdf.ValueTypeNames.String,'[]'),
        ('karma:object:diffuselimit',Sdf.ValueTypeNames.Float,0.),('karma:object:reflectlimit',Sdf.ValueTypeNames.Float,1.),
        ('karma:global:colorlimit',Sdf.ValueTypeNames.Float,1e6),('karma:global:colorlimitindirect',Sdf.ValueTypeNames.Float,1e6)):
        attribute(settings.GetPrim(),key,kind,value)
    outputs(stage,settings,aov)
    stage.GetRootLayer().Save()
    return dict(name=name,seed=seed,negative=negative,aov=aov,stage=str(path),stage_sha256=sha(path),material=model,
                surface_path='/materials/openpbr/surface',binding='/materials/openpbr',fallback=list(FALLBACK))


def outputs(stage,settings,albedo=False):
    product=UsdRender.Product.Define(stage,'/Render/product');product.CreateProductTypeAttr('raster')
    product.CreateProductNameAttr('beauty.exr');paths=[]
    attribute(product.GetPrim(),'driver:parameters:husk:OpenEXR:multipart',Sdf.ValueTypeNames.Bool,False)
    attribute(product.GetPrim(),'driver:parameters:husk:OpenEXR:compression',Sdf.ValueTypeNames.String,'zip')
    for name,dtype,format_name,clear_type,clear in (
        ('color','color4f','color4f',Sdf.ValueTypeNames.Float4,Gf.Vec4f(0)),
        ('albedo','color3f','color3f',Sdf.ValueTypeNames.Color3f,Gf.Vec3f(0))):
        if name=='albedo' and not albedo:continue
        variable=UsdRender.Var.Define(stage,'/Render/Vars/'+name);prim=variable.GetPrim()
        variable.CreateSourceNameAttr(name);variable.CreateSourceTypeAttr('raw');variable.CreateDataTypeAttr(dtype)
        for key,kind,value in (
            ('name',Sdf.ValueTypeNames.String,name),('channel_prefix',Sdf.ValueTypeNames.String,'beauty' if name=='color' else name),
            ('format',Sdf.ValueTypeNames.Token,format_name),('multiSampled',Sdf.ValueTypeNames.Bool,False),('clearValue',clear_type,clear),
            ('source_colorspace',Sdf.ValueTypeNames.String,'raw'),('output_colorspace',Sdf.ValueTypeNames.String,'raw')):
            attribute(prim,'driver:parameters:aov:husk:'+key,kind,value)
        paths.append(variable.GetPath())
    product.CreateOrderedVarsRel().SetTargets(paths);settings.CreateProductsRel().SetTargets([product.GetPath()])
    # Raw color is Hydra's output; Karma's native beauty is a named LPE. Keep
    # equivalent scene/transport in one immutable stage and select only the
    # renderer-specific output binding with husk --settings.
    layer=stage.GetRootLayer();native=Sdf.Layer.FindOrOpen(str(HERE/'native_karma_vars.usda'))
    assert Sdf.CopySpec(native,'/Render/Products/Vars/beauty',layer,'/Render/Vars/karma_beauty')
    prim=stage.GetPrimAtPath('/Render/Vars/karma_beauty')
    prim.GetAttribute('driver:parameters:aov:husk:format').Set('color4f')
    for key in ('source_colorspace','output_colorspace'):attribute(prim,'driver:parameters:aov:husk:'+key,Sdf.ValueTypeNames.String,'raw')
    assert Sdf.CopySpec(layer,'/Render/product',layer,'/Render/karma_product')
    stage.GetPrimAtPath('/Render/karma_product').GetRelationship('orderedVars').SetTargets(['/Render/Vars/karma_beauty'])
    assert Sdf.CopySpec(layer,'/Render/settings',layer,'/Render/karma_settings')
    stage.GetPrimAtPath('/Render/karma_settings').GetRelationship('products').SetTargets(['/Render/karma_product'])


def main():
    assert sha(NATIVE)==NATIVE_SHA,'Native Material Library fixture changed'
    out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False);cases=[]
    for name in MODELS:
        for seed in SEEDS:cases.append(make(out/(name+'_'+str(seed)+'.usda'),name,seed))
    for negative in ('texture','emission','colorspace','unbound','missing'):
        name='fallback_'+negative;cases.append(make(out/(name+'.usda'),name,193,negative=negative))
    cases.append(make(out/'bound_albedo.usda','weighted_dome',197,aov=True))
    manifest=dict(scope='Native Material Library ingress, exact-USD shared-model CPU transport, independent Lambert/Karma checks, explicit fallback; glossy Karma appearance diagnostic only',
                  generator_sha256=sha(Path(__file__)),native_fixture_sha256=NATIVE_SHA,native_karma_vars_sha256=sha(HERE/'native_karma_vars.usda'),cases=cases,
                  limits=dict(mean_relative_max=.03,rgb_relative_rmse_max=.12,mean_standard_error_multiplier=5,
                              mean_relative_floor=.01,absolute_floor=5e-4,alpha_max_abs=1e-6,
                              analytic_lambert_max_abs=5e-5,fallback_max_abs=5e-5,albedo_max_abs=2e-6,transport_quadrature_max_abs=2e-5),
                  comparison_scope='Twelve identical USD material stages per seed, float32 raw linear EXRs. CPU reference shares the admitted Adobe BSDF model but independently integrates actual USD transport. All non-Lambert Karma results are diagnostic because OpenPBR1.1.1 explicitly permits implementation choices. No candidate-derived reference, spatial alignment, or exposure fitting.',
                  finite_geometry='Explicit Karma physical emitter visibility and shadow flags match documented HoudiniRT behavior.',
                  output_binding='Equivalent scene/transport in one immutable USD; separate RenderSettings products select canonical native Karma LPE beauty or Hydra rawcolor, bothfloat32.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(dict(cases=len(cases),path=str(out/'manifest.json'))))


if __name__=='__main__':main()
