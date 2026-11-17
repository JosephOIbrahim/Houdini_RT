"""Compile and run only the independent CPU reference, with pinned dependencies."""
from datetime import datetime,timezone
from pathlib import Path
import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
VS=Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--glm',type=Path,required=True);args=parser.parse_args()
    assert (args.glm/'glm/glm.hpp').is_file()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out=ROOT/'build/m6/reference'/(stamp+'-'+secrets.token_hex(4));out.mkdir(parents=True)
    sources=[(ROOT/'third_party/openpbr-bsdf','vendor'),(args.glm/'glm','glm/glm')]
    hashes={}
    for source,label in sources:
        for p in source.rglob('*'):
            if p.is_file():
                target=out/'inputs'/label/p.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
                hashes[str(p)]=sha(p);shutil.copy2(p,target);assert sha(target)==hashes[str(p)]
    for name in ('cpu_reference.cpp','make_fixtures.py','run_cpu.py'):
        p=HERE/name;hashes[str(p)]=sha(p);shutil.copy2(p,out/'inputs'/name)
    code=subprocess.call([sys.executable,'-B',str(out/'inputs/make_fixtures.py'),str(out/'fixtures')])
    assert code==0
    batch=out/'compile.cmd'
    commands=['@echo off','setlocal',f'call "{VS}" > toolchain.log 2>&1','if errorlevel 1 exit /b 13',
              f'cl /nologo /std:c++20 /O2 /fp:precise /EHsc /I"{out/"inputs/glm"}" /I"{out/"inputs/vendor"}" "{out/"inputs/cpu_reference.cpp"}" /Fe:cpu_reference.exe > compile.log 2>&1',
              'exit /b %ERRORLEVEL%','']
    batch.write_bytes('\r\n'.join(commands).encode())
    receipt=dict(status='running',scope='CPU-only independent material reference generation',output=str(out),original_hashes=hashes)
    def write():(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    write();print(out,flush=True)
    code=subprocess.call(['cmd.exe','/d','/c',str(batch)],cwd=out);receipt['compile_exit']=code
    if not code:
        with (out/'reference.log').open('wb') as log:
            code=subprocess.call([str(out/'cpu_reference.exe'),str(out/'fixtures/cpu_jobs.txt'),str(out/'cpu.csv')],cwd=out,stdout=log,stderr=subprocess.STDOUT)
        receipt['reference_exit']=code
    receipt['source_unchanged']=all(sha(Path(p))==h for p,h in hashes.items())
    if not code and not receipt['source_unchanged']:code=97
    receipt['status']='generated' if not code else 'failed';receipt['exit_code']=code
    receipt['finished_utc']=datetime.now(timezone.utc).isoformat()
    receipt['artifact_hashes']={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and p.name!='receipt.json'}
    write();print(json.dumps(dict(status=receipt['status'],path=str(out),code=code)),flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
