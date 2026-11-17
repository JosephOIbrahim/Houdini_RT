"""Generate analytic assets before candidate renders; run with matching hython."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,math,struct,zlib
import numpy as np
import OpenImageIO as oiio
import oracle

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def png(path,rows,bits=8):
    """Independent minimal RGB PNG encoder: no production decoder or image writer."""
    h=len(rows);w=len(rows[0]);maximum=(1<<bits)-1
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    samples=[]
    for row in rows:
        data=[max(0,min(maximum,round(c*maximum))) for p in row for c in p[:3]]
        samples.append(b'\0'+(bytes(data) if bits==8 else struct.pack('>'+str(len(data))+'H',*data)))
    body=chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,bits,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(samples)))+chunk(b'IEND',b'')
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+body)

def write_image(path,rows,format):
    values=np.ascontiguousarray(rows,dtype=np.float32);height,width,channels=values.shape
    output=oiio.ImageOutput.create(str(path));assert output is not None,path
    spec=oiio.ImageSpec(width,height,channels,format)
    if path.suffix=='.jpg':spec.attribute('CompressionQuality',95)
    assert output.open(str(path),spec),output.geterror()
    assert output.write_image(values),output.geterror();assert output.close()

def decoded(path):
    source=oiio.ImageInput.open(str(path));assert source is not None,path
    spec=source.spec();pixels=np.asarray(source.read_image('float'),dtype=np.float64);source.close()
    def finite_json(value):
        if isinstance(value,list):return [finite_json(v) for v in value]
        return value if math.isfinite(value) else 'NaN' if math.isnan(value) else 'Infinity' if value>0 else '-Infinity'
    return dict(width=spec.width,height=spec.height,channels=list(spec.channelnames),storage=str(spec.format),pixels=finite_json(pixels.tolist()))

def chart(width,height):
    return [[tuple(((x*37+y*61+offset)%221+17)/255 for offset in (0,71,143)) for x in range(width)] for y in range(height)]

def environments():
    result={}
    for name,color in (('white',(1.,1.,1.)),('color',(.2,.6,3.)),('black',(0.,0.,0.))):
        result[name]=[[color]*8 for _ in range(4)]
    result['direction']=[[(.5+.4*d[0],.5+.4*d[1],.5+.4*d[2]) for x in range(16) for d in [oracle.latlong_direction((x+.5)/16,(y+.5)/8)]] for y in range(8)]
    for name,x,y in (('sun',5,3),('pole',7,0),('seam',31,7)):
        rows=[[(.001,.001,.001)]*32 for _ in range(16)];rows[y][x]=(10000.,4000.,1000.);result[name]=rows
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);assets={}
    def record(path,authored,usage):
        found=decoded(path);assets[path.name]=dict(path=str(path),sha256=sha(path),authored_pixels=authored,decoded=found,usage=usage)
    for w,h in ((4,4),(5,3)):
        rows=chart(w,h)
        for bits in (8,16):
            path=out/f'chart_{w}x{h}_{bits}.png';png(path,rows,bits);record(path,rows,'Raw versus sRGB color, orientation, filtering, wrap')
        for ext,fmt in (('jpg',oiio.UINT8),('exr',oiio.FLOAT)):
            path=out/f'chart_{w}x{h}.{ext}';write_image(path,rows,fmt);record(path,rows,'Codec and sampling; JPEG oracle uses decoded texels')
    for name,rows in environments().items():
        path=out/f'env_{name}.exr';write_image(path,rows,oiio.FLOAT);record(path,rows,'Linear HDRI radiance and importance sampling')
        if name=='color':
            path=out/'env_color.hdr';write_image(path,rows,oiio.FLOAT);record(path,rows,'Radiance RGBE high-dynamic-range decode')
    for name,rows,fmt in (('scalar_roughness',[[((x+4*y+1)/17,) for x in range(4)] for y in range(4)],oiio.FLOAT),
                          ('scalar_ior',[[((x+4*y)/8+1,) for x in range(4)] for y in range(4)],oiio.FLOAT),
                          ('chart_half',chart(4,4),oiio.HALF)):
        path=out/(name+'.exr');write_image(path,rows,fmt);record(path,rows,'Single-channel scalar domains or HALF decode')
    for name,value in (('negative',-.1),('nan',float('nan')),('infinity',float('inf'))):
        path=out/f'invalid_{name}.exr';rows=[[(value,.5,.5)]*2 for _ in range(2)]
        write_image(path,rows,oiio.FLOAT);record(path,None,'Explicit invalid-texel rejection; never stale-success')
    (out/'corrupt.png').write_bytes(b'not a PNG file\n')
    manifest=dict(schema=1,status='generated',scope='Independent assets frozen before candidate observation',created_utc=datetime.now(timezone.utc).isoformat(),
        generator_sha256=sha(__file__),oracle_sha256=sha(Path(__file__).with_name('oracle.py')),assets=assets,
        invalid_files={'corrupt.png':sha(out/'corrupt.png'),'missing.png':None})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n');print(json.dumps(dict(output=str(out),assets=len(assets))))
if __name__=='__main__':main()
