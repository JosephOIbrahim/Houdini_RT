"""Independent artifact judge; imports no renderer code and starts no GPU work."""
import argparse
import array
import csv
import json
import math
from pathlib import Path
import re

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('artifacts',type=Path)
args=parser.parse_args(); out=args.artifacts.resolve()
rows=list(csv.DictReader((out/'stats.csv').open()))
cuts=(1,7,33,257)
names={'batch%d_%d'%(b,n) for b in (1,2,8,32) for n in cuts}
names.update(('invalid_input_recovery','discard_reset','resized','resize_back','color_edit',
              'dome_edit','camera_edit','seed_edit','empty','after_empty'))
errors=[]; details={}; images={}
def check(condition,message):
    if not condition: errors.append(message)
check(len(rows)==len(names) and {r['case'] for r in rows}==names,'missing or duplicate cases')
for row in rows:
    name=row['case']; width=int(row['width']); height=int(row['height']); pixels=width*height
    data=array.array('f'); data.frombytes((out/(name+'.f32')).read_bytes())
    check(len(data)==pixels*4,name+' pixel dimensions')
    check(all(math.isfinite(v) for v in data),name+' nonfinite pixels')
    images[name]=data
    samples=int(row['samples']); batch=int(row['batch'])
    match=re.fullmatch(r'batch(\d+)_(\d+)',name)
    if match:
        stops=cuts[:cuts.index(samples)+1]
        spans=[stops[0]]+[b-a for a,b in zip(stops,stops[1:])]
        submits=sum(math.ceil(n/batch) for n in spans); resolves=len(stops)
    else: submits=math.ceil(samples/batch); resolves=1
    if name=='empty': submits=0
    expected={'trace_submits':submits,'trace_dispatches':submits,
        'traced_samples':0 if name=='empty' else samples,
        'resolve_submits':resolves,'resolve_dispatches':resolves,'readback_count':resolves,
        'readback_bytes':resolves*pixels*16,'clear_submits':1,'compatibility_readback_submits':0,
        'accum_device_local':1,'resolve_device_local':1,'errors':0,'warnings':0}
    for key,wanted in expected.items(): check(int(row[key])==wanted,f'{name} {key}: {row[key]} != {wanted}')
    for key in ('accum_bytes','resolve_bytes','readback_allocated'):
        check(int(row[key])>=pixels*16,name+' undersized '+key)
    for key in ('last_batch_seconds','max_batch_seconds','last_resolve_seconds','total_resolve_seconds'):
        check(math.isfinite(float(row[key])) and float(row[key])>=0,name+' invalid timing '+key)
    check(float(row['max_batch_seconds'])>=float(row['last_batch_seconds']),name+' max batch timing')
    check(all(-1e-7<=data[i+3]<=1.000001 for i in range(0,len(data),4)),name+' invalid alpha')
    if name=='empty':
        check(not any(data),name+' nonzero transparent image'); details[name]={'max_abs':max(map(abs,data))}; continue
    albedo=(.6,.1,.3) if name=='color_edit' else (.2,.5,.8)
    dome=(1.4,2.2,2.8) if name=='dome_edit' else (.7,1.1,1.4)
    value=[a*d for a,d in zip(albedo,dome)]
    worst=max(abs(data[i+c]-value[c]*data[i+3]) for i in range(0,len(data),4) for c in range(3))
    check(worst<8e-6,name+' analytic albedo*dome*coverage mismatch '+str(worst))
    covered=sum(data[i+3]>.999 for i in range(0,len(data),4))
    check(covered>100 and covered<pixels//2,name+' expected surface/background coverage')
    details[name]={'analytic_max_abs':worst,'covered_pixels':covered,'trace_submits':submits,
                   'readback_count':resolves,'readback_bytes':int(row['readback_bytes'])}
for samples in cuts:
    reference=images['batch1_'+str(samples)]
    for batch in (2,8,32):
        name='batch%d_%d'%(batch,samples)
        worst=max(abs(a-b) for a,b in zip(reference,images[name]))
        check(worst<=1e-6,name+' batch-size changed pixels '+str(worst))
        details[name]['batch1_max_abs']=worst
reference=images['batch1_257']
for name in ('invalid_input_recovery','discard_reset','resize_back','after_empty'):
    worst=max(abs(a-b) for a,b in zip(reference,images[name]))
    check(worst<=1e-6,name+' reset/recovery retained stale pixels '+str(worst))
def center(data,width):
    total=sum(data[3::4]); return sum((i//4%width)*data[i+3] for i in range(0,len(data),4))/total
shift=center(images['camera_edit'],128)-center(reference,128)
check(abs(shift-(-128*.25/3))<.2,'camera edit centroid mismatch '+str(shift))
check(sum(abs(a-b)>1e-7 for a,b in zip(reference,images['seed_edit']))>10,'seed edit did not alter edge samples')
log=(out/'run.log').read_text(encoding='utf-8-sig',errors='replace')
check('validation=active' in log,'validation not active')
check(not re.search(r'VUID|Validation Error|VK_ERROR',log),'Vulkan diagnostic in log')
check((out/'teardown.txt').read_text().strip()=='errors=0 warnings=0','teardown validation')
result={'passed':not errors,'cases':len(rows),'errors':errors,'details':details,
        'scope':'Supplementary analytic GPU batching/resolve checks, not a sealed s6 exam'}
(out/'judge.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'passed':result['passed'],'cases':result['cases'],'errors':errors},indent=2))
raise SystemExit(0 if not errors else 16)
