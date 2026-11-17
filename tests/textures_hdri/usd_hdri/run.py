"""Exact sealed-payload sequential HDRI husk acceptance; GPU slot required."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,os,secrets,shutil,subprocess,sys
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
HFS=Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def inventory(path):return {p.relative_to(path).as_posix():sha(p) for p in path.rglob('*') if p.is_file()}
def sources():return {p.relative_to(ROOT).as_posix():sha(p) for folder in ('src/hdRT','third_party/openpbr-bsdf') for p in (ROOT/folder).rglob('*') if p.is_file()}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--sealed',required=True,type=Path);parser.add_argument('--manifest',required=True,type=Path);parser.add_argument('--limit',type=int);args=parser.parse_args()
    sealed=json.loads(args.sealed.read_text());assert sealed['status']=='passed'
    plugin=args.sealed.resolve().parent/'runbox/build/plugin_stage';assert (plugin/'hdRT.dll').is_file()
    manifest_path=args.manifest.resolve();manifest=json.loads(manifest_path.read_text());assert manifest['status']=='frozen'
    out=ROOT/'build/m7/runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-usd-hdri-'+secrets.token_hex(4));out.mkdir(parents=True)
    scripts={str(p):sha(p) for p in list(HERE.glob('*.py'))+[HERE.parent/'oracle.py']}
    for p,d in manifest['dependencies'].items():assert sha(p)==d
    archive=out/'executed_scripts';archive.mkdir()
    for p in scripts:shutil.copy2(p,archive/Path(p).name)
    shutil.copytree(manifest_path.parent,out/'frozen_fixtures')
    native=sources();payload=inventory(plugin)
    assert all(sealed['source_before'].get(p)==d for p,d in native.items()),'Source differs from passed sealed gate'
    assert all(sealed['artifacts'].get((plugin/p).relative_to(args.sealed.resolve().parent).as_posix(),{}).get('sha256')==d for p,d in payload.items()),'Payload differs from passed sealed gate'
    receipt=dict(status='running',diagnostic_only=bool(args.limit),output=str(out),manifest=str(manifest_path),manifest_before_render_sha256=sha(manifest_path),
        sealed_receipt=str(args.sealed.resolve()),sealed_receipt_sha256=sha(args.sealed),native_source_files=native,payload=str(plugin),payload_hashes=payload,
        shader_hashes={p.name:sha(p) for p in plugin.rglob('*.spv')},scripts_before=scripts,husk_executable_sha256=sha(HFS/'bin/husk.exe'),renders=[],started_utc=datetime.now(timezone.utc).isoformat())
    def write():(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    env=dict(os.environ)
    for key in list(env):
        if key.startswith('HOUDINIRT_') or key in ('PYTHONHOME','PYTHONPATH','HOUDINI_PACKAGE_DIR','HOUDINI_DSO_PATH','PXR_PLUGINPATH_NAME','OCIO'):env.pop(key,None)
    env.update(HOUDINI_USER_PREF_DIR=str(out/'prefs__HVER__'),HOUDINI_PATH='&',HOUDINI_NO_ENV_FILE='1',PXR_PLUGINPATH_NAME=str(plugin),HOUDINIRT_VALIDATION='1',HOUDINIRT_DEBUG='1',PYTHONDONTWRITEBYTECODE='1')
    def execute(command,log_path):
        with log_path.open('wb') as log:
            child=subprocess.Popen(command,env=env,cwd=out,stdout=log,stderr=subprocess.STDOUT);receipt['owned_child_pid']=child.pid;write()
            try:code=child.wait(timeout=180)
            except subprocess.TimeoutExpired:child.kill();child.wait();raise
        if code:raise RuntimeError(f'child exit{code}: {log_path}')
    code=0;write();print(out,flush=True)
    try:
        selected=manifest['renders'][:args.limit] if args.limit else manifest['renders']
        for item in selected:
            case=next(c for c in manifest['cases'] if c['name']==item['case']);assert sha(item['stage'])==item['stage_sha256']
            for label,renderer in ([('karma','BRAY_HdKarma')] if case['karma'] else [])+[('candidate','HdRTRendererPlugin')]:
                stem=item['case']+'_'+str(item['seed'])+'.'+label;image=out/(stem+'.exr');log=out/(stem+'.log')
                settings='/Render/karma_settings' if label=='karma' else '/Render/settings'
                command=[str(HFS/'bin/husk.exe'),'-R',renderer,'--settings',settings,'-V2','-o',str(image),item['stage']]
                execute(command,log);assert image.is_file()
                receipt['renders'].append(dict(case=item['case'],seed=item['seed'],label=label,renderer=renderer,render_settings=settings,command=command,
                    image=str(image),image_sha256=sha(image),log=str(log),log_sha256=sha(log)));write();print(stem,flush=True)
        if not args.limit:
            execute([str(HFS/'bin/hython.exe'),'-B',str(HERE/'judge.py'),str(out)],out/'judge.log')
            receipt['judge_sha256']=sha(out/'judge.json')
        receipt['status']='passed' if not args.limit else 'diagnostic_rendered'
    except BaseException as exc:receipt.update(status='failed',error=repr(exc));code=16
    receipt['source_unchanged']=sources()==native;receipt['payload_unchanged']=inventory(plugin)==payload
    receipt['scripts_unchanged']=all(sha(p)==d for p,d in scripts.items()) and all(sha(p)==d for p,d in manifest['dependencies'].items())
    receipt['fixtures_unchanged']=sha(manifest_path)==receipt['manifest_before_render_sha256'] and all(sha(r['stage'])==r['stage_sha256'] for r in manifest['renders'])
    receipt['assets_unchanged']=all(sha(a['path'])==a['sha256'] for a in manifest['assets'].values())
    if not all(receipt[k] for k in ('source_unchanged','payload_unchanged','scripts_unchanged','fixtures_unchanged','assets_unchanged')):
        receipt.update(status='failed',error='Frozen source/payload/test/fixture/asset changed');code=97
    receipt['finished_utc']=datetime.now(timezone.utc).isoformat();write();print(json.dumps(dict(status=receipt['status'],output=str(out),error=receipt.get('error'))));return code
if __name__=='__main__':raise SystemExit(main())
