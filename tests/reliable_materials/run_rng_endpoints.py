"""Compile CPU endpoint observations, then independently judge finite-domain math."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
VS=Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
GLM=ROOT/'build/m6/deps/glm-0af55ccecd98d4e5a8d1fad7de25ba429d60e863'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    inputs=[p for folder in (ROOT/'third_party/openpbr-bsdf',GLM/'glm') for p in folder.rglob('*') if p.is_file()]
    inputs += [HERE/'rng_endpoints.cpp',Path(__file__).resolve()]
    before={str(p):sha(p) for p in inputs}
    shutil.copy2(HERE/'rng_endpoints.cpp',out/'rng_endpoints.cpp')
    commands=['@echo off','setlocal',f'call "{VS}" > toolchain.log 2>&1','if errorlevel 1 exit /b 13',
              f'cl /nologo /std:c++20 /O2 /fp:precise /EHsc /I"{GLM}" /I"{ROOT/"third_party/openpbr-bsdf"}" rng_endpoints.cpp /Fe:rng_endpoints.exe > compile.log 2>&1',
              'exit /b %ERRORLEVEL%','']
    (out/'compile.cmd').write_bytes('\r\n'.join(commands).encode())
    receipt=dict(status='running',scope='CPU endpoint and exact shader-PCG reproduction; no GPU execution',input_hashes=before)
    def write():(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    write();code=subprocess.call(['cmd.exe','/d','/c',str(out/'compile.cmd')],cwd=out);receipt['compile_exit']=code
    if code:receipt['status']='failed';write();return code
    with (out/'observations.json').open('wb') as output:
        code=subprocess.call([str(out/'rng_endpoints.exe')],cwd=out,stdout=output)
    receipt['native_exit']=code
    failures=[]
    def require(ok,why):
        if not ok:failures.append(why)
    data=json.loads((out/'observations.json').read_text())
    require(code==0,'CPU observation failed')
    require(data['bins']==2**23 and data['invalid']==data['non_increasing']==data['non_midpoint']==0,'Not every bin is a unique interior midpoint')
    require(data['min']==2**-24 and data['max']==1-2**-24 and data['sum']==2**22,'Exact IEEE midpoint extremes/sum differ')
    require(data['endpoint_old']['pdf']==0 and data['endpoint_new']['pdf']>0,'Adobe endpoint rejection not reproduced/repaired')
    require(len(data['hits'])>0 and {h['seed'] for h in data['hits']}=={101,107,113},'Expected observed stream seeds missing')
    for hit in data['hits']:
        require(0<=hit['words'][1]<256 and hit['old']['pdf']==0,'Old exact stream did not reject')
        require(hit['new']['pdf']>0 and hit['new']['z']>0,'Midpoint exact stream rejected')
        require(all(math.isfinite(x) and x>0 for x in hit['new']['weight']),'Repaired sample weight invalid')
        require(all(abs(x-y)<2e-7 for x,y in zip(hit['new']['weight'],hit['new']['evaluated_weight'])),'Repaired sample/evaluation inconsistency')
    unchanged=all(sha(Path(p))==h for p,h in before.items());require(unchanged,'CPU compile inputs changed')
    receipt.update(status='passed' if not failures else 'failed',failures=failures,inputs_unchanged=unchanged,
                   rejected_paths=len(data['hits']),observations_sha256=sha(out/'observations.json'),
                   executable_sha256=sha(out/'rng_endpoints.exe'),shader_path=str(ROOT/'src/hdRT/shaders/furnace.comp'),
                   shader_sha256=sha(ROOT/'src/hdRT/shaders/furnace.comp'),
                   proof_limit='The recorded shader hash is provenance, not execution. GPU and sealed regressions remain required.')
    write();print(json.dumps(dict(status=receipt['status'],rejected_paths=len(data['hits']),output=str(out),failures=failures)))
    return 0 if not failures else 16


if __name__=='__main__':raise SystemExit(main())
