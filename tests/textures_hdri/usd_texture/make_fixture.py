"""Independent CPU fixtures/expectations for actual USD texture delivery."""
import argparse,hashlib,json,math,shutil,sys
from pathlib import Path
import numpy as np
import OpenImageIO as oiio
import PyOpenColorIO as ocio
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdRender,UsdShade
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import oracle
FALLBACK=(.05,.6,.1)
POINTS=[(-2,-2,0),(0,-2,0),(2,-2,0),(-2,2,0),(0,2,0),(2,2,0)]
INDICES=[0,1,4,3,1,2,5,4]
UV=[(0,0),(.5,0),(1,0),(0,1),(.5,1),(1,1)]
SLOTS={'base_color':(.75,.25,.5),'base_weight':.5,'base_metalness':.5,
       'specular_color':(.25,.5,.75),'specular_weight':.5,'specular_roughness':.5,'specular_ior':2.}
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def attribute(prim,name,kind,value):prim.CreateAttribute(name,kind,custom=True).Set(value)
def output_image(path,rows,format=oiio.FLOAT):
    values=np.asarray(rows,dtype=np.float32);spec=oiio.ImageSpec(values.shape[1],values.shape[0],values.shape[2],format)
    out=oiio.ImageOutput.create(str(path));assert out and out.open(str(path),spec)
    assert out.write_image(values) and out.close()
    inp=oiio.ImageInput.open(str(path));assert inp
    pixels=np.asarray(inp.read_image('float')).tolist();inp.close();return pixels
def outputs(stage,settings,name,out):
    product=UsdRender.Product.Define(stage,'/Render/product');product.CreateProductTypeAttr('raster')
    product.CreateProductNameAttr(str(out/(name+'.exr')))
    attribute(product.GetPrim(),'driver:parameters:husk:OpenEXR:multipart',Sdf.ValueTypeNames.Bool,False)
    attribute(product.GetPrim(),'driver:parameters:husk:OpenEXR:compression',Sdf.ValueTypeNames.String,'zip')
    variables=[]
    for source,dtype,kind,clear in [('color','color4f',Sdf.ValueTypeNames.Float4,Gf.Vec4f(0)),
                                    ('albedo','color3f',Sdf.ValueTypeNames.Color3f,Gf.Vec3f(0))]:
        var=UsdRender.Var.Define(stage,'/Render/Vars/'+source);var.CreateSourceNameAttr(source)
        var.CreateSourceTypeAttr('raw');var.CreateDataTypeAttr(dtype);prim=var.GetPrim()
        for key,value_type,value in [('name',Sdf.ValueTypeNames.String,source),
            ('channel_prefix',Sdf.ValueTypeNames.String,'beauty' if source=='color' else source),
            ('format',Sdf.ValueTypeNames.Token,dtype),('multiSampled',Sdf.ValueTypeNames.Bool,False),
            ('clearValue',kind,clear),('source_colorspace',Sdf.ValueTypeNames.String,'raw'),
            ('output_colorspace',Sdf.ValueTypeNames.String,'raw')]:
            attribute(prim,'driver:parameters:aov:husk:'+key,value_type,value)
        variables.append(var.GetPath())
    product.CreateOrderedVarsRel().SetTargets(variables);settings.CreateProductsRel().SetTargets([product.GetPath()])
def coordinates(mesh,domain):
    if domain=='missing_uv':return
    interpolation={'indexed':'faceVarying','seam_float3':'faceVarying','bad_indices':'faceVarying'}.get(domain,domain)
    three=domain=='seam_float3'
    values=[(.35,.65)] if domain=='constant' else [(.2,.3),(.8,.7)] if domain=='uniform' else UV
    if interpolation=='faceVarying':values=[UV[i] for i in INDICES]
    if three:values=[(0,0),(1,0),(1,1),(0,1)]*2
    pv=UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord3fArray if three else Sdf.ValueTypeNames.TexCoord2fArray,interpolation)
    if domain in ('indexed','bad_indices'):
        values=UV;pv.SetIndices(INDICES if domain=='indexed' else INDICES[:-1]+[999])
    pv.Set([Gf.Vec3f(u,v,2) for u,v in values] if three else [Gf.Vec2f(u,v) for u,v in values])
