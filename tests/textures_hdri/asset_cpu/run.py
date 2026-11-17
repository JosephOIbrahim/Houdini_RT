"""Prepare, compile, execute and independently judge the CPU asset contract."""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
HFS=Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path)
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--build',type=Path,default=ROOT/'build/m7/asset-cpu-build');args=p.parse_args()
    args.out=args.out.resolve();assert not args.out.exists()
    sources={f'src/hdRT/{n}':sha(ROOT/'src/hdRT'/n) for n in ('imageAssets.cpp','imageAssets.h','vkTextures.h')}
    tests={str(f.relative_to(ROOT)):sha(f) for f in HERE.iterdir() if f.is_file()}
    env=os.environ.copy();env['PATH']=str(HFS/'bin')+';C:/Program Files/CMake/bin;'+env.get('PATH','')
    config=HFS/'packages/ocio/houdini-config-v3.0.0_aces-v2.0_ocio-v2.5.ocio';env['OCIO']=str(config)
    exe=args.build/'Release/asset_probe.exe'
    commands=[
        [str(HFS/'bin/hython.exe'),str(HERE/'prepare.py'),str(args.manifest.resolve()),str(args.out)],
        ['cmake','-S',str(HERE),'-B',str(args.build),'-G','Visual Studio 17 2022','-A','x64',
         '-DCMAKE_PREFIX_PATH='+str(HFS/'toolkit/cmake')],
        ['cmake','--build',str(args.build),'--config','Release','--parallel','1'],
        [str(exe),str(args.out)],
        [sys.executable,str(HERE/'judge.py'),str(args.out)]]
    results=[]
    for index,command in enumerate(commands):
        start=time.perf_counter();run=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True)
        if not args.out.exists(): args.out.mkdir(parents=True)
        log=args.out/f'command-{index}.log';log.write_text(run.stdout+run.stderr,encoding='utf-8')
        results.append(dict(command=command,exit_code=run.returncode,seconds=time.perf_counter()-start,
                            log=str(log),log_sha256=sha(log)))
        if run.returncode:break
    changed=[p for p,h in {**sources,**tests}.items() if sha(ROOT/p)!=h]
    passed=len(results)==5 and all(r['exit_code']==0 for r in results) and not changed
    receipt=dict(status='passed' if passed else 'failed',scope='CPU image decoding/color/cache/cancellation; no GPU',
                 native_source_files=sources,test_source_files=tests,changed_files=changed,commands=results,
                 executable=str(exe),executable_sha256=sha(exe) if exe.exists() else None,
                 config=str(config),config_sha256=sha(config),manifest_sha256=sha(args.manifest),
                 judge_sha256=sha(args.out/'judge.json') if (args.out/'judge.json').exists() else None)
    (args.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(status=receipt['status'],receipt=str(args.out/'receipt.json'))))
    return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
