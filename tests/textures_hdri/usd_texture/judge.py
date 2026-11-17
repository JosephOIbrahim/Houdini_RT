"""Actual FLOAT channels versus frozen independent guides and paired constants."""
import hashlib,json,re,sys
from pathlib import Path
import numpy as np
import OpenImageIO as oiio
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):
    image=oiio.ImageInput.open(str(path));assert image,oiio.geterror()
    channels={};headers=[];part=0
    while image.seek_subimage(part,0):
        spec=image.spec();formats=list(spec.channelformats) or [spec.format]*spec.nchannels
        pixels=np.asarray(image.read_image('float'))
        headers.append(dict(part=part,names=list(spec.channelnames),formats=list(map(str,formats)),
                            width=spec.width,height=spec.height,x=spec.x,y=spec.y))
        for i,name in enumerate(spec.channelnames):
            assert name not in channels,'duplicate EXR channel '+name
            channels[name]=(pixels[:,:,i],str(formats[i]))
        part+=1
    image.close()
    channels={('beauty.'+name if name in 'RGBA' and len(name)==1 else name):value for name,value in channels.items()}
    return channels,headers
def main():
    out=Path(sys.argv[1]).resolve();run=json.loads((out/'receipt.json').read_text())
    manifest_path=out/'fixtures/manifest.json';manifest=json.loads(manifest_path.read_text());contract=manifest['contract']
    failures=[];rows=[];beauties={};renders={r['case']:r for r in run['renders']}
    def need(okay,message):
        if not okay:failures.append(message)
    need(sha(manifest_path)==run['fixture_manifest_sha256'],'fixture manifest changed')
    need(len(manifest['cases'])==42 and len(renders)==42,'case census')
    expected_channels={'beauty.'+c for c in 'RGBA'}|{'albedo.'+c for c in 'RGB'}
    for case in manifest['cases']:
        name=case['name'];render=renders.get(name);row=dict(case=name);rows.append(row)
        need(bool(render),name+': missing render receipt')
        if not render:continue
        stage=Path(case['stage']);expected_path=Path(case['expected']);image=Path(case['image'])
        need(sha(stage)==case['stage_sha256'],name+': USD changed')
        need(sha(expected_path)==case['expected_sha256'],name+': expected guide changed')
        if case.get('source_asset_sha256'):
            need(sha(case['source_asset'])==case['source_asset_sha256'],name+': encoded asset changed')
        log=Path(render['log']).read_text(errors='replace')
        need(sha(render['log'])==render['log_sha256'],name+': render log changed')
        need(render['exit_code']==0 and image.is_file(),name+': failed render/missing image')
        need('HoudiniRT: validation layer active' in log,name+': validation inactive')
        need(not any(t in log.casefold() for t in ('vuid-','validation warning','validation error','vk_error','houdinirt: error:')),name+': native error')
        need(bool(re.search(r'\[debug\] present renderer=.*samples=128\b',log)),name+': no complete current publication')
        if case['negative']:
            need('fallback' in log.lower() and ('/World/mesh' in log or '/materials/openpbr' in log),name+': missing path-specific fallback')
        else:
            need('synced: OpenPBR 1' in log,name+': material was not accepted')
            need('fallback' not in log.lower(),name+': unexpected fallback')
        if not image.is_file():continue
        need(sha(image)==render['image_sha256'],name+': rendered image changed')
        channels,headers=read(image);row.update(headers=headers,image_sha256=sha(image))
        need(set(channels)==expected_channels,name+': channel schema '+repr(sorted(channels)))
        if not expected_channels<=set(channels):continue
        for channel,(values,kind) in channels.items():
            need(kind in ('float','float32'),name+': not FLOAT32 '+channel)
            need(values.shape==tuple(reversed(contract['resolution'])) and np.isfinite(values).all(),name+': nonfinite/wrong raster '+channel)
        guide=np.stack([channels['albedo.'+c][0] for c in 'RGB'],axis=-1)
        target=np.asarray(json.loads(expected_path.read_text()),dtype=np.float64)
        maximum=float(np.max(np.abs(guide-target)));row['guide_max_abs']=maximum
        need(maximum<=contract['guide_max_abs'],name+': guide maxabs '+str(maximum))
        beauties[name]=np.stack([channels['beauty.'+c][0] for c in 'RGBA'],axis=-1)
    pairs=[]
    for case in manifest['cases']:
        if not case.get('pair'):continue
        name=case['name'];control=case['pair']
        if any(key not in beauties for key in (name,control,'control_baseline')):
            failures.append(name+': paired beauty unavailable');continue
        maximum=float(np.max(np.abs(beauties[name]-beauties[control])))
        changed=float(np.max(np.abs(beauties[control][:,:,:3]-beauties['control_baseline'][:,:,:3])))
        need(maximum<=contract['paired_constant_max_abs'],name+': paired constant maxabs '+str(maximum))
        need(changed>contract['each_slot_control_min_difference_from_baseline'],name+': setup did not exercise this input')
        pairs.append(dict(texture=name,constant=control,max_abs=maximum,control_difference_from_baseline=changed))
    need(len(pairs)==7,'seven independent slot comparisons required')
    result=dict(status='passed' if not failures else 'failed',scope=manifest['scope'],failures=failures,
                cases=rows,paired_slots=pairs,payload=run['payload'],payload_hashes=run['payload_hashes'],
                fixture_manifest_sha256=sha(manifest_path),judge_sha256=sha(__file__),contract=contract)
    (out/'judge.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status=result['status'],cases=len(rows),paired_slots=len(pairs),failures=failures),indent=2))
    return 0 if not failures else 16
if __name__=='__main__':raise SystemExit(main())
