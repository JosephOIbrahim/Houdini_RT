"""H22 CPU-only judge after exact installed replay; never fits image correspondence."""
from pathlib import Path
import hashlib,json,re,sys
import numpy as np
import OpenImageIO as oiio
from pxr import Usd,UsdShade
from native_evidence import workflow
HERE=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pixels(path):
    inp=oiio.ImageInput.open(str(path));assert inp is not None,oiio.geterror()
    spec=inp.spec();names=list(spec.channelnames)
    data=np.asarray(inp.read_image('float'),dtype=np.float64).reshape(spec.height,spec.width,spec.nchannels)
    inp.close();return data[...,[names.index(c) for c in ('R','G','B','A')]]


def scene_controls(stage,name,controls,require):
    want=dict(base='base_a.png',rough='rough_a.exr',sky='sky_a.exr',scale=[1,1],offset=[0,0],rotate=0.,rotation=[0,0,0],exposure=0.)
    want.update({'uv_transform':dict(scale=[1.7,.8],offset=[.2,-.1],rotate=30.),'roughness_map':dict(rough='rough_b.exr'),
        'image_swap':dict(base='base_b.png'),'hdri_rotation':dict(rotation=[0,60,0]),'hdri_exposure':dict(exposure=1.),
        'hdri_swap':dict(sky='sky_b.exr'),'missing_image':dict(base='deliberately_missing.png')}.get(name,{}))
    base='/materials/openpbr/'
    def shader(path,identifier):
        obj=UsdShade.Shader(stage.GetPrimAtPath(base+path));require(obj.GetIdAttr().Get()==identifier,name+': native shader '+path);return obj
    surface=shader('surface','ND_open_pbr_surface_surfaceshader')
    image=shader('base_image','ND_image_color3');rough=shader('roughness_image','ND_image_float')
    uv=shader('uv0','ND_texcoord_vector2');place=shader('uv_placement','ND_place2d_vector2')
    for mesh in ('red','green'):
        bound=UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/World/'+mesh)).ComputeBoundMaterial('full')[0]
        require(str(bound.GetPath())=='/materials/openpbr',name+': material binding '+mesh)
        prim=stage.GetPrimAtPath('/World/'+mesh)
        require([tuple(v) for v in prim.GetAttribute('primvars:st').Get()]==[(0,0),(1,0),(1,1),(0,1)],name+': authored UVs')
    for node,slot in ((image,'base_color'),(rough,'specular_roughness')):
        require(list(map(str,surface.GetInput(slot).GetAttr().GetConnections()))==[str(node.GetPath())+'.outputs:out'],name+': surface texture connection')
        require(list(map(str,node.GetInput('texcoord').GetAttr().GetConnections()))==[base+'uv_placement.outputs:out'],name+': image UV connection')
    require(list(map(str,place.GetInput('texcoord').GetAttr().GetConnections()))==[base+'uv0.outputs:out'],name+': UV placement connection')
    for node,key,space,record in ((image,'base','srgb_texture','image_file'),(rough,'rough','Raw','roughness_file')):
        file=node.GetInput('file').Get();require(Path(file.path).name==want[key] and Path(file.path).resolve()==Path(controls[record]).resolve(),name+': image file')
        require(node.GetInput('file').GetAttr().GetMetadata('colorSpace')==space,name+': color space metadata')
    for key,default in (('scale',[1,1]),('offset',[0,0]),('rotate',0.)):
        value=place.GetInput(key).Get();value=default if value is None else value
        require(np.max(abs(np.asarray(value)-want[key]))<2e-6,name+': placement '+key)
    dome=stage.GetPrimAtPath('/Lights/environment');require(dome.GetTypeName()=='DomeLight_1',name+': native dome schema')
    require(Path(dome.GetAttribute('inputs:texture:file').Get().path).name==want['sky'],name+': HDRI file')
    require(dome.GetAttribute('poleAxis').Get()=='Y' and dome.GetAttribute('inputs:texture:format').Get()=='latlong',name+': dome mapping')
    require(np.max(abs(np.asarray(dome.GetAttribute('xformOp:rotateXYZ').Get())-want['rotation']))<2e-6,name+': dome rotation')
    require(abs(dome.GetAttribute('inputs:exposure').Get()-want['exposure'])<2e-6,name+': dome exposure')
    return want


