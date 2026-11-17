"""H22 CPU-only: exact-USD reference inputs and independent area quadrature."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,math,shutil,subprocess,sys
import numpy as np
from pxr import Usd,UsdGeom,UsdShade
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
VS=Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
GLM=ROOT/'build/m6/deps/glm-0af55ccecd98d4e5a8d1fad7de25ba429d60e863'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def value(prim,name,default):
    found=prim.GetAttribute(name).Get();return default if found is None else found


def lighting(prim):
    assert not value(prim,'inputs:enableColorTemperature',False)
    return np.array(value(prim,'inputs:color',(1,1,1)))*float(value(prim,'inputs:intensity',1))*2**float(value(prim,'inputs:exposure',0))


def extract(case):
    stage=Usd.Stage.Open(case['stage']);cache=UsdGeom.XformCache();shader=UsdShade.Shader(stage.GetPrimAtPath(case['surface_path']))
    camera=stage.GetPrimAtPath('/Camera');matrix=np.array(cache.GetLocalToWorldTransform(camera));assert np.max(abs(matrix-np.array([[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,1,1]])))<1e-12
    assert value(camera,'projection',None)=='orthographic'
    assert float(value(camera,'horizontalAperture',0))==24 and float(value(camera,'verticalAperture',0))==18
    meshes=[p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)];assert len(meshes)==1
    mesh=meshes[0]
    assert np.array_equal(np.array(cache.GetLocalToWorldTransform(mesh)),np.eye(4))
    assert np.array_equal(np.array(mesh.GetAttribute('points').Get()),[[-3,-3,0],[3,-3,0],[3,3,0],[-3,3,0]])
    assert list(mesh.GetAttribute('faceVertexCounts').Get())==[4]
    assert list(mesh.GetAttribute('faceVertexIndices').Get())==[0,1,2,3]
    assert value(mesh,'orientation','rightHanded')=='rightHanded'
    assert value(mesh,'subdivisionScheme',None)=='none'
    assert not mesh.GetAttribute('normals').HasAuthoredValueOpinion()
    assert not mesh.GetAttribute('primvars:normals').HasAuthoredValueOpinion()
    material=[]
    for key in ('base_color','base_weight','specular_color','specular_weight','base_metalness','specular_roughness','specular_ior','base_diffuse_roughness'):
        v=shader.GetInput(key).Get();material.extend(list(v) if key.endswith('color') else [v])
    dome=lighting(stage.GetPrimAtPath('/Lights/Dome'));rect=np.zeros(3);center=np.zeros(3);half=[0,0];distant=np.zeros(3);wi=np.array([0,0,1])
    p=stage.GetPrimAtPath('/Lights/Rect')
    if p:
        assert not value(p,'inputs:normalize',False)
        matrix=np.array(cache.GetLocalToWorldTransform(p));assert np.max(abs(matrix[:3,:3]-np.eye(3)))==0
        center=matrix[3,:3];half=[float(value(p,'inputs:width',1))*.5,float(value(p,'inputs:height',1))*.5];rect=lighting(p)
    p=stage.GetPrimAtPath('/Lights/Distant')
    if p:
        assert float(value(p,'inputs:angle',None))==0
        matrix=np.array(cache.GetLocalToWorldTransform(p));wi=matrix[2,:3]/np.linalg.norm(matrix[2,:3]);distant=lighting(p)
    if material[9]==0:assert np.max(abs(distant))==0,'Narrow-lobe sampler requires no delta distant source'
    return dict(material=material,dome=dome.tolist(),rect=rect.tolist(),center=center.tolist(),half=half,distant=distant.tolist(),wi=wi.tolist())


def main():
    manifest_path=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(exist_ok=False)
    manifest=json.loads(manifest_path.read_text());jobs=[];cases=[]
    inputs=[p for folder in (ROOT/'third_party/openpbr-bsdf',GLM/'glm') for p in folder.rglob('*') if p.is_file()]
    input_hashes={str(p):sha(p) for p in inputs}
    ys=np.linspace(2,93,16,dtype=int);xs=np.linspace(2,125,16,dtype=int)
    points=[dict(row=int(y),col=int(x),x=(x+.5)/128*2.4-1.2,y=.9-(y+.5)/96*1.8) for y in ys for x in xs]
    (out/'points.txt').write_text('\n'.join(' '.join(map(str,[p['row'],p['col'],p['x'],p['y']])) for p in points)+'\n')
    seen={}
    for case in manifest['cases']:
        if case['negative'] or case['aov']:continue
        values=extract(case)
        if case['name'] in seen:assert seen[case['name']]==values;continue
        seen[case['name']]=values
        numbers=sum((values[k] for k in ('material','dome','rect','center','half','distant','wi')),[])
        jobs.append(' '.join(map(str,[case['name']]+numbers)))
        selected=list(range(len(points)))
        if values['material'][9]==0:
            # Exact geometry-derived exclusion of the sharp reflected emitter's
            # pixel-filter discontinuity; never inspect candidate pixel values.
            cx,cy,_=values['center'];hx,hy=values['half'];margin=2*2.4/128
            selected=[i for i,p in enumerate(points) if min(abs(p['x']-(cx-hx)),abs(p['x']-(cx+hx)),abs(p['y']-(cy-hy)),abs(p['y']-(cy+hy)))>margin]
        cases.append(dict(name=case['name'],values=values,pixel_indices=selected))
    (out/'jobs.txt').write_text('\n'.join(jobs)+'\n')
    header=['struct Quad {double x,w;};']
    for n in (32,64,128,256):
        x,w=np.polynomial.legendre.leggauss(n);assert abs(sum(w)-2)<1e-12
        header.append('const Quad q'+str(n)+'[]={'+','.join('{'+format(a,'.17g')+','+format(b,'.17g')+'}' for a,b in zip(x,w))+'};')
    (out/'quadrature.hpp').write_text('\n'.join(header)+'\n');shutil.copy2(HERE/'cpu_transport.cpp',out/'cpu_transport.cpp')
    recipe=['@echo off','setlocal',f'call "{VS}" > toolchain.log 2>&1','if errorlevel 1 exit /b 13',
            f'cl /nologo /std:c++20 /O2 /fp:precise /EHsc /I"{GLM}" /I"{ROOT/"third_party/openpbr-bsdf"}" cpu_transport.cpp /Fe:cpu_transport.exe > compile.log 2>&1','exit /b %ERRORLEVEL%','']
    (out/'compile.cmd').write_bytes('\r\n'.join(recipe).encode())
    code=subprocess.call(['cmd.exe','/d','/c',str(out/'compile.cmd')],cwd=out);assert code==0,code
    with (out/'reference.log').open('wb') as log:code=subprocess.call([str(out/'cpu_transport.exe'),str(out/'jobs.txt'),str(out/'points.txt'),str(out/'reference.csv')],cwd=out,stdout=log,stderr=subprocess.STDOUT)
    assert code==0,code
    assert {str(p):sha(p) for p in inputs}==input_hashes,'CPU compile inputs changed'
    result=dict(status='generated',created_utc=datetime.now(timezone.utc).isoformat(),fixture_manifest_sha256=sha(manifest_path),cases=cases,points=points,
                scope='Exact USD adapter/transport reference using the same specified Adobe BSDF model with independent CPU integration; not independent BSDF implementation',
                quadrature='Rectangle32/64 Gauss-Legendre; isotropic normal-view dome128/256; point centers with declared filter floor. Roughness-zero uses524288 independent BSDF samples, geometry-derived edge exclusion.',
                compile_input_hashes=input_hashes,compile_inputs_unchanged=True,
                files={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    (out/'reference_manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(out)


if __name__=='__main__':main()
