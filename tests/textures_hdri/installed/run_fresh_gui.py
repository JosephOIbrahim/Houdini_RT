"""Prepare/freeze, then explicitly run one owned regular Houdini process per stage."""
from datetime import datetime,timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
HFS=Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')
HELPERS=HERE


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def stamp():return datetime.now(timezone.utc).isoformat()
def write(path,data):path.write_text(json.dumps(data,indent=2)+'\n')
def files(folder):return {p.relative_to(folder).as_posix():sha(p) for p in folder.rglob('*') if p.is_file()}
def check_hashes(mapping):
    for path,digest in mapping.items():assert sha(path)==digest,'Input changed: '+path
def processes():
    command="@(Get-Process -Name houdini,hython,husk -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{pid=$_.Id; name=$_.ProcessName; created=$_.StartTime.ToUniversalTime().ToString('o'); path=$_.Path} }) | ConvertTo-Json -Compress"
    text=subprocess.check_output(['powershell.exe','-NoProfile','-Command',command],text=True).strip()
    data=json.loads(text) if text else [];return data if isinstance(data,list) else [data]


def prepare(result_path,label,qualification):
    assert label.replace('-','').isalnum()
    result_path=result_path.resolve();result=json.loads(result_path.read_text());assert result['status']=='passed'
    audit=json.loads((result_path.parent/'process_audit.json').read_text());assert audit['owned_child_exited'] and audit['preexisting_unchanged']
    assert qualification is not None,'Supply the preserved independent H22 capture-domain qualification receipt'
    qualification=qualification.resolve()
    manifest_path=Path(result['manifest']);manifest=json.loads(manifest_path.read_text());assert sha(manifest_path)==result['manifest_sha256']
    payload=Path(manifest['installation_root']);plugin=payload/'renderer'
    for relative,digest in manifest['files'].items():assert sha(payload/relative)==digest,relative
    source_root=Path(manifest['source_workspace'])
    source_hashes={str(source_root/p):h for p,h in manifest['source_files'].items()};check_hashes(source_hashes)
    out=result_path.parent/label;out.mkdir(exist_ok=False);archive=out/'executed_harness';archive.mkdir()
    helper_names=('installed_workflow_base.py','event_journal.py','generation_journal.py')
    originals=[HERE/'fresh_gui.py',Path(__file__).resolve()]+[HELPERS/name for name in helper_names]
    scripts={str(p):sha(p) for p in HERE.iterdir() if p.is_file()}
    scripts.update({str(p):sha(p) for p in originals})
    for p in originals:shutil.copy2(p,archive/p.name)
    for p in HERE.iterdir():
        if p.is_file() and p.name not in ('fresh_gui.py','run_fresh_gui.py'):shutil.copy2(p,archive/p.name)
    frozen_inputs={str(result_path):sha(result_path),str(manifest_path):sha(manifest_path)}
    qualification_data=json.loads(qualification.read_text());assert qualification_data['status']=='qualified'
    check_hashes(qualification_data['files']);frozen_inputs.update(qualification_data['files'])
    frozen_inputs[str(qualification)]=sha(qualification);shutil.copy2(qualification,out/qualification.name)
    asset_manifest=Path(result['asset_manifest']);assert sha(asset_manifest)==result['asset_manifest_sha256']
    frozen_inputs[str(asset_manifest)]=sha(asset_manifest)
    jobs=[];expected_missing=[]
    for name,capture in result['images'].items():
        folder=out/name;folder.mkdir()
        stage=Path(capture['stage_path']);assert sha(stage)==capture['stage_sha256']
        assert sha(capture['path'])==capture['sha256']
        assert capture['authored_resolution']==[626,647]
        frozen_inputs[str(stage)]=sha(stage);frozen_inputs[capture['path']]=capture['sha256']
        color_context=capture['native_color_context']
        assert sha(color_context['path'])==color_context['sha256']
        frozen_inputs[color_context['path']]=color_context['sha256']
        for asset in capture['asset_files'].values():
            assert sha(asset['path'])==asset['sha256'];frozen_inputs[asset['path']]=asset['sha256']
        if capture['missing_asset_expected']:
            missing=capture['native_texture_controls']['image_file'];assert not Path(missing).exists()
            expected_missing.append(missing)
        config=folder/'sampling-config.json';write(config,capture['child_config'])
        job=dict(name=name,capture=capture,color_management=result['viewer_color_management'],
            display_controls=result['capture_display_override']['during'],
            original_result=str(result_path),original_result_sha256=sha(result_path),
            image=str(folder/'fresh.exr'),result=str(folder/'result.json'),config=str(config))
        job_path=folder/'job.json';write(job_path,job);frozen_inputs[str(job_path)]=sha(job_path)
        frozen_inputs[str(config)]=sha(config);jobs.append(str(job_path))
    receipt=dict(status='prepared',scope='Fresh delegate per exact exported stage through the same normal Houdini host flipbook; not raw-husk equivalence',
        reference_kind='fresh_normal_houdini_gui_flipbook',raw_husk_equivalence=False,prepared_utc=stamp(),
        result=str(result_path),result_sha256=sha(result_path),manifest=str(manifest_path),manifest_sha256=sha(manifest_path),
        plugin=str(plugin),payload_hashes=files(plugin),installed_files=manifest['files'],source_hashes=source_hashes,
        scripts=scripts,archive_hashes=files(archive),inputs=frozen_inputs,jobs=jobs,renders=[],expected_missing_assets=expected_missing,
        executable=str(HFS/'bin/houdini.exe'),executable_sha256=sha(HFS/'bin/houdini.exe'),
        capture_reference_qualification=str(qualification),capture_reference_qualification_sha256=sha(qualification))
    write(out/'receipt.json',receipt);shutil.copy2(out/'receipt.json',out/'prepared.json')
    write(out/'freeze.json',dict(frozen_utc=stamp(),receipt_sha256=sha(out/'prepared.json'),
        scripts=scripts,archive_hashes=receipt['archive_hashes'],inputs=frozen_inputs,reference_kind=receipt['reference_kind']))
    print(out,flush=True);return 0