def uv_at(case,x,y):
    domain=case.get('domain','faceVarying')
    uv=(.35,.65) if domain=='constant' else ((.2,.3) if x<0 else (.8,.7)) if domain=='uniform' else ((x+2)/4,(y+2)/4)
    if domain=='seam_float3':uv=((x+2)/2 if x<0 else x/2,(y+2)/4)
    graph=case.get('graph','implicit_uv')
    u,v=uv
    if graph=='usd_transform2d':
        angle=math.pi/6;u,v=2*u,3*v;uv=(math.cos(angle)*u-math.sin(angle)*v+.25,math.sin(angle)*u+math.cos(angle)*v-.1)
    elif graph=='place2d':
        angle=math.pi/6;u,v=u/2,v/3;uv=(math.cos(angle)*u+math.sin(angle)*v-.25,-math.sin(angle)*u+math.cos(angle)*v+.1)
    if case.get('wrap'):uv=(uv[0]*3-.75,uv[1]*3-.75)
    return uv
def make(case,out,native,contract):
    name=case['name'];path=out/(name+'.usda');stage=Usd.Stage.CreateNew(str(path))
    stage.SetMetadata('renderSettingsPrimPath','/Render/settings');UsdGeom.SetStageUpAxis(stage,'Y')
    graph=case.get('graph','implicit_uv');layer=Sdf.Layer.FindOrOpen(str(native/(graph+'.usda')))
    assert layer and Sdf.CopySpec(layer,'/materials',stage.GetRootLayer(),'/materials')
    shader=UsdShade.Shader(stage.GetPrimAtPath('/materials/openpbr/surface'))
    model={'base_color':(.25,.5,.75),'base_weight':1.,'base_metalness':0.,'specular_color':(1,1,1),
           'specular_weight':1.,'specular_roughness':.3,'specular_ior':1.5}
    if case.get('slot') and case.get('control'):model[case['slot']]=SLOTS[case['slot']]
    for key,value in model.items():
        color=isinstance(value,tuple);item=shader.CreateInput(key,Sdf.ValueTypeNames.Color3f if color else Sdf.ValueTypeNames.Float)
        item.DisconnectSource();item.Set(Gf.Vec3f(*value) if color else value)
    mesh=UsdGeom.Mesh.Define(stage,'/World/mesh');mesh.CreatePointsAttr(POINTS)
    mesh.CreateFaceVertexCountsAttr([4,4]);mesh.CreateFaceVertexIndicesAttr(INDICES);mesh.CreateSubdivisionSchemeAttr('none')
    mesh.CreateDisplayColorPrimvar('constant').Set([Gf.Vec3f(*FALLBACK)])
    coordinates(mesh,case.get('domain','faceVarying'))
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(UsdShade.Material(stage.GetPrimAtPath('/materials/openpbr')))
    if not case.get('control'):
        slot=case.get('slot','base_color');scalar=not isinstance(model[slot],tuple)
        image=UsdShade.Shader(stage.GetPrimAtPath('/materials/openpbr/base_image'))
        image.CreateIdAttr('ND_image_float' if scalar else 'ND_image_color3')
        image.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(case['asset']))
        image.GetInput('file').GetAttr().SetColorSpace(case.get('space','Raw'))
        image.CreateInput('filtertype',Sdf.ValueTypeNames.String).Set('linear')
        image.CreateInput('uaddressmode',Sdf.ValueTypeNames.String).Set(case.get('wrap','periodic'))
        image.CreateInput('vaddressmode',Sdf.ValueTypeNames.String).Set(case.get('wrap','periodic'))
        shader.GetInput(slot).ConnectToSource(image.CreateOutput('out',Sdf.ValueTypeNames.Float if scalar else Sdf.ValueTypeNames.Color3f))
        if graph in ('usd_transform2d','place2d') or case.get('wrap'):
            source=UsdShade.Shader.Define(stage,'/materials/openpbr/uv_source');source.CreateIdAttr('ND_texcoord_vector2')
            if case.get('wrap'):
                affine=UsdShade.Shader.Define(stage,'/materials/openpbr/wrap_transform');affine.CreateIdAttr('ND_UsdTransform2d')
                affine.CreateInput('scale',Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(3))
                affine.CreateInput('translation',Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(-.75))
                image.CreateInput('texcoord',Sdf.ValueTypeNames.Float2).ConnectToSource(affine.CreateOutput('out',Sdf.ValueTypeNames.Float2))
            else:affine=UsdShade.Shader(stage.GetPrimAtPath('/materials/openpbr/'+('uv_transform' if graph=='usd_transform2d' else 'uv_place')))
            affine.CreateInput('texcoord' if graph=='place2d' else 'in',Sdf.ValueTypeNames.Float2).ConnectToSource(source.CreateOutput('out',Sdf.ValueTypeNames.Float2))
        if case.get('negative')=='unsupported_graph':image.CreateIdAttr('ND_noise3d_color3')
    dome=UsdLux.DomeLight.Define(stage,'/Lights/white');dome.CreateIntensityAttr(.2)
    # Outside the camera raster but visible to the receiver: all seven slot
    # controls must change actual glossy/diffuse beauty relative to baseline.
    rect=UsdLux.RectLight.Define(stage,'/Lights/rect');rect.CreateWidthAttr(.6);rect.CreateHeightAttr(.8)
    rect.CreateIntensityAttr(8.);rect.AddTranslateOp().Set(Gf.Vec3d(2.85,.25,2))
    camera=UsdGeom.Camera.Define(stage,'/Camera');camera.CreateProjectionAttr('orthographic')
    camera.CreateHorizontalApertureAttr(50.);camera.CreateVerticalApertureAttr(50.)
    camera.CreateClippingRangeAttr((.1,10.));camera.AddTranslateOp().Set(Gf.Vec3d(0,0,3))
    settings=UsdRender.Settings.Define(stage,'/Render/settings');settings.CreateCameraRel().SetTargets([camera.GetPath()])
    settings.CreateResolutionAttr(Gf.Vec2i(*contract['resolution']))
    for key,value in [('spp',contract['samples']),('seed',contract['seed']),('bounces',contract['bounces'])]:
        attribute(settings.GetPrim(),'houdinirt:'+key,Sdf.ValueTypeNames.Int,value)
    outputs(stage,settings,name,out);stage.GetRootLayer().Save()
    expected=[];w,h=contract['resolution']
    for iy in range(h):
        row=[];y=2.5-(iy+.5)*5/h
        for ix in range(w):
            x=-2.5+(ix+.5)*5/w;color=(0.,0.,0.)
            if -2<x<2 and -2<y<2:
                color=FALLBACK if case.get('negative') else model['base_color']
                weight=1. if case.get('negative') else model['base_weight']
                if not case.get('control') and not case.get('negative'):
                    slot=case.get('slot','base_color');u,v=uv_at(case,x,y)
                    sampled=oracle.sample_texture(case['pixels'],u,v,wrap_u=case.get('wrap','periodic'),wrap_v=case.get('wrap','periodic'))
                    if slot=='base_color':color=sampled
                    elif slot=='base_weight':weight=sampled[0]
                color=tuple(c*weight for c in color)
            row.append(color)
        expected.append(row)
    expected_path=out/(name+'.expected.json');expected_path.write_text(json.dumps(expected)+'\n')
    return dict(name=name,stage=str(path),stage_sha256=sha(path),image=str(out/(name+'.exr')),
        expected=str(expected_path),expected_sha256=sha(expected_path),negative=case.get('negative'),
        control=case.get('control',False),pair=case.get('pair'),slot=case.get('slot'),
        source_asset=case.get('asset'),source_asset_sha256=sha(case['asset']) if Path(case.get('asset','!')).is_file() else None)
