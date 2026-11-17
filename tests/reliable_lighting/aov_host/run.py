"""Freeze and prepare CPU fixtures; --render additionally runs owned husk children."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
HFS=Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    candidates=parser.add_mutually_exclusive_group(required=True)
    candidates.add_argument('--sealed',type=Path)
    candidates.add_argument('--candidate',type=Path,help='Supplementary diagnostic only; does not claim sealed acceptance')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--render',action='store_true')
    args=parser.parse_args()
    gate=json.loads(args.sealed.read_text()) if args.sealed else None
    if gate and (gate['status']!='passed' or gate['mode']!='s5'): raise RuntimeError('sealed s5 pass required')
    plugin=Path(gate['runbox'])/'build/plugin_stage' if gate else args.candidate.resolve()
    payload={p.relative_to(plugin).as_posix():sha(p) for p in plugin.rglob('*') if p.is_file()}
    for name in ('hdRT.dll','plugInfo.json','capabilities.json','shaders/furnace.spv','shaders/resolve.spv'):
        if not (plugin/name).is_file(): raise RuntimeError('missing payload: '+name)
        if gate:
            key=(plugin/name).relative_to(args.sealed.resolve().parent).as_posix()
            if payload[name]!=gate['artifacts'][key]['sha256']: raise RuntimeError('sealed payload mismatch: '+name)
    out=args.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    inputs=out/'inputs'; inputs.mkdir()
    for name in ('make_fixture.py','judge.py','run.py'): shutil.copy2(HERE/name,inputs/name)
    scripts={p.name:sha(p) for p in inputs.iterdir()}
    receipt={'status':'preparing','scope':'Actual H22.0.429 husk AOV delivery; isolated preferences',
             'started_utc':datetime.now(timezone.utc).isoformat(),'output':str(out),
             'payload':str(plugin.resolve()),'payload_hashes':payload,'scripts_before':scripts,
             'sealed_receipt':str(args.sealed.resolve()) if gate else None,
             'sealed_sha256':sha(args.sealed) if gate else None,'diagnostic_only':not bool(gate),'renders':[]}
    def write(): (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    env=dict(os.environ)
    for key in list(env):
        if key.startswith('HOUDINIRT_') or key in ('PYTHONHOME','PYTHONPATH','HOUDINI_PACKAGE_DIR','HOUDINI_DSO_PATH','PXR_PLUGINPATH_NAME','OCIO'):
            env.pop(key,None)
    env.update(HOUDINI_USER_PREF_DIR=str(out/'prefs__HVER__'),HOUDINI_PATH='&',HOUDINI_NO_ENV_FILE='1',
               PXR_PLUGINPATH_NAME=str(plugin),HOUDINIRT_VALIDATION='1',HOUDINIRT_DEBUG='1',HOUDINI_OIIO_EXR='1')
    def execute(command,log,timeout=60):
        with log.open('wb') as stream:
            child=subprocess.Popen(command,cwd=out,env=env,stdout=stream,stderr=subprocess.STDOUT)
            receipt['owned_child_pid']=child.pid; write()
            try: code=child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(); raise RuntimeError('owned child timed out: '+str(log))
        return code
    write(); print(str(out),flush=True)
    result=0
    try:
        code=execute([str(HFS/'bin/hython.exe'),'-B',str(inputs/'make_fixture.py'),str(out/'fixtures')],out/'prepare.log')
        if code: raise RuntimeError('fixture generation failed')
        manifest=out/'fixtures/manifest.json'; receipt['fixture_manifest_sha256']=sha(manifest); write()
        if not args.render:
            receipt['status']='prepared'; receipt['gpu_launched']=False
        else:
            receipt['status']='running'; receipt['gpu_launched']=True; write()
            for case in json.loads(manifest.read_text())['cases']:
                log=out/(case['name']+'.log')
                command=[str(HFS/'bin/husk.exe'),'-R','HdRTRendererPlugin','-V2','-o',case['image'],case['stage']]
                code=execute(command,log)
                image=Path(case['image'])
                receipt['renders'].append({'case':case['name'],'exit_code':code,'log':str(log),
                    'log_sha256':sha(log),'image_exists':image.is_file(),'image_sha256':sha(image) if image.is_file() else None})
                write(); print(case['name']+' exit='+str(code),flush=True)
            code=execute([str(HFS/'bin/hython.exe'),'-B',str(inputs/'judge.py'),str(out)],out/'judge.log')
            if code: raise RuntimeError('independent AOV judge failed')
            receipt['status']='passed'
    except Exception as error:
        receipt.update(status='failed',error=repr(error)); result=16
    receipt['payload_hashes_after']={p.relative_to(plugin).as_posix():sha(p) for p in plugin.rglob('*') if p.is_file()}
    receipt['scripts_after']={p.name:sha(p) for p in inputs.iterdir() if p.is_file()}
    if receipt['payload_hashes_after']!=payload or receipt['scripts_after']!=scripts:
        receipt.update(status='failed',error='frozen inputs changed'); result=97
    receipt['finished_utc']=datetime.now(timezone.utc).isoformat(); write()
    print(json.dumps({'status':receipt['status'],'output':str(out),'error':receipt.get('error')})); return result


if __name__=='__main__': raise SystemExit(main())
