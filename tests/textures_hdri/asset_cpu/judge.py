"""Independent pixel/range/diagnostic check; never transforms candidate pixels."""
import argparse, csv, hashlib, json, math, struct
from pathlib import Path

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def judge(directory):
    prepared=json.loads((directory/'prepared.json').read_text())
    actual={r['case']:r for r in csv.DictReader((directory/'actual.tsv').open(),delimiter='\t')}
    failures=[];rows=[]
    for path,digest in prepared['inputs'].items():
        if not Path(path).is_file() or sha(path)!=digest:failures.append('changed input '+path)
    expected_names={case['name'] for case in prepared['cases']}
    if set(actual)!=expected_names:failures.append('actual case census differs')
    for case in prepared['cases']:
        found=actual.get(case['name'],{});success=found.get('success')=='1'
        valid=case['expected'] is not None;error=found.get('error','')
        passed=success==valid
        row=dict(name=case['name'],expected_success=valid,actual_success=success,error=error)
        if valid and success:
            height=len(case['expected']);width=len(case['expected'][0])
            expected=[float(c) for scanline in case['expected'] for pixel in scanline for c in pixel]
            data=(directory/(case['name']+'.rgba')).read_bytes()
            values=struct.unpack('<'+str(len(data)//4)+'f',data)
            passed &= int(found['width'])==width and int(found['height'])==height and len(values)==len(expected)
            finite=all(math.isfinite(v) for v in values)
            maximum=max((abs(a-b) for a,b in zip(values,expected)),default=math.inf)
            # Raw/codec and pinned named OCIO checks use float precision. The
            # analytic sRGB comparison includes measured OCIO fast-processor
            # approximation (white endpoint 1.00002408), frozen before run02.
            analytic=case['name'] in ('auto_srgb','explicit_srgb','white_srgb')
            tolerance=3e-5 if analytic else 1e-6
            passed &= finite and maximum<=tolerance
            row.update(max_abs_error=maximum,tolerance=tolerance,finite=finite)
        elif not valid:passed &= bool(error) and case['path'] in error
        row['passed']=bool(passed);rows.append(row)
        if not passed:failures.append(case['name'])
    lifecycle=list(csv.DictReader((directory/'lifecycle.tsv').open(),delimiter='\t'))
    if len(lifecycle)!=1:failures.append('missing lifecycle evidence')
    else:
        state=lifecycle[0]
        for name in ('file_change','domain_separation','missing_recovery','resume','config_domain','config_snapshot'):
            if state.get(name)!='1':failures.append('lifecycle '+name)
        if int(state['cancel_callbacks'])<1 or not 0<=float(state['cancel_seconds'])<.25:
            failures.append('bounded cancellation')
    files={p.name:sha(p) for p in directory.iterdir() if p.is_file() and p.name!='judge.json'}
    result=dict(status='passed' if not failures else 'failed',scope='CPU decoder, color interpretation and asset cache only; no GPU',
                cases=rows,lifecycle=lifecycle,failures=failures,prepared_sha256=sha(directory/'prepared.json'),
                judge_sha256=sha(__file__),files=files)
    (directory/'judge.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status=result['status'],cases=len(rows),failures=failures)))
    return 0 if not failures else 1
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);args=p.parse_args()
    raise SystemExit(judge(args.directory))
