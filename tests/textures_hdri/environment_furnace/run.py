"""Freeze and run a supplemental real-HDRI OpenPBR white-furnace matrix."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,secrets,shutil,subprocess,sys
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
VS='C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat'
SDK=Path('C:/VulkanSDK/1.4.341.1')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--sealed',required=True,type=Path);args=parser.parse_args()
 sealed=json.loads(args.sealed.read_text());assert sealed['status']=='passed'
 plugin=Path(sealed['runbox'])/'build/plugin_stage'
 out=ROOT/'build/m7/runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-environment-furnace-'+secrets.token_hex(4));out.mkdir(parents=True)
 native={};original={}
 for folder in ('src/hdRT','third_party/openpbr-bsdf'):
  for path in (ROOT/folder).rglob('*'):
   if not path.is_file():continue
   rel=path.relative_to(ROOT).as_posix();native[rel]=sha(path);assert sealed['source_before'].get(rel)==native[rel]
   target=out/'inputs/tree'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target);original[str(path)]=native[rel]
 for name in ('probe.cpp','judge.py','run.py'):
  path=HERE/name;shutil.copy2(path,out/name);original[str(path)]=sha(path)
 (out/'shaders').mkdir()
 for name in ('furnace.spv','resolve.spv'):
  path=plugin/'shaders'/name;digest=sha(path);assert sealed['artifacts'][path.relative_to(args.sealed.resolve().parent).as_posix()]['sha256']==digest
  shutil.copy2(path,out/'shaders'/name);original[str(path)]=digest
 contract=dict(scope='Supplementary actual1x1whiteHDRI OpenPBR furnace: analytic unit radiance, dielectric/metal roughness0/.3/1 at viewcos1/.1, three independent seeds; environment NEE and BSDF escape MIS both active',
  sigma=6,absolute_floor=.0002,relative_floor=.002,fixed_absolute_floor=.0005,fixed_relative_max=.02,expected=1,case_count=12,seeds=[101,107,113],samples=4096)
 (out/'contract.json').write_text(json.dumps(contract,indent=2)+'\n')
 source=out/'inputs/tree/src/hdRT'
 commands=['@echo off','setlocal',f'call "{VS}" > toolchain.log 2>&1','if errorlevel 1 exit /b 13',
  f'cl /nologo /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"{SDK/"Include"}" /I"{source}" "{out/"probe.cpp"}" "{source/"vkFurnace.cpp"}" /Fe:probe.exe /link /LIBPATH:"{SDK/"Lib"}" vulkan-1.lib > compile.log 2>&1','exit /b %ERRORLEVEL%','']
 (out/'compile.cmd').write_bytes('\r\n'.join(commands).encode())
 receipt=dict(status='preparing',output=str(out),native_source_files=native,shader_hashes={p.name:sha(p) for p in (out/'shaders').iterdir()},
  sealed_receipt=str(args.sealed.resolve()),sealed_receipt_sha256=sha(args.sealed),original_hashes=original,contract_sha256=sha(out/'contract.json'))
 def write():(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
 write();print(out,flush=True);code=subprocess.call(['cmd.exe','/d','/c',str(out/'compile.cmd')],cwd=out);receipt['compile_exit']=code
 if not code:
  with (out/'run.log').open('wb') as log:
   child=subprocess.Popen([str(out/'probe.exe'),str(out/'shaders/furnace.spv'),str(out/'raw')],cwd=out,stdout=log,stderr=subprocess.STDOUT);receipt['owned_gpu_pid']=child.pid;write()
   try:code=child.wait(timeout=180)
   except subprocess.TimeoutExpired:child.kill();child.wait();code=15
  receipt['gpu_exit']=code
 if not code:
  with (out/'judge.log').open('wb') as log:code=subprocess.call([sys.executable,'-B',str(out/'judge.py'),str(out)],stdout=log,stderr=subprocess.STDOUT)
  receipt['judge_exit']=code
 receipt['source_unchanged']=all(sha(p)==h for p,h in original.items()) and sha(out/'contract.json')==receipt['contract_sha256']
 if not receipt['source_unchanged']:code=97
 receipt.update(status='passed' if not code else 'failed',exit_code=code,finished_utc=datetime.now(timezone.utc).isoformat())
 receipt['artifacts']={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file() and p.name!='receipt.json'}
 write();print(json.dumps(dict(status=receipt['status'],output=str(out),code=code)));return code
if __name__=='__main__':raise SystemExit(main())
