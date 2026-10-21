"""Verify authored scene membership separately from layout solver evidence."""
from collections import Counter


def onscreen(rect,viewport,tolerance=.02):
    return (min(rect['w'],rect['h'])>0 and rect['x']<viewport['width']-tolerance and
            rect['x']+rect['w']>tolerance and rect['y']<viewport['height']-tolerance and rect['y']+rect['h']>tolerance)


def scene_membership_errors(project,device,layout,native_elements=None):
    """No extra/absent scene nodes or newly exposed offscreen controls.

    Only source-space mode promises identical script branches. Target-space
    mode intentionally allows responsive native scripts to choose other branches.
    """
    from multires_app.engine.coordinate_space import source_canvas
    if source_canvas(project.get('model',{})) is None:return []
    source=next((l for l in project['sourceDevice']['layouts'] if l['state']==layout['state']),None)
    if source is None:return [{'kind':'missing-source-scene','state':layout['state']}]
    nodes=project['model']['nodes'];source_viewport=project['sourceDevice']['viewport']
    def rows(layout,transformed=False):
        result=[]
        for row in layout['rows']:
            uid=project['nodeOrder'][row[0]]
            if row[5]<=0 or nodes[uid]['tag']=='Group':continue
            b=dict(zip(('x','y','w','h'),row[1:5]))
            if transformed:
                t=layout.get('transforms',{}).get(project['nodeComponents'].get(uid),{'sx':1,'sy':1,'tx':0,'ty':0})
                b={'x':b['x']*t['sx']+t['tx'],'y':b['y']*t['sy']+t['ty'],'w':b['w']*t['sx'],'h':b['h']*t['sy']}
            result.append((uid,b))
        return result
    original=rows(source)
    backgrounds={u for c in project.get('components',[]) if c['role']=='background' for u in c['members']}
    # Pointer feedback follows the actual target touch, which changes after
    # layout. Its binding is checked against that live input during readback.
    backgrounds.update(u for c in project.get('components',[]) if c.get('pointerEffect') for u in c['members'])
    actual=rows(layout,True) if native_elements is None else [(e['id'],e['rect']) for e in native_elements
        if e.get('visible') and e['tag']!='Group' and e['id'] in nodes]
    errors=[]
    for kind,expected,found in [
        ('visible-node',Counter(uid for uid,_ in original),Counter(uid for uid,_ in actual)),
        ('onscreen-node',Counter(uid for uid,b in original if uid not in backgrounds and onscreen(b,source_viewport)),
                         Counter(uid for uid,b in actual if uid not in backgrounds and onscreen(b,device['viewport'])))]:
        for uid in sorted(expected.keys()|found.keys()):
            if expected[uid]!=found[uid]:errors.append({'kind':kind,'node':uid,'expected':expected[uid],'actual':found[uid]})
    return errors
