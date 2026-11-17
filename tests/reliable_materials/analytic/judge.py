"""Independent physical/reference/image/cost judge. Never imports renderer code."""
from pathlib import Path
import csv
import hashlib
import json
import math
import sys
import numpy as np


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    out=Path(sys.argv[1]).resolve();reference=out/'inputs/reference'
    rows=json.loads((out/'inputs/probe/selected.json').read_text())
    limits=json.loads((reference/'contract.json').read_text())
    cpu={r['name']:r for r in csv.DictReader((reference/'cpu.csv').open())}
    stats={(r['case'],int(r['seed'])):r for r in csv.DictReader((out/'stats.csv').open())}
    failures=[];evidence=[];hashes={};previous=None
    def require(okay,message):
        if not okay:failures.append(message)
    log=(out/'run.log').read_text(errors='replace');hashes['run.log']=sha(out/'run.log')
    require('validation=active' in log,'Validation was not active')
    require(not any(term in log for term in ('VUID-','Validation Error','Validation Warning','VK_ERROR')),'Vulkan messages')
    require((out/'teardown.txt').read_text()=='errors=0 warnings=0\n','Teardown validation')
    require(len(stats)==sum(len(r['seeds']) for r in rows),'Unexpected output row count')
    for index,row in enumerate(rows):
        name=row['name'];ref=cpu[name];images=[]
        mean_ref=np.array([float(ref['mean_'+c]) for c in 'rgb']);se_ref=np.array([float(ref['se_'+c]) for c in 'rgb'])
        f=np.array([float(ref['fcos_'+c]) for c in 'rgb'])/row['light'][2]
        reverse=np.array([float(ref['reverse_fcos_'+c]) for c in 'rgb'])/row['view'][2]
        reciprocal=np.abs(f-reverse)
        require(np.all(reciprocal<=limits['reciprocity_abs_floor']+limits['reciprocity_rel_floor']*np.maximum(abs(f),abs(reverse))),name+': reciprocity')
        require(int(ref['nonfinite'])==0 and int(ref['negative'])==0,name+': CPU invalid values')
        require(float(ref['weight_mean_rel'])<=limits['sampling_weight_mean_rel_max'],name+': CPU sample weight consistency')
        strict_pdf=row['material'][9]>=limits['strict_pdf_roughness_min'] and np.max(mean_ref)>1e-8
        if strict_pdf:require(float(ref['pdf_mean_rel'])<=limits['sampling_pdf_mean_rel_max'],name+': CPU sample PDF consistency')
        for seed in row['seeds']:
            stem=name+'_'+str(seed);path=out/(stem+'.rgba32f');hashes[path.name]=sha(path)
            image=np.fromfile(path,dtype='<f4').astype(np.float64).reshape(row['height'],row['width'],4)
            require(np.isfinite(image).all() and np.min(image)>=0,name+': invalid GPU pixels')
            require(np.max(abs(image[...,3]-1))<=limits['alpha_max_abs'],name+': coverage')
            images.append(image[...,:3]);s=stats[name,seed]
            for key in ('errors','warnings'):require(int(s[key])==0,name+': '+key)
            require(int(s['traced_samples'])==row['spp'] and int(s['trace_submits'])==math.ceil(row['spp']/32),name+': sample accounting')
            require(int(s['resolve_submits'])==1 and int(s['readback_count'])==2 and int(s['readback_bytes'])==row['width']*row['height']*80,name+': resolve/guide accounting')
            if index:
                require(all(int(s[k])==0 for k in ('blas_builds','blas_refits','tlas_builds','tlas_updates','geometry_bytes')),name+': material edit rebuilt geometry')
                changed=row['material']!=previous
                require((0<int(s['table_bytes'])<=256) if changed else int(s['table_bytes'])==0,name+': shading table update')
            guide_path=out/(stem+'.guides');hashes[guide_path.name]=sha(guide_path)
            guide=np.fromfile(guide_path,dtype='<f4').reshape(-1,16)
            expected_albedo=np.array(row['material'][:3])*row['material'][3]
            require(np.max(abs(guide[:,12:15]-expected_albedo))<=2e-6,name+': diagnostic base color')
            require(np.max(abs(guide[:,:3]-[0,0,1]))<=2e-6,name+': normal guide')
            fresh=out/(stem+'.fresh.rgba32f')
            if fresh.exists():
                hashes[fresh.name]=sha(fresh);require(fresh.read_bytes()==path.read_bytes(),name+': live/fresh difference')
        previous=row['material'];images=np.array(images);means=images.mean(axis=(1,2));mean=means.mean(axis=0)
        se=np.std(means,axis=0,ddof=1)/math.sqrt(len(means));combined=np.sqrt(se*se+se_ref*se_ref)
        if row['mode']=='distant':
            target=np.array([float(ref['fcos_'+c]) for c in 'rgb'])*[1.3,.7,2.1]
            bound=limits['distant_abs_floor']+limits['distant_rel_floor']*abs(target)
            require(np.all(abs(mean-target)<=bound),name+': distant directional reference')
        else:
            target=mean_ref
            bound=limits['mean_sigma']*combined+limits['cpu_gpu_mean_abs_floor']+limits['cpu_gpu_mean_rel_floor']*abs(target)
            ceiling=limits['cpu_gpu_mean_fixed_abs_ceiling']+limits['cpu_gpu_mean_fixed_rel_ceiling']*abs(target)
            require(np.all(abs(mean-target)<=bound),name+': statistical CPU transport reference')
            require(np.all(abs(mean-target)<=ceiling),name+': fixed CPU transport ceiling')
        if row['mode']=='furnace':
            require(np.all(mean<=1+limits['energy_upper_abs_floor']+limits['mean_sigma']*se),name+': generated energy')
            if row['expected'] is not None:
                floor=limits['lambert_abs_floor'] if name.startswith('lambert') else limits['furnace_white_abs_floor']
                require(np.all(abs(mean-row['expected'])<=floor+limits['mean_sigma']*se),name+': analytic furnace energy')
        evidence.append(dict(name=name,mean=mean.tolist(),se=se.tolist(),reference=target.tolist(),reference_se=se_ref.tolist(),
                             absolute_error=abs(mean-target).tolist(),bound=bound.tolist(),reciprocity_abs=reciprocal.tolist(),
                             cpu_pdf_mean_rel=float(ref['pdf_mean_rel']),strict_pdf_applied=bool(strict_pdf)))
    receipt=dict(status='passed' if not failures else 'failed',scope=limits['scope'],failures=failures,rows=evidence,limits=limits,files=hashes)
    (out/'judge.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(status=receipt['status'],rows=len(evidence),failures=failures),indent=2))
    return 0 if not failures else 16


if __name__=='__main__':raise SystemExit(main())
