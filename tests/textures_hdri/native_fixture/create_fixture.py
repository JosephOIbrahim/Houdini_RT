"""CPU-only native texture/UV/dome exports and tiny deterministic analytic assets."""
import hashlib,json
from pathlib import Path
import hou,voptoolutils,OpenImageIO as oiio,numpy as np
from pxr import UsdShade
from output_path import output_path
ROOT=output_path('receipt.json', require_empty=True);ASSETS=ROOT/'assets';ASSETS.mkdir(exist_ok=True)
assert hou.applicationVersion()==(22,0,429) and hou.hipFile.isNewFile()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def image(name,pixels,format):
    path=ASSETS/name;spec=oiio.ImageSpec(pixels.shape[1],pixels.shape[0],pixels.shape[2],format)
    output=oiio.ImageOutput.create(str(path));assert output.open(str(path),spec)
    assert output.write_image(pixels);output.close();return path
rgb=np.array([[[255,0,0],[0,255,0]],[[0,0,255],[128,128,128]]],dtype=np.uint8)
color=image('quadrants_srgb.png',rgb,oiio.UINT8)
scalar=image('roughness.exr',np.array([[[.1],[.3]],[[.6],[.9]]],dtype=np.float32),oiio.FLOAT)
hdr=np.ones((4,8,3),dtype=np.float32)*.125;hdr[1,2]=[8,4,2];hdr[2,6]=[1,2,4]
environment=image('latlong.exr',hdr,oiio.FLOAT)
geometry=hou.node('/stage').createNode('pythonscript','uv_geometry')
geometry.parm('python').set("""
from pxr import UsdGeom,UsdRender,Gf,Sdf
stage=hou.pwd().editableStage()
m=UsdGeom.Mesh.Define(stage,'/World/mesh')
m.CreatePointsAttr([(-2,-2,0),(2,-2,0),(2,2,0),(-2,2,0)])
m.CreateFaceVertexCountsAttr([4]);m.CreateFaceVertexIndicesAttr([0,1,2,3])
m.CreateSubdivisionSchemeAttr('none');m.CreateDisplayColorAttr([Gf.Vec3f(.9,.1,.1)])
uv=UsdGeom.PrimvarsAPI(m).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'faceVarying')
uv.Set([(0,0),(1,0),(1,1),(0,1)])
c=UsdGeom.Camera.Define(stage,'/Camera');c.CreateProjectionAttr('orthographic')
c.CreateHorizontalApertureAttr(60);c.CreateVerticalApertureAttr(60)
UsdGeom.Xformable(c).AddTranslateOp().Set(Gf.Vec3d(0,0,5))
s=UsdRender.Settings.Define(stage,'/Render/settings');s.CreateCameraRel().SetTargets(['/Camera'])
s.CreateResolutionAttr(Gf.Vec2i(64,64))
for k,v in [('spp',256),('bounces',4),('seed',101)]:s.GetPrim().CreateAttribute('houdinirt:'+k,Sdf.ValueTypeNames.Int).Set(v)
""")
dome=hou.node('/stage').createNode('domelight','native_dome');dome.setInput(0,geometry)
dome.parm('primpath').set('/World/dome');dome.parm('xn__inputstexturefile_r3ah').set(str(environment))
dome.parm('xn__inputstextureformat_06ah').set('latlong');dome.parm('poleAxis').set('Y')
library=hou.node('/stage').createNode('materiallibrary','native_materials');library.setInput(0,dome)
library.parm('genpreviewshaders').set(0)
builder=voptoolutils._setupMtlXBuilderSubnet(destination_node=library,name='openpbr')
surface=builder.createNode('mtlxopen_pbr_surface','surface');builder.node('surface_output').setInput(0,surface)
builder.node('mtlxstandard_surface').destroy()
texture=builder.createNode('mtlximage','base_image');texture.parm('file').set(str(color))
texture.parm('filecolorspace').set('srgb_texture')
rough=builder.createNode('mtlximage','roughness_image');rough.parm('signature').set('default')
rough.parm('file').set(str(scalar));rough.parm('filecolorspace').set('Raw')
surface.setInput(surface.inputIndex('base_color'),texture)
surface.setInput(surface.inputIndex('specular_roughness'),rough)
uv=builder.createNode('mtlxtexcoord','uv0')
prop=builder.createNode('mtlxgeompropvalue','st_primvar');prop.parm('signature').set('vector2');prop.parm('geomprop').set('st')
transform=builder.createNode('mtlxUsdTransform2d','uv_transform');transform.setInput(transform.inputIndex('in'),uv)
transform.parmTuple('scale').set((2,3));transform.parm('rotation').set(30);transform.parmTuple('translation').set((.25,-.1))
place=builder.createNode('mtlxplace2d','uv_place');place.setInput(place.inputIndex('texcoord'),uv)
place.parmTuple('scale').set((2,3));place.parm('rotate').set(30);place.parmTuple('offset').set((.25,-.1))
assignment=hou.node('/stage').createNode('assignmaterial','native_assignment');assignment.setInput(0,library)
assignment.parm('primpattern1').set('/World/mesh');assignment.parm('matspecpath1').set('/materials/openpbr')
report={'houdini':hou.applicationVersionString(),'exports':{},'assets':{},'native_nodes':{}}
for n in (dome,library,builder,surface,texture,rough,uv,prop,transform,place,assignment):
    report['native_nodes'][n.path()]={'type':n.type().name(),'inputs':n.inputNames() if isinstance(n,hou.VopNode) else []}
for name,source in [('implicit_uv',None),('texcoord',uv),('geomprop_st',prop),('usd_transform2d',transform),('place2d',place)]:
    for node in (texture,rough):node.setInput(node.inputIndex('texcoord'),source)
    try:assignment.cook(force=True)
    except Exception:
        for n in (geometry,dome,library,assignment):print(n.path(),n.errors(),n.warnings())
        raise
    assert not assignment.errors(),assignment.errors()
    stage=assignment.stage();path=ROOT/(name+'.usda');assert stage.Export(str(path))
    shaders={}
    for prim in stage.Traverse():
        shader=UsdShade.Shader(prim)
        if not shader:continue
        shaders[str(prim.GetPath())]={'id':str(shader.GetIdAttr().Get()),'inputs':{
            p.GetBaseName():{'value':str(p.Get()),'type':str(p.GetTypeName()),'color_space':str(p.GetAttr().GetColorSpace()),
            'connections':list(map(str,p.GetAttr().GetConnections()))} for p in shader.GetInputs()}}
    report['exports'][name]={'path':str(path),'sha256':sha(path),'shaders':shaders,
        'dome_type':stage.GetPrimAtPath('/World/dome').GetTypeName(),
        'dome_attributes':{p.GetName():str(p.Get()) for p in stage.GetPrimAtPath('/World/dome').GetAttributes() if p.HasAuthoredValueOpinion()}}
for p in ASSETS.iterdir():report['assets'][p.name]={'path':str(p),'sha256':sha(p)}
report['script_sha256']=sha(__file__)
(ROOT/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
print('Native texture fixture exports:',list(report['exports']))
