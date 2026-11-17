"""Independent raw-EXR radiometric and actual USD delivery checks."""
from pathlib import Path
import hashlib,json,math,re,sys
import numpy as np
import OpenImageIO as oiio
from pxr import Usd,UsdGeom,UsdShade
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def image(path):
    inp=oiio.ImageInput.open(str(path));assert inp,oiio.geterror();spec=inp.spec()
    names=list(spec.channelnames);data=np.asarray(inp.read_image('float'),dtype=np.float64)
    info=dict(width=spec.width,height=spec.height,format=str(spec.format),channel_formats=[str(x) for x in spec.channelformats],channels=names)
    inp.close();return data[:,:,[names.index(c) for c in 'RGBA']],info
def main():
    out=Path(sys.argv[1]);receipt=json.loads((out/'receipt.json').read_text());mp=Path(receipt['manifest'])
    assert sha(mp)==receipt['manifest_before_render_sha256'],'Frozen manifest changed'
    manifest=json.loads(mp.read_text());limits=manifest['limits'];failures=[];checks=[];groups={}
    def require(ok,message):
        if not ok:failures.append(message)
    for p,digest in manifest['dependencies'].items():require(sha(p)==digest,'Frozen dependency changed: '+p)
    for item in manifest['assets'].values():require(sha(item['path'])==item['sha256'],'Encoded asset changed')
    expected_count=sum(1+int(next(c for c in manifest['cases'] if c['name']==r['case'])['karma']) for r in manifest['renders'])
    require(len(receipt['renders'])==expected_count,'Incomplete render inventory')
    by_key={(r['case'],r['seed'],r['label']):r for r in receipt['renders']}
    for source in manifest['renders']:
        case=next(c for c in manifest['cases'] if c['name']==source['case']);name=case['name']
        require(sha(source['stage'])==source['stage_sha256'],name+': changed stage')
        stage=Usd.Stage.Open(source['stage']);settings=stage.GetPrimAtPath('/Render/settings')
        require(tuple(settings.GetAttribute('resolution').Get())==tuple(manifest['resolution']),name+': raster settings')
        require(settings.GetAttribute('houdinirt:spp').Get()==4096 and settings.GetAttribute('houdinirt:bounces').Get()==0,name+': transport settings')
        camera=UsdGeom.Camera(stage.GetPrimAtPath('/Camera'));require(camera.GetProjectionAttr().Get()=='orthographic',name+': camera type')
        if not case['empty']:
            material=UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/World/Receiver')).ComputeBoundMaterial()[0]
            require(str(material.GetPath())=='/materials/openpbr',name+': native material binding')
        for label in ('candidate','karma') if case['karma'] else ('candidate',):
            record=by_key[(name,source['seed'],label)];require(sha(record['image'])==record['image_sha256'] and sha(record['log'])==record['log_sha256'],name+': artifact changed')
            pixels,info=image(record['image']);require(info['format']=='float' and all(x=='float' for x in info['channel_formats']),name+': float output required')
            require((info['width'],info['height'])==tuple(manifest['resolution']),name+': wrong dimensions')
            require(np.isfinite(pixels).all(),name+': nonfinite image')
            require(float(np.max(abs(pixels[...,3]-(0 if case['empty'] else 1))))<=limits['alpha_max_abs'],name+': coverage')
            log=Path(record['log']).read_text(errors='replace');low=log.casefold()
            require(not any(s in low for s in ('validation warning','validation error','vuid-','vk_error')),name+': Vulkan validation message')
            if label=='candidate':
                require('HoudiniRT: validation layer active' in log,name+': validation inactive')
                require(bool(re.search(r'present renderer=\S+ generation=\d+ scene=\d+ samples=4096\b',log)),name+': incomplete frame')
                if not case['empty']:require('material /materials/openpbr synced: OpenPBR 1' in log,name+': native OpenPBR unused')
                if case['negative']:
                    require('/Lights/Dome0:' in log and 'textured dome excluded' in log,name+': missing path-specific exclusion')
                else:require('textured dome excluded' not in log,name+': unexpected map exclusion')
            groups.setdefault(name,{}).setdefault(label,[]).append(pixels[...,:3])
    for case in manifest['cases']:
        name=case['name'];expected=np.asarray(case['expected']);delta=np.asarray(case['quadrature_delta']);group=groups[name]
        for label,images in group.items():
            means=np.asarray(images).mean(axis=(1,2));mean=means.mean(axis=0)
            se=means.std(axis=0,ddof=1)/math.sqrt(len(means)) if len(means)>1 else np.zeros(3)
            error=abs(mean-expected);bound=limits['mean_sigma']*se+limits['mean_abs_floor']+limits['mean_relative_floor']*abs(expected)+delta
            fixed=limits['fixed_abs_floor']+limits['fixed_relative_max']*abs(expected)+delta
            require(np.all(error<=bound) and np.all(error<=fixed),name+'/'+label+': independent Lambert energy')
            if case['negative'] or case['empty']:
                maximum=float(np.max(abs(np.asarray(images)-expected)))
                require(maximum<=limits['deterministic_max_abs'],name+'/'+label+': deterministic exclusion/empty image')
            checks.append(dict(case=name,label=label,expected=expected.tolist(),mean=mean.tolist(),standard_error=se.tolist(),error=error.tolist(),statistical_bound=bound.tolist(),fixed_bound=fixed.tolist()))
        if case['karma']:
            a=np.asarray(group['candidate']).mean(axis=(1,2));b=np.asarray(group['karma']).mean(axis=(1,2));am=a.mean(0);bm=b.mean(0)
            se=np.sqrt(a.var(0,ddof=1)/len(a)+b.var(0,ddof=1)/len(b));error=abs(am-bm)
            bound=limits['karma_sigma']*se+limits['mean_abs_floor']+limits['karma_relative_floor']*abs(bm)
            require(np.all(error<=bound) and np.all(error<=limits['fixed_abs_floor']+limits['karma_relative_max']*abs(bm)),name+': shared Lambert Karma comparison')
            checks.append(dict(case=name,kind='independent Karma shared Lambert',candidate=am.tolist(),karma=bm.tolist(),standard_error=se.tolist(),error=error.tolist(),bound=bound.tolist()))
    result=dict(status='passed' if not failures else 'failed',scope=manifest['scope'],failures=failures,checks=checks,limits=limits,
        manifest_sha256=sha(mp),receipt_sha256=sha(out/'receipt.json'),judge_sha256=sha(__file__),glossy_karma_parity='not claimed')
    (out/'judge.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(status=result['status'],checks=len(checks),failures=failures),indent=2))
    return 0 if not failures else 16
if __name__=='__main__':raise SystemExit(main())
