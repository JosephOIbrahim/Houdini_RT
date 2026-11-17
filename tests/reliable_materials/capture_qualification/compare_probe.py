from pathlib import Path
import hashlib,json,sys
import numpy as np
import OpenImageIO as oiio
root=Path(sys.argv[1]).resolve();result=json.loads((root/'result.json').read_text());replay=json.loads((root/'replay-01/receipt.json').read_text())
contract=Path(__file__).resolve().parents[3]/'tests/reliable_materials/installed/judge_contract.json'
limits=json.loads(contract.read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):
    i=oiio.ImageInput.open(str(p));s=i.spec();names=list(s.channelnames);a=np.asarray(i.read_image('float'),dtype=np.float64);i.close();return a[...,[names.index(c) for c in ('R','G','B','A')]]
rows=[]
for name,capture in result['images'].items():
    r=next(x for x in replay['renders'] if x['name']==name)
    assert sha(capture['path'])==capture['sha256'] and sha(r['image'])==r['image_sha256'] and sha(capture['stage_path'])==r['stage_sha256']
    a=read(capture['path']);b=read(r['image']);assert a.shape==b.shape
    delta=a-b;alpha=float(np.max(abs(delta[:,:,3])));am=a[:,:,:3].mean(axis=(0,1));bm=b[:,:,:3].mean(axis=(0,1))
    nrmse=float(np.sqrt(np.mean(delta[:,:,:3]**2))/np.sqrt(np.mean(b[:,:,:3]**2)))
    mean_pass=bool(np.all(abs(am-bm)<=limits['replay_channel_mean_relative_max']*abs(bm)+limits['replay_channel_mean_absolute_floor']))
    rows.append(dict(name=name,shape=list(a.shape),alpha_max_abs=alpha,rgb_nrmse=nrmse,gui_mean=am.tolist(),replay_mean=bm.tolist(),max_abs=np.max(abs(delta),axis=(0,1)).tolist(),
                     passed=bool(mean_pass and alpha<=limits['replay_alpha_max_abs'] and nrmse<=limits['replay_relative_rgb_rmse_max'])))
    image=root/(name+'_difference.png');o=oiio.ImageOutput.create(str(image));assert o.open(str(image),oiio.ImageSpec(a.shape[1],a.shape[0],3,oiio.UINT8));assert o.write_image(np.ascontiguousarray(np.uint8(np.clip(abs(delta[:,:,:3])*3,0,1)**(1/2.2)*255)));o.close()
answer=dict(status='passed' if all(r['passed'] for r in rows) else 'failed',scope='Capture-driver diagnostic only; unchanged frozen image bounds',contract_sha256=sha(contract),rows=rows)
(root/'probe_comparison.json').write_text(json.dumps(answer,indent=2)+'\n');print(json.dumps(answer,indent=2))
