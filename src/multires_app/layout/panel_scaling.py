"""Evidence-based transient panel recognition and shared foreground sizing."""
from multires_app.pipeline.scene_clusters import box_union


def infer_panel_scale_families(project):
    nodes=project['model']['nodes'];source=project.get('sourceDevice',{});vp=source.get('viewport',{})
    if not vp or not source.get('layouts'):return []
    profiles=[project.get('sourceProfile',{}),*[d['profile'] for d in project.get('devices',[])]]
    if any(p.get('adaptation',{}).get('anchorLayout',{}).get(k) for p in profiles for k in ('groups','constraints')):return []
    order=project['nodeOrder'];samples=[]
    for layout in source['layouts']:
        samples.append({order[r[0]]:r for r in layout['rows'] if r[5]>0})
    clocks=[c for c in project['components'] if c['role']=='header' and not c.get('layout') and
            any(nodes[u]['tag'] in {'Time','DateTime'} for u in c['members'])]
    panels=[]
    for c in project['components']:
        if c.get('layout') or len(c.get('bindingButtons',[]))<3 or c['role'] in {'background','gesture-layer','decoration','interaction-layer'}:continue
        if any(u in samples[0] for u in c['members'] if nodes[u]['tag'] not in {'Group','Button'}):continue
        gated=any(nodes[u]['tag']=='Group' and nodes[u]['attributes'].get('visibility') for u in c['roots'])
        if not gated:continue
        for sample in samples[1:]:
            rows=[sample[u] for u in c['members'] if u in sample and nodes[u]['tag'] not in {'Group','Button'}]
            if not rows:continue
            b=box_union([dict(zip(('x','y','w','h'),r[1:5])) for r in rows])
            surface=any(nodes[order[r[0]]]['tag']=='Image' and r[3]*r[4]>=b['w']*b['h']*.8 for r in rows)
            if (surface and vp['width']*.4<=b['w']<=vp['width']*1.02 and vp['height']*.2<=b['h']<=vp['height']*.85
                and abs(b['x']+b['w']/2-vp['width']/2)<=vp['width']*.03):
                c['role']='dialog';c['semanticRole']='settings-panel';panels.append(c);break
    if not panels or not clocks:return []
    family='transient-panel-foreground'
    members=[*clocks,*panels,*[c for c in project['components'] if c.get('cornerAttachment')]]
    for c in members:c['resourceScaleFamily']=family
    for c in clocks:c['semanticRole']='datetime'
    evidence={'id':family,'members':[c['id'] for c in members],'panels':[c['id'] for c in panels],
              'references':[c['id'] for c in clocks],
              'provenance':'initially hidden centered image-backed panel with controls; co-visible clock and corner controls',
              'policy':'one device-wide resource scale; reserve full vertical scene including bottom attachment margins'}
    project['resourceScaleFamilies']=[evidence]
    return [evidence]


def shared_resource_scales(project,device,preferred,glyph_scales,region):
    """Size a family before freezing public content, using all source states.

    Preserve a conservative source vertical band including its bottom margin.
    This prevents a newly opened panel from being squeezed independently into
    the remainder of a screen whose clock was already frozen at a larger size.
    """
    result={};evidence=[];vp=project['sourceDevice']['viewport'];order=project['nodeOrder'];nodes=project['model']['nodes']
    owners=project['nodeComponents'];components={c['id']:c for c in project['components']}
    for family in project.get('resourceScaleFamilies',[]):
        members=set(family['members']);top=vp['height'];max_width=0.;found=False
        for layout in project['sourceDevice']['layouts']:
            boxes=[]
            for r in layout['rows']:
                u=order[r[0]]
                if owners.get(u) not in members or r[5]<=0 or nodes[u]['tag']=='Group' or min(r[3:5])<=0:continue
                if r[1]+r[3]<=0 or r[1]>=vp['width'] or r[2]+r[4]<=0 or r[2]>=vp['height']:continue
                boxes.append(dict(zip(('x','y','w','h'),r[1:5])))
            if boxes:
                b=box_union(boxes);top=min(top,max(0,b['y']));max_width=max(max_width,b['w']);found=True
        if not found:continue
        vertical=region['h']/max(1,vp['height']-top)
        horizontal=region['w']/max(1,max_width)
        scale=min(preferred,vertical*.97,horizontal*.97,*[glyph_scales[c] for c in members if c in glyph_scales])
        for cid in members:
            if cid in components:result[cid]=scale
        evidence.append({**family,'scale':scale,'preferredScale':preferred,'sourceBandTop':top,'verticalLimit':vertical,
                         'horizontalLimit':horizontal,'reservedSlack':.03})
    device['resourceScaleFamilies']=evidence
    return result
