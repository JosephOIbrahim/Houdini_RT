"""Freeze before images; actual renders require --render and a passed sealed payload."""
import argparse,hashlib,json,os,shutil,subprocess
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
HFS=Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def inventory(directory):return {p.relative_to(directory).as_posix():sha(p) for p in directory.rglob('*') if p.is_file()}
def main():
    p=argparse.ArgumentParser();p.add_argument('--sealed',type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--assets',type=Path,default=ROOT/'build/m7/reference-assets-02/manifest.json')
    p.add_argument('--native',type=Path,default=ROOT/'build/m7/native-texture-fixture')
    p.add_argument('--render',action='store_true');args=p.parse_args()
    if args.render and not args.sealed:raise RuntimeError('Rendering requires an exact sealed s5 receipt')
    gate=json.loads(args.sealed.read_text()) if args.sealed else None
    if gate and (gate['status']!='passed' or gate['mode']!='s5'):raise RuntimeError('Sealed s5 pass required')
    plugin=Path(gate['runbox'])/'build/plugin_stage' if gate else None
    payload=inventory(plugin) if plugin else {}
    if gate:
        for name,digest in payload.items():
            key=(plugin/name).relative_to(args.sealed.resolve().parent).as_posix()
            if gate['artifacts'].get(key,{}).get('sha256')!=digest:raise RuntimeError('Unbound payload '+name)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);inputs=out/'inputs';inputs.mkdir()
    for name in ('make_fixture.py','judge.py','run.py','contract.json'):shutil.copy2(HERE/name,inputs/name)
    shutil.copy2(HERE.parent/'oracle.py',inputs/'oracle.py')
    scripts=inventory(inputs);source=inventory(ROOT/'src/hdRT');vendor=inventory(ROOT/'third_party/openpbr-bsdf')
    native_files={'src/hdRT/'+p:h for p,h in source.items()}
    native_files.update({'third_party/openpbr-bsdf/'+p:h for p,h in vendor.items()})
    if gate:
        for name,digest in native_files.items():
            if gate['source_before'].get(name)!=digest:raise RuntimeError('Source differs from sealed payload '+name)
    receipt=dict(status='preparing',scope='Actual USD texture/UV ingress, raw FLOAT guides/beauty; isolated preferences',
        started_utc=datetime.now(timezone.utc).isoformat(),output=str(out),payload=str(plugin) if plugin else None,
        payload_hashes=payload,native_source_files=native_files,scripts_before=scripts,renders=[],
        sealed_receipt=str(args.sealed.resolve()) if gate else None,sealed_sha256=sha(args.sealed) if gate else None)
    receipt['shader_hashes']={p:h for p,h in payload.items() if p.endswith('.spv')}
    def write(): (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    env=dict(os.environ)
    for key in list(env):
        if key.startswith('HOUDINIRT_') or key in ('PYTHONHOME','PYTHONPATH','HOUDINI_PACKAGE_DIR','HOUDINI_DSO_PATH','PXR_PLUGINPATH_NAME','OCIO'):
            env.pop(key,None)
    config=HFS/'packages/ocio/houdini-config-v3.0.0_aces-v2.0_ocio-v2.5.ocio'
    env.update(HOUDINI_USER_PREF_DIR=str(out/'prefs__HVER__'),HOUDINI_PATH='&',HOUDINI_NO_ENV_FILE='1',
        HOUDINIRT_VALIDATION='1',HOUDINIRT_DEBUG='1',HOUDINI_OIIO_EXR='1',OCIO=str(config))
    if plugin:env['PXR_PLUGINPATH_NAME']=str(plugin)
    receipt.update(ocio_config=str(config),ocio_config_sha256=sha(config))
    def execute(command,log,timeout=90):
        with log.open('wb') as stream:
            child=subprocess.Popen(command,cwd=out,env=env,stdout=stream,stderr=subprocess.STDOUT)
            try:
                receipt['owned_child_pid']=child.pid;write();return child.wait(timeout=timeout)
            finally:
                if child.poll() is None:child.kill();child.wait()
                receipt['owned_child_exit_code']=child.returncode;write()
    write();result=0
    try:
        code=execute([str(HFS/'bin/hython.exe'),'-B',str(inputs/'make_fixture.py'),str(out/'fixtures'),
                      '--assets',str(args.assets.resolve()),'--native',str(args.native.resolve())],out/'prepare.log')
        if code:raise RuntimeError('Fixture generation failed')
        manifest=out/'fixtures/manifest.json';receipt['fixture_manifest_sha256']=sha(manifest);write()
        receipt['frozen_inputs']=dict(scripts=scripts,fixture_manifest_sha256=sha(manifest),
                                      payload_hashes=payload,native_source_files=native_files)
        (out/'freeze.json').write_text(json.dumps(receipt['frozen_inputs'],indent=2)+'\n')
        if args.render:
            receipt['status']='running';write()
            for case in json.loads(manifest.read_text())['cases']:
                log=out/(case['name']+'.log');image=Path(case['image'])
                command=[str(HFS/'bin/husk.exe'),'-R','HdRTRendererPlugin','-V2','-o',str(image),case['stage']]
                code=execute(command,log)
                receipt['renders'].append(dict(case=case['name'],command=command,exit_code=code,log=str(log),
                    log_sha256=sha(log),image_sha256=sha(image) if image.is_file() else None))
                write();print(case['name']+' exit='+str(code),flush=True)
            code=execute([str(HFS/'bin/hython.exe'),'-B',str(inputs/'judge.py'),str(out)],out/'judge.log')
            if code:raise RuntimeError('Independent actual-USD texture judge failed')
            receipt['status']='passed'
        else:receipt['status']='prepared'
    except Exception as error:
        receipt.update(status='failed',error=repr(error));result=16
    after={'src/hdRT/'+p:h for p,h in inventory(ROOT/'src/hdRT').items()}
    after.update({'third_party/openpbr-bsdf/'+p:h for p,h in inventory(ROOT/'third_party/openpbr-bsdf').items()})
    receipt.update(scripts_after=inventory(inputs),native_source_files_after=after,
                   payload_hashes_after=inventory(plugin) if plugin else {},finished_utc=datetime.now(timezone.utc).isoformat())
    receipt['source_unchanged']=after==native_files
    if scripts!=receipt['scripts_after'] or after!=native_files or payload!=receipt['payload_hashes_after']:
        receipt.update(status='failed',error='Frozen source, scripts or payload changed');result=97
    write();print(json.dumps(dict(status=receipt['status'],output=str(out),error=receipt.get('error'))))
    return result
if __name__=='__main__':raise SystemExit(main())
