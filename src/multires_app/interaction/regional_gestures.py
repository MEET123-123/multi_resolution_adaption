"""Keep broad directional gesture regions independent of decorative card scale."""
from __future__ import annotations

def split_regional_gestures(project):
    from multires_app.interaction.interaction_semantics import swipe_axes
    source=project.get('sourceDevice',{});vp=source.get('viewport',{})
    if not vp:return []
    model=project['model'];components={c['id']:c for c in project['components']}
    bindings=model.get('layoutBindings',{}).get('bindings',[]);splits=[]
    # Global gesture surfaces may start below a top strip, or extend beyond
    # the phone's bottom edge. Expanding all of them to (0,0,screenW,screenH)
    # erases their original hit-order partitions and steals other gestures.
    for c in project['components']:
        if c['role']!='gesture-layer' or c.get('gestureRegion') or c.get('gestureRegions') and c.get('gestureRegionCrops'):continue
        if model['nodes'].get(c['id'],{}).get('tag')!='Button':continue
        regions={};crops={}
        for layout in source['layouts']:
            row=next((r for r in layout['rows'] if project['nodeOrder'][r[0]]==c['id'] and r[5]>0),None)
            if row is None:continue
            x,y,w,h=row[1:5];left=max(0,x);top=max(0,y);right=min(vp['width'],x+w);bottom=min(vp['height'],y+h)
            if right<=left or bottom<=top:continue
            regions[layout['state']]={'x':left/vp['width'],'y':top/vp['height'],
                'w':(right-left)/vp['width'],'h':(bottom-top)/vp['height']}
            crops[layout['state']]={'x':(left-x)/w,'y':(top-y)/h,'w':(right-left)/w,'h':(bottom-top)/h}
        if regions:c['gestureRegions']=regions;c['gestureRegionCrops']=crops
    for binding in bindings:
        uid=binding['button'];cid=project['nodeComponents'].get(uid);old=components.get(cid)
        if not old or old.get('gestureRegion') or old['role']=='gesture-layer' or old.get('layout'):continue
        attrs=model['nodes'][uid]['attributes']
        axes=swipe_axes(model,uid)
        if len(axes)!=1 or attrs.get('interceptTouch','false').lower()=='true':continue
        # A large dependency fan-out distinguishes page/viewport manipulation
        # from a small slider or isolated icon. No variable names are assumed.
        if len(binding.get('effectTargets',[]))<5:continue
        rows=[r for l in source['layouts'] for r in l['rows'] if project['nodeOrder'][r[0]]==uid and r[5]>0]
        if not rows:continue
        row=rows[0];x,y,w,h=row[1:5]
        if w*h<vp['width']*vp['height']*.1:continue
        left=max(0,x);top=max(0,y);right=min(vp['width'],x+w);bottom=min(vp['height'],y+h)
        if right<=left or bottom<=top:continue
        if (right-left)*(bottom-top)<vp['width']*vp['height']*.12:continue
        normalized={'x':left/vp['width'],'y':top/vp['height'],
                    'w':(right-left)/vp['width'],'h':(bottom-top)/vp['height']}
        component={'id':uid,'name':'Directional gesture region','roots':[uid],'members':[uid],
                   'role':'gesture-layer','sourceBounds':{'x':x,'y':y,'w':w,'h':h},
                   'gestureRegion':normalized,'gestureRegionCrop':{'x':(left-x)/w,'y':(top-y)/h,'w':(right-left)/w,'h':(bottom-top)/h},'gestureAxes':axes,'bindingButtons':[uid],
                   'inference':'large directional gesture with multi-element geometry effects',
                   'reviewRequired':True,'layoutFollowers':[]}
        if old['id']==uid:
            old.update(component)
        else:
            old['members']=[x for x in old['members'] if x!=uid]
            old['roots']=[x for x in old.get('roots',[]) if x!=uid]
            old['bindingButtons']=[x for x in old.get('bindingButtons',[]) if x!=uid]
            project['components'].append(component)
        components[uid]=component;project['nodeComponents'][uid]=uid
        splits.append({'button':uid,'previousComponent':cid,'axis':axes[0],'region':normalized})
    if splits:project.setdefault('regionalGestureSplits',[]).extend(splits)
    for device in project.get('devices',[]):apply_regional_transforms(project,device)
    return splits


def apply_regional_transforms(project,device):
    regions={c['id']:c for c in project['components'] if c.get('gestureRegion') or c.get('gestureRegions')}
    if not regions:return
    vp=device['viewport']
    for layout in device.get('layouts',[]):
        for row in layout['rows']:
            uid=project['nodeOrder'][row[0]];cid=project['nodeComponents'].get(uid)
            if cid not in regions or min(row[3:5])<=0:continue
            c=regions[cid];r=c.get('gestureRegions',{}).get(layout['state'],c.get('gestureRegion'))
            if r is None:continue
            crop=c.get('gestureRegionCrops',{}).get(layout['state'],c.get('gestureRegionCrop',{'x':0,'y':0,'w':1,'h':1}))
            sx=r['w']*vp['width']/(row[3]*crop['w']);sy=r['h']*vp['height']/(row[4]*crop['h'])
            layout.setdefault('transforms',{})[cid]={'sx':sx,'sy':sy,
                'tx':r['x']*vp['width']-(row[1]+row[3]*crop['x'])*sx,'ty':r['y']*vp['height']-(row[2]+row[4]*crop['y'])*sy}
