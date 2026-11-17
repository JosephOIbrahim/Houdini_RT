"""Declare fixtures and limits before GPU observations; writes no production files."""
from pathlib import Path
import argparse
import hashlib
import json
import math


def material(color=(.8,.8,.8), weight=1, metal=0, spec_color=(1,1,1), spec_weight=1, rough=.3, ior=1.5):
    return list(color)+[weight]+list(spec_color)+[spec_weight,metal,rough,ior,0]


def direction(cosine, phi=0):
    sine=math.sqrt(1-cosine*cosine)
    return [sine*math.cos(phi),sine*math.sin(phi),cosine]


def fixtures():
    rows=[]
    def add(name,model,cosine,mode='furnace',expected=None,wi=None,environment=0):
        rows.append(dict(name=name,material=model,normal=[0,0,1],view=direction(cosine),
                         light=wi or direction(.6,1.1),mode=mode,expected=expected,environment=environment,
                         width=24,height=24,spp=2048,seeds=[101,107,113],extent=.08,
                         cpu_samples=524288 if mode!='distant' else 0,cpu_seed=173+len(rows)*71))
    for metal in (0,1):
        for rough in (0,.1,.5,1):
            for cosine in (1,.7,.3,.1):
                add(f'white_m{metal}_r{rough}_v{cosine}',material(color=(1,1,1),metal=metal,rough=rough),cosine,expected=[1,1,1])
    for name,color,weight in [('black',(0,0,0),1),('grey',(.18,.18,.18),1),('color',(.15,.45,.8),1),('weighted',(.15,.45,.8),.35)]:
        for cosine in (1,.1):
            add(f'lambert_{name}_v{cosine}',material(color=color,weight=weight,spec_weight=0),cosine,expected=[x*weight for x in color])
    variants={
        'default':material(), 'metal_color':material(color=(.8,.3,.07),metal=1,rough=.3),
        'mixed':material(color=(.2,.6,.8),metal=.45,rough=.5),
        'spec_tint':material(color=(.3,.2,.1),spec_color=(.2,.8,.5),rough=.3),
        'spec_weight':material(spec_weight=.25,rough=.4),
        'ior_one':material(ior=1), 'ior_high':material(ior=2.5),
        'smooth_metal':material(color=(.8,.3,.07),metal=1,rough=0),
        'smooth_dielectric':material(rough=0)}
    for name,model in variants.items():
        for cosine in (1,.3): add(f'absorbing_{name}_v{cosine}',model,cosine)
    for name in ('default','metal_color','mixed','spec_tint','spec_weight','ior_high'):
        for cosine in (1,.3):
            for mu,phi in ((.9,.4),(.4,2.4)):
                add(f'distant_{name}_v{cosine}_l{mu}',variants[name],cosine,'distant',wi=direction(mu,phi))
    for name in ('default','metal_color','mixed','smooth_metal','smooth_dielectric'):
        for env,label in ((1,'rect'),(2,'disk'),(3,'sphere')):
            add(f'{label}_{name}',variants[name],1,'finite',environment=env)
    for name in ('smooth_metal','smooth_dielectric'):
        for cosine in (.3,.1):
            for env,label in ((1,'rect'),(2,'disk'),(3,'sphere')):
                add(f'{label}_{name}_grazing{cosine}',variants[name],cosine,'finite',environment=env)
    return rows


CONTRACT={
    'schema':1,'scope':'Supplementary integrated opaque OpenPBR, not a sealed exam',
    'mean_sigma':5,'furnace_white_abs_floor':.005,'lambert_abs_floor':2e-5,
    'cpu_gpu_mean_abs_floor':.001,'cpu_gpu_mean_rel_floor':.01,
    'cpu_gpu_mean_fixed_abs_ceiling':.015,'cpu_gpu_mean_fixed_rel_ceiling':.03,
    'distant_abs_floor':5e-5,'distant_rel_floor':.005,
    'reciprocity_abs_floor':2e-6,'reciprocity_rel_floor':2e-4,
    'sampling_weight_mean_rel_max':.001,'sampling_pdf_mean_rel_max':.001,
    'strict_pdf_roughness_min':.01,
    'pdf_endpoint_scope':'Pinned Adobe clamps authored roughness0 to0.001 and disables delta lobes. CPU preflight observed floating-point PDF discrepancies for these narrow lobes. Strict PDF consistency applies to roughness>=0.01 and nonzero energy; endpoint discrepancy is recorded, while integrated energy/finite-light/reference image requirements still apply.',
    'energy_upper_abs_floor':.005,'alpha_max_abs':1e-6,'fresh_max_abs':0,
    'cpu_reference':'Pinned Apache Adobe implementation, array LUTs, independent RNG and transport. Shared-model agreement is not independent physical proof.',
    'finite_reference':'CPU BSDF sampling integrates radiance3 over independent rectangle/disk/sphere ray intersections and the continuous orthographic raster footprint. Each finite emitter is centered two units along the mirror direction and faces the receiver; grazing roughness0 cases exercise overlapping light/BSDF proposals.',
    'statistics':'Per-channel mean and SE over independent CPU samples and over the three independently seeded GPU image means; invalid samples are zero. Fixed ceilings apply as well as5SE+floor.',
    'energy_scope':'Unemissive furnace only; approved colors, weights, metalness and roughness in[0,1], IOR[1,3], zero diffuse roughness.'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    rows=fixtures();(args.output/'fixtures.json').write_text(json.dumps(rows,indent=2)+'\n')
    (args.output/'contract.json').write_text(json.dumps(CONTRACT,indent=2)+'\n')
    lines=[]
    for row in rows:
        numbers=row['material']+row['normal']+row['view']+row['light']
        lines.append(' '.join(map(str,[row['name'],row['cpu_samples'],row['cpu_seed'],row['environment']]+numbers)))
    (args.output/'cpu_jobs.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'rows':len(rows),'modes':{k:sum(r['mode']==k for r in rows) for k in ('furnace','distant','finite')}}))


if __name__=='__main__':main()
