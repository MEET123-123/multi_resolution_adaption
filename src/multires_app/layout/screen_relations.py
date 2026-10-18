"""Conservative, auditable screen attachments and distributed footer rails."""
from __future__ import annotations
import re


def edge_intent(box, viewport):
    w,h=viewport['width'],viewport['height']
    if min(box['w'],box['h'])<=0 or box['x']<-.1 or box['y']<-.1 or box['x']+box['w']>w+.1 or box['y']+box['h']>h+.1:return {}
    result={}
    for axis,size,extent,start,end in [('x','w',w,'left','right'),('y','h',h,'top','bottom')]:
        if box[size]>extent*.6:continue
        margins=[(start,box[axis]),(end,extent-box[axis]-box[size])]
        name,distance=min(margins,key=lambda item:item[1])
        if distance<=min(96.,extent*.05):result[axis]={'edge':name,'margin':max(0.,distance),'provenance':'source margin within five percent of screen edge'}
    return result


def screen_aliases(model):
    aliases={'screen_width':'x','screen_height':'y'}
    for _ in range(8):
        before=len(aliases)
        for name,var in model.get('variables',{}).items():
            expression=var.get('attributes',{}).get('expression','').strip().strip('() ')
            match=re.fullmatch(r'#([A-Za-z_]\w*)',expression)
            if match and match[1] in aliases:aliases[name]=aliases[match[1]]
        if len(aliases)==before:break
    return aliases


def scripted_edges(box, viewport, members, model, aliases=None):
    result=edge_intent(box,viewport);aliases=screen_aliases(model) if aliases is None else aliases
    for uid in members:
        attrs=model.get('nodes',{}).get(uid,{}).get('attributes',{})
        for axis,size,extent,end in [('x','w',viewport['width'],'right'),('y','h',viewport['height'],'bottom')]:
            expression=attrs.get(axis,'').strip().lstrip('(')
            match=re.match(r'#([A-Za-z_]\w*)\s*\)?\s*-',expression)
            margin=extent-box[axis]-box[size]
            if match and aliases.get(match[1])==axis and box[size]<extent*.6 and -.1<=margin<=extent*.2 and box[axis]>=0:
                result[axis]={'edge':end,'margin':max(0.,margin),'provenance':'script position subtracts from screen dimension (including direct aliases)'}
    return result


def footer_rails(elements, viewport):
    """Require >=3 peers, a wide source span, alignment and uniform spacing.

    Deliberately excludes authored layouts, overlays and partial/offscreen rows.
    Far-apart buttons need not be spatial neighbours to form a rail.
    """
    w,h=viewport['width'],viewport['height'];candidates=[]
    for e in elements:
        b=e['bbox']
        if e.get('special') or e.get('layout') or e['role'] in {'popup','dialog','overlay'}:continue
        if b['y']<h*.78 or b['x']<0 or b['x']+b['w']>w or b['y']+b['h']>h+.1:continue
        if b['w']>w*.25 or b['h']>h*.15:continue
        candidates.append(e)
    rails=[];used=set()
    for seed in sorted(candidates,key=lambda e:(e['bbox']['y'],e['id'])):
        if seed['id'] in used:continue
        b=seed['bbox'];cy=b['y']+b['h']/2
        row=[e for e in candidates if e['id'] not in used and e['role']==seed['role'] and
             abs(e['bbox']['y']+e['bbox']['h']/2-cy)<=max(3.,min(b['h'],e['bbox']['h'])*.12)]
        row.sort(key=lambda e:e['bbox']['x'])
        if len(row)<3:continue
        bs=[e['bbox'] for e in row];centers=[b['x']+b['w']/2 for b in bs]
        gaps=[r['x']-l['x']-l['w'] for l,r in zip(bs,bs[1:])]
        steps=[r-l for l,r in zip(centers,centers[1:])]
        if min(gaps)<0 or centers[-1]-centers[0]<w*.5:continue
        uniform=lambda values:max(values)-min(values)<=max(3.,sum(values)/len(values)*.06)
        if uniform(steps):mode='equal-centers'
        elif uniform(gaps):mode='equal-gaps'
        else:continue
        equal_cells=mode=='equal-centers' and max(abs(c/w-(i+.5)/len(row)) for i,c in enumerate(centers))<=.035
        rails.append({'members':[e['id'] for e in row],'mode':'equal-cells' if equal_cells else mode,
                      'leftMargin':bs[0]['x'],'rightMargin':w-bs[-1]['x']-bs[-1]['w'],
                      'bottomMargin':h-max(b['y']+b['h'] for b in bs),
                      'centerBottomMargin':h-sum(b['y']+b['h']/2 for b in bs)/len(bs),
                      'provenance':'aligned footer peers with uniform source spacing','confidence':.92})
        used.update(e['id'] for e in row)
    return rails