def main():
    result_path=Path(sys.argv[1]).resolve();replay_path=Path(sys.argv[2]).resolve()
    result=json.loads(result_path.read_text());replay=json.loads(replay_path.read_text());out=result_path.parent
    limits=json.loads((HERE/'judge_contract.json').read_text());failures=[];evidence=[];hashes={str(p):sha(p) for p in HERE.iterdir() if p.is_file()}
    def require(okay,message):
        if not okay:failures.append(message)
    require(result['status']=='passed' and result.get('all_requested_workflow_checks'),'GUI workflow incomplete')
    require(replay['status']=='rendered' and replay['result_sha256']==sha(result_path),'Replay input mismatch')
    require(replay['scripts_unchanged'] and all(sha(Path(p))==h for p,h in replay['scripts'].items()),'Replay judge/contract/helpers changed')
    frozen=HERE/'freeze.json';freeze=json.loads(frozen.read_text())
    require(all(sha(HERE/p)==h for p,h in freeze['files'].items()),'Pre-GUI acceptance files changed')
    audit=json.loads((out/'process_audit.json').read_text());require(audit['owned_child_exited'] and audit['preexisting_unchanged'],'Process isolation/exit failed')
    manifest_path=Path(result['manifest']);manifest=json.loads(manifest_path.read_text());payload=Path(manifest['installation_root'])
    require(sha(manifest_path)==result['manifest_sha256']==replay['manifest_sha256'],'Installed manifest changed')
    for name,digest in manifest['files'].items():require(sha(payload/name)==digest,'Installed file changed: '+name)
    require(Path(result['loaded_delegate']).resolve()==(payload/'renderer/hdRT.dll').resolve(),'Wrong loaded DLL')
    for report in result['support_reports']:require(sha(report['profile_path'])==report['profile_sha256'],'Support profile changed')
    log=out/'stderr.log';raw=log.read_text(errors='replace')
    require('HoudiniRT: validation layer active' in raw,'Vulkan validation not active')
    require(not any(x in raw.casefold() for x in ('vuid-','validation error','validation warning','vk_error')),'Vulkan message')
    events,ack=workflow(result,log,limits,require)
    for phase in ('image','dome'):
        observation=result['native_control_observations'][phase]
        require(sha(observation['path'])==observation['sha256'],phase+': CUA marker changed')
        marker=json.loads(Path(observation['path']).read_text());require(marker==observation['observation'] and marker['observed'] is True and marker['phase']==phase,phase+': no native control observation')
    for capture in result['images'].values():
        for asset in capture['asset_files'].values():require(sha(asset['path'])==asset['sha256'],'GUI asset changed')
        context=capture['native_color_context'];require(sha(context['path'])==context['sha256'],'Native OCIO config changed')
        if capture['missing_asset_expected']:require(not Path(capture['native_texture_controls']['image_file']).exists(),'Deliberately missing file unexpectedly exists')
    require(set(result['images'])==set(limits['capture_names']),'Wrong image set')
    renders={r['name']:r for r in replay['renders']};require(set(renders)==set(limits['capture_names']),'Wrong replay set')
    images={}
    for name,capture in result['images'].items():
        path=Path(capture['path']);stage_path=Path(capture['stage_path']);r=renders[name]
        require(sha(path)==capture['sha256'] and sha(stage_path)==capture['stage_sha256']==r['stage_sha256'],name+': GUI image/stage changed')
        require(sha(r['image'])==r['image_sha256'] and sha(r['log'])==r['log_sha256'],name+': replay artifact changed')
        require(json.loads(Path(r['config']).read_text())==capture['child_config'],name+': replay config mismatch')
        stage=Usd.Stage.Open(str(stage_path));settings=stage.GetPrimAtPath('/Render/settings')
        actual={key:settings.GetAttribute('houdinirt:'+key).Get() for key in ('spp','bounces','seed')}
        require(actual==capture['effective_render_settings']==dict(spp=limits['sample_budget'],bounces=limits['bounces'],seed=limits['seed']),name+': settings mismatch')
        require(capture['active_camera']=='/Camera' and list(map(str,settings.GetRelationship('camera').GetTargets()))==['/Camera'],name+': different camera')
        materials=scene_controls(stage,name,capture['native_texture_controls'],require)
        a=pixels(path);b=pixels(r['image']);images[name]=a
        require(a.shape==b.shape and a.shape[2]==4,name+': raster mismatch')
        require(np.isfinite(a).all() and np.isfinite(b).all(),name+': nonfinite pixels')
        require(list(settings.GetAttribute('resolution').Get())==capture['authored_resolution']==[a.shape[1],a.shape[0]],name+': authored/image resolution mismatch')
        alpha=float(np.max(abs(a[...,3]-b[...,3])))
        error=a[...,:3]-b[...,:3];am=a[...,:3].mean(axis=(0,1));bm=b[...,:3].mean(axis=(0,1))
        nrmse=float(np.sqrt(np.mean(error**2))/max(np.sqrt(np.mean(b[...,:3]**2)),1e-12))
        require(alpha<=limits['replay_alpha_max_abs'],name+': replay coverage mismatch')
        require(nrmse<=limits['replay_relative_rgb_rmse_max'],name+': replay spatial mismatch')
        require(np.all(abs(am-bm)<=limits['replay_channel_mean_relative_max']*abs(bm)+limits['replay_channel_mean_absolute_floor']),name+': replay mean mismatch')
        renderlog=Path(r['log']).read_text(errors='replace')
        require('HoudiniRT: validation layer active' in renderlog,name+': replay validation inactive')
        require(not any(x in renderlog.casefold() for x in ('vuid-','validation error','validation warning','vk_error')),name+': replay Vulkan message')
        require(re.search(r'present renderer=\S+ generation=\d+ scene=\d+ samples=256\b',renderlog) is not None,name+': replay incomplete')
        if name=='missing_image':require('/materials/openpbr' in renderlog and 'whole-material displayColor Lambert fallback' in renderlog,name+': no native fallback warning')
        else:require('material /materials/openpbr synced: OpenPBR 1' in renderlog,name+': no native OpenPBR ingress')
        hashes.update({str(p):sha(p) for p in (path,stage_path,Path(r['image']),Path(r['log']),Path(r['config']))})
        evidence.append(dict(name=name,materials=materials,shape=list(a.shape),alpha_max_abs=alpha,rgb_relative_rmse=nrmse,
                             gui_mean=am.tolist(),replay_mean=bm.tolist(),max_abs=float(np.max(abs(error)))))
    # Paired changes must affect pixels; this rejects stale images without fitting
    # their appearance to a different implementation's glossy model.
    changes={name:float(np.max(abs(images[name][...,:3]-images['baseline'][...,:3]))) for name in ('uv_transform','roughness_map','image_swap','hdri_rotation','hdri_exposure','hdri_swap','missing_image')}
    for name,delta in changes.items():require(delta>1e-5,name+': native constant produced no visible pixel change')
    restored=float(np.max(abs(images['restored']-images['baseline'])))
    require(restored<=limits['restored_image_max_abs'],'Restoring native image/dome controls did not restore baseline image')
    latencies=[r for r in result['latency_samples'] if r['kind']=='camera']
    require(len(latencies)==limits['camera_edit_count'],'Camera edit count')
    for p in (result_path,replay_path,manifest_path,log,out/'process_audit.json'):hashes[str(p)]=sha(p)
    answer=dict(status='passed' if not failures else 'failed',scope=limits['scope'],failures=failures,images=evidence,
                limits=limits,changed_parameter_pixel_deltas=changes,restored_image_max_abs=restored,native_event_count=len(events),active_pause_ack=ack,
                camera_latency_ms={key:float(np.percentile([r['edit_to_publication_observed_ms'] for r in latencies],n)) for key,n in (('p50',50),('p95',95))},
                installed_file_count=len(manifest['files']),files=hashes)
    (out/'independent_judge.json').write_text(json.dumps(answer,indent=2)+'\n');print(json.dumps(dict(status=answer['status'],failures=failures,images=len(evidence)),indent=2))
    return 0 if not failures else 16


if __name__=='__main__':raise SystemExit(main())