def run(out):
    out=out.resolve();path=out/'receipt.json';receipt=json.loads(path.read_text());assert receipt['status']=='prepared'
    freeze=json.loads((out/'freeze.json').read_text());assert sha(path)==freeze['receipt_sha256']
    assert sha(out/'prepared.json')==freeze['receipt_sha256']
    archive=out/'executed_harness';manifest=json.loads(Path(receipt['manifest']).read_text())
    payload=Path(manifest['installation_root'])
    def audit():
        check_hashes(receipt['scripts']);check_hashes(receipt['inputs']);check_hashes(receipt['source_hashes'])
        assert files(archive)==receipt['archive_hashes']
        assert files(Path(receipt['plugin']))==receipt['payload_hashes']
        assert sha(manifest['package_path'])==manifest['package_sha256']
        assert all(not Path(p).exists() for p in receipt['expected_missing_assets'])
        for p,h in receipt['installed_files'].items():assert sha(payload/p)==h,p
    audit();initial=processes();write(out/'existing_processes.json',initial)
    receipt.update(status='running',started_utc=stamp(),existing_processes=initial,freeze_sha256=sha(out/'freeze.json'))
    discovery=('HOUDINI_USER_PREF_DIR','HOUDINI_PATH','HOUDINI_PACKAGE_SKIP','HOUDINI_NO_ENV_FILE',
               'PXR_PLUGINPATH_NAME','PYTHONPATH','HOUDINI_PACKAGE_DIR','HOUDINI_DSO_PATH')
    env=dict(os.environ);assert all(not env.get(k) for k in discovery),{k:env.get(k) for k in discovery}
    for key in list(env):
        if key.startswith(('RT_','HOUDINIRT_')):env.pop(key)
    env.update(RT_INSTALLED_VERIFY='1',RT_INSTALLED_MANIFEST=receipt['manifest'],RT_INSTALLED_EXIT='1',
        RT_LIVE_HARNESS_ROOT=str(archive),HOUDINIRT_DEBUG='1',HOUDINIRT_VALIDATION='1',PYTHONDONTWRITEBYTECODE='1')
    info=subprocess.STARTUPINFO();info.dwFlags|=subprocess.STARTF_USESHOWWINDOW;info.wShowWindow=0
    code=0
    try:
        for job_path in receipt['jobs']:
            audit();job=json.loads(Path(job_path).read_text());folder=Path(job_path).parent
            env.update(RT_FRESH_GUI_JOB=job_path,RT_INSTALLED_OUTPUT=job['result'],
                HOUDINIRT_S3_CONFIG=job['config'],RT_LIVE_DEBUG_LOG=str(folder/'stderr.log'))
            command=[receipt['executable'],'-foreground','-geometry=1280x720+40+40','waitforui',str(archive/'fresh_gui.py')]
            before=processes();write(folder/'existing_processes.json',before)
            with (folder/'stdout.log').open('wb') as stdout,(folder/'stderr.log').open('wb') as stderr:
                child=subprocess.Popen(command,env=env,cwd=ROOT,stdout=stdout,stderr=stderr,startupinfo=info)
                owned=None
                try:
                    owned=next(p for p in processes() if p['pid']==child.pid)
                    launch=dict(pid=child.pid,created=owned['created'],command=command,started_utc=stamp(),
                        job=job_path,job_sha256=sha(job_path),script=str(archive/'fresh_gui.py'),script_sha256=sha(archive/'fresh_gui.py'),
                        manifest=receipt['manifest'],manifest_sha256=receipt['manifest_sha256'],
                        discovery_environment={k:env.get(k) for k in discovery},discovery_overrides='None; normal preferences/packages',
                        startup_window='Hidden; owned child shows its native main window for viewport rendering')
                    write(folder/'launch.json',launch);receipt['owned_pid']=child.pid;write(path,receipt)
                    status=child.wait(timeout=150)
                finally:
                    forced=child.poll() is None
                    if forced:child.kill();child.wait()  # Only this exact Popen process handle.
                    after=processes();identities={(p['pid'],p['created']) for p in after}
                    identity=(child.pid,owned['created']) if owned else None
                    check=dict(owned_pid=child.pid,owned_creation=owned['created'] if owned else None,
                        owned_child_exited=child.poll() is not None and (identity is None or identity not in identities),
                        preexisting_unchanged=all((p['pid'],p['created']) in identities for p in before),
                        forced_owned_cleanup=forced,exit_code=child.returncode,observed_utc=stamp(),before=before,after=after)
                    write(folder/'process_audit.json',check)
            assert status==0,(job['name'],status)
            assert check['owned_child_exited'] and check['preexisting_unchanged'],check
            child_result=json.loads(Path(job['result']).read_text())
            assert child_result['status']=='passed' and child_result['reference_completed']
            assert child_result['pid']==child.pid and child_result['stage_sha256']==job['capture']['stage_sha256']
            assert child_result['capture']['sha256']==sha(job['image'])
            assert Path(child_result['loaded_delegate']).resolve()==(payload/'renderer/hdRT.dll').resolve()
            receipt['renders'].append(dict(name=job['name'],command=command,stage=job['capture']['stage_path'],
                stage_sha256=job['capture']['stage_sha256'],image=job['image'],image_sha256=sha(job['image']),
                log=str(folder/'stderr.log'),log_sha256=sha(folder/'stderr.log'),config=job['config'],config_sha256=sha(job['config']),
                child_result=job['result'],child_result_sha256=sha(job['result']),launch=str(folder/'launch.json'),
                launch_sha256=sha(folder/'launch.json'),process_audit=str(folder/'process_audit.json'),
                process_audit_sha256=sha(folder/'process_audit.json')))
            write(path,receipt);print('fresh GUI complete: '+job['name'],flush=True)
        audit();receipt['status']='rendered'
    except BaseException as error:receipt.update(status='failed',error=repr(error));code=16
    try:audit();receipt['scripts_unchanged']=receipt['installed_unchanged']=receipt['result_unchanged']=True
    except BaseException as error:receipt.update(status='failed',error=repr(error));code=97
    after=processes();identities={(p['pid'],p['created']) for p in after}
    receipt['preexisting_unchanged']=all((p['pid'],p['created']) in identities for p in initial)
    if not receipt['preexisting_unchanged']:receipt.update(status='failed',error='A preexisting process changed');code=97
    receipt['finished_utc']=stamp();write(path,receipt)
    print(json.dumps(dict(status=receipt['status'],output=str(out),error=receipt.get('error'))),flush=True);return code


def main():
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare',type=Path);group.add_argument('--run',type=Path)
    parser.add_argument('--label',default='fresh-gui-01');parser.add_argument('--qualification',type=Path);args=parser.parse_args()
    return prepare(args.prepare,args.label,args.qualification) if args.prepare else run(args.run)


if __name__=='__main__':raise SystemExit(main())
