"""Independent unit-energy white-furnace oracle, no Adobe implementation imported."""
from pathlib import Path
import csv,hashlib,json,math,sys
import numpy as np
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 out=Path(sys.argv[1]);contract=json.loads((out/'contract.json').read_text());rows=list(csv.DictReader((out/'raw/stats.csv').open()));fail=[];checks=[]
 def need(ok,msg):
  if not ok:fail.append(msg)
 need(len(rows)==37,'Expected12cases times3seeds plus teardown')
 for case in range(12):
  selected=[r for r in rows if r['case']==str(case)];need([int(r['seed']) for r in selected]==[101,107,113],'Seed census')
  images=[np.fromfile(out/'raw'/(str(case)+'_'+str(seed)+'.rgba32f'),dtype='<f4').astype(np.float64).reshape(32,32,4) for seed in (101,107,113)]
  need(all(np.isfinite(im).all() for im in images),'Nonfinite furnace image')
  need(max(float(np.max(abs(im[...,3]-1))) for im in images)<=1e-6,'Furnace receiver coverage')
  means=np.asarray(images)[...,:3].mean(axis=(1,2));mean=means.mean(0);se=means.std(0,ddof=1)/math.sqrt(3);error=abs(mean-1)
  stat=contract['sigma']*se+contract['absolute_floor']+contract['relative_floor'];fixed=contract['fixed_absolute_floor']+contract['fixed_relative_max']
  need(np.all(error<=stat) and np.all(error<=fixed),'Unit-energy furnace case '+str(case))
  for row in selected:
   need(int(row['samples'])==4096,'Wrong sample count')
   if case:need(all(int(row[k])==0 for k in ('blas','tlas','geometry_bytes')),'Material-only edit rebuilt geometry')
  checks.append(dict(case=case,metal=float(selected[0]['metal']),roughness=float(selected[0]['roughness']),view_cos=float(selected[0]['view_cos']),mean=mean.tolist(),standard_error=se.tolist(),error=error.tolist(),statistical_bound=stat.tolist(),fixed_bound=fixed))
 need(all(int(r['errors'])==int(r['warnings'])==0 for r in rows),'Vulkan errors/warnings')
 log=(out/'run.log').read_text(errors='replace').casefold();need(not any(s in log for s in ('validation warning','validation error','vuid-','vk_error')),'Vulkan log message')
 result=dict(status='passed' if not fail else 'failed',scope=contract['scope'],failures=fail,checks=checks,contract=contract,files={p.relative_to(out).as_posix():sha(p) for p in (out/'raw').iterdir()})
 (out/'judge.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(status=result['status'],failures=fail)));return 0 if not fail else 16
if __name__=='__main__':raise SystemExit(main())
