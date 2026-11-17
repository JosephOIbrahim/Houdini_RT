"""CPU-only cook/export of the exact forthcoming regular-GUI native fixture."""
import hashlib,importlib.util,json,os
from pathlib import Path
from types import SimpleNamespace as NS
import hou
from pxr import UsdShade
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2]
os.environ['RT_LIVE_HARNESS_ROOT']=str(ROOT);os.environ['RT_TEXTURE_AUTOSTART']='0'
spec=importlib.util.spec_from_file_location('texture_fixture',ROOT/'gui_textures_hdri.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
smoke=module.TextureSmoke.__new__(module.TextureSmoke)
smoke.assets=REPO/'build/m7/gui-assets'
smoke.viewer=NS(curViewport=lambda:NS(size=lambda:(0,0,626,647)))
smoke.result=dict(native_light_edits=[],native_material_edits=[],texture_controls=[])
smoke.lights={};smoke.write=lambda:None
smoke.create_scene()
out=REPO/'build/m7/gui-fixture-cpu';out.mkdir(parents=True,exist_ok=True)
rows=[]
for name in module.CASES:
    smoke.apply_case(name);smoke.output_node.cook(force=True)
    assert not smoke.output_node.errors(),smoke.output_node.errors()
    stage=smoke.output_node.stage();path=out/(name+'.usda');assert stage.Export(str(path))
    image=UsdShade.Shader(stage.GetPrimAtPath('/materials/openpbr/base_image'))
    assert image.GetIdAttr().Get()=='ND_image_color3'
    assert image.GetInput('file').GetAttr().GetColorSpace()=='srgb_texture'
    dome=stage.GetPrimAtPath('/Lights/environment')
    assert dome.GetTypeName()=='DomeLight_1' and dome.GetAttribute('poleAxis').Get()=='Y'
    for mesh in ('red','green'):
        assert stage.GetPrimAtPath('/World/'+mesh).GetAttribute('primvars:st').Get()
    row=smoke.record_texture_controls(name)
    row.update(stage=str(path),stage_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    rows.append(row)
receipt=dict(status='passed',houdini=hou.applicationVersionString(),no_gui_or_render=True,cases=rows,
    harness_files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')})
(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(dict(status='passed',cases=len(rows),receipt=str(out/'receipt.json'))))
