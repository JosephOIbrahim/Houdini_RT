"""Exact-scene CPU transport comparison; shared BSDF model, independent integration."""
from pathlib import Path
import csv
import hashlib
import json
import math
import numpy as np


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def check(out,receipt,groups,limits,require):
    folder=out/'transport';manifest_path=folder/'reference_manifest.json'
    require(sha(manifest_path)==receipt['transport_reference_before_render_sha256'],'CPU reference manifest changed')
    reference=json.loads(manifest_path.read_text())
    require(reference['fixture_manifest_sha256']==receipt['fixture_manifest_before_render_sha256'],'CPU reference used different fixtures')
    require(reference['compile_inputs_unchanged'],'CPU compile inputs changed')
    for name,digest in reference['files'].items():require(sha(folder/name)==digest,'CPU reference artifact changed: '+name)
    for name,digest in reference['compile_input_hashes'].items():require(sha(Path(name))==digest,'CPU compile input changed: '+name)
    rows={}
    with (folder/'reference.csv').open() as stream:
        for row in csv.DictReader(stream):rows.setdefault(row['name'],[]).append(row)
    results=[]
    for case in reference['cases']:
        name=case['name'];selected=case['pixel_indices'];table=rows[name]
        require(len(table)==256 and len(selected)>=128,name+': fixed reference domain count')
        points=reference['points']
        require(all(int(r['row'])==p['row'] and int(r['col'])==p['col'] for r,p in zip(table,points)),name+': reference point ordering')
        a=np.array(groups[name]['candidate'])
        a=np.array([a[:,points[i]['row'],points[i]['col'],:] for i in selected]).transpose(1,0,2)
        ref32=np.array([[float(table[i][c]) for c in ('r32','g32','b32')] for i in selected])
        ref64=np.array([[float(table[i][c]) for c in ('r64','g64','b64')] for i in selected])
        ref_se=np.array([[float(table[i][c]) for c in ('se_r','se_g','se_b')] for i in selected])
        require(np.isfinite(ref64).all() and np.min(ref64)>=0,name+': invalid CPU reference')
        convergence=float(np.max(abs(ref64-ref32)))
        require(convergence<=limits['transport_quadrature_max_abs'],name+': CPU quadrature convergence')
        am=a.mean(axis=1);mean=am.mean(axis=0);expected=ref64.mean(axis=0)
        gpu_se=np.std(am,axis=0,ddof=1)/math.sqrt(3)
        # Roughness-zero uses common random samples across pixels. Averaging SEs
        # conservatively retains full correlation rather than assuming independence.
        cpu_se=ref_se.mean(axis=0);se=np.sqrt(gpu_se**2+cpu_se**2)
        quad_error=np.abs(ref64-ref32).mean(axis=0)
        error=abs(mean-expected)
        bound=limits['mean_standard_error_multiplier']*se+limits['mean_relative_floor']*abs(expected)+limits['absolute_floor']+quad_error
        fixed=limits['mean_relative_max']*abs(expected)+limits['absolute_floor']
        nrmse=float(np.sqrt(np.mean((a.mean(axis=0)-ref64)**2))/max(np.sqrt(np.mean(ref64**2)),1e-12))
        require(np.all(error<=bound),name+': exact-USD CPU transport statistical mean')
        require(np.all(error<=fixed),name+': exact-USD CPU transport fixed mean')
        require(nrmse<=limits['rgb_relative_rmse_max'],name+': exact-USD CPU transport spatial error')
        results.append(dict(name=name,kind='shared-model CPU / independent transport',pixels=len(selected),candidate_mean=mean.tolist(),
                            reference_mean=expected.tolist(),relative_mean_error=(error/np.maximum(abs(expected),1e-12)).tolist(),
                            gpu_standard_error=gpu_se.tolist(),cpu_standard_error=cpu_se.tolist(),combined_standard_error=se.tolist(),
                            absolute_statistical_bound=bound.tolist(),absolute_fixed_bound=fixed.tolist(),relative_rmse=nrmse,
                            quadrature_max_abs=convergence,reference_samples=int(table[0]['samples'])))
    require(len(results)==12,'Expected12 exact-USD transport cases')
    return results
