"""Judge GPU probe artifacts; never imports or executes the renderer."""
import argparse, array, csv, json, math
from pathlib import Path
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('artifact_directory', type=Path, help='directory containing stats.csv and RGBA .f32 files')
parser.add_argument('--log', type=Path, help='probe stdout/stderr log; defaults to artifact_directory/run.log')
args = parser.parse_args()
out = args.artifact_directory.resolve()
rows = list(csv.DictReader((out / 'stats.csv').open()))
expected = {
    'initial': (1,0,1,0,120,48), 'unchanged': (0,0,0,0,0,0),
    'transform': (0,0,1,0,0,0), 'color': (0,0,0,0,0,48),
    'hidden': (0,0,1,0,0,0), 'visible': (0,0,1,0,0,0),
    'points': (1,0,1,0,96,0), 'topology': (1,0,1,0,168,48),
    'mirror_nonuniform': (0,0,1,0,0,0), 'insert': (1,0,1,0,120,96),
    'remove': (0,0,1,0,0,48), 'readd': (1,0,1,0,168,48),
    'empty': (0,0,0,0,0,96), 'empty_mesh': (0,0,0,0,0,48),
    'after_empty': (1,0,1,0,120,48), 'after_failure': (1,0,1,0,120,48),
    'zero_scale': (0,0,0,0,0,0), 'zero_scale_unchanged': (0,0,0,0,0,0),
    'zero_scale_hidden': (0,0,0,0,0,0), 'zero_scale_recovery': (0,0,1,0,0,0),
    'infinite_translation': (0,0,0,0,0,0), 'nan_hidden': (0,0,0,0,0,0),
    'nan_hidden_unchanged': (0,0,0,0,0,0), 'nonfinite_recovery': (0,0,1,0,0,0),
    'teardown': (0,0,0,0,0,0),
}
keys = ['blas_builds','blas_refits','tlas_builds','tlas_updates','geometry_bytes','table_bytes']
errors, details, images = [], {}, {}
def check(ok, message):
    if not ok: errors.append(message)
def image(name):
    data = array.array('f'); data.frombytes((out/name).read_bytes())
    check(len(data)==128*96*4, name+' image dimensions')
    check(all(math.isfinite(x) for x in data), name+' nonfinite')
    return data
for row in rows:
    name = row['case']
    check(tuple(int(row[k]) for k in keys)==expected[name], name+' counters mismatch')
    check(int(row['errors'])==0 and int(row['warnings'])==0, name+' Vulkan validation messages')
    check(int(row['submit_in_range'])==1, name+' submit timestamp outside render interval')
    if name=='teardown': continue
    a, b = image(name+'.live.f32'), image(name+'.fresh.f32')
    images[name]=a
    worst = max(abs(x-y) for x,y in zip(a,b))
    check(worst<=1e-6, name+' fresh-render mismatch: '+str(worst))
    details[name]={'fresh_max_abs':worst,'covered_pixels':sum(a[i+3]>.999 for i in range(0,len(a),4))}
    if name in ('hidden','empty','empty_mesh','zero_scale','zero_scale_unchanged',
                'zero_scale_hidden','infinite_translation','nan_hidden','nan_hidden_unchanged'):
        check(not any(a), name+' nonzero empty image')
        check(row['has_scene']=='0', name+' invalid instance marked renderable')
        continue
    check(details[name]['covered_pixels']>100, name+' too few covered pixels')
    if name in ('insert','readd'): continue
    albedo = (.2,.4,.6) if name in ('initial','unchanged','transform','after_empty','after_failure',
                                  'zero_scale_recovery','nonfinite_recovery') else (.7,.2,.35)
    if name=='remove': albedo=(.9,.8,.1)
    want = tuple(c*d for c,d in zip(albedo,(1,.75,.5)))
    worst = max(abs(a[i+c]-want[c]*a[i+3]) for i in range(0,len(a),4) for c in range(3))
    details[name]['analytic_max_abs']=worst
    check(worst<2e-6, name+' analytic albedo*dome*coverage mismatch: '+str(worst))
for before,after in [('initial','transform'),('transform','color'),('visible','points'),('topology','mirror_nonuniform'),('mirror_nonuniform','insert'),('insert','remove')]:
    changed=sum(any(abs(images[before][i+c]-images[after][i+c])>.02 for c in range(4)) for i in range(0,128*96*4,4))
    check(changed>100, after+' edit changed too few pixels: '+str(changed))
    details[after]['changed_pixels']=changed
for a,b in [('initial','unchanged'),('color','visible'),('points','topology'),('insert','readd'),('initial','after_empty'),
            ('initial','after_failure'),('initial','zero_scale_recovery'),('initial','nonfinite_recovery')]:
    worst=max(abs(x-y) for x,y in zip(images[a],images[b]))
    check(worst<1e-6, a+'/'+b+' equivalence mismatch: '+str(worst))
baked=image('mirror_nonuniform.baked.f32')
worst=max(abs(x-y) for x,y in zip(images['mirror_nonuniform'],baked))
check(worst<1e-6, 'mirrored nonuniform instance vs baked geometry mismatch: '+str(worst))
details['mirror_nonuniform']['baked_max_abs']=worst
log=(args.log or out/'run.log').read_text()
check('VUID' not in log and 'Validation Error' not in log,'Vulkan diagnostic in run log')
result={'passed':not errors,'cases':len(expected)-1,'errors':errors,'details':details}
(out/'judge.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
raise SystemExit(0 if not errors else 16)
