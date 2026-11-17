"""Analytic geometry and typed EXR checks, independent of native output formulas."""
import hashlib
import json
from pathlib import Path
import re
import sys
import numpy as np
import OpenImageIO as oiio


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    image=oiio.ImageInput.open(str(path))
    if image is None: raise RuntimeError(oiio.geterror())
    channels={}; metadata=[]; part=0
    while image.seek_subimage(part,0):
        spec=image.spec(); names=list(spec.channelnames)
        formats=list(spec.channelformats) or [spec.format]*spec.nchannels
        pixels=np.asarray(image.read_image('double')).reshape(spec.height,spec.width,spec.nchannels)
        metadata.append({'part':part,'width':spec.width,'height':spec.height,'x':spec.x,'y':spec.y,
                         'names':names,'formats':[str(f) for f in formats]})
        for i,name in enumerate(names):
            if name in channels: raise RuntimeError('duplicate channel '+name)
            # OIIO normalizes integer->floating conversions to [0,1]. Read native
            # uint channels directly so IDs and the 0xffffffff miss retain bits.
            data=pixels[:,:,i]
            if str(formats[i]) in ('uint','uint32'):
                data=np.asarray(image.read_image(part,0,i,i+1,'uint')).reshape(spec.height,spec.width).astype(np.float64)
            channels[name]=(data,str(formats[i]))
        part+=1
    image.close(); return channels,metadata


def main():
    out=Path(sys.argv[1]).resolve(); run=json.loads((out/'receipt.json').read_text())
    manifest=out/'fixtures/manifest.json'; failures=[]; rows=[]
    def need(okay,detail):
        if not okay: failures.append(detail)
    need(sha(manifest)==run['fixture_manifest_sha256'],'fixture manifest changed')
    renders={item['case']:item for item in run['renders']}
    for case in json.loads(manifest.read_text())['cases']:
        label=case['name']; render=renders[label]; stage=Path(case['stage']); image=Path(case['image'])
        need(sha(stage)==case['stage_sha256'],label+': stage changed')
        log=Path(render['log']).read_text(errors='replace')
        need(not any(token in log for token in ('VUID-','Validation Error','VK_ERROR')),label+': native validation error')
        if case['invalid']=='format':
            channels,metadata=read(image)
            need(render['exit_code']==0 and '[debug] present renderer=' in log,label+': host-coerced format failed')
            need(channels.get('normal.normal',(None,None))[1]=='uint',label+': missing coerced uint output')
            rows.append({'case':label,'scope':'Husk coerces native normal descriptor to uint for EXR; not a rejected native binding',
                         'exit_code':render['exit_code'],'parts':metadata,'image_sha256':sha(image)})
            continue
        if case['invalid']=='name':
            explicit_error=bool(re.search(r'(?i)(error|invalid|unsupported)',log))
            published='[debug] present renderer=' in log
            need(render['exit_code']!=0,label+': husk reported success for unsupported binding')
            need(explicit_error,label+': no rejection diagnostic')
            need(not published,label+': unsupported binding published a frame')
            rows.append({'case':label,'exit_code':render['exit_code'],'explicit_error':explicit_error,
                         'native_frame_published':published,'image_created':image.is_file()})
            continue
        need(render['exit_code']==0,label+': husk failed')
        need('HoudiniRT: validation layer active' in log,label+': validation not active')
        need('HoudiniRT: ERROR:' not in log,label+': native render error')
        if not image.is_file(): failures.append(label+': missing image'); continue
        channels,metadata=read(image); row={'case':label,'image_sha256':sha(image),'parts':metadata,'max_errors':{}}
        rows.append(row)
        # H22 gives standard beauty/Pz special names; scalar AOVs retain both
        # the driver layer and scalar component name. Preserve raw metadata.
        names={'R':'beauty.R','G':'beauty.G','B':'beauty.B','A':'beauty.A',
               'Pz.cameraDepth':'cameraDepth','depth.depth':'depth',
               'primId.primId':'primId','elementId.elementId':'elementId','instanceId.instanceId':'instanceId'}
        channels={names.get(name,name):value for name,value in channels.items()}
        expected_names=['beauty.'+n for n in 'RGBA']+['normal.'+n for n in 'xyz']+['Neye.'+n for n in 'xyz']
        expected_names+=['depth','cameraDepth','primId','elementId','instanceId']+['albedo.'+n for n in 'RGB']
        need(set(channels)==set(expected_names),label+': unexpected channels '+repr(sorted(channels)))
        if not set(expected_names)<=set(channels): continue
        for name,(data,fmt) in channels.items():
            need(data.shape==(48,48) and bool(np.isfinite(data).all()),label+': shape/nonfinite '+name)
            expected_type=('uint','uint32','int','int32') if name in ('primId','elementId','instanceId') else ('float','float32')
            need(fmt in expected_type,label+': wrong stored type '+name+' '+fmt)
        xx,yy=np.meshgrid(-1.5+(np.arange(48)+.5)*3/48,1.5-(np.arange(48)+.5)*3/48)
        left=(xx>-1)&(xx<-.25)&(abs(yy)<.75); right=(xx>.25)&(xx<1)&(abs(yy)<.75)
        hit=left|right; tilt=np.where(left,.6,-.6)
        normal=[np.where(hit,tilt,-1),np.where(hit,0.,-1),np.where(hit,.8,-1)]
        world=[np.where(hit,.8,-1),normal[1],np.where(hit,-tilt,-1)] if case['rotated'] else normal
        expected={'beauty.R':np.zeros_like(xx),'beauty.G':np.zeros_like(xx),'beauty.B':np.zeros_like(xx),
                  'beauty.A':hit.astype(float),'depth':np.where(hit,2.9/9.9,1),
                  'cameraDepth':np.where(hit,2.9,0),'elementId':np.where(hit,np.where(left,0,1),-1),
                  'instanceId':np.full_like(xx,-1)}
        expected.update({'normal.'+key:world[i] for i,key in enumerate('xyz')})
        expected.update({'Neye.'+key:normal[i] for i,key in enumerate('xyz')})
        expected.update({'albedo.'+key:np.where(hit,value,0) for key,value in zip('RGB',(.2,.4,.6))})
        for key in ('primId','elementId','instanceId'):
            values,fmt=channels[key]
            if fmt in ('uint','uint32'): values=np.where(values>=2**31,values-2**32,values)
            channels[key]=(values,fmt)
        primitive=channels['primId'][0]; assigned=np.unique(primitive[hit])
        need(len(assigned)==1 and assigned[0]>=0 and float(assigned[0]).is_integer(),label+': invalid Hydra primitive ID')
        need(bool(np.all(primitive[~hit]==-1)),label+': primitive-ID misses not cleared')
        row['assigned_primitive_ids']=assigned.tolist()
        for name,target in expected.items():
            error=float(np.max(np.abs(channels[name][0]-target))); row['max_errors'][name]=error
            need(error<=4e-5,label+': value mismatch '+name+' '+str(error))
    result={'status':'passed' if not failures else 'failed','scope':run['scope'],'failures':failures,
            'cases':rows,'payload':run['payload'],'payload_hashes':run['payload_hashes'],
            'fixture_manifest_sha256':sha(manifest),'judge_sha256':sha(Path(__file__))}
    (out/'judge.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'cases':len(rows),'failures':failures},indent=2))
    return 0 if not failures else 16


if __name__=='__main__': raise SystemExit(main())
