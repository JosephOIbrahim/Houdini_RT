"""Deterministic artist-fixture inputs; independent judges supply their own oracles."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
import OpenImageIO as oiio


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    files={}
    def image(name,pixels,kind):
        path=out/name;spec=oiio.ImageSpec(pixels.shape[1],pixels.shape[0],pixels.shape[2],kind)
        output=oiio.ImageOutput.create(str(path));assert output.open(str(path),spec)
        assert output.write_image(pixels);output.close()
        files[name]=dict(path=str(path),sha256=sha(path),shape=list(pixels.shape),values=pixels.tolist())
    y,x=np.mgrid[0:4,0:4]
    a=np.stack((32+50*x,24+53*y,224-33*x-19*y),axis=-1).astype(np.uint8)
    b=np.stack((220-30*y,180-35*x,20+42*x+12*y),axis=-1).astype(np.uint8)
    image('base_a.png',a,oiio.UINT8);image('base_b.png',b,oiio.UINT8)
    image('rough_a.exr',(.1+.05*x+.15*y)[...,None].astype(np.float32),oiio.FLOAT)
    image('rough_b.exr',(.85-.15*x-.05*y)[...,None].astype(np.float32),oiio.FLOAT)
    sky=np.full((8,16,3),.125,dtype=np.float32)
    sky[2:4,2:5]=[4,2,1];sky[4:6,11:14]=[.5,1,2]
    other=np.full((8,16,3),.15,dtype=np.float32)
    other[1:3,7:11]=[1,3,1];other[5:7,1:4]=[2,.5,.25]
    image('sky_a.exr',sky,oiio.FLOAT);image('sky_b.exr',other,oiio.FLOAT)
    receipt=dict(status='prepared',scope='Immutable native GUI input images, not independent renderer reference',
                 recipe=str(Path(__file__).resolve()),recipe_sha256=sha(__file__),files=files)
    (out/'assets.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(out/'assets.json')


if __name__=='__main__':main()
