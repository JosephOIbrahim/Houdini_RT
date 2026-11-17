"""Build/run native CPU ingress tests and bind exact dependencies to their result."""
import argparse, hashlib, json, os, subprocess, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HFS=Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path)
    p.add_argument('--build',type=Path,default=ROOT/'build/m7/cpu');args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    names=('material.cpp material.h materialTexture.cpp materialTexture.h mesh.cpp mesh.h '
           'meshNormals.cpp meshNormals.h meshUV.cpp meshUV.h world.cpp world.h scene.h '
           'light.cpp light.h renderParam.h vkFurnace.h vkTextures.h log.h').split()
    source={f'src/hdRT/{n}':sha(ROOT/'src/hdRT'/n) for n in names}
    paths=list(Path(__file__).parent.glob('*'))+[
        ROOT/'tests/reliable_materials/material_translation.cpp',ROOT/'tests/reliable_lighting/mesh_normals.cpp']
    tests={str(f.relative_to(ROOT)):sha(f) for f in paths if f.is_file()}
    fixture=ROOT/'build/m7/native-texture-fixture'
    inputs={str(f):sha(f) for f in fixture.rglob('*') if f.is_file()}
    commands=[['cmake','-S',str(Path(__file__).parent),'-B',str(args.build),'-G','Visual Studio 17 2022','-A','x64',
               '-DCMAKE_PREFIX_PATH='+str(HFS/'toolkit/cmake')],
              ['cmake','--build',str(args.build),'--config','Release','--parallel','1'],
              ['ctest','--test-dir',str(args.build),'-C','Release','-V']]
    env=os.environ.copy();env['PATH']=str(HFS/'bin')+';C:/Program Files/CMake/bin;'+env.get('PATH','')
    results=[]
    for index,command in enumerate(commands):
        start=time.perf_counter();result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True)
        log=args.out/f'{index}.log';log.write_text(result.stdout+result.stderr,encoding='utf-8')
        results.append(dict(command=command,exit_code=result.returncode,seconds=time.perf_counter()-start,
                            log=str(log),log_sha256=sha(log)))
        if result.returncode:break
    changed=[str(path) for path,digest in {**{str(ROOT/p):h for p,h in source.items()},
            **{str(ROOT/p):h for p,h in tests.items()},**inputs}.items() if not Path(path).is_file() or sha(path)!=digest]
    passed=len(results)==3 and all(r['exit_code']==0 for r in results) and not changed
    executables={n:sha(args.build/'Release'/f'{n}.exe') for n in ('texture_ingress','material_regression','normal_regression')
                 if (args.build/'Release'/f'{n}.exe').is_file()}
    receipt=dict(status='passed' if passed else 'failed',scope='CPU metadata/UV domains/publication and native Houdini22.0.429 ingress; no GPU',
                 native_source_files=source,test_source_files=tests,fixture_files=inputs,changed_files=changed,
                 commands=results,executable_hashes=executables)
    (args.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(status=receipt['status'],receipt=str(args.out/'receipt.json'))))
    return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
