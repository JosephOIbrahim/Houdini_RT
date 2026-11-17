"""Snapshot and build the integrated material probe; GPU execution is explicit."""
from datetime import datetime,timezone
from pathlib import Path
import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
VS=Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
SDK=Path('C:/VulkanSDK/1.4.341.1')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def header(rows):
    def array(values):return '{'+','.join(format(x,'.9g')+'f' if '.' in format(x,'.9g') else format(x,'.9g')+'.0f' for x in values)+'}'
    lines=['std::vector<Case> cases() { return {']
    for row in rows:
        mode={'furnace':0,'distant':1,'finite':2}[row['mode']]
        lines.append('{"'+row['name']+'",'+','.join(map(str,[mode,row['environment'],row['width'],row['height'],row['spp']]))+','+
                     array(row['material'])+','+array(row['view'])+','+array(row['light'])+',{' +','.join(map(str,row['seeds']))+'}},')
    return '\n'.join(lines+['}; }',''])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--shaders',type=Path,required=True);parser.add_argument('--run',action='store_true')
    parser.add_argument('--limit',type=int,default=0,help='Diagnostic only: first N rows')
    args=parser.parse_args();reference=args.reference.resolve()
    assert json.loads((reference/'receipt.json').read_text())['status']=='generated'
    rows=json.loads((reference/'fixtures/fixtures.json').read_text());rows=rows[:args.limit] if args.limit else rows
    out=ROOT/'build/m6/runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-gpu-'+secrets.token_hex(4))
    out.mkdir(parents=True);original={};native={}
    def copy(source,target):
        original[str(source)]=sha(source);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target);assert sha(target)==original[str(source)]
    for subtree in ('src/hdRT','third_party/openpbr-bsdf'):
        for p in (ROOT/subtree).rglob('*'):
            if p.is_file():
                native[str(p.relative_to(ROOT))]=sha(p);copy(p,out/'inputs/tree'/p.relative_to(ROOT))
    for name in ('gpu_probe.cpp','run_gpu.py','judge.py'):
        copy(HERE/name,out/'inputs/probe'/name)
    for name in ('furnace.spv','resolve.spv'):copy(args.shaders/name,out/'inputs/shaders'/name)
    for name in ('cpu.csv','receipt.json'):copy(reference/name,out/'inputs/reference'/name)
    for name in ('fixtures.json','contract.json'):copy(reference/'fixtures'/name,out/'inputs/reference'/name)
    (out/'inputs/probe/fixtures.hpp').write_text(header(rows))
    (out/'inputs/probe/selected.json').write_text(json.dumps(rows,indent=2)+'\n')
    source=out/'inputs/tree/src/hdRT';probe=out/'inputs/probe';batch=out/'compile.cmd'
    commands=['@echo off','setlocal',f'call "{VS}" > toolchain.log 2>&1','if errorlevel 1 exit /b 13',
              f'cl /nologo /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"{SDK/"Include"}" /I"{source}" "{probe/"gpu_probe.cpp"}" "{source/"vkFurnace.cpp"}" /Fe:gpu_probe.exe /link /LIBPATH:"{SDK/"Lib"}" vulkan-1.lib > compile.log 2>&1',
              'exit /b %ERRORLEVEL%','']
    batch.write_bytes('\r\n'.join(commands).encode())
    receipt=dict(status='running',scope='Supplementary integrated opaque OpenPBR GPU',diagnostic_only=bool(args.limit),
                 output=str(out),native_source_files=native,original_hashes=original,reference=str(reference),
                 shader_hashes={n:sha(out/'inputs/shaders'/n) for n in ('furnace.spv','resolve.spv')})
    def write():(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    write();print(out,flush=True);code=subprocess.call(['cmd.exe','/d','/c',str(batch)],cwd=out);receipt['compile_exit']=code
    if not code and args.run:
        with (out/'run.log').open('wb') as log:
            process=subprocess.Popen([str(out/'gpu_probe.exe'),str(out/'inputs/shaders/furnace.spv'),str(out)],cwd=out,stdout=log,stderr=subprocess.STDOUT)
            receipt['owned_gpu_pid']=process.pid;write();code=process.wait()
        receipt['gpu_exit']=code
        if not code:
            with (out/'judge.log').open('wb') as log:
                code=subprocess.call([sys.executable,'-B',str(probe/'judge.py'),str(out)],cwd=out,stdout=log,stderr=subprocess.STDOUT)
            receipt['judge_exit']=code
    receipt['source_unchanged']=all(sha(Path(p))==h for p,h in original.items())
    if not code and not receipt['source_unchanged']:code=97
    receipt['status']='passed' if not code and args.run else 'compiled_only' if not code else 'failed'
    receipt['exit_code']=code;receipt['finished_utc']=datetime.now(timezone.utc).isoformat()
    receipt['artifact_hashes']={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and p.name!='receipt.json'}
    write();print(json.dumps(dict(status=receipt['status'],code=code,out=str(out))),flush=True);return code


if __name__=='__main__':raise SystemExit(main())
