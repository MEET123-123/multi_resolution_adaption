"""Patch opening attributes and approved generated parameters, preserving source text."""
import re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

# Quoted '>' characters, comments, CDATA and processing instructions are atomic.
TOKENS=re.compile(r'<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>|<![^>]*>|</?[A-Za-z_][\w:.-]*(?:[^<>\"\']|\"[^\"]*\"|\'[^\']*\')*>')
ATTR=re.compile(r'([\w:.-]+)(\s*=\s*)([\"\'])([\s\S]*?)\3')
GEOMETRY={'x','y','w','h','width','height','size','space'}


def patch_script(data,original_nodes,root,resource_remaps=None,coordinate_space=None):
    """Original nodes are (Element reference, original attributes, original children).

    Native scripts require existing gesture-coordinate expressions to be remapped
    as well as geometry. These approved changes are reported separately, never
    treated as arbitrary command edits.
    """
    from multires_app.engine.coordinate_space import replace_references,EXPRESSION_FIELDS
    from multires_app.export.touch_coordinates import pure_touch_definitions,expand_touch,has_touch,restore_inverse,is_capture_trigger
    touch_definitions=pure_touch_definitions((n.tag,a) for n,a,_ in original_nodes)
    reverse={v:k for k,v in (coordinate_space or {}).get('aliases',{}).items()}
    old_ids={id(n) for n,_,_ in original_nodes};old_attributes={id(n):a for n,a,_ in original_nodes};changes=[];insertions={}
    for node,attrs,children in original_nodes:
        current_original=[c for c in node if id(c) in old_ids]
        if current_original!=children:raise ValueError('writeback changed original child order or removed nodes')
        if set(attrs)-set(node.attrib):raise ValueError('writeback removed attributes')
        for key,value in node.attrib.items():
            if attrs.get(key)==value:continue
            resource_change=key=='src' and (resource_remaps or {}).get(id(node))==value and value.startswith('_multires/')
            canvas_change=key in EXPRESSION_FIELDS and value!=attrs.get(key) and replace_references(value,reverse)==attrs.get(key)
            allowed=key in GEOMETRY or resource_change or canvas_change
            if key in {'expression','condition'}:
                # Only original touch expressions may be remapped; variable names,
                # resource references and ordinary business conditions stay intact.
                expanded=expand_touch(attrs.get(key,''),touch_definitions)
                allowed=canvas_change or (has_touch(expanded) and '#mr_' in value and
                    restore_inverse(replace_references(value,reverse))==expanded)
            if not allowed:raise ValueError('writeback field is not approved: '+str(node.tag)+'.'+key)
            changes.append({'tag':node.tag,'attribute':key,'old':attrs.get(key),'new':value,
                            'kind':'resource-family-scale' if resource_change else 'geometry' if key in GEOMETRY else 'source-canvas-input-remap' if canvas_change else 'gesture-coordinate-remap'})
        added=[c for c in node if id(c) not in old_ids]
        if added:
            for child in added:
                is_parameter=node is root and child.tag=='Var' and re.fullmatch(r'mr_(?:layout_|state_)?[0-9a-f]+(?:_[a-z]+)?',child.get('name',''))
                is_capture=node.tag=='Trigger' and child.tag=='VariableCommand' and re.fullmatch(r'mr_[0-9a-f]+_(?:sx|sy|tx|ty)',child.get('name',''))
                is_down=node.tag in {'Triggers','Button'} and is_capture_trigger(child)
                if not(is_parameter or is_capture or is_down):raise ValueError('unapproved generated script node: '+str(child.tag))
                if list(child) and not is_down:raise ValueError('generated parameters must be leaves')
            # The exporter only inserts generated nodes before original children.
            if list(node)[:len(added)]!=added:raise ValueError('generated nodes must precede original children')
            insertions[id(node)]=''.join(ET.tostring(c,encoding='unicode') for c in added)
    text=data.decode('utf-8-sig');matches=[m for m in TOKENS.finditer(text) if not m.group().startswith(('</','<!','<?'))]
    nodes=[n for n,_,_ in original_nodes if isinstance(n.tag,str)]
    if len(matches)!=len(nodes):raise ValueError('source XML token/node mismatch; export refused')
    edits=[]
    for match,node in zip(matches,nodes):
        raw=match.group();name=re.match(r'<([\w:.-]+)',raw).group(1)
        if name!=node.tag:raise ValueError('source XML order mismatch')
        existing=set()
        def replace(attr):
            key=attr[1];existing.add(key);value=node.get(key)
            # Reuse untouched lexemes, including their original entity escaping.
            old=old_attributes[id(node)]
            if old.get(key)==value:return attr[0]
            return key+attr[2]+attr[3]+escape(value,{'"':'&quot;',"'":'&apos;'})+attr[3]
        patched=ATTR.sub(replace,raw)
        extra=''.join(' '+key+'="'+escape(value,{'"':'&quot;'})+'"' for key,value in node.attrib.items() if key not in existing)
        end=patched.rfind('/>') if patched.endswith('/>') else patched.rfind('>')
        patched=patched[:end]+extra+patched[end:]
        if id(node) in insertions:
            if patched.endswith('/>'):patched=patched[:-2]+'>'+insertions[id(node)]+'</'+node.tag+'>'
            else:patched+=insertions[id(node)]
        if raw!=patched:edits.append((match.start(),match.end(),patched))
    for start,end,value in reversed(edits):text=text[:start]+value+text[end:]
    output=(b'\xef\xbb\xbf' if data.startswith(b'\xef\xbb\xbf') else b'')+text.encode('utf-8')
    # Semantic equality proves the lexical patch included every intended change.
    parsed=ET.fromstring(output,parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    def signature(n):return (n.tag if isinstance(n.tag,str) else 'comment',n.attrib,n.text if n.text and n.text.strip() else None,[signature(c) for c in n])
    if signature(parsed)!=signature(root):raise ValueError('lexical writeback does not match approved XML')
    return output,{'passed':True,'sourceTextPreservedOutsidePatches':True,'originalNodes':len(nodes),
                   'changedAttributes':len(changes),'generatedParameters':sum(len(list(n))-len(children) for n,_,children in original_nodes),
                   'allowedGeometryFields':sorted(GEOMETRY),'approvedSystemChanges':'mr_ layout/state parameters, explicit source-canvas dimension aliases, and inverse gesture coordinate captures',
                   'assetReferencesPreserved':not bool(resource_remaps),'approvedResourceFamilyRemaps':len(resource_remaps or {}),
                   'originalNodeOrderPreserved':True,'coordinateSpace':coordinate_space or {'policy':'target'},'changes':changes}
