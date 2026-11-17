"""CPU-only public MaterialX witness; never a replacement for renderer acceptance.

Equations/coefficients follow installed MaterialX Apache-2.0 GLSL functions named
in the output. No Karma implementation is inspected or inferred to be identical.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import sys
import numpy as np
from pxr import Gf, Usd, UsdGeom

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HFS = Path('C:/Program Files/Side Effects Software/Houdini 22.0.429')
MX = HFS/'houdini/materialx/libraries'
COMMIT = 'f8d6d947dfae4c9b599965a86c22826ea7a8dbfb'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def directional_albedo(alpha, f0=1., f90=1.):
    # mx_ggx_dir_albedo_analytic at NdotV=1; no fitted values from either renderer.
    x, y = 1., alpha
    coefficients = np.array([
        [.1003,.9345,1,1],[-.6303,-2.323,-1.765,.2281],[9.748,2.229,8.263,15.94],
        [-2.038,-3.748,11.53,-55.83],[29.34,1.424,28.96,13.08],[-8.245,-.7684,-7.507,41.26],
        [-26.44,1.436,-36.11,54.9],[19.99,.2913,15.86,300.2],[-5.448,.6286,33.37,-285.1]])
    terms = np.array([1,x,y,x*y,x*x,y*y,x*x*y,x*y*y,x*x*y*y])
    r = terms @ coefficients
    a, b = np.clip(r[:2]/r[2:],0,1)
    return f0*a + f90*b


def ggx(nl, alpha):
    # V=N, H=(V+L)/|V+L|; f*cos = D*F*G2/(4*NdotV).
    nh = np.sqrt((1+nl)/2)
    d = alpha**2/(np.pi*(nh*nh*(alpha**2-1)+1)**2)
    g = 2*nl/(np.sqrt(alpha**2+(1-alpha**2)*nl*nl)+nl)
    return nh, d*g/4


def dielectric(nl, color, roughness, ior):
    alpha = roughness**2
    nh, microfacet = ggx(nl, alpha)
    g = np.sqrt(ior**2+nh**2-1)
    fresnel = .5*((g-nh)/(g+nh))**2*(1+(((g+nh)*nh-1)/((g-nh)*nh+1))**2)
    ess = directional_albedo(alpha)
    comp = 1+fresnel*(1-ess)/ess
    e = directional_albedo(alpha, ((ior-1)/(ior+1))**2, 1.)
    transmitted = 1-e*comp
    return color*nl/np.pi*transmitted + microfacet*fresnel*comp


def material(stage):
    shader = stage.GetPrimAtPath('/materials/openpbr/surface')
    return {a.GetBaseName(): a.Get() for a in shader.GetAttributes() if a.GetName().startswith('inputs:')}


def assert_fixture(stage):
    camera = stage.GetPrimAtPath('/Camera')
    assert camera.GetAttribute('projection').Get() == 'orthographic'
    assert camera.GetAttribute('xformOp:translate').Get() == Gf.Vec3d(0,0,1)
    assert UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath('/World/Receiver')) == Gf.Matrix4d(1)
    assert all(p[2] == 0 for p in stage.GetPrimAtPath('/World/Receiver').GetAttribute('points').Get())


def metal_rectangle(stage, order):
    assert_fixture(stage)
    m = material(stage)
    assert m['base_metalness'] == 1 and m['specular_color'] == Gf.Vec3f(1)
    camera = stage.GetPrimAtPath('/Camera')
    width, height = stage.GetPrimAtPath('/Render/settings').GetAttribute('resolution').Get()
    x = ((np.arange(2,width-2)+.5)/width-.5)*camera.GetAttribute('horizontalAperture').Get()*.1
    y = ((np.arange(2,height-2)+.5)/height-.5)*camera.GetAttribute('verticalAperture').Get()*.1
    px, py = [v.ravel() for v in np.meshgrid(x,y)]
    light = stage.GetPrimAtPath('/Lights/Rect')
    center = np.array(light.GetAttribute('xformOp:translate').Get(),dtype=float)
    w, h = light.GetAttribute('inputs:width').Get(), light.GetAttribute('inputs:height').Get()
    intensity = light.GetAttribute('inputs:intensity').Get()
    assert not light.GetAttribute('inputs:normalize').Get()
    nodes, weights = np.polynomial.legendre.leggauss(order)
    alpha = float(m['specular_roughness'])**2
    f0 = np.array(m['base_color'],dtype=float)*m['base_weight']
    ess = directional_albedo(alpha)
    result = np.zeros((len(px),3))
    for i, u in enumerate(nodes):
        for j, v in enumerate(nodes):
            d2 = (center[0]+w*.5*u-px)**2 + (center[1]+h*.5*v-py)**2 + center[2]**2
            nl = center[2]/np.sqrt(d2)
            nh, factor = ggx(nl, alpha)
            fresnel = f0+(1-f0)*(1-nh[:,None])**5  # F82=F90=1
            comp = 1+fresnel*(1-ess)/ess
            response = factor[:,None]*fresnel*comp*m['specular_weight']
            result += response*(intensity*nl/d2*weights[i]*weights[j]*w*h*.25)[:,None]
    return result.mean(axis=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--spec', type=Path, default=ROOT/'build/m6/model-qualification/spec-v1.1.1.html')
    parser.add_argument('--license', type=Path, default=ROOT/'build/m6/model-qualification/spec-LICENSE')
    args = parser.parse_args()
    run, out = args.run.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    provenance = json.loads((HERE/'provenance.json').read_text())
    assert sha(args.spec) == provenance['specification']['sha256'], 'Pinned specification changed'
    assert sha(args.license) == provenance['specification']['license_sha256'], 'Pinned license changed'
    for item in provenance['installed_materialx']:
        assert sha(Path(item['path'])) == item['sha256'], item['path']
    stages = {name: run/'fixtures'/(name+'_101.usda') for name in ('default_distant','rough_metal_rect')}
    distant = Usd.Stage.Open(str(stages['default_distant']))
    assert_fixture(distant)
    m = material(distant)
    light = distant.GetPrimAtPath('/Lights/Distant')
    angle = light.GetAttribute('xformOp:rotateY').Get()
    nl = math.cos(math.radians(angle))
    assert m['base_color'] == Gf.Vec3f(.8) and m['specular_weight'] == 1
    assert m['specular_color'] == Gf.Vec3f(1) and m['base_metalness'] == 0
    prediction = float(dielectric(nl,float(m['base_color'][0]),m['specular_roughness'],m['specular_ior'])
                       * light.GetAttribute('inputs:intensity').Get())
    metal = Usd.Stage.Open(str(stages['rough_metal_rect']))
    q16, q24 = metal_rectangle(metal,16), metal_rectangle(metal,24)
    observed = json.loads((run/'judge.json').read_text())
    rows = {r['name']:r for r in observed['checks'] if r['kind']=='independent Karma'}
    result = dict(scope='Public MaterialX predictive witness only; not Karma implementation proof or renderer acceptance',
        script_sha256=sha(Path(__file__)),observed_judge_path=str(run/'judge.json'),observed_judge_sha256=sha(run/'judge.json'),
        fixture_hashes={str(p):sha(p) for p in stages.values()},
        default_distant=dict(predicted_public_materialx=prediction,karma_mean=rows['default_distant']['karma_mean'],
                             candidate_mean=rows['default_distant']['candidate_mean'],
                             relative_error_to_karma=abs(prediction/rows['default_distant']['karma_mean'][0]-1)),
        rough_metal_rect=dict(predicted_public_materialx_order16=q16.tolist(),predicted_public_materialx_order24=q24.tolist(),
                             quadrature_change_max_abs=float(np.max(abs(q24-q16))),
                             karma_mean=rows['rough_metal_rect']['karma_mean'],candidate_mean=rows['rough_metal_rect']['candidate_mean'],
                             relative_error_to_karma=(abs(q24/rows['rough_metal_rect']['karma_mean']-1)).tolist()))
    assert np.max(abs(q24-q16)) < 1e-10, 'Public-model rectangle quadrature did not converge'
    (out/'materialx_witness.json').write_text(json.dumps(result,indent=2)+'\n')
    files = {
        'bxdf/open_pbr_surface.mtlx':['OpenPBR1.1.1 nodedef','dielectric_base','metal_bsdf','open_pbr_anisotropy'],
        'pbrlib/genglsl/mx_dielectric_bsdf.glsl':['mx_dielectric_bsdf'],
        'pbrlib/genglsl/mx_layer_bsdf.glsl':['mx_layer_bsdf'],
        'pbrlib/genglsl/mx_generalized_schlick_bsdf.glsl':['mx_generalized_schlick_bsdf'],
        'pbrlib/genglsl/lib/mx_microfacet_specular.glsl':['mx_ggx_NDF','mx_ggx_smith_G2','mx_ggx_dir_albedo_analytic','mx_ggx_energy_compensation','mx_fresnel_dielectric']}
    sources = dict(specification=dict(version='1.1.1',commit=COMMIT,
        url='https://github.com/AcademySoftwareFoundation/OpenPBR/blob/'+COMMIT+'/index.html',
        archived_path=str(args.spec.resolve()),sha256=sha(args.spec),
        relevant_lines={'glossy_diffuse':[585,602],'microfacet_multiple_scattering':[369,377]},
        license_path=str(args.license.resolve()),license_sha256=sha(args.license)),
        installed_materialx=[dict(path=str(MX/p),sha256=sha(MX/p),functions=f) for p,f in files.items()],
        adobe=[dict(path=str(ROOT/p),sha256=sha(ROOT/p)) for p in
            ('third_party/openpbr-bsdf/README.md','third_party/openpbr-bsdf/impl/openpbr_diffuse_lobe.h',
             'third_party/openpbr-bsdf/impl/openpbr_bsdf.h','third_party/openpbr-bsdf/impl/openpbr_microfacet_multiple_scattering_lobes.h')
             if (ROOT/p).is_file()])
    (out/'normative_sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    receipt = dict(status='passed',scope='Pinned public sources and predictive math reproduced; not Karma parity',
        karma_parity=False,renderer_acceptance=False,model='Installed public MaterialX GLSL, analytic directional-albedo mode',
        exact_private_karma_metal_cause='unresolved',
        command=sys.argv,script_sha256=sha(Path(__file__)),provenance_sha256=sha(HERE/'provenance.json'),
        sources_sha256=sha(out/'normative_sources.json'),result_sha256=sha(out/'materialx_witness.json'),
        specification_sha256=sha(args.spec),fixture_hashes=result['fixture_hashes'],
        observations_sha256=result['observed_judge_sha256'],
        files={str(p):sha(p) for p in (Path(__file__),HERE/'provenance.json',args.spec,args.license,
            out/'normative_sources.json',out/'materialx_witness.json')})
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__': main()
