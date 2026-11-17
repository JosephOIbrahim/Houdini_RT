"""Bind the diagnostic evidence without accepting failed pixel comparisons."""
from pathlib import Path
import hashlib,json,re
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
files=[ROOT/'gui-accepted-02/independent_husk_judge.json',ROOT/'gui-accepted-02/independent_husk_judge.log',
 ROOT/'gui-accepted-02/husk_failure_archive.json',ROOT/'gui-accepted-02/composite_diagnostic.json',
 ROOT/'gui-aa-probe-01/probe_comparison.json',ROOT/'gui-overlay-probe-01/probe_comparison.json',
 ROOT/'gui-ocio-probe-01/probe_comparison.json',ROOT/'gui-karma-control-01/karma-reference-01/receipt.json',
 ROOT/'gui-karma-control-01/karma-reference-01/comparison.json',ROOT/'gui-karma-control-01/karma-reference-01/karma.log']
for child in ('gui-accepted-02','gui-aa-probe-01','gui-overlay-probe-01','gui-ocio-probe-01','gui-karma-control-01'):
    files.extend(ROOT/child/f for f in ('result.json','process_audit.json'))
    audit=json.loads((ROOT/child/'process_audit.json').read_text());assert audit['owned_child_exited'] and audit['preexisting_unchanged']
karma=json.loads(files[8].read_text());log=files[9].read_text()
rays=int(re.search(r'Camera Rays:\s*([\d,]+)',log).group(1).replace(',',''));assert rays==626*647*256
original=json.loads(files[0].read_text());assert original['status']=='failed'
for child in ('gui-aa-probe-01','gui-overlay-probe-01','gui-ocio-probe-01'):
    assert json.loads((ROOT/child/'probe_comparison.json').read_text())['status']=='failed'
answer=dict(status='qualified',scope='Reference domain qualification only; does not turn failed raw-husk comparisons into passing results',
 original_raw_husk_comparison_status='failed',raw_aov_parity_claimed=False,
 finding='Native Karma reproduces the fractional-coverage discrepancy between viewport flipbook and exact-stage raw headless output. AA-off, hidden selection/origin guides, and OCIO-off do not remove the HoudiniRT discrepancy. The same-host fresh-GUI reference is therefore a separate live-versus-fresh workflow-equivalence test.',
 limitation='Host flipbook output is not a raw AOV export. No alpha remapping, pixel fitting, cropping, masks, or tolerance changes are applied. Underlying host compositor cause is not proven by the alpha-over-self diagnostic.',
 karma=dict(camera_rays=rays,expected_camera_rays=626*647*256,alpha_max_abs=karma['alpha_max_abs'],raw_edge_rmse=karma['raw_alpha_edge_rmse'],alpha_over_self_diagnostic_rmse=karma['alpha_over_self_edge_rmse']),
 future_reference_kind='fresh_regular_GUI',files={str(p):sha(p) for p in files},script_sha256=sha(__file__))
(ROOT/'capture_reference_qualification.json').write_text(json.dumps(answer,indent=2)+'\n');print(json.dumps(dict(status=answer['status'],files=len(files))))
