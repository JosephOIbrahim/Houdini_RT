"""After owned GUI exit, replay exact exported stages with the installed payload."""
from datetime import datetime,timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
HFS=Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('result',type=Path);parser.add_argument('--label',default='replay-01');args=parser.parse_args()
    assert args.label.replace('-','').isalnum()
    result_path=args.result.resolve();result=json.loads(result_path.read_text());assert result['status']=='passed'
    audit=json.loads((result_path.parent/'process_audit.json').read_text());assert audit['owned_child_exited'] and audit['preexisting_unchanged']
    manifest_path=Path(result['manifest']);manifest=json.loads(manifest_path.read_text());assert sha(manifest_path)==result['manifest_sha256']
    payload=Path(manifest['installation_root']);plugin=payload/'renderer'
    for relative,digest in manifest['files'].items():assert sha(payload/relative)==digest,relative
    out=result_path.parent/args.label;out.mkdir(exist_ok=False)
    receipt=dict(status='running',scope='Exact installed exported-stage GUI replay',result=str(result_path),result_sha256=sha(result_path),
                 manifest=str(manifest_path),manifest_sha256=sha(manifest_path),plugin=str(plugin),
                 payload_hashes={p.relative_to(plugin).as_posix():sha(p) for p in plugin.rglob('*') if p.is_file()},renders=[])
    receipt['scripts']={str(p):sha(p) for p in HERE.iterdir() if p.is_file()}
    def write():(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    env=dict(os.environ)
    for key in list(env):
        if key.startswith('HOUDINIRT_') or key in ('PYTHONHOME','PYTHONPATH','HOUDINI_PACKAGE_DIR','HOUDINI_DSO_PATH','PXR_PLUGINPATH_NAME','OCIO'):
            env.pop(key,None)
    env.update(HOUDINI_USER_PREF_DIR=str(out/'prefs__HVER__'),HOUDINI_PATH='&',HOUDINI_NO_ENV_FILE='1',
               PXR_PLUGINPATH_NAME=str(plugin),HOUDINIRT_DEBUG='1',HOUDINIRT_VALIDATION='1')
    code=0;write();print(out,flush=True)
    try:
        for name,capture in result['images'].items():
            stage=Path(capture['stage_path']);assert sha(stage)==capture['stage_sha256']
            config=out/(name+'.config.json');config.write_text(json.dumps(capture['child_config'])+'\n')
            env['HOUDINIRT_S3_CONFIG']=str(config)
            image=out/(name+'.exr');log_path=out/(name+'.log')
            command=[str(HFS/'bin/husk.exe'),'-R','HdRTRendererPlugin','-V2','-o',str(image),str(stage)]
            with log_path.open('wb') as log:
                child=subprocess.Popen(command,env=env,cwd=out,stdout=log,stderr=subprocess.STDOUT)
                receipt['owned_pid']=child.pid;write()
                try:status=child.wait(timeout=180)
                except subprocess.TimeoutExpired:child.kill();child.wait();raise
            assert status==0,(name,status)
            receipt['renders'].append(dict(name=name,command=command,stage=str(stage),stage_sha256=sha(stage),
                image=str(image),image_sha256=sha(image),log=str(log_path),log_sha256=sha(log_path),config=str(config),config_sha256=sha(config)))
            write();print('replayed '+name,flush=True)
        receipt['status']='rendered'
    except BaseException as exc:receipt.update(status='failed',error=repr(exc));code=16
    receipt['result_unchanged']=sha(result_path)==receipt['result_sha256']
    receipt['installed_unchanged']=all(sha(payload/p)==h for p,h in manifest['files'].items())
    receipt['scripts_unchanged']=all(sha(Path(p))==h for p,h in receipt['scripts'].items())
    if not receipt['result_unchanged'] or not receipt['installed_unchanged'] or not receipt['scripts_unchanged']:receipt.update(status='failed',error='input changed');code=97
    receipt['finished_utc']=datetime.now(timezone.utc).isoformat();write()
    print(json.dumps(dict(status=receipt['status'],path=str(out))),flush=True);return code


if __name__=='__main__':raise SystemExit(main())