def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--assets',required=True,type=Path)
    p.add_argument('--native',required=True,type=Path);args=p.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);generated=out/'assets';generated.mkdir()
    contract_path=Path(__file__).with_name('contract.json');contract=json.loads(contract_path.read_text())
    manifest=json.loads(args.assets.read_text());native_receipt=json.loads((args.native/'receipt.json').read_text())
    for entry in native_receipt['exports'].values():assert sha(entry['path'])==entry['sha256']
    assets=manifest['assets']
    for entry in assets.values():assert sha(entry['path'])==entry['sha256']
    def source(name,space='Raw'):
        entry=assets[name];pixels=entry['decoded']['pixels']
        if space=='srgb_texture':pixels=[[[oracle.srgb_to_linear(c) for c in pixel[:3]] for pixel in row] for row in pixels]
        return dict(asset=entry['path'],space=space,pixels=pixels)
    base=source('chart_4x4_8.png','srgb_texture');cases=[]
    for domain in ('constant','uniform','vertex','varying','faceVarying','indexed','seam_float3'):
        cases.append(dict(base,name='uv_'+domain,domain=domain))
    for graph in ('texcoord','geomprop_st','usd_transform2d','place2d'):
        cases.append(dict(base,name='native_'+graph,graph=graph))
    for name in ('chart_4x4_16.png','chart_4x4.jpg','chart_4x4.exr','chart_half.exr','chart_4x4_8.png'):
        case=dict(source(name,'srgb_texture' if name=='chart_4x4_8.png' else 'Raw'),name='codec_'+name.replace('.','_'))
        if name in ('chart_4x4_8.png','chart_4x4.exr'):case['space']=''
        cases.append(case)
    named_path=generated/'named.png';rows=output_image(named_path,[[[.2,.3,.4]]*2]*2,oiio.UINT8)
    processor=ocio.GetCurrentConfig().getProcessor('ACEScg',ocio.ROLE_SCENE_LINEAR).getDefaultCPUProcessor()
    converted=[[processor.applyRGB(pixel) for pixel in row] for row in rows]
    assert all(0<=c<=1 for row in converted for pixel in row for c in pixel)
    cases.append(dict(name='named_ocio',asset=str(named_path),space='ACEScg',pixels=converted))
    hdr_path=generated/'reflectance.hdr';rows=output_image(hdr_path,[[[.2,.4,.8]]*2]*2)
    cases.append(dict(name='codec_hdr',asset=str(hdr_path),space='Raw',pixels=rows))
    bad_path=generated/'corrupt.png';bad_path.write_bytes(b'not a PNG')
    for negative in ('missing_uv','bad_indices','missing_file','corrupt_file','invalid_values','unsupported_graph'):
        case=dict(base,name='fallback_'+negative,negative=negative)
        if negative in ('missing_uv','bad_indices'):case['domain']=negative
        if negative=='missing_file':case['asset']=str(generated/'missing.png')
        if negative=='corrupt_file':case['asset']=str(bad_path)
        if negative=='invalid_values':case['asset']=assets['invalid_nan.exr']['path'];case['space']='Raw'
        cases.append(case)
    for wrap in ('clamp','mirror','constant'):cases.append(dict(base,name='address_'+wrap,wrap=wrap))
    for slot,value in SLOTS.items():
        image_path=generated/(slot+'.exr');color=isinstance(value,tuple)
        rows=output_image(image_path,[[list(value) if color else [value]]])
        if not color:rows=[[[pixel[0]]*3 for pixel in row] for row in rows]
        common=dict(slot=slot,asset=str(image_path),space='Raw',pixels=rows)
        cases.append(dict(common,name='slot_'+slot,pair='control_'+slot))
        cases.append(dict(common,name='control_'+slot,control=True))
    cases.append(dict(base,name='control_baseline',control=True))
    names=[case['name'] for case in cases];assert len(names)==len(set(names))==42
    records=[make(case,out,args.native.resolve(),contract) for case in cases]
    frozen=dict(status='prepared',scope=contract['scope'],contract=contract,cases=records,
        asset_manifest=str(args.assets.resolve()),asset_manifest_sha256=sha(args.assets),
        native_receipt_sha256=sha(args.native/'receipt.json'),native_exports=native_receipt['exports'],
        generator_sha256=sha(__file__),oracle_sha256=sha(Path(oracle.__file__)),contract_sha256=sha(contract_path),
        ocio_config_id=ocio.GetCurrentConfig().getCacheID())
    (out/'manifest.json').write_text(json.dumps(frozen,indent=2)+'\n')
    print(json.dumps(dict(status='prepared',cases=len(records),paired_controls=7)))
if __name__=='__main__':main()
