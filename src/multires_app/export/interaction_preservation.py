"""Independently compare native behavior trees after geometry writeback."""
import re
import math
from xml.etree import ElementTree as ET
from multires_app.engine.scene_engine import VISUAL
from multires_app.export.layout_writeback import GEOMETRY

INVERSE=re.compile(r'\(\(#(touch_(?:begin_)?[xy])-\(([^()]*)\)\)/\(([^()]*)\)\)')
GENERATED=re.compile(r'mr_(?:layout_|state_)?[0-9a-f]+(?:_[a-z]+)?$')
CAPTURE=re.compile(r'mr_[0-9a-f]+_(?:sx|sy|tx|ty)$')


def audit_preservation(source,candidate,writeback):
    original=ET.fromstring(source);adapted=ET.fromstring(candidate)
    names={n.get('name') for n in original.iter() if n.get('name')}
    from multires_app.engine.coordinate_space import replace_references,EXPRESSION_FIELDS
    canvas=writeback.get('coordinateSpace',{});aliases=canvas.get('aliases',{});reverse={v:k for k,v in aliases.items()}
    if aliases:
        if canvas.get('policy')!='source' or set(aliases)!={'screen_width','screen_height'} or len(reverse)!=2:
            raise ValueError('Invalid source canvas input mapping')
        for dimension,name in aliases.items():
            parameters=[n for n in adapted if n.tag=='Var' and n.get('name')==name]
            value=canvas.get('viewport',{}).get(dimension.removeprefix('screen_'))
            if name in names or not GENERATED.fullmatch(name) or len(parameters)!=1 or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise ValueError('Invalid source canvas parameter')
            if parameters[0].attrib!={'name':name,'expression':str(float(value))} or list(parameters[0]):
                raise ValueError('Source canvas parameter is not the approved constant')
    remaps={(c['tag'],c['attribute'],c['new']):c['old'] for c in writeback['changes']
            if c['kind']=='resource-family-scale'}
    coordinate_changes=0;generated=0
    def tree(node,parent=None,is_adapted=False):
        nonlocal coordinate_changes,generated
        name=node.get('name','')
        if is_adapted and name not in names:
            if (parent is adapted and node.tag=='Var' and GENERATED.fullmatch(name) or
                parent is not None and parent.tag=='Trigger' and node.tag=='VariableCommand' and CAPTURE.fullmatch(name)):
                if list(node):raise ValueError('Generated layout parameters cannot contain behavior')
                generated+=1;return None
        attrs=dict(node.attrib)
        if node.tag in VISUAL|{'Position','Size','Item','Source'}:
            for key in GEOMETRY:attrs.pop(key,None)
        if is_adapted:
            for key,value in list(attrs.items()):
                if key in EXPRESSION_FIELDS:
                    value=replace_references(value,reverse);attrs[key]=value
                if key in {'condition','expression'}:
                    def inverse(match):
                        for term in match.group(2,3):
                            if not re.fullmatch(r'#mr_[a-z0-9_]+|-?\d+(?:\.\d+)?(?:e[+-]?\d+)?',term):
                                raise ValueError('Unexpected gesture coordinate adapter')
                        return '#'+match[1]
                    restored,count=INVERSE.subn(inverse,value)
                    coordinate_changes+=count;attrs[key]=restored
                if (node.tag,key,value) in remaps:attrs[key]=remaps[(node.tag,key,value)]
        return (node.tag,attrs,(node.text or '').strip(),[value for child in node
            if (value:=tree(child,node,is_adapted)) is not None])
    if tree(original)!=tree(adapted,is_adapted=True):
        raise ValueError('Adaptation changed original behavior, hierarchy, or non-layout attributes')
    return {'passed':True,'originalVariables':sum(n.tag=='Var' for n in original.iter()),
            'originalTriggers':sum(n.tag=='Trigger' for n in original.iter()),
            'originalCommands':sum(n.tag.endswith('Command') for n in original.iter()),
            'originalButtons':sum(n.tag=='Button' for n in original.iter()),
            'coordinateReferencesMapped':coordinate_changes,'generatedLayoutParametersAndCaptures':generated,
            'originalHierarchyPreserved':True,'originalConditionsPreservedAfterInverseCoordinateMapping':True,
            'sourceCanvasInputs':canvas if aliases else None,
            'scope':'Original tree equality after restoring approved source-canvas inputs and inverse touch coordinates, excluding visual geometry and approved glyph families. Does not assert native device execution.'}
