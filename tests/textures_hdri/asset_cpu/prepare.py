"""Freeze encoded fixtures and independent expected pixels before native decode."""
import argparse, hashlib, json, math, shutil, os
from pathlib import Path
import numpy as np
import OpenImageIO as oiio
import PyOpenColorIO as ocio

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, values, **metadata):
    values=np.ascontiguousarray(values,dtype=np.float32)
    spec=oiio.ImageSpec(values.shape[1],values.shape[0],values.shape[2],oiio.FLOAT)
    for key,value in metadata.items():
        if key in ('x','y','full_x','full_y'): setattr(spec,key,value)
        else: spec.attribute(key,value)
    output=oiio.ImageOutput.create(str(path)); assert output and output.open(str(path),spec)
    assert output.write_image(values) and output.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('manifest',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads(args.manifest.read_text());cases=[];inputs={str(args.manifest.resolve()):sha(args.manifest)}
    config=ocio.GetCurrentConfig()
    def case(name,path,space,channels,pixels=None,minimum=0,maximum=1,environment=False,why=''):
        target=[]
        if pixels is not None:
            for row in pixels:
                values=[]
                for pixel in row:
                    rgb=([pixel[0]]*3 if channels==1 or len(pixel)<3 else list(pixel[:3]))
                    if channels==3 and space in ('','srgb_texture') and path.suffix.lower() in ('.png','.jpg'):
                        rgb=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
                    elif channels==3 and space not in ('','Raw','raw'):
                        rgb=config.getProcessor(space,ocio.ROLE_SCENE_LINEAR).getDefaultCPUProcessor().applyRGB(rgb)
                    values.append(rgb+[1.])
                target.append(values)
        cases.append(dict(name=name,path=str(path),space=space,channels=channels,minimum=minimum,maximum=maximum,
                          environment=environment,expected=target or None,rejection=why))
        if path.exists(): inputs[str(path)]=sha(path)
    for name,asset in manifest['assets'].items():
        path=Path(asset['path']);assert sha(path)==asset['sha256']
        invalid=name.startswith('invalid_');pixels=asset['decoded']['pixels']
        case('raw_'+name.replace('.','_'),path,'Raw',3,None if invalid else pixels,maximum=3.4e38,why='invalid decoded value' if invalid else '')
    chart=manifest['assets']['chart_4x4_8.png'];path=Path(chart['path']);pixels=chart['decoded']['pixels']
    case('auto_srgb',path,'',3,pixels)
    case('explicit_srgb',path,'srgb_texture',3,pixels)
    case('scalar_raw_ignores_space',path,'srgb_texture',1,pixels)
    for name in ('chart_4x4.exr','env_color.hdr'):
        asset=manifest['assets'][name]
        case('auto_'+name.replace('.','_'),Path(asset['path']),'',3,asset['decoded']['pixels'],maximum=3.4e38)
    case('named_acescg',path,'ACEScg',3,pixels,maximum=3.4e38,minimum=-3.4e38)
    case('unknown_space',path,'no_such_colorspace_for_test',3,why='unknown OCIO space')
    case('range_reject',path,'Raw',3,maximum=.01,why='input range')
    for name,valid in (('scalar_ior.exr',True),('scalar_roughness.exr',False)):
        asset=manifest['assets'][name]
        case('ior_domain_'+Path(name).stem,Path(asset['path']),'Raw',1,
             asset['decoded']['pixels'] if valid else None,minimum=1,maximum=3,why='' if valid else 'IOR range')
    case('missing',out/'missing.png','Raw',3,why='missing file')
    malformed=out/'malformed.png';malformed.write_bytes(b'not an image')
    case('malformed',malformed,'Raw',3,why='malformed image')
    unsupported=out/'unsupported.txt';unsupported.write_bytes(path.read_bytes())
    case('unsupported',unsupported,'Raw',3,why='extension')
    for name,metadata in [('orientation',{'Orientation':6}),('window',{'x':1}),('display_origin',{'full_x':1})]:
        item=out/(name+'.exr');write(item,np.full((2,2,3),.25),**metadata)
        case(name,item,'Raw',3,why=name)
    deep=out/'deep.exr';spec=oiio.ImageSpec(2,2,3,oiio.FLOAT);spec.deep=True
    values=oiio.DeepData();values.init(spec)
    for pixel in range(4):
        values.set_samples(pixel,1)
        for channel in range(3):values.set_deep_value(pixel,channel,0,.25)
    output=oiio.ImageOutput.create(str(deep));assert output and output.open(str(deep),spec)
    assert output.write_deep_image(values) and output.close()
    case('deep',deep,'Raw',3,why='deep EXR')
    white=out/'white.png';write(white,np.ones((2,2,3)))
    case('white_srgb',white,'',3,[[[1,1,1]]*2]*2)
    write(out/'cancel.png',np.ones((2048,2048,3)))
    shutil.copyfile(path,out/'first.png');shutil.copyfile(Path(manifest['assets']['chart_5x3_8.png']['path']),out/'second.png')
    endpoint=config.getProcessor('srgb_texture',ocio.ROLE_SCENE_LINEAR).getDefaultCPUProcessor().applyRGB([1,1,1])
    root=Path(__file__).resolve().parents[3]
    for name in ('imageAssets.cpp','imageAssets.h','vkTextures.h'):
        source=root/'src/hdRT'/name;inputs[str(source)]=sha(source)
    for source in Path(__file__).resolve().parent.glob('*'):
        if source.is_file():inputs[str(source)]=sha(source)
    config_path=Path(os.environ['OCIO']).resolve();inputs[str(config_path)]=sha(config_path)
    frozen=out/'frozen';frozen.mkdir()
    for source in list(Path(__file__).resolve().parent.glob('*'))+[root/'src/hdRT'/n for n in ('imageAssets.cpp','imageAssets.h','vkTextures.h')]:
        if source.is_file():shutil.copyfile(source,frozen/source.name)
    frozen=dict(status='prepared',cases=cases,inputs=inputs,config_id=config.getCacheID(),
                endpoint_white=endpoint,script_sha256=sha(__file__))
    (out/'prepared.json').write_text(json.dumps(frozen,indent=2)+'\n')
    (out/'requests.tsv').write_text(''.join('\t'.join(map(str,[c['name'],c['path'],c['space'] or '-',c['channels'],
        c['minimum'],c['maximum'],int(c['environment'])]))+'\n' for c in cases))
    print(json.dumps(dict(status='prepared',count=len(cases),white_endpoint=endpoint)))
if __name__=='__main__':main()
