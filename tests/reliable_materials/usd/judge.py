"""Read actual USD/EXR/log artifacts; independently judge native material delivery."""
from pathlib import Path
import hashlib
import json
import math
import re
import sys
import numpy as np
import OpenImageIO as oiio
from pxr import Usd,UsdShade
import transport_judge


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def image(path):
    inp=oiio.ImageInput.open(str(path));assert inp is not None,oiio.geterror()
    spec=inp.spec();names=list(spec.channelnames)
    pixels=np.asarray(inp.read_image('float'),dtype=np.float64).reshape(spec.height,spec.width,spec.nchannels)
    metadata=dict(width=spec.width,height=spec.height,channels=names,format=str(spec.format),
                  channel_formats=[str(x) for x in spec.channelformats])
    inp.close();return pixels,names,metadata


def main():
    out=Path(sys.argv[1]).resolve();receipt=json.loads((out/'receipt.json').read_text())
    assert sha(out/'fixtures/manifest.json')==receipt['fixture_manifest_before_render_sha256'],'Fixture manifest changed after rendering began'
    manifest=json.loads((out/'fixtures/manifest.json').read_text());limits=manifest['limits'];failures=[];groups={};checks=[];hashes={}
    def require(okay,message):
        if not okay:failures.append(message)
    render_by_key={(r['case'],r['seed'],r['renderer']):r for r in receipt['renders']}
    require(len(render_by_key)==78,'Expected36Karma+42candidate renders')
    for case in manifest['cases']:
        stage_path=Path(case['stage']);require(sha(stage_path)==case['stage_sha256'],'Stage hash changed')
        stage=Usd.Stage.Open(str(stage_path));require(bool(stage),'Cannot read stage')
        native_settings=stage.GetPrimAtPath('/Render/settings');karma_settings=stage.GetPrimAtPath('/Render/karma_settings')
        require({str(a.GetName()):str(a.Get()) for a in native_settings.GetAttributes()}=={str(a.GetName()):str(a.Get()) for a in karma_settings.GetAttributes()},case['name']+': transport settings differ')
        require(native_settings.GetRelationship('camera').GetTargets()==karma_settings.GetRelationship('camera').GetTargets(),case['name']+': camera settings differ')
        native_var=stage.GetPrimAtPath('/Render/Vars/color');karma_var=stage.GetPrimAtPath('/Render/Vars/karma_beauty')
        require(native_var.GetAttribute('sourceName').Get()=='color' and native_var.GetAttribute('sourceType').Get()=='raw',case['name']+': Hydra output binding')
        require(karma_var.GetAttribute('sourceName').Get()=='C.*[LO]' and karma_var.GetAttribute('sourceType').Get()=='lpe' and karma_var.GetAttribute('driver:parameters:aov:husk:name').Get()=='C',case['name']+': canonical Karma output binding')
        if not case['negative']:
            material=UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/World/Receiver')).ComputeBoundMaterial()[0]
            require(str(material.GetPath())==case['binding'],case['name']+': unexpected binding')
            shader=UsdShade.Shader(stage.GetPrimAtPath(case['surface_path']))
            require(shader.GetIdAttr().Get()=='ND_open_pbr_surface_surfaceshader',case['name']+': wrong model')
        for label,renderer in (('karma','BRAY_HdKarma'),('candidate','HdRTRendererPlugin')):
            if label=='karma' and (case['negative'] or case['aov']):continue
            r=render_by_key[case['name'],case['seed'],renderer];path=Path(r['image']);log_path=Path(r['log'])
            require(r['render_settings']==('/Render/karma_settings' if label=='karma' else '/Render/settings'),case['name']+': wrong selected settings')
            require(sha(path)==r['image_sha256'] and sha(log_path)==r['log_sha256'],'Artifact hash changed')
            hashes[str(path)]=sha(path);hashes[str(log_path)]=sha(log_path)
            data,names,metadata=image(path);require(np.isfinite(data).all(),case['name']+': nonfinite EXR')
            require(metadata['format']=='float' and not any(x!='float' for x in metadata['channel_formats']),case['name']+'/'+label+': expected explicit float32 EXR')
            rgba=data[:,:,[names.index(c) for c in ('R','G','B','A')]]
            require(rgba.shape[:2]==(96,128),case['name']+': wrong raster')
            require(np.max(abs(rgba[...,3]-1))<=limits['alpha_max_abs'],case['name']+': coverage mismatch')
            log=log_path.read_text(errors='replace')
            if label=='candidate':
                require('HoudiniRT: validation layer active' in log,case['name']+': validation not active')
                require(not any(s in log.casefold() for s in ('vuid-','validation error','validation warning','vk_error')),case['name']+': validation message')
                require(re.search(r'present renderer=\S+ generation=\d+ scene=\d+ samples=4096\b',log) is not None,case['name']+': incomplete publication')
                if not case['negative']:
                    require('material /materials/openpbr synced: OpenPBR 1' in log,case['name']+': did not execute bound OpenPBR')
                elif case['negative'] in ('texture','emission','colorspace'):
                    require('WARNING: material /materials/openpbr:' in log and 'whole-material displayColor Lambert fallback' in log,case['name']+': missing path-specific fallback warning')
                if case['negative']:
                    error=float(np.max(abs(rgba[...,:3]-case['fallback'])))
                    require(error<=limits['fallback_max_abs'],case['name']+': fallback image differs from declared displayColor')
                    checks.append(dict(name=case['name'],kind='fallback',max_abs=error));continue
                if case['aov']:
                    expected=np.array(case['material']['base_color'])*case['material']['base_weight']
                    albedo=data[:,:,[names.index('albedo.'+c) for c in 'RGB']]
                    error=float(np.max(abs(albedo-expected)))
                    require(error<=limits['albedo_max_abs'],'Bound OpenPBR albedo AOV')
                    require(metadata['format']=='float' and not any(x!='float' for x in metadata['channel_formats']),'Bound albedo is not float')
                    checks.append(dict(name='bound_albedo',kind='actual raw AOV',expected=expected.tolist(),max_abs=error,metadata=metadata));continue
            if label=='candidate' and case['name'] in ('lambert_dome','weighted_dome'):
                expected=np.array(case['material']['base_color'])*case['material']['base_weight']
                require(np.max(abs(rgba[...,:3]-expected))<=limits['analytic_lambert_max_abs'],case['name']+'/'+label+': analytic Lambert')
            groups.setdefault(case['name'],{}).setdefault(label,[]).append(rgba[...,:3])
    checks.extend(transport_judge.check(out,receipt,groups,limits,require))
    for name,group in groups.items():
        a=np.array(group['candidate'])[:,2:-2,2:-2,:];b=np.array(group['karma'])[:,2:-2,2:-2,:];require(len(a)==len(b)==3,name+': seed count')
        am=a.mean(axis=(1,2));bm=b.mean(axis=(1,2));amean=am.mean(axis=0);bmean=bm.mean(axis=0)
        se=np.sqrt(np.var(am,axis=0,ddof=1)/3+np.var(bm,axis=0,ddof=1)/3)
        error=abs(amean-bmean);relative=error/np.maximum(abs(bmean),1e-12)
        if name in ('lambert_dome','weighted_dome'):
            case=next(x for x in manifest['cases'] if x['name']==name and not x['aov'])
            expected=np.array(case['material']['base_color'])*case['material']['base_weight']
            karma_se=np.std(bm,axis=0,ddof=1)/math.sqrt(3)
            analytic_bound=limits['mean_standard_error_multiplier']*karma_se+limits['analytic_lambert_max_abs']
            require(np.all(abs(bmean-expected)<=analytic_bound),name+'/karma: analytic statistical mean')
            require(np.all(abs(bmean-expected)<=limits['mean_relative_max']*expected+limits['absolute_floor']),name+'/karma: analytic fixed mean')
        bound=limits['mean_standard_error_multiplier']*se+limits['mean_relative_floor']*abs(bmean)+limits['absolute_floor']
        nrmse=float(np.sqrt(np.mean((a.mean(axis=0)-b.mean(axis=0))**2))/max(np.sqrt(np.mean(b.mean(axis=0)**2)),1e-12))
        parity_pass=bool(np.all(error<=bound) and np.all(error<=limits['mean_relative_max']*abs(bmean)+limits['absolute_floor']) and nrmse<=limits['rgb_relative_rmse_max'])
        if name in ('lambert_dome','weighted_dome'):require(parity_pass,name+': independent Karma Lambert comparison')
        checks.append(dict(name=name,kind='independent Karma Lambert' if name in ('lambert_dome','weighted_dome') else 'Karma appearance diagnostic / implementation-dependent OpenPBR',
                           original_parity_limits_pass=parity_pass,candidate_mean=amean.tolist(),karma_mean=bmean.tolist(),
                           relative_mean_error=relative.tolist(),standard_error=se.tolist(),absolute_bound=bound.tolist(),relative_rmse=nrmse))
    qualification=receipt['model_qualification'];prior=receipt['original_strict_comparison']
    for path,digest in qualification['files'].items():require(sha(Path(path))==digest,'Model qualification artifact changed')
    for key in ('receipt','judge'):require(sha(Path(prior[key]))==prior[key+'_sha256'],'Original strict comparison changed')
    require(json.loads(Path(prior['receipt']).read_text())['status']=='failed','Original strict failure not preserved')
    result=dict(status='passed' if not failures else 'failed',scope=manifest['scope'],failures=failures,checks=checks,limits=limits,files=hashes,
                karma_parity_status='failed / diagnostic, not claimed matched',original_strict_comparison=prior,model_qualification=qualification,
                reference_scope='Adobe shared BSDF model with independent CPU transport; physical/reciprocity/GPU matrix proofs remain separate. All non-Lambert Karma comparisons are appearance diagnostics.')
    (out/'judge.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(status=result['status'],checks=len(checks),failures=failures),indent=2))
    return 0 if not failures else 16


if __name__=='__main__':raise SystemExit(main())
