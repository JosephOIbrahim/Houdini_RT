"""Freeze independent GPU texture/UV/environment targets before candidate output."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,math,struct
import oracle
WIDTH=HEIGHT=32
HALF_EXTENT=.75
OFFSET_X=.0234375  # Half a pixel: no deterministic guide hits the UV seam.

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def f32(x):return struct.unpack('<f',struct.pack('<f',x))[0]
def textures(manifest):
    result={}
    for source,name,space in (('chart_4x4_8.png','chart4','raw'),('chart_5x3.exr','chart5','raw'),
                               ('chart_4x4_8.png','chart_srgb','srgb'),('scalar_roughness.exr','scalar','raw')):
        data=manifest['assets'][source]['decoded'];rows=data['pixels']
        if space=='srgb':rows=[[[f32(oracle.srgb_to_linear(v)) for v in p] for p in row] for row in rows]
        if len(rows[0][0])==1:rows=[[p*3 for p in row] for row in rows]
        result[name]=dict(rows=rows,source=source,color=space)
    for key in ('white','color','black','direction','sun','pole','seam'):
        result['env_'+key]=dict(rows=manifest['assets']['env_'+key+'.exr']['decoded']['pixels'],source='env_'+key+'.exr',color='raw')
    return result

def guide_cases():
    rows=[]
    def add(name,texture='chart4',filtertype='linear',wrap_u='periodic',wrap_v='periodic',affine=(1,0,0,0,1,0),seam=False,slot=0):
        rows.append(dict(name=name,kind='guide',texture=texture,filter=filtertype,wrap_u=wrap_u,wrap_v=wrap_v,
            affine=affine,seam=seam,slot=slot,width=WIDTH,height=HEIGHT,spp=1,seeds=[101]))
    add('chart_linear');add('chart_closest',filtertype='closest');add('chart_npot','chart5');add('chart_srgb','chart_srgb')
    add('facevarying_seam',seam=True);add('uv_quarter_turn',affine=(0,-1,1,1,0,0))
    for mode in ('constant','clamp','periodic','mirror'):
        for filt in ('closest','linear'):
            add(f'{mode}_{filt}',filtertype=filt,wrap_u=mode,wrap_v=mode,affine=(2.5,0,-.75,0,1.5,-.25))
            add(f'npot_{mode}_{filt}',texture='chart5',filtertype=filt,wrap_u=mode,wrap_v=mode,affine=(2.5,0,-.75,0,1.5,-.25))
    add('independent_axes',wrap_u='mirror',wrap_v='constant',affine=(3,0,-1,0,2,-.5))
    add('base_weight_scalar','scalar',slot=1)
    return rows

def environment_cases():
    rows=[]
    def add(name,envs,normal=(0,0,1),uniform=(0,0,0)):
        rows.append(dict(name=name,kind='environment',environments=envs,normal=normal,uniform=uniform,
            width=32,height=32,spp=4096,seeds=[101,107,113],rho=[.2,.5,.8]))
    def env(texture,tint=(1,1,1),rotation=0):return dict(texture='env_'+texture,tint=tint,rotation=rotation)
    for key in ('white','color','black','direction','sun','pole','seam'):
        add('env_'+key,[env(key)])
    add('north_pole',[env('pole')],normal=(0,1,0))
    add('direction_yaw90',[env('direction',rotation=90)])
    add('direction_yaw180',[env('direction',rotation=180)])
    add('colored_gain',[env('direction',(.5,2,4))])
    add('two_rotated',[env('direction',(.5,1,1.5)),env('color',(.2,.3,.4),90)])
    add('uniform_plus_map',[env('direction')],uniform=(.2,.3,.4))
    add('eight_maps',[env('direction',(1/8,1/8,1/8),45*i) for i in range(8)])
    return rows

def guide_reference(case,images):
    out=[];rows=images[case['texture']]['rows'];a=case['affine']
    for y in range(HEIGHT):
        wy=HALF_EXTENT*(2*(y+.5)/HEIGHT-1)
        for x in range(WIDTH):
            wx=OFFSET_X+HALF_EXTENT*(2*(x+.5)/WIDTH-1);u=.5+wx/2;v=.5+wy/2
            if case['seam'] and wy>wx:u+=.375
            st=(a[0]*u+a[1]*v+a[2],a[3]*u+a[4]*v+a[5])
            color=oracle.sample_texture(rows,*st,case['filter'],case['wrap_u'],case['wrap_v'])
            if case['slot']==1:color=[color[0]*c for c in (.2,.5,.8)]
            out.extend(color);out.append(1.)
    return out

CONTRACT=dict(schema=1,scope='Supplementary texture/UV/HDRI acceptance, not a sealed exam',
 guide_max_abs=5e-6,alpha_max_abs=1e-6,fresh_max_abs=0,
 environment_sigma=6,environment_stat_abs_floor=.0002,environment_stat_relative_floor=.002,
 environment_fixed_abs_floor=.0005,environment_fixed_relative_max=.02,
 quadrature_abs_floor=2e-5,quadrature_relative_max=2e-5,
 pdf_abs_floor=1e-6,pdf_relative_max=5e-6,pdf_cdf_rounding_bound=2**-22,pdf_integral_abs_max=2e-5,
 environment_value_abs_floor=2e-5,environment_value_relative_max=2e-5,
 histogram_sigma=6,histogram_count_floor=5,
 UV='Top-left file rows, bottom-left material st, triangle-corner interpolation; no guide pixel on an ambiguous shared edge',
 environment='OpenEXR/USD latitude-longitude, bilinear level0, periodicU/clampV, canonical +Y pole/+Z center',
 proposal='95% Rec709-luminance times exact cell solid angle plus5% uniform sphere; evaluate total radiance and mixturePDF',
 statistics='Three independent seeded image means; SE across seed means. Both fixed error ceiling and6SE+floor must pass.',
 unsupported='No mip/footprint/cubic, UDIM, alpha opacity, arbitrary graphs, nonrigid dome warp, or primary camera environment visibility is claimed.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--assets',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    manifest=json.loads(args.assets.read_text());assert manifest['status']=='generated'
    for item in manifest['assets'].values():assert sha(item['path'])==item['sha256']
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);(out/'guides').mkdir()
    images=textures(manifest);cases=guide_cases()+environment_cases();cache={};convergence=[]
    for case in cases:
        if case['kind']=='guide':
            values=guide_reference(case,images);path=out/'guides'/(case['name']+'.rgba32f')
            path.write_bytes(struct.pack('<'+str(len(values))+'f',*values));case['reference']=str(path);case['reference_sha256']=sha(path)
        else:
            totals=[list(case['uniform']),list(case['uniform'])]
            for env in case['environments']:
                angle=math.radians(env['rotation']);c=math.cos(angle);s=math.sin(angle);nx,ny,nz=case['normal']
                normal=(c*nx+s*nz,ny,-s*nx+c*nz);key=(env['texture'],normal)
                if key not in cache:
                    rows=images[env['texture']]['rows']
                    targets=[oracle.lambert_bilinear_latlong(rows,normal,n) for n in (16,32)]
                    delta=[abs(a-b) for a,b in zip(*targets)]
                    assert all(d<=CONTRACT['quadrature_abs_floor']+CONTRACT['quadrature_relative_max']*abs(v) for d,v in zip(delta,targets[-1])),(key,targets)
                    cache[key]=targets;convergence.append(dict(texture=env['texture'],normal=normal,orders=[16,32],targets=targets,delta=delta))
                for index,target in enumerate(cache[key]):
                    for channel,value in enumerate(target):totals[index][channel]+=value*env['tint'][channel]
            case['expected']=[c*rho for c,rho in zip(totals[-1],case['rho'])]
            case['quadrature_delta']=[abs(a-b)*rho for a,b,rho in zip(*totals,case['rho'])]
        print('reference '+case['name'],flush=True)
    (out/'textures.json').write_text(json.dumps(images,indent=2)+'\n')
    (out/'fixtures.json').write_text(json.dumps(cases,indent=2)+'\n')
    (out/'contract.json').write_text(json.dumps(CONTRACT,indent=2)+'\n')
    (out/'quadrature.json').write_text(json.dumps(convergence,indent=2)+'\n')
    scripts=[Path(__file__),Path(__file__).with_name('oracle.py'),Path(__file__).with_name('test_oracle.py')]
    receipt=dict(status='generated',created_utc=datetime.now(timezone.utc).isoformat(),scope='Independent deterministic references frozen before candidate output',
        assets=str(args.assets.resolve()),assets_sha256=sha(args.assets),scripts={str(p.resolve()):sha(p) for p in scripts},
        files={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()},guide_cases=sum(c['kind']=='guide' for c in cases),environment_cases=sum(c['kind']=='environment' for c in cases))
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(dict(output=str(out),cases=len(cases))))
if __name__=='__main__':main()
