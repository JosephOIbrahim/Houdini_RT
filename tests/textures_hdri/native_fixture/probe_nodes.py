"""CPU-only installed H22 node/default inventory; does not create a renderer."""
import hashlib,json
from pathlib import Path
import hou,voptoolutils
from output_path import output_path
ROOT=output_path('node_inventory.json')
assert hou.applicationVersion()==(22,0,429) and hou.hipFile.isNewFile()
library=hou.node('/stage').createNode('materiallibrary','native_materials')
library.parm('genpreviewshaders').set(0)
builder=voptoolutils._setupMtlXBuilderSubnet(destination_node=library,name='openpbr')
report={'houdini':hou.applicationVersionString(),'nodes':{},'node_types':{}}
for category,names in ((hou.vopNodeTypeCategory(),('image','texcoord','geomprop','transform2d','place2d')),
                       (hou.lopNodeTypeCategory(),('domelight',))):
    report['node_types'][category.name()]=[name for name in category.nodeTypes() if any(key in name.lower() for key in names)]
def inspect(node):
    parms={}
    for parm in node.parms():
        if parm.parmTemplate().type() in (hou.parmTemplateType.Folder,hou.parmTemplateType.FolderSet):continue
        try:value=parm.eval()
        except Exception as e:value=str(e)
        item={'value':value,'label':parm.parmTemplate().label()}
        try:item.update(menu_items=parm.menuItems(),menu_labels=parm.menuLabels())
        except Exception:pass
        parms[parm.name()]=item
    return {'type':node.type().name(),'parms':parms,'inputs':node.inputNames() if isinstance(node,hou.VopNode) else []}
for name in ('mtlximage','mtlxtexcoord','mtlxgeompropvalue','mtlxtransform2d','mtlxplace2d'):
    try:report['nodes'][name]=inspect(builder.createNode(name,name+'_probe'))
    except Exception as e:report['nodes'][name]={'error':str(e)}
for name in ('domelight','domelight::2.0'):
    try:report['nodes'][name]=inspect(hou.node('/stage').createNode(name,'native_'+name.split(':')[0]))
    except Exception as e:report['nodes'][name]={'error':str(e)}
report['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(ROOT/'node_inventory.json').write_text(json.dumps(report,indent=2)+'\n')
print('Native node inventory:',ROOT/'node_inventory.json')
