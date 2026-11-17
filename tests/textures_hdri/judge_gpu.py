"""Independent image, PDF, sampling, and actual-work judge; never calls native code."""
from pathlib import Path
import csv,hashlib,json,math,sys
import numpy as np
import oracle
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(path,shape):
    values=np.fromfile(path,dtype='<f4').astype(np.float64);return values.reshape(shape)

def env_reference(case,images,directions):
    count=len(directions);uniform=np.array(case['uniform'],dtype=np.float64)
    value=np.tile(uniform,(count,1));weighted=np.full(count,max(0,float(uniform@np.array([.2126,.7152,.0722]))),dtype=np.float64)
    total=float(weighted[0])*4*math.pi;rounding=np.zeros(count)
    for env in case['environments']:
        pixels=np.asarray(images[env['texture']]['rows'],dtype=np.float64);h,w=pixels.shape[:2]
        areas=np.array([oracle.cell_solid_angle(w,h,y) for y in range(h)])
        luma=pixels@np.array([.2126,.7152,.0722]);base_integral=float(np.sum(luma*areas[:,None]))
        importance=np.zeros((h,w)) if base_integral<=0 else luma*areas[:,None]/base_integral
        tint=np.array(env['tint']);power=float(np.sum(((pixels*tint)@np.array([.2126,.7152,.0722]))*areas[:,None]));total+=power
        angle=math.radians(env['rotation']);c=math.cos(angle);s=math.sin(angle)
        matrix=np.array([[c,0,s],[0,1,0],[-s,0,c]]);local=directions@matrix.T;local/=np.linalg.norm(local,axis=1)[:,None]
        u=(.5-np.arctan2(local[:,0],local[:,2])/(2*math.pi))%1;v=np.arccos(np.clip(local[:,1],-1,1))/math.pi
        x=np.minimum(w-1,(u*w).astype(int));y=np.minimum(h-1,(v*h).astype(int))
        if power:
            weighted+=power*importance[y,x]/areas[y];rounding+=power/areas[y]
        pixel_rows=pixels.tolist()
        for index,(uu,vv) in enumerate(zip(u,v)):
            value[index]+=np.asarray(oracle.sample_texture(pixel_rows,float(uu),float(1-vv),'linear','periodic','clamp'))*tint
    pdf=np.full(count,1/(4*math.pi)) if total<=0 else .05/(4*math.pi)+.95*weighted/total
    rounding=np.zeros(count) if total<=0 else .95*rounding/total
    return value,pdf,rounding

