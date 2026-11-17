"""Snapshot native sources, compile actual kernels, then explicitly run/judge."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,secrets,shutil,subprocess,sys
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
VS=Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
SDK=Path('C:/VulkanSDK/1.4.341.1')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def number(x):
    value=format(float(x),'.9g');return value+('f' if any(c in value for c in '.eE') else '.0f')
def array(values):return '{'+','.join(number(v) for v in values)+'}'
def header(cases,images):
    names=list(images);lines=['std::vector<std::shared_ptr<const HdRTVkTexture>> textures() {', 'std::vector<std::shared_ptr<const HdRTVkTexture>> result;']
    for name,image in images.items():
        rows=image['rows'];flat=[v for row in rows for pixel in row for v in list(pixel[:3])+[1.]]
        lines.append('{auto p=std::make_shared<HdRTVkTexture>();p->id="'+name+'";p->width='+str(len(rows[0]))+';p->height='+str(len(rows))+';p->rgba='+array(flat)+';result.push_back(p);}')
    lines+=['return result; }','std::vector<Case> cases() { std::vector<Case> result;']
    wrap={'constant':0,'clamp':1,'periodic':2,'mirror':3}
    for case in cases:
        lines.append('{Case c;c.name="'+case['name']+'";c.spp='+str(case['spp'])+';c.seeds={'+','.join(map(str,case['seeds']))+'};')
        if case['kind']=='guide':
            lines.append('c.texture='+str(names.index(case['texture']))+';c.filter='+str(case['filter']=='linear').lower()+';c.wrapU='+str(wrap[case['wrap_u']])+';c.wrapV='+str(wrap[case['wrap_v']])+';c.slot='+str(case['slot'])+';c.seam='+str(case['seam']).lower()+';c.affine='+array(case['affine'])+';')
        else:
            lines.append('c.environment=true;c.normal='+array(case['normal'])+';c.uniform='+array(case['uniform'])+';')
            for env in case['environments']:lines.append('c.environments.push_back({'+str(names.index(env['texture']))+','+array(env['tint'])+','+number(env['rotation'])+'});')
        lines.append('result.push_back(c);}')
    return '\n'.join(lines+['return result;}',''])

PDF_MAIN='''
void main() {
    uvec2 p=gl_GlobalInvocationID.xy;
    if(any(greaterThanEqual(p,frame.size.xy)))return;
    uint index=p.y*frame.size.x+p.x;
    uint rng=mix32(index^mix32(frame.size.w));
    vec3 d=frame.size.z==1u?sampleEnvironment(rng):environmentDirection((vec2(p)+.5)/vec2(frame.size.xy));
    accum.sum[index]=frame.size.z==1u?vec4(d,environmentPdf(d)):vec4(environmentValue(d),environmentPdf(d));
}
'''

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reference',type=Path,required=True);parser.add_argument('--shaders',type=Path,required=True)
    parser.add_argument('--run',action='store_true');parser.add_argument('--limit',type=int,default=0);args=parser.parse_args()
    reference=args.reference.resolve();reference_receipt=json.loads((reference/'receipt.json').read_text());assert reference_receipt['status']=='generated'
    for p,h in reference_receipt['files'].items():assert sha(reference/p)==h,p
    for p,h in reference_receipt['scripts'].items():assert sha(p)==h,p
    cases=json.loads((reference/'fixtures.json').read_text());cases=cases[:args.limit] if args.limit else cases
    images=json.loads((reference/'textures.json').read_text())
    out=ROOT/'build/m7/runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-gpu-'+secrets.token_hex(4));out.mkdir(parents=True)
    original={};native={}
    def copy(p,target):
        original[str(p.resolve())]=sha(p);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target);assert sha(target)==sha(p)
    for sub in ('src/hdRT','third_party/openpbr-bsdf'):
        for p in (ROOT/sub).rglob('*'):
            if p.is_file():native[p.relative_to(ROOT).as_posix()]=sha(p);copy(p,out/'inputs/tree'/p.relative_to(ROOT))
    for name in ('gpu_probe.cpp','run_gpu.py','judge_gpu.py','oracle.py'):
        copy(HERE/name,out/'inputs/probe'/name)
    for p in reference.rglob('*'):
        if p.is_file():copy(p,out/'inputs/reference'/p.relative_to(reference))
    for name in ('furnace.spv','resolve.spv'):copy(args.shaders/name,out/'inputs/shaders'/name)
    source=out/'inputs/tree/src/hdRT';probe=out/'inputs/probe'
    (probe/'fixtures.hpp').write_text(header(cases,images));(probe/'selected.json').write_text(json.dumps(cases,indent=2)+'\n')
    shader=(source/'shaders/furnace.comp').read_text();assert shader.count('void main()')==1
    diagnostic=source/'shaders/pdf_probe.comp';diagnostic.write_text(shader.replace('void main()','void rendererMain()',1)+PDF_MAIN)
    (out/'inputs/pdf-shaders').mkdir();copy(args.shaders/'resolve.spv',out/'inputs/pdf-shaders/resolve.spv')
    commands=['@echo off','setlocal',f'call "{VS}" > toolchain.log 2>&1','if errorlevel 1 exit /b 13',
        f'"{SDK/"Bin/glslangValidator.exe"}" -V --target-env vulkan1.2 -S comp "{diagnostic}" -o "{out/"inputs/pdf-shaders/furnace.spv"}" > shader.log 2>&1',
        'if errorlevel 1 exit /b 12',
        f'cl /nologo /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"{SDK/"Include"}" /I"{source}" "{probe/"gpu_probe.cpp"}" "{source/"vkFurnace.cpp"}" /Fe:gpu_probe.exe /link /LIBPATH:"{SDK/"Lib"}" vulkan-1.lib > compile.log 2>&1',
        'exit /b %ERRORLEVEL%','']
    (out/'compile.cmd').write_bytes('\r\n'.join(commands).encode())
    receipt=dict(status='preparing',output=str(out),diagnostic_only=bool(args.limit),native_source_files=native,original_hashes=original,
        reference=str(reference),reference_receipt_sha256=sha(reference/'receipt.json'),shader_hashes={n:sha(args.shaders/n) for n in ('furnace.spv','resolve.spv')})
    def write():(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    write();print(out,flush=True);code=subprocess.call(['cmd.exe','/d','/c',str(out/'compile.cmd')],cwd=out);receipt['compile_exit']=code
    if not code and args.run:
        with (out/'run.log').open('wb') as log:
            child=subprocess.Popen([str(out/'gpu_probe.exe'),str(out/'inputs/shaders/furnace.spv'),str(out/'inputs/pdf-shaders/furnace.spv'),str(out/'raw')],cwd=out,stdout=log,stderr=subprocess.STDOUT)
            receipt['owned_gpu_pid']=child.pid;write()
            try:code=child.wait(timeout=300)
            except subprocess.TimeoutExpired:child.kill();child.wait();code=15
        receipt['gpu_exit']=code
        if not code:
            with (out/'judge.log').open('wb') as log:code=subprocess.call([sys.executable,'-B',str(probe/'judge_gpu.py'),str(out)],stdout=log,stderr=subprocess.STDOUT)
            receipt['judge_exit']=code
    receipt['source_unchanged']=all(sha(p)==h for p,h in original.items())
    if not receipt['source_unchanged']:code=97
    receipt.update(status='passed' if args.run and not code else 'compiled_only' if not code else 'failed',exit_code=code,finished_utc=datetime.now(timezone.utc).isoformat())
    receipt['artifact_hashes']={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file() and p.name!='receipt.json'}
    write();print(json.dumps(dict(status=receipt['status'],output=str(out),code=code)));return code
if __name__=='__main__':raise SystemExit(main())
