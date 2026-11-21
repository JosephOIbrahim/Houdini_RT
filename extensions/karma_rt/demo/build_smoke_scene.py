"""Generate a ready-to-test Solaris/Karma lookdev demo scene for KarmaRT live GUI smoke testing."""
from __future__ import annotations

import os
from pathlib import Path
import hou

OUT_DIR = Path(__file__).resolve().parent
OUT_DIR.mkdir(parents=True, exist_ok=True)
ext = ".hiplc" if hou.licenseCategory() == hou.licenseCategoryType.Indie else ".hip"
OUT_PATH = (OUT_DIR / f"karmart_solaris_smoke{ext}").as_posix()

hou.hipFile.clear(suppress_save_prompt=True)
stage = hou.node("/stage")

# 1. Clean Node-Flow /shot/{geo,LGT,MTL,cam} USD hierarchy
shot_prim = stage.createNode("primitive", "shot_hierarchy")
shot_prim.parm("primpath").set("/shot/geo")
shot_prim.parm("primkind").set("group")

# 2. Hero Lookdev Geometry (Test Pig Head + Ground Plane inside SOP Create)
hero = stage.createNode("sopcreate", "hero_pighead")
hero.parm("pathprefix").set("/shot/geo/$OS")
hero.setInput(0, shot_prim)
sopnet = hero.node("sopnet/create")
pig = sopnet.createNode("testgeometry_pighead", "pighead")
if pig.parm("shader") is not None:
    pig.parm("shader").set(0)
xform = sopnet.createNode("xform", "lift_pig")
xform.setInput(0, pig)
xform.parm("ty").set(0.85)
name_sop = sopnet.createNode("name", "name_pig")
name_sop.setInput(0, xform)
name_sop.parm("name1").set("pig_geo")
out_sop = sopnet.createNode("output", "OUT")
out_sop.setInput(0, name_sop)
out_sop.setDisplayFlag(True)
out_sop.setRenderFlag(True)

floor_lop = stage.createNode("sopcreate", "ground_floor")
floor_lop.parm("pathprefix").set("/shot/geo/$OS")
floor_lop.setInput(0, hero)
f_sopnet = floor_lop.node("sopnet/create")
grid = f_sopnet.createNode("grid", "floor_grid")
grid.parm("sizex").set(18)
grid.parm("sizey").set(18)
grid_name = f_sopnet.createNode("name", "name_floor")
grid_name.setInput(0, grid)
grid_name.parm("name1").set("floor_geo")
f_out = f_sopnet.createNode("output", "OUT")
f_out.setInput(0, grid_name)
f_out.setDisplayFlag(True)
f_out.setRenderFlag(True)

# 3. Material Library + MaterialX OpenPBR / Karma Material Builder shaders
matlib = stage.createNode("materiallibrary", "shot_materials")
matlib.setInput(0, floor_lop)
matlib.parm("matpathprefix").set("/shot/MTL/")

# Try mtlxstandard_surface inside materiallibrary
pig_mat = matlib.createNode("mtlxstandard_surface", "hero_copper")
pig_mat.parm("base_colorr").set(0.82)
pig_mat.parm("base_colorg").set(0.45)
pig_mat.parm("base_colorb").set(0.28)
pig_mat.parm("metalness").set(0.65)
pig_mat.parm("specular_roughness").set(0.22)

floor_mat = matlib.createNode("mtlxstandard_surface", "matte_slate")
floor_mat.parm("base_colorr").set(0.18)
floor_mat.parm("base_colorg").set(0.20)
floor_mat.parm("base_colorb").set(0.23)
floor_mat.parm("specular_roughness").set(0.55)

matlib.parm("materials").set(2)
matlib.parm("matnode1").set("hero_copper")
matlib.parm("matpath1").set("/shot/MTL/hero_copper")
matlib.parm("assign1").set(1)
matlib.parm("geopath1").set("/shot/geo/hero_pighead")
matlib.parm("matnode2").set("matte_slate")
matlib.parm("matpath2").set("/shot/MTL/matte_slate")
matlib.parm("assign2").set(1)
matlib.parm("geopath2").set("/shot/geo/ground_floor")

# 4. Lighting (Karma Physical Sky + Key Area Light under /shot/LGT)
sky = stage.createNode("karmaphysicalsky", "physical_sky")
sky.setInput(0, matlib)
sky.parm("primpath").set("/shot/LGT/physical_sky")

key_lgt = stage.createNode("light::2.0", "key_light")
key_lgt.setInput(0, sky)
key_lgt.parm("primpath").set("/shot/LGT/key_light")
if key_lgt.parm("tx") is not None:
    key_lgt.parmTuple("t").set((4.0, 5.0, 4.0))
    key_lgt.parmTuple("r").set((-35.0, 45.0, 0.0))
if key_lgt.parm("xn__inputsexposure_vya") is not None:
    key_lgt.parm("xn__inputsexposure_vya").set(1.5)

# 5. Shot Camera under /shot/cam/main
cam = stage.createNode("camera", "shot_cam")
cam.setInput(0, key_lgt)
cam.parm("primpath").set("/shot/cam/main")
cam.parmTuple("t").set((0.0, 1.8, 5.2))
cam.parmTuple("r").set((-10.0, 0.0, 0.0))

# 6. Production Karma Render Settings (128 samples XPU -> NEVER mutated by KarmaRT!) + USD Render ROP
krs = stage.createNode("karmarendersettings", "production_karma_settings")
krs.setInput(0, cam)
krs.parm("engine").set("xpu")
krs.parm("camera").set("/shot/cam/main")
krs.parm("pathtracedsamples").set(128)
if krs.parm("res_mode") is not None:
    krs.parm("res_mode").set("manual")
if krs.parmTuple("resolution") is not None:
    try:
        krs.parmTuple("resolution").deleteAllKeyframes()
        krs.parmTuple("resolution").set((1280, 720))
    except Exception:
        pass

rop = stage.createNode("usdrender_rop", "usdrender_rop")
rop.setInput(0, krs)
rop.parm("rendersettings").set("/Render/production_karma_settings")

krs.setDisplayFlag(True)
stage.layoutChildren()

# Verify clean cook before saving
st = krs.stage(apply_viewport_overrides=True)
assert st is not None
hou.hipFile.save(OUT_PATH)
print(f"DEMO_SCENE_SAVED={OUT_PATH}")
