"""Qualify one lexical path-comparison defect without changing the frozen audit."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,sys
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
path=Path(sys.argv[1]).resolve();replay=read(path);out=path.parent
original_path=out/'independent_freshness.json';original=read(original_path)
expected=[r['name']+': not normal preferences' for r in replay['renders']]
assert original['status']=='failed' and original['failures']==expected
assert len(original['cases'])==len(replay['renders'])==9 and replay['status']=='rendered'
assert all(sha(p)==h for p,h in original['files'].items()),'Frozen audit input changed'
manifest_path=Path(replay['manifest']);manifest=read(manifest_path)
assert sha(manifest_path)==replay['manifest_sha256']
target=Path(manifest['preferences_dir']);assert target.is_dir()
rows=[];bound={str(path):sha(path),str(original_path):sha(original_path),str(out/'independent_freshness.log'):sha(out/'independent_freshness.log'),str(manifest_path):sha(manifest_path)}
for item in replay['renders']:
    child=read(item['child_result']);assert sha(item['child_result'])==item['child_result_sha256']
    observed=Path(child['normal_preferences']);assert observed.is_dir()
    # The two strings differ only in slash spelling. Check actual directory identity.
    assert child['normal_preferences'].replace('\\','/')==manifest['preferences_dir'].replace('\\','/')
    assert observed.resolve()==target.resolve() and observed.samefile(target)
    rows.append(dict(name=item['name'],manifest_text=manifest['preferences_dir'],hom_text=child['normal_preferences'],resolved_directory=str(observed.resolve()),same_existing_directory=True))
    bound[item['child_result']]=sha(item['child_result'])
answer=dict(status='passed',scope='Complete frozen freshness audit plus explicit filesystem-identity correction for its sole path-string comparison defect',
 finished_utc=datetime.now(timezone.utc).isoformat(),reference_kind=replay['reference_kind'],raw_husk_equivalence=False,
 original_frozen_audit_status=original['status'],original_failures=original['failures'],
 correction='Manifest forward-slash and HOM backslash strings name the same existing Windows directory in all nine processes. Path.resolve and Path.samefile independently confirm identity. No original audit, input, numeric limit, pixel, or render was changed.',
 failures=[],cases=original['cases'],path_checks=rows,files=bound,original_audit_input_hashes=original['files'],script_sha256=sha(__file__))
(out/'independent_freshness_path_qualified.json').write_text(json.dumps(answer,indent=2)+'\n');print(json.dumps(dict(status='passed',cases=len(rows))))