def main():
    out=Path(sys.argv[1]).resolve();raw=out/'raw';reference=out/'inputs/reference'
    receipt=json.loads((out/'receipt.json').read_text());cases=json.loads((out/'inputs/probe/selected.json').read_text())
    images=json.loads((reference/'textures.json').read_text());limits=json.loads((reference/'contract.json').read_text());failures=[];results=[];pdf_results=[]
    def require(ok,message):
        if not ok:failures.append(message)
    frozen=json.loads((reference/'receipt.json').read_text())
    require(all(sha(reference/p)==h for p,h in frozen['files'].items()),'Reference changed')
    csvrows=list(csv.DictReader((raw/'stats.csv').open()));validation=list(csv.DictReader((raw/'validation.csv').open()))
    require(all(int(r['errors'])==0 and int(r['warnings'])==0 for r in csvrows+validation),'Vulkan validation not silent')
    log=(out/'run.log').read_text(errors='replace').casefold()
    require('validation=active' in log and not any(s in log for s in ('vuid-','validation error','validation warning','vk_error')),'Vulkan log message/inactive')
    for case in cases:
        means=[];name=case['name'];guide_error=0.
        for seed in case['seeds']:
            beauty=read(raw/f'{name}_{seed}.rgba32f',(32,32,4));guide=read(raw/f'{name}_{seed}.guides',(32,32,16))
            require(np.isfinite(beauty).all() and np.isfinite(guide[:,:,:8]).all() and np.isfinite(guide[:,:,12:]).all(),name+': nonfinite image/guide')
            require(float(np.max(abs(beauty[:,:,3]-1)))<=limits['alpha_max_abs'],name+': coverage')
            if case['kind']=='guide':
                expected=read(reference/'guides'/Path(case['reference']).name,(32,32,4))
                guide_error=float(np.max(abs(guide[:,:,12:]-expected)));require(guide_error<=limits['guide_max_abs'],name+': UV/texture guide mismatch')
            means.append(beauty[:,:,:3].mean(axis=(0,1)))
        row=dict(name=name,guide_max_abs=guide_error)
        if case['kind']=='environment':
            means=np.asarray(means);mean=means.mean(axis=0);se=means.std(axis=0,ddof=1)/math.sqrt(len(means));expected=np.asarray(case['expected']);error=abs(mean-expected)
            statistical=limits['environment_sigma']*se+limits['environment_stat_abs_floor']+limits['environment_stat_relative_floor']*abs(expected)+np.asarray(case['quadrature_delta'])
            fixed=limits['environment_fixed_abs_floor']+limits['environment_fixed_relative_max']*abs(expected)
            require(bool(np.all(error<=statistical) and np.all(error<=fixed)),name+': environment energy')
            row.update(mean=mean.tolist(),standard_error=se.tolist(),expected=expected.tolist(),error=error.tolist(),statistical_bound=statistical.tolist(),fixed_bound=fixed.tolist())
        results.append(row)
        repeat=next(r for r in csvrows if r['operation']=='unchanged' and r['case']==name)
        require(all(int(repeat[k])==0 for k in ('blas_builds','blas_refits','tlas_builds','tlas_updates','geometry_bytes','table_bytes','uv_bytes','texture_bytes')),name+': unchanged snapshot uploaded/rebuilt')
        if case['kind']=='guide' and name!='chart_linear':
            actual=next(r for r in csvrows if r['operation']=='render' and r['case']==name)
            require(all(int(actual[k])==0 for k in ('blas_builds','blas_refits','tlas_builds','tlas_updates','geometry_bytes')),name+': material/UV edit rebuilt geometry')
        if name in ('facevarying_seam','uv_quarter_turn'):
            actual=next(r for r in csvrows if r['operation']=='render' and r['case']==name)
            require(int(actual['uv_bytes'])==12*4,name+': UV edit did not upload exactly triangle-corner coordinates')
        if case['kind']!='environment':continue
        samples=read(raw/(name+'.pdf_samples'),(-1,4));values=read(raw/(name+'.pdf_values'),(32*64,4))
        require(np.isfinite(samples).all() and np.isfinite(values).all(),name+': nonfinite PDF/value')
        require(float(np.max(abs(np.linalg.norm(samples[:,:3],axis=1)-1)))<2e-6,name+': nonunit environment sample')
        require(bool(np.all(samples[:,3]>=.05/(4*math.pi)-1e-7)),name+': proposal lost uniform support')
        directions=np.array([oracle.latlong_direction((x+.5)/64,(y+.5)/32) for y in range(32) for x in range(64)])
        expected_value,expected_pdf,rounding=env_reference(case,images,directions)
        pdf_error=abs(values[:,3]-expected_pdf)
        pdf_bound=limits['pdf_abs_floor']+limits['pdf_relative_max']*abs(expected_pdf)+limits['pdf_cdf_rounding_bound']*rounding
        require(bool(np.all(pdf_error<=pdf_bound)),name+': independently evaluated PDF mismatch')
        value_error=abs(values[:,:3]-expected_value)
        value_bound=limits['environment_value_abs_floor']+limits['environment_value_relative_max']*abs(expected_value)
        require(bool(np.all(value_error<=value_bound)),name+': environment orientation/filter/radiance mismatch')
        areas=np.repeat([oracle.cell_solid_angle(64,32,y) for y in range(32)],64)
        integral=float(values[:,3]@areas);require(abs(integral-1)<=limits['pdf_integral_abs_max'],name+': PDF not normalized')
        directions=samples[:,:3]/np.linalg.norm(samples[:,:3],axis=1)[:,None]
        u=(.5-np.arctan2(directions[:,0],directions[:,2])/(2*math.pi))%1
        v=np.arccos(np.clip(directions[:,1],-1,1))/math.pi
        x=np.minimum(63,(u*64).astype(int));y=np.minimum(31,(v*32).astype(int));histogram=np.bincount(y*64+x,minlength=2048)
        probability=expected_pdf*areas;count=len(samples);expected_count=count*probability
        count_bound=limits['histogram_count_floor']+limits['histogram_sigma']*np.sqrt(np.maximum(0,count*probability*(1-probability)))+count*pdf_bound*areas
        require(bool(np.all(abs(histogram-expected_count)<=count_bound)),name+': sample distribution disagrees with independent PDF')
        pdf_results.append(dict(name=name,pdf_integral=integral,pdf_max_abs=float(pdf_error.max()),radiance_max_abs=float(value_error.max()),
            samples=count,maximum_histogram_bound_fraction=float(np.max(abs(histogram-expected_count)/count_bound))))
    answer=dict(status='passed' if not failures else 'failed',scope=limits['scope'],failures=failures,
        diagnostic_only=receipt['diagnostic_only'],limits=limits,cases=results,pdf_cases=pdf_results,validation=validation,
        files={p.relative_to(out).as_posix():sha(p) for p in raw.rglob('*') if p.is_file()},
        reference_receipt_sha256=sha(reference/'receipt.json'),judge_sha256=sha(__file__))
    (out/'judge.json').write_text(json.dumps(answer,indent=2)+'\n');print(json.dumps(dict(status=answer['status'],failures=failures,cases=len(results),pdf_cases=len(pdf_results)),indent=2));return 0 if not failures else 16
if __name__=='__main__':raise SystemExit(main())
