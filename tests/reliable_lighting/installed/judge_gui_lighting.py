"""Run with H22 hython after owned GUI exit. Independent USD/light/pixel/log judge."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import numpy as np
import OpenImageIO as oiio
from pxr import Usd, UsdGeom

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ORACLE_PATH = REPO / 'tests/reliable_lighting/analytic/oracle.py'
spec = importlib.util.spec_from_file_location('independent_lighting_oracle', ORACLE_PATH)
oracle = importlib.util.module_from_spec(spec); spec.loader.exec_module(oracle)


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pixels(path):
    inp=oiio.ImageInput.open(str(path)); assert inp is not None,oiio.geterror()
    spec=inp.spec(); names=list(spec.channelnames)
    array=np.asarray(inp.read_image('float'),dtype=np.float64).reshape(spec.height,spec.width,spec.nchannels)
    inp.close()
    return array[..., [names.index(c) for c in ('R','G','B','A')]], dict(width=spec.width,height=spec.height,channels=names)


def attr(prim,name,default=None):
    value=prim.GetAttribute(name).Get()
    return default if value is None else value


def stage_lights(stage,cache):
    lights=[]
    kinds={'RectLight':'rect','DiskLight':'disk','SphereLight':'sphere','DistantLight':'distant'}
    for prim in stage.Traverse():
        if prim.GetTypeName() not in kinds: continue
        if UsdGeom.Imageable(prim).ComputeVisibility()==UsdGeom.Tokens.invisible: continue
        assert not attr(prim,'inputs:enableColorTemperature',False),'Fixture unexpectedly enables temperature'
        kind=kinds[prim.GetTypeName()]; matrix=np.array(cache.GetLocalToWorldTransform(prim),dtype=np.float64)
        normal=oracle.unit(-matrix[2,:3])
        light=dict(type=kind,position=matrix[3,:3].tolist(),normal=normal.tolist(),
                   color=list(attr(prim,'inputs:color',[1,1,1])),intensity=float(attr(prim,'inputs:intensity',1)),
                   exposure=float(attr(prim,'inputs:exposure',0)),normalize=bool(attr(prim,'inputs:normalize',False)),path=str(prim.GetPath()))
        if kind=='rect':
            light.update(u=(matrix[0,:3]*float(attr(prim,'inputs:width',1))*.5).tolist(),v=(matrix[1,:3]*float(attr(prim,'inputs:height',1))*.5).tolist())
        elif kind=='disk':
            radius=float(attr(prim,'inputs:radius',.5))
            light.update(u=(matrix[0,:3]*radius).tolist(),v=(matrix[1,:3]*radius).tolist())
        elif kind=='sphere':
            scale=np.linalg.norm(matrix[:3,:3],axis=1)
            assert np.max(abs(scale-scale[0]))<1e-6,'Fixture sphere is not uniformly scaled'
            light['radius']=float(attr(prim,'inputs:radius',.5))*scale[0]
        else: light['angle']=float(attr(prim,'inputs:angle',.53))
        lights.append(light)
    assert {light['path'] for light in lights}=={'/Lights/rect','/Lights/disk','/Lights/sphere','/Lights/distant'}
    for prim in stage.Traverse():
        if prim.GetTypeName()=='DomeLight':
            assert float(attr(prim,'inputs:intensity',1))==0,'Unexpected nonzero GUI dome'
    return lights


def receiver_samples(stage,camera_path,shape,limit,border):
    h,w=shape; cache=UsdGeom.XformCache()
    # The GUI requests bounces4, but this specific scene has exactly two
    # coplanar, +Z-facing diffuse receivers. Every scattered ray has positive
    # world Z and cannot hit another diffuse surface; analytic emitter hits
    # terminate. Its exact transport therefore equals the direct-light oracle.
    mesh_paths={str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)}
    assert mesh_paths=={'/World/red','/World/green'},('Unexpected scattering geometry',mesh_paths)
    camera=stage.GetPrimAtPath(camera_path)
    assert camera and attr(camera,'projection')=='orthographic'
    matrix=np.array(cache.GetLocalToWorldTransform(camera),dtype=np.float64)
    assert np.max(abs(matrix[:3,:3]-np.eye(3)))<1e-7,'Fixture expected unrotated camera'
    assert float(attr(camera,'horizontalApertureOffset',0))==0 and float(attr(camera,'verticalApertureOffset',0))==0
    extent_x=float(attr(camera,'horizontalAperture'))*.1
    extent_y=float(attr(camera,'verticalAperture'))*.1
    xx=(np.arange(w)+.5)/w*extent_x-extent_x/2+matrix[3,0]
    yy=extent_y/2-(np.arange(h)+.5)/h*extent_y+matrix[3,1]
    groups=[]
    for name in ('red','green'):
        prim=stage.GetPrimAtPath('/World/'+name); mesh=UsdGeom.Mesh(prim)
        assert prim and UsdGeom.Imageable(prim).ComputeVisibility()!=UsdGeom.Tokens.invisible
        local=np.array(mesh.GetPointsAttr().Get(),dtype=np.float64)
        transform=np.array(cache.GetLocalToWorldTransform(prim),dtype=np.float64)
        world=np.column_stack((local,np.ones(len(local))))@transform
        assert np.max(abs(world[:,2]))<1e-7 and len(local)==4
        assert list(mesh.GetFaceVertexCountsAttr().Get())==[4] and list(mesh.GetFaceVertexIndicesAttr().Get())==[0,1,2,3]
        assert mesh.GetOrientationAttr().Get()==UsdGeom.Tokens.rightHanded
        normal_primvar=prim.GetAttribute('primvars:normals')
        assert not mesh.GetNormalsAttr().HasAuthoredValueOpinion() and not (normal_primvar and normal_primvar.HasAuthoredValueOpinion())
        geometric=oracle.unit(np.cross(world[1,:3]-world[0,:3],world[2,:3]-world[0,:3]))
        assert np.max(abs(geometric-[0,0,1]))<1e-7,'Receiver can scatter toward other geometry'
        minimum=world[:,:2].min(axis=0);maximum=world[:,:2].max(axis=0)
        ix=np.where((xx>minimum[0]+border*extent_x/w)&(xx<maximum[0]-border*extent_x/w))[0]
        iy=np.where((yy>minimum[1]+border*extent_y/h)&(yy<maximum[1]-border*extent_y/h))[0]
        side=int(math.sqrt(limit)); assert len(ix)>=side and len(iy)>=side,'Insufficient fixed receiver pixels'
        ix=ix[np.linspace(0,len(ix)-1,side).round().astype(int)]
        iy=iy[np.linspace(0,len(iy)-1,side).round().astype(int)]
        col,row=np.meshgrid(ix,iy); col=col.ravel();row=row.ravel()
        points=np.column_stack((xx[col],yy[row],np.zeros(len(row))))
        colors=np.array(mesh.GetDisplayColorAttr().Get(),dtype=np.float64); assert colors.shape==(1,3)
        groups.append(dict(name=name,rows=row,cols=col,points=points,albedo=colors[0]))
    return groups,stage_lights(stage,cache)


def native_events(path):
    pattern=re.compile(r'\[debug\] (pause request|pause ack|resume|request|present|batch) renderer=')
    sessions={}; count=0; events=[]; latest={}; stale=[]
    for number,line in enumerate(path.read_text(errors='replace').splitlines(),1):
        if not line.startswith('HoudiniRT:'): continue
        created=re.search(r'render session=(\S+) created',line)
        if created: count+=1;sessions[created[1].lower()]=count
        match=pattern.search(line)
        if not match: continue
        item={'kind':match[1].replace(' ','_'),'line':number,'sequence':len(events)}
        for key,value in re.findall(r'(\w+)=([^\s]+)',line):
            if key=='renderer': item[key]=value.lower();continue
            try:item[key]=float(value) if '.' in value else int(value)
            except ValueError:item[key]=value
        item['session']=sessions.get(item['renderer']); identity=(item['renderer'],item['session'])
        if item['kind']=='request':latest[identity]=item
        if item['kind']=='present':
            request=latest.get(identity)
            if not request or (request['generation'],request['scene'])!=(item['generation'],item['scene']):stale.append(item)
        events.append(item)
    assert not stale,('Stale/missing-request publication',stale)
    return events


def main():
    result_path=Path(sys.argv[1]).resolve(); result=json.loads(result_path.read_text()); out=result_path.parent
    limits=json.loads((HERE/'judge_contract.json').read_text()); failures=[]; evidence=[]; hashes={str(result_path):sha(result_path),str(ORACLE_PATH):sha(ORACLE_PATH),str(HERE/'judge_contract.json'):sha(HERE/'judge_contract.json')}
    def require(okay,text):
        if not okay:failures.append(text)
    require(result['status']=='passed' and result.get('all_requested_workflow_checks'), 'GUI workflow did not finish')
    manifest_path=Path(result['manifest']);manifest=json.loads(manifest_path.read_text());payload=Path(manifest['installation_root'])
    require(sha(manifest_path)==result['manifest_sha256'],'Installed manifest changed')
    for relative,want in manifest['files'].items():
        path=payload/relative;require(path.is_file() and sha(path)==want,'Installed file mismatch: '+relative)
    require(Path(result['loaded_delegate']).resolve()==(payload/'renderer/hdRT.dll').resolve(),'Wrong loaded native DLL path')
    hashes[str(manifest_path)]=sha(manifest_path)
    log=out/'stderr.log';hashes[str(log)]=sha(log);raw=log.read_text(errors='replace')
    require('VUID-' not in raw and 'Validation Error' not in raw and 'VK_ERROR' not in raw,'Vulkan validation message')
    require('HoudiniRT: validation layer active' in raw,'Validation not proven active')
    events=native_events(log)
    observed=result['native_work_events'];require(len(events)==len(observed),'Native event census mismatch')
    for native,saved in zip(events,observed):
        require(all(saved.get(k)==v for k,v in native.items() if k!='line'),'Recorded native event differs from raw log')
    def selected_event(record,kind):
        if 'sequence' in record:
            found=events[record['sequence']] if 0<=record['sequence']<len(events) else None
        else:
            found=next((e for e in events if e['line']==record.get('line')),None)
        assert found and found['kind']==kind,('Selected native event missing',kind,record)
        for key,value in record.items():
            if key in found:require(found[key]==value,'Selected native event field differs: '+kind+'/'+key)
        for key in ('renderer','generation'):
            require(key in record and record[key]==found[key],'Selected event lacks identity: '+kind+'/'+key)
        return found
    pause=result['active_pause']; ack=pause['ack']; identity=(ack['renderer'],ack['session'])
    require(0<ack['samples']<65536 and pause['confirmed_hold_seconds']>=8,'Pause was not an active-render hold')
    ack_raw=selected_event(ack,'pause_ack')
    resume=next((e for e in events if e['sequence']>ack['sequence'] and (e.get('renderer'),e.get('session'))==identity and e['kind']=='resume'),None)
    require(resume is not None,'Resume absent')
    if resume:
        require(not any(e['kind'] in ('batch','present') and (e.get('renderer'),e.get('session'))==identity for e in events[ack['sequence']+1:resume['sequence']]),'GPU work while acknowledged paused')
        require(resume['time_s']-ack_raw['time_s']>=8,'Raw native paused interval shorter than8seconds')
    latest=result['paused_light_edit']['resumed_latest']
    latest_raw=selected_event(latest,'present')
    require(resume is not None and latest_raw['sequence']>resume['sequence'] and (latest_raw['renderer'],latest_raw['session'])==identity,'Paused-edit image is not after the matching native resume')
    require(latest['generation']>ack['generation'] and latest['samples']>0,'Paused light edit did not produce latest image')
    require(latest['scene']!=result['paused_light_edit']['edit']['before_request']['scene'],'Paused light edit did not change scene version')
    reports=result['support_reports'];require(len(reports)>=2 and result.get('support_stale_after_native_light_edit'),'Support report refresh/staleness not demonstrated')
    for report in reports:
        require('/Unsupported/cylinder' in report['text'] and 'Finite light geometry' in report['text'],'Support report omitted fixture limitation')
        require(sha(report['profile_path'])==report['profile_sha256'],'Support profile mismatch')
    restored=[row for row in result['records'] if row['step']=='restored']
    require(bool(restored),'No renderer restoration record')
    if restored:
        last=restored[-1];require(last['native_renderer']==result['previous_renderer'] and last['native_paused']==result['previous_paused'],'Previous renderer/pause not restored')
        require(last['other_viewer']['renderer']=='Karma CPU' and not last['other_viewer']['paused'],'Second viewer changed')
    final=result['guide_observations'][-1]
    require(final['guides']==result['original_guides'] and final['viewports']==result['startup_viewports'],'Original viewer guides not restored')
    names={'rect_base','distant_base','disk_base','sphere_base','rect_exposure0','rect_exposure1'}
    require(set(result['images'])==names,'Unexpected image set'); exposure={}
    for name,capture in result['images'].items():
        path=Path(capture['path']);stage_path=Path(capture['stage_path']);hashes[str(path)]=sha(path);hashes[str(stage_path)]=sha(stage_path)
        require(sha(path)==capture['sha256'] and sha(stage_path)==capture['stage_sha256'],name+': image/stage changed')
        raw_before=selected_event(capture['request_before'],'request')
        raw_after=selected_event(capture['request_after'],'request')
        raw_present_before=selected_event(capture['present_before'],'present')
        raw_present_after=selected_event(capture['present_after'],'present')
        require(len({(e['renderer'],e['session']) for e in (raw_before,raw_after,raw_present_before,raw_present_after)})==1,name+': capture mixed renderer sessions')
        require(raw_present_after['sequence']>=raw_present_before['sequence'] and raw_present_after['sequence']>raw_after['sequence'],name+': capture selected an old publication')
        require(capture['request_before']['generation']==capture['request_after']['generation']==capture['present_after']['generation'],name+': capture changed generation')
        require(capture['present_after']['samples']==limits['sample_budget'],name+': wrong sample budget')
        image,metadata=pixels(path);require(bool(np.isfinite(image).all()),name+': nonfinite pixels')
        stage=Usd.Stage.Open(str(stage_path));groups,lights=receiver_samples(stage,capture['camera']['camera_path'],image.shape[:2],limits['receiver_samples_per_mesh'],limits['receiver_border_pixels'])
        case_rows=[]; exposure[name]=[]
        for group in groups:
            reference=np.zeros((len(group['points']),3))
            for light in lights:reference+=oracle.irradiance(group['points'],[0,0,1],light,64,emitters=lights)/math.pi
            reference*=group['albedo']
            selected=image[group['rows'],group['cols']];actual=selected[:,:3]
            require(float(np.max(abs(selected[:,3]-1)))<=limits['alpha_full_coverage_max_abs'],name+': receiver alpha mismatch')
            mean_ref=reference.mean(axis=0);mean_actual=actual.mean(axis=0);residual=actual-reference
            se=np.std(residual,axis=0,ddof=1)/math.sqrt(len(residual))
            mean_error=abs(mean_actual-mean_ref)/np.maximum(abs(mean_ref),1e-12)
            nrmse=float(np.sqrt(np.mean(residual**2))/max(np.sqrt(np.mean(reference**2)),1e-12))
            statistical=limits['mean_standard_error_multiplier']*se+limits['relative_quadrature_pixel_filter_floor']*abs(mean_ref)
            require(float(np.max(mean_error))<=limits['image_channel_mean_relative_error_max'],name+'/'+group['name']+': mean irradiance mismatch')
            require(nrmse<=limits['image_relative_rgb_rmse_max'],name+'/'+group['name']+': spatial irradiance mismatch')
            require(bool(np.all(abs(mean_actual-mean_ref)<=statistical)),name+'/'+group['name']+': statistical mean bound')
            case_rows.append(dict(receiver=group['name'],pixels=len(actual),actual_mean=mean_actual.tolist(),reference_mean=mean_ref.tolist(),relative_mean_error=mean_error.tolist(),relative_rmse=nrmse,standard_error=se.tolist(),statistical_abs_error_limit=statistical.tolist()))
            exposure[name].append(actual)
        evidence.append(dict(image=name,metadata=metadata,receivers=case_rows,lights=lights))
    zero=np.concatenate(exposure['rect_exposure0']);one=np.concatenate(exposure['rect_exposure1'])
    ratio=one.mean(axis=0)/(2*zero.mean(axis=0))
    scaling_rmse=float(np.sqrt(np.mean((one-2*zero)**2))/np.sqrt(np.mean((2*zero)**2)))
    require(float(np.max(abs(ratio-1)))<=limits['exposure_mean_ratio_relative_error_max'],'Native exposure parameter failed intensity doubling')
    require(scaling_rmse<=limits['exposure_scaled_relative_rmse_max'],'Exposure images do not scale coherently')
    latencies=[x for x in result['latency_samples'] if x['kind']=='camera'];require(len(latencies)==6,'Camera edit count mismatch')
    receipt=dict(status='passed' if not failures else 'failed',scope=limits['scope'],failures=failures,images=evidence,thresholds=limits,
                 exposure_ratio_to_expected=ratio.tolist(),exposure_scaled_relative_rmse=scaling_rmse,native_event_count=len(events),active_pause_ack=ack,
                 camera_latency_ms=dict(p50=float(np.percentile([x['edit_to_publication_observed_ms'] for x in latencies],50)),p95=float(np.percentile([x['edit_to_publication_observed_ms'] for x in latencies],95))),
                 files=hashes,installed_file_count=len(manifest['files']))
    (out/'independent_judge.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=receipt['status'],failures=failures,images=len(evidence)),indent=2))
    return 0 if not failures else 16


if __name__=='__main__':raise SystemExit(main())
