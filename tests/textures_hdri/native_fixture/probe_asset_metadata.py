import json
from pathlib import Path
from pxr import Usd,Sdf
from output_path import output_path
root=output_path('asset_metadata.json')
stage=Usd.Stage.Open(str(root/'texcoord.usda'))
attr=stage.GetAttributeAtPath('/materials/openpbr/base_image.inputs:file')
meta=attr.GetMetadata('default');value=attr.Get()
result={'default_metadata':{'path':meta.path,'resolvedPath':meta.resolvedPath},
        'attribute_get':{'path':value.path,'resolvedPath':value.resolvedPath},
        'time_varying':attr.ValueMightBeTimeVarying()}
result['layer_defaults']=[{'layer':spec.layer.identifier,'path':spec.default.path,
                         'resolvedPath':spec.default.resolvedPath}
                        for spec in attr.GetPropertyStack() if spec.HasDefaultValue()]
(root/'asset_metadata.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
