from pathlib import Path
import hashlib,json
import numpy as np
import OpenImageIO as oiio
ROOT=Path(__file__).resolve().parent/'gui-karma-control-01'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):
    i=oiio.ImageInput.open(str(p));s=i.spec();names=list(s.channelnames);a=np.asarray(i.read_image('float'),dtype=np.float64);i.close()
    return a[...,[names.index(c) for c in ('R','G','B','A')]],dict(width=s.width,height=s.height,format=str(s.format),channels=names)
r=json.loads((ROOT/'result.json').read_text());cap=r['karma_capture'];ref=json.loads((ROOT/'karma-reference-01/receipt.json').read_text())
assert ref['status']=='rendered' and sha(cap['path'])==cap['sha256'] and sha(ref['image'])==ref['image_sha256']
a,aspec=read(cap['path']);b,bspec=read(ref['image']);assert a.shape==b.shape
mask=(b[:,:,3]>.01)&(b[:,:,3]<.99);both=(a[:,:,3]==1)&(b[:,:,3]==1)
pred=b[:,:,3]*(2-b[:,:,3]);diff=a-b
receipt=dict(status='diagnostic_complete',scope='Native Karma viewport flipbook versus exact-stage headless; stochastic coverage differs between renderer invocations, so no strict equality acceptance is claimed',gui_spec=aspec,reference_spec=bspec,
 gui_image_sha256=sha(cap['path']),reference_image_sha256=sha(ref['image']),stage_sha256=sha(cap['stage']),script_sha256=sha(__file__),
 alpha_max_abs=float(np.max(abs(diff[:,:,3]))),edge_pixels=int(mask.sum()),
 raw_alpha_edge_rmse=float(np.sqrt(np.mean(diff[:,:,3][mask]**2))),
 alpha_over_self_edge_rmse=float(np.sqrt(np.mean((a[:,:,3][mask]-pred[mask])**2))),
 gui_alpha_edge_mean=float(a[:,:,3][mask].mean()),headless_alpha_edge_mean=float(b[:,:,3][mask].mean()),
 alpha_over_self_edge_max=float(np.max(abs(a[:,:,3][mask]-pred[mask]))),
 interior_rgb_gui=np.mean(a[:,:,:3][both],axis=0).tolist(),interior_rgb_headless=np.mean(b[:,:,:3][both],axis=0).tolist(),
 gui_mean=a.mean(axis=(0,1)).tolist(),headless_mean=b.mean(axis=(0,1)).tolist())
samples=np.where(mask);receipt['edge_examples']=[dict(x=int(x),y=int(y),gui=float(a[y,x,3]),headless=float(b[y,x,3]),over=float(pred[y,x])) for y,x in zip(samples[0][::max(1,len(samples[0])//16)],samples[1][::max(1,len(samples[1])//16)])]
(ROOT/'karma-reference-01/comparison.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
