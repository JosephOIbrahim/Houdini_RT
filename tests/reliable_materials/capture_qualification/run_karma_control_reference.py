"""Diagnostic exact-stage native Karma reference; no production mutation."""
from pathlib import Path
import hashlib,json,os,subprocess,time
ROOT=Path(__file__).resolve().parent/'gui-karma-control-01'
HFS=Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
result_path=ROOT/'result.json';result=json.loads(result_path.read_text());cap=result['karma_capture']
audit=json.loads((ROOT/'process_audit.json').read_text());assert audit['owned_child_exited'] and audit['preexisting_unchanged']
stage=Path(cap['stage']);assert sha(stage)==cap['stage_sha256'] and sha(cap['path'])==cap['sha256']
out=ROOT/'karma-reference-01';out.mkdir(exist_ok=False)
env=dict(os.environ)
for key in list(env):
    if key.startswith('HOUDINIRT_') or key in ('PYTHONHOME','PYTHONPATH','HOUDINI_PACKAGE_DIR','HOUDINI_DSO_PATH','PXR_PLUGINPATH_NAME','OCIO'):env.pop(key,None)
env.update(HOUDINI_USER_PREF_DIR=str(out/'prefs__HVER__'),HOUDINI_PATH='&',HOUDINI_NO_ENV_FILE='1')
image=out/'karma.exr';log=out/'karma.log'
command=[str(HFS/'bin/husk.exe'),'-R','BRAY_HdKarma','--settings','/Render/settings','-V2','-o',str(image),str(stage)]
receipt=dict(status='running',scope='Native Karma host-flipbook diagnostic; source workflow later failed unrelated grid assertion',stage=str(stage),stage_sha256=sha(stage),result_sha256=sha(result_path),script_sha256=sha(__file__),command=command)
def write():(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
write();started=time.monotonic()
with log.open('wb') as f:
    p=subprocess.Popen(command,env=env,cwd=out,stdout=f,stderr=subprocess.STDOUT);receipt['owned_pid']=p.pid;write()
    try:code=p.wait(timeout=180)
    except subprocess.TimeoutExpired:p.kill();p.wait();raise
receipt.update(status='rendered' if code==0 and image.exists() else 'failed',exit_code=code,elapsed_seconds=time.monotonic()-started,log=str(log),log_sha256=sha(log),image=str(image),image_sha256=sha(image) if image.exists() else None,stage_unchanged=sha(stage)==cap['stage_sha256'])
write();print(json.dumps(receipt));raise SystemExit(code)
