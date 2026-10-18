"""Stable visible resource bounds; authored offscreen waiting panels stay inactive."""
import math


def intersects(b, viewport):
    return (min(b['w'], b['h']) > 0 and b['x'] < viewport['width'] and
            b['y'] < viewport['height'] and b['x']+b['w'] > 0 and b['y']+b['h'] > 0)


def stable_resources(project, layout, source_layout):
    viewport=project.get('sourceDevice',{}).get('viewport',{})
    if not viewport:return []
    active=set()
    for row in (source_layout or {}).get('rows',[]):
        b=dict(zip(('x','y','w','h'),row[1:5]))
        if row[5]>0 and intersects(b,viewport):active.add(row[0])
    result=[]
    roles={c['id']:c['role'] for c in project['components']}
    for row in layout.get('rows',[]):
        uid=project['nodeOrder'][row[0]];cid=project['nodeComponents'].get(uid)
        b=dict(zip(('x','y','w','h'),row[1:5]))
        if (row[0] not in active or row[5]<=0 or roles.get(cid) in {'background','gesture-layer'} or
            project['model']['nodes'][uid]['tag']=='Group' or min(b['w'],b['h'])<=0 or
            not all(math.isfinite(v) for v in b.values())):continue
        result.append({'id':uid,'component':cid,'rect':b})
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
