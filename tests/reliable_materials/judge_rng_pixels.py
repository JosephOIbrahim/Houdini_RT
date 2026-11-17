"""CPU/OIIO correlation of predetermined PCG rejection coordinates with old EXRs."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import OpenImageIO as oiio


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('observations',type=Path)
    parser.add_argument('render_run',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();observations=json.loads(args.observations.read_text());results=[];failures=[]
    source=json.loads((args.render_run/'receipt.json').read_text())
    for seed in (101,107,113):
        render=next(r for r in source['renders'] if r['case']=='lambert_dome' and r['seed']==seed and r['renderer']=='HdRTRendererPlugin')
        path=Path(render['image']);assert sha(path)==render['image_sha256']
        image=oiio.ImageInput.open(str(path));assert image,oiio.geterror()
        spec=image.spec();assert (spec.width,spec.height)==(128,96) and str(spec.format)=='float'
        pixels=np.asarray(image.read_image('float')).reshape(spec.height,spec.width,spec.nchannels)
        blue=pixels[...,list(spec.channelnames).index('B')];image.close()
        median=float(np.median(blue));deficit=median-blue
        # Half of the independently expected one-sample loss separates it from
        # float accumulation rounding. Coordinates are predicted before reading.
        threshold=.8/4096*.5
        observed={tuple(map(int,p)) for p in np.argwhere(deficit>threshold)}
        predicted={(h['row_exr'],h['x']) for h in observations['hits'] if h['seed']==seed}
        if observed!=predicted:failures.append(dict(seed=seed,missing=sorted(predicted-observed),extra=sorted(observed-predicted)))
        details=[dict(row=row,x=x,blue=float(blue[row,x]),deficit=float(deficit[row,x]),
                      expected_one_sample_loss=.8/4096) for row,x in sorted(predicted)]
        results.append(dict(seed=seed,image=str(path),image_sha256=sha(path),typical_blue=median,
                            predicted_pixels=sorted(predicted),observed_pixels=sorted(observed),details=details))
    result=dict(status='passed' if not failures else 'failed',scope='Historical zero-PDF pixel correlation, not corrected GPU acceptance',
                observations_sha256=sha(args.observations),render_receipt_sha256=sha(args.render_run/'receipt.json'),
                script_sha256=sha(Path(__file__)),checks=results,failures=failures)
    args.out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(status=result['status'],failures=failures)))
    return 0 if not failures else 16


if __name__=='__main__':raise SystemExit(main())
