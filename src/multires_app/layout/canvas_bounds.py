"""Stable visible resource bounds; authored offscreen waiting panels stay inactive."""
import math


def intersects(b, viewport):
    return (min(b['w'], b['h']) > 0 and b['x'] < viewport['width'] and
            b['y'] < viewport['height'] and b['x']+b['w'] > 0 and b['y']+b['h'] > 0)


def stable_resources(project, layout, source_layout):
    viewport=project.get('sourceDevice',{}).get('viewport',{})
    if not viewport:return []
    active={}
    for row in (source_layout or {}).get('rows',[]):
        b=dict(zip(('x','y','w','h'),row[1:5]))
        if row[5]>0 and intersects(b,viewport):
            # Constrain only the portion visible on the authored canvas. Making
            # the full frame fit would reveal icons on the hidden half of a menu.
            active.setdefault(row[0],[]).append(b)
        else:active.setdefault(row[0],[]).append(None)
    result=[]
    occurrences={}
    roles={c['id']:c['role'] for c in project['components']}
    for row in layout.get('rows',[]):
        uid=project['nodeOrder'][row[0]];cid=project['nodeComponents'].get(uid)
        b=dict(zip(('x','y','w','h'),row[1:5]))
        index=occurrences.get(row[0],0);occurrences[row[0]]=index+1
        if (row[0] not in active or row[5]<=0 or roles.get(cid) in {'background','gesture-layer'} or
            project['model']['nodes'][uid]['tag']=='Group' or min(b['w'],b['h'])<=0 or
            not all(math.isfinite(v) for v in b.values())):continue
        source=active[row[0]][min(index,len(active[row[0]])-1)]
        if source is None:continue
        clipped=dict(b)
        for axis,size,extent in [('x','w','width'),('y','h','height')]:
            start=max(0.,source[axis]);end=min(viewport[extent],source[axis]+source[size])
            clipped[axis]=b[axis]+(start-source[axis])*b[size]/source[size]
            clipped[size]=(end-start)*b[size]/source[size]
        result.append({'id':uid,'component':cid,'rect':clipped})
    return result


def violations(resources, transforms, region, tolerance=.01):
    result=[]
    for e in resources:
        t=transforms.get(e['component'],{'sx':1,'sy':1,'tx':0,'ty':0});b=e['rect']
        q={'x':b['x']*t['sx']+t['tx'],'y':b['y']*t['sy']+t['ty'],
           'w':b['w']*t['sx'],'h':b['h']*t['sy']}
        if (q['x']<region['x']-tolerance or q['y']<region['y']-tolerance or
            q['x']+q['w']>region['x']+region['w']+tolerance or
            q['y']+q['h']>region['y']+region['h']+tolerance):result.append({**e,'target':q})
    return result
