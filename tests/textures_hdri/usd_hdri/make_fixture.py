"""Freeze actual USD dome ingress and independent Lambert targets before renders."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, math, sys
import numpy as np
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdRender,UsdShade
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(HERE.parent));import oracle
LEGACY=ROOT/'tests/reliable_materials/usd/make_fixture.py'
spec=importlib.util.spec_from_file_location('output_recipe',LEGACY);recipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(recipe)
RHO=(.2,.5,.8);SEEDS=(101,107,113);SIZE=(48,32)
LIMITS=dict(alpha_max_abs=1e-6,mean_sigma=6,mean_abs_floor=.0002,mean_relative_floor=.002,
            fixed_abs_floor=.0005,fixed_relative_max=.02,quadrature_abs_floor=2e-5,quadrature_relative_max=2e-5,
            deterministic_max_abs=5e-5,karma_sigma=6,karma_relative_floor=.005,karma_relative_max=.02)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def attr(p,n,t,v):p.CreateAttribute(n,t,custom=True).Set(v)
def dome(asset='env_direction.exr',**kw):return dict(asset=asset,pole='Y',rotation=0,intensity=1,exposure=0,color=[1,1,1],format='latlong',**kw)
def cases():
    result=[]
    def add(name,lights,**kw):result.append(dict(name=name,lights=lights,up='Y',old=False,uniform=0,karma=False,negative=False,empty=False,shadow=False)|kw)
    for key in ('white','color','black','direction','sun','pole','seam'):
        add(key,[dome('env_'+key+'.exr')],karma=key in ('white','color','black'))
    add('rgbe_hdr',[dome('env_color.hdr')],karma=True)
    for angle in (90,180):
        d=dome();d['rotation']=angle;add('yaw'+str(angle),[d])
    d=dome('env_direction.exr');d.update(intensity=.5,exposure=2,color=[.25,.5,1]);add('gain',[d])
    d=dome('env_pole.exr');d['pole']='scene';add('scene_z',[d],up='Z')
    d=dome('env_pole.exr');d['pole']='Z';add('explicit_z',[d])
    add('old_stage_z',[dome('env_pole.exr')],old=True,up='Z')
    d=dome('env_color.exr');d.update(rotation=90,color=[.2,.3,.4]);add('multiple',[dome(),d],uniform=.15)
    lights=[]
    for i in range(8):
        d=dome();d.update(rotation=45*i,intensity=.125);lights.append(d)
    add('eight_maps',lights)
    add('explicit_non2to1',[dome('chart_5x3.exr')])
    add('transparent_primary',[dome('env_color.exr')],empty=True)
    add('occluded_white',[dome('env_white.exr')],shadow=True,karma=True)
    for name,asset in (('missing','missing.exr'),('negative','invalid_negative.exr'),('nan','invalid_nan.exr'),('infinity','invalid_infinity.exr')):
        add('excluded_'+name,[dome(asset)],negative=True,uniform=.15)
    d=dome('chart_5x3.exr');d['format']='automatic';add('excluded_automatic',[d],negative=True,uniform=.15)
    d=dome();d['scale']=[1,2,1];add('excluded_nonrigid',[d],negative=True,uniform=.15)
    d=dome();d['colorspace']='not_an_ocio_space';add('excluded_colorspace',[d],negative=True,uniform=.15)
    return result

def shadow_average(order):
    # Exact radiometric geometry: z=1 black rectangle blocks constant-white dome.
    # Integrate receiver pixel box AND blocker area independently; camera z=.5.
    nodes,weights=np.polynomial.legendre.leggauss(order)
    rx=(nodes*.6)[:,None,None,None];ry=(nodes*.4)[None,:,None,None]
    lx=(nodes*.4)[None,None,:,None];ly=(nodes*.4)[None,None,None,:]
    kernel=1/((lx-rx)**2+(ly-ry)**2+1)**2
    w=weights[:,None,None,None]*weights[None,:,None,None]*weights[None,None,:,None]*weights[None,None,None,:]
    return float(np.sum(kernel*w)*(.4*.4)/(4*math.pi))

def reference(case,assets,cache):
    if case['empty']:return [0,0,0],[0,0,0]
    totals=[[case['uniform']]*3 for _ in range(2)]
    if not case['negative']:
        for light in case['lights']:
            rows=assets[light['asset']]['decoded']['pixels'];angle=math.radians(light['rotation'])
            normal=(-math.sin(angle),0,math.cos(angle))
            pole='Y' if case['old'] else case['up'] if light['pole']=='scene' else light['pole']
            if pole=='Z':normal=(normal[0],normal[2],-normal[1])
            key=(light['asset'],normal)
            if key not in cache:cache[key]=[oracle.lambert_bilinear_latlong(rows,normal,n) for n in (16,32)]
            gain=light['intensity']*2**light['exposure']
            for k,target in enumerate(cache[key]):
                for c in range(3):totals[k][c]+=target[c]*gain*light['color'][c]
    if case['shadow']:
        for k,n in enumerate((16,32)):totals[k]=[v-shadow_average(n) for v in totals[k]]
    values=[[v*r for v,r in zip(t,RHO)] for t in totals]
    error=[abs(a-b) for a,b in zip(*values)]
    assert all(d<=LIMITS['quadrature_abs_floor']+LIMITS['quadrature_relative_max']*abs(v) for d,v in zip(error,values[-1]))
    return values[-1],error

def make(path,case,seed,native,assets):
    stage=Usd.Stage.CreateNew(str(path));UsdGeom.SetStageUpAxis(stage,case['up']);UsdGeom.SetStageMetersPerUnit(stage,1.)
    assert Sdf.CopySpec(native,'/materials',stage.GetRootLayer(),'/materials')
    shader=UsdShade.Shader(stage.GetPrimAtPath('/materials/openpbr/surface'))
    for key,value in dict(base_color=RHO,base_weight=1.,base_metalness=0.,specular_color=(1,1,1),specular_weight=0.,specular_roughness=.3,specular_ior=1.5).items():
        inp=shader.CreateInput(key,Sdf.ValueTypeNames.Color3f if isinstance(value,tuple) else Sdf.ValueTypeNames.Float)
        inp.DisconnectSource();inp.Set(Gf.Vec3f(*value) if isinstance(value,tuple) else value)
    def mesh(name,z,extent,color):
        m=UsdGeom.Mesh.Define(stage,name);m.CreatePointsAttr([(-extent,-extent,z),(extent,-extent,z),(extent,extent,z),(-extent,extent,z)])
        m.CreateFaceVertexCountsAttr([4]);m.CreateFaceVertexIndicesAttr([0,1,2,3]);m.CreateSubdivisionSchemeAttr('none')
        m.CreateDisplayColorPrimvar('constant').Set([Gf.Vec3f(*color)]);return m
    if not case['empty']:
        receiver=mesh('/World/Receiver',0,3,RHO);UsdShade.MaterialBindingAPI.Apply(receiver.GetPrim()).Bind(UsdShade.Material(shader.GetPrim().GetParent()))
    if case['shadow']:mesh('/World/Blocker',1,.4,(0,0,0))
    UsdGeom.Scope.Define(stage,'/Lights')
    for index,light in enumerate(case['lights']):
        path_light='/Lights/Dome'+str(index);assert Sdf.CopySpec(native,'/World/dome',stage.GetRootLayer(),path_light)
        p=stage.GetPrimAtPath(path_light);p.SetTypeName('DomeLight' if case['old'] else 'DomeLight_1')
        for key,kind,value in (
            ('inputs:intensity',Sdf.ValueTypeNames.Float,light['intensity']),('inputs:exposure',Sdf.ValueTypeNames.Float,light['exposure']),
            ('inputs:color',Sdf.ValueTypeNames.Color3f,Gf.Vec3f(*light['color'])),('inputs:texture:format',Sdf.ValueTypeNames.Token,light['format']),
            ('poleAxis',Sdf.ValueTypeNames.Token,light['pole']),('xformOp:rotateXYZ',Sdf.ValueTypeNames.Float3,Gf.Vec3f(0,light['rotation'],0)),
            ('xformOp:scale',Sdf.ValueTypeNames.Float3,Gf.Vec3f(*light.get('scale',[1,1,1])))):
            attr(p,key,kind,value)
        asset=assets.get(light['asset']);filename=asset['path'] if asset else str(Path(next(iter(assets.values()))['path']).parent/light['asset'])
        p.GetAttribute('inputs:texture:file').Set(Sdf.AssetPath(filename))
        if 'colorspace' in light:p.GetAttribute('inputs:texture:file').SetColorSpace(light['colorspace'])
    if case['uniform']:
        light=UsdLux.DomeLight.Define(stage,'/Lights/Uniform');light.CreateIntensityAttr(case['uniform'])
    camera=UsdGeom.Camera.Define(stage,'/Camera');camera.CreateProjectionAttr('orthographic');camera.CreateHorizontalApertureAttr(12.);camera.CreateVerticalApertureAttr(8.)
    camera.CreateClippingRangeAttr((.01,10.));camera.AddTranslateOp().Set(Gf.Vec3d(0,0,.5))
    settings=UsdRender.Settings.Define(stage,'/Render/settings');settings.CreateCameraRel().SetTargets([camera.GetPath()]);settings.CreateResolutionAttr(Gf.Vec2i(*SIZE))
    stage.SetMetadata('renderSettingsPrimPath','/Render/settings')
    for name,kind,value in (
        ('houdinirt:spp',Sdf.ValueTypeNames.Int,4096),('houdinirt:bounces',Sdf.ValueTypeNames.Int,0),('houdinirt:seed',Sdf.ValueTypeNames.Int,seed),
        ('karma:global:randomseed',Sdf.ValueTypeNames.Int,seed),('karma:global:convergence_mode',Sdf.ValueTypeNames.Token,'Path Traced'),
        ('karma:global:pathtracedsamples',Sdf.ValueTypeNames.Int,4096),('karma:global:pixelfilter',Sdf.ValueTypeNames.String,'box'),
        ('karma:global:pixelfiltersize',Sdf.ValueTypeNames.Float,2.),('karma:global:imagefilter',Sdf.ValueTypeNames.String,'[]'),
        ('karma:object:diffuselimit',Sdf.ValueTypeNames.Float,0.),('karma:object:reflectlimit',Sdf.ValueTypeNames.Float,1.),
        ('karma:global:colorlimit',Sdf.ValueTypeNames.Float,1e8),('karma:global:colorlimitindirect',Sdf.ValueTypeNames.Float,1e8)):
        attr(settings.GetPrim(),name,kind,value)
    recipe.outputs(stage,settings,False);stage.GetRootLayer().Save()
    return dict(stage=str(path),stage_sha256=sha(path),seed=seed,case=case['name'])

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--assets',required=True,type=Path);parser.add_argument('--native',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    assets=json.loads(args.assets.read_text())['assets'];native=Sdf.Layer.FindOrOpen(str(args.native.resolve()));assert native
    for a in assets.values():assert sha(a['path'])==a['sha256']
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);rows=cases();renders=[];cache={}
    for case in rows:
        case['expected'],case['quadrature_delta']=reference(case,assets,cache)
        for seed in SEEDS if not case['negative'] and not case['empty'] else (101,):renders.append(make(out/(case['name']+'_'+str(seed)+'.usda'),case,seed,native,assets))
        print('frozen '+case['name'],flush=True)
    dependencies=[Path(__file__),Path(oracle.__file__),LEGACY,LEGACY.with_name('native_karma_vars.usda'),args.native,args.assets]
    manifest=dict(status='frozen',scope='Actual USD/Hydra textured-dome ingress and independent Lambert radiometry; Karma only shared constant-map Lambert and shadow domains',
        limits=LIMITS,rho=RHO,resolution=SIZE,spp=4096,cases=rows,renders=renders,assets=assets,
        dependencies={str(p.resolve()):sha(p) for p in dependencies},native=str(args.native.resolve()),
        output_binding='Same immutable scene/transport; canonical native Karma LPE float beauty versus Hydra raw float color.',
        statistics='Three seed image means, standard error across independent seeds, both 6SE+floor and fixed2% ceilings. Full image average, no alignment, masks or fitting.',
        quadrature='Independent bilinear level0 radiance integration, orders16/32, horizon and texel-cell splits; black rectangular occluder uses four-dimensional area quadrature.',
        glossy_karma='No new glossy OpenPBR parity claim; previous pinned model qualification remains applicable.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(dict(cases=len(rows),stages=len(renders),manifest=str(out/'manifest.json'))))
if __name__=='__main__':main()
