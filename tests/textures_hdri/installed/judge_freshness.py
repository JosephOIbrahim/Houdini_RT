"""Independent freshness/provenance audit; whole-frame image judge is unchanged."""
from pathlib import Path
import hashlib,importlib.util,json,re,sys
from pxr import Usd
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('native_reference_evidence',HERE/'native_evidence.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def values(stage):
    return {str(p.GetPath()):dict(type=p.GetTypeName(),attributes={a.GetName():dict(value=str(a.Get()),type=str(a.GetTypeName()),connections=list(map(str,a.GetConnections())),color_space=str(a.GetMetadata('colorSpace'))) for a in p.GetAttributes()},relationships={a.GetName():list(map(str,a.GetTargets())) for a in p.GetRelationships()}) for p in stage.Traverse() if not str(p.GetPath()).startswith('/HoudiniLayerInfo')}
def main():
    path=Path(sys.argv[1]).resolve();r=read(path);out=path.parent;fail=[];rows=[];files={str(path):sha(path),str(Path(__file__).resolve()):sha(__file__)}
    def require(ok,message):
        if not ok:fail.append(message)
    def hashed(p,h):
        files[str(p)]=sha(p);require(files[str(p)]==h,'Hash changed: '+str(p))
    require(r['status']=='rendered','Fresh reference incomplete')
    require(r['reference_kind']=='fresh_normal_houdini_gui_flipbook' and r['raw_husk_equivalence'] is False,'Incorrect reference scope')
    audit_freeze=HERE/'freeze.json'
    require(sha(__file__)==read(audit_freeze)['files'][Path(__file__).name],'Independent audit changed after pre-image freeze')
    files[str(audit_freeze)]=sha(audit_freeze)
    original=read(r['result']);hashed(r['result'],r['result_sha256'])
    freeze=read(out/'freeze.json');prepared=out/'prepared.json';hashed(prepared,freeze['receipt_sha256'])
    before=read(prepared)
    for key in ('scripts','archive_hashes','inputs','reference_kind'):
        require(r[key]==freeze[key]==before[key],'Prepared reference changed: '+key)
    for key in ('source_hashes','installed_files','payload_hashes','result_sha256','manifest_sha256','jobs'):
        require(r[key]==before[key],'Prepared reference changed: '+key)
    for key in ('scripts','source_hashes','inputs'):
        for p,h in r[key].items():hashed(p,h)
    for rel,h in r['archive_hashes'].items():hashed(out/'executed_harness'/rel,h)
    require(r['scripts_unchanged'] and r['installed_unchanged'] and r['result_unchanged'] and r['preexisting_unchanged'],'Final input checks failed')
    manifest=read(r['manifest']);hashed(r['manifest'],r['manifest_sha256'])
    payload=Path(manifest['installation_root'])
    for rel,h in manifest['files'].items():hashed(payload/rel,h)
    for p,h in manifest['preserved_registrations'].items():hashed(p,h)
    hashed(manifest['package_path'],manifest['package_sha256'])
    require(set(x['name'] for x in r['renders'])==set(original['images']),'Wrong case set')
    identities=set()
    for row in r['renders']:
        name=row['name'];source=original['images'][name]
        for key in ('image','log','config','child_result','launch','process_audit'):
            hashed(row[key],row[key+'_sha256'])
        child=read(row['child_result']);launch=read(row['launch']);audit=read(row['process_audit'])
        require(child['status']=='passed' and child['reference_completed'],name+': child incomplete')
        require(audit['owned_child_exited'] and audit['preexisting_unchanged'] and not audit['forced_owned_cleanup'] and audit['exit_code']==0,name+': process preservation/clean exit failed')
        require(child['pid']==launch['pid']==audit['owned_pid'] and child['pid']!=original['pid'],name+': wrong process identity')
        identity=(launch['pid'],launch['created']);require(identity not in identities,name+': process reused');identities.add(identity)
        after={(p['pid'],p['created']) for p in audit['after']}
        require(identity not in after and all((p['pid'],p['created']) in after for p in audit['before']),name+': recorded process identity check failed')
        require(audit['owned_creation']==launch['created'],name+': process creation mismatch')
        require(all(not v for v in launch['discovery_environment'].values()),name+': discovery environment override')
        hashed(launch['script'],launch['script_sha256']);hashed(launch['job'],launch['job_sha256'])
        for p,h in child['harness_files'].items():hashed(p,h)
        require(Path(child['normal_preferences']).resolve()==Path(manifest['preferences_dir']).resolve() and Path(child['normal_preferences']).samefile(manifest['preferences_dir']),name+': not normal preferences')
        require(Path(child['loaded_delegate']).resolve()==(payload/'renderer/hdRT.dll').resolve(),name+': wrong DLL')
        require(child['stage_sha256']==row['stage_sha256']==source['stage_sha256'],name+': stage mismatch');hashed(row['stage'],source['stage_sha256'])
        require(child['effective_render_settings']==source['effective_render_settings']==dict(spp=256,bounces=4,seed=101),name+': settings mismatch')
        require(child['child_config']==source['child_config']==read(row['config']),name+': fallback config mismatch')
        require(child['actual_viewport_size']==source['viewport_size'],name+': viewport mismatch')
        require(child['composed_stage_values']==values(Usd.Stage.Open(row['stage'])),name+': displayed scene differs')
        display=child['display_override'];require(display['before']==display['restored'],name+': display not restored')
        for k,v in original['viewer_color_management'].items():require(display['during'][k]==v,name+': display mismatch '+k)
        require(display['during']['lights'] is False,name+': light guides visible')
        for key,value in original['capture_display_override']['during'].items():require(display['during'][key]==value,name+': native presentation mismatch '+key)
        require(child['native_color_context']==source['native_color_context'],name+': OCIO config/working space mismatch')
        hashed(child['native_color_context']['path'],child['native_color_context']['sha256'])
        cap=child['capture'];require(cap['sha256']==row['image_sha256'] and cap['camera']==source['active_camera'] and cap['viewport_size']==source['viewport_size'],name+': captured image identity mismatch')
        require(cap['display']==display['during'],name+': capture display mismatch')
        require(child['flipbook_applied']==dict(beauty_only=True,use_resolution=False,frame_time_limit=.25,frame_progress_limit=100.,all_viewports=False),name+': flipbook settings mismatch')
        events=native.native_events(Path(row['log']));raw=Path(row['log']).read_text(errors='replace')
        sessions=re.findall(r'render session=(\S+) created',raw);require(len(sessions)==1,name+': not exactly one fresh delegate')
        selected=[]
        for saved,kind in ((cap['before']['request'],'request'),(cap['before']['present'],'present'),(cap['request_after'],'request'),(cap['present_after'],'present')):
            event=events[saved['sequence']];require(event['kind']==kind,name+': selected wrong event')
            require(all(saved.get(k)==v for k,v in event.items() if k!='line'),name+': selected event differs from raw log');selected.append(event)
        require(len({(e['renderer'],e['session'],e['generation'],e['scene']) for e in selected})==1,name+': capture state changed')
        require(selected[0]['renderer']==sessions[0].lower() and selected[-1]['samples']==256 and selected[1]['samples']==256,name+': incomplete fresh capture')
        require(child['restored_renderer']==child['previous_renderer'] and child['restored_paused']==child['previous_paused'],name+': original state not restored')
        rows.append(dict(name=name,pid=child['pid'],created=identity[1],native_renderer=sessions[0],generation=selected[-1]['generation'],scene=selected[-1]['scene'],samples=256,events=len(events)))
    answer=dict(status='passed' if not fail else 'failed',scope='Independent fresh regular-Houdini process/delegate and immutable-stage provenance; pixel equivalence is judged separately without changed limits',failures=fail,reference_kind=r['reference_kind'],raw_husk_equivalence=False,cases=rows,files=files)
    (out/'independent_freshness.json').write_text(json.dumps(answer,indent=2)+'\n');print(json.dumps(dict(status=answer['status'],failures=fail,cases=len(rows)),indent=2));return 0 if not fail else 16
if __name__=='__main__':raise SystemExit(main())
