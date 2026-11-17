"""Bind both independently judged actual-USD packs to one exact accepted payload."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def norm(d):return {p.replace('\\','/'):h for p,h in d.items()}
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--textures',type=Path,required=True);parser.add_argument('--hdri',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 records={};children={};artifacts={}
 for label,path in (('textures',args.textures),('hdri',args.hdri)):
  path=path.resolve();r=read(path);jpath=path.with_name('judge.json');judge=read(jpath)
  assert r['status']==judge['status']=='passed' and not judge['failures'] and r['source_unchanged'],label
  records[label]=r;children[label]=dict(receipt=str(path),receipt_sha256=sha(path),judge=str(jpath),judge_sha256=sha(jpath),scope=r['scope'] if 'scope' in r else judge['scope'],render_count=len(r['renders']))
  artifacts[str(path)]=sha(path);artifacts[str(jpath)]=sha(jpath)
  for row in r['renders']:
   for key in ('log','image'):
    p=row.get(key)
    if p is None and key=='image':
     manifest=read(path.parent/'fixtures/manifest.json');p=next(c['image'] for c in manifest['cases'] if c['name']==row['case'])
    assert sha(p)==row[key+'_sha256'],(label,p);artifacts[p]=sha(p)
  manifest=Path(r.get('manifest',path.parent/'fixtures/manifest.json'))
  expected=r.get('manifest_before_render_sha256',r.get('fixture_manifest_sha256'));assert sha(manifest)==expected
  artifacts[str(manifest)]=sha(manifest)
 native=norm(records['textures']['native_source_files']);payload=norm(records['textures']['payload_hashes'])
 assert native==norm(records['hdri']['native_source_files']) and payload==norm(records['hdri']['payload_hashes'])
 for rel,digest in native.items():assert sha(ROOT/rel)==digest,rel
 plugin=Path(records['hdri']['payload'])
 for rel,digest in payload.items():assert sha(plugin/rel)==digest,rel
 for r in records.values():
  assert {n:payload['shaders/'+n] for n in ('furnace.spv','resolve.spv')}=={Path(n).name:h for n,h in r['shader_hashes'].items()}
 qualification=ROOT/'tests/reliable_materials/model_qualification'
 qualified_files={str(p):sha(p) for p in qualification.iterdir() if p.is_file()}
 out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
 for label,child in children.items():
  for kind in ('receipt','judge'):shutil.copy2(child[kind],out/(label+'_'+kind+'.json'))
 result=dict(status='passed',scope='Combined actual Houdini22 USD texture/UV/material delivery and independent HDRI Lambert energy/shadows; seven texture slots use matched constant beauty controls',
  created_utc=datetime.now(timezone.utc).isoformat(),native_source_files=native,source_unchanged=True,payload=str(plugin),payload_hashes=payload,
  shader_hashes={n:payload['shaders/'+n] for n in ('furnace.spv','resolve.spv')},subproofs=children,files=artifacts,
  render_count=sum(x['render_count'] for x in children.values()),actual_texture_cases=len(read(args.textures.parent/'judge.json')['cases']),
  actual_hdri_cases=len(read(Path(records['hdri']['manifest']))['cases']),
  karma_scope='Only shared constant-environment Lambert and analytically occluded Lambert comparisons are accepted. No glossy OpenPBR Karma parity is claimed.',
  prior_model_qualification=dict(scope='Previously established pinned OpenPBR1.1.1 implementation choices remain applicable; no reclassification of earlier failed glossy parity',files=qualified_files),
  script_sha256=sha(__file__))
 (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(status='passed',renders=result['render_count'],receipt=str(out/'receipt.json'))))
if __name__=='__main__':main()
