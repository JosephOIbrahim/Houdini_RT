"""Read native Houdini color context without modifying it or rendering."""
import hashlib,json,os
from pathlib import Path
import hou
import PyOpenColorIO as ocio
from output_path import output_path
ROOT=output_path('ocio_context.json')
result={'houdini':hou.applicationVersionString(),'environment':{k:os.environ.get(k) for k in
    ('OCIO','HOUDINI_OCIO','HOUDINI_OCIO_FILENAME','HOUDINI_OCIO_ON')},
    'native_config':hou.Color.ocio_configPath(),'native_roles':hou.Color.ocio_roles(),
    'native_spaces':hou.Color.ocio_spaces(),'default_display':hou.Color.ocio_defaultDisplay(),
    'default_view':hou.Color.ocio_defaultView(),'ocio_version':ocio.GetVersion(),'configs':{},'native_transforms':{}}
for name,factory in [('current',ocio.GetCurrentConfig),('environment',ocio.Config.CreateFromEnv),
                     ('native_path',lambda:ocio.Config.CreateFromFile(result['native_config']))]:
    try:
        config=factory();samples={}
        for source in ('srgb_texture','Raw','lin_rec709','ACEScg','scene_linear'):
            try:samples[source]=config.getProcessor(source,'scene_linear').getDefaultCPUProcessor().applyRGB([.5,.2,.1])
            except Exception as e:samples[source]={'error':str(e)}
        result['configs'][name]={'name':config.getName(),'description':config.getDescription(),
            'cache_id':config.getCacheID(),'scene_linear':config.getColorSpace('scene_linear').getName(),
            'roles':list(config.getRoles()),'samples':samples}
    except Exception as e:result['configs'][name]={'error':str(e)}
for source in ('srgb_texture','Raw','lin_rec709','ACEScg','scene_linear'):
    try:result['native_transforms'][source]=hou.Color((.5,.2,.1)).ocio_transform(source,'scene_linear','').rgb()
    except Exception as e:result['native_transforms'][source]={'error':str(e)}
result['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(ROOT/'ocio_context.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('native_config','environment','default_display','default_view','native_transforms')},indent=2))
