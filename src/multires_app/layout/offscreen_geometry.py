"""Keep waiting artwork outside its authored edge after uniform scaling."""
def retain_offscreen(source,current,transform,source_viewport,region):
    result=dict(transform)
    for axis,size,extent,scale,offset in [('x','w','width','sx','tx'),('y','h','height','sy','ty')]:
        end=source[axis]+source[size];current_end=current[axis]+current[size]
        start=result[offset]+current[axis]*result[scale]
        finish=result[offset]+current_end*result[scale]
        low=region[axis];high=low+region[size]
        # Both settled source and target must wait at the same edge. Do not
        # constrain a moving object whose target scene has already entered.
        if end<=0 and current_end<=0 and finish>low:
            result[offset]=low+end*result[scale]-current_end*result[scale]
        elif source[axis]>=source_viewport[extent] and current[axis]>=high and start<high:
            gap=source[axis]-source_viewport[extent]
            result[offset]=high+gap*result[scale]-current[axis]*result[scale]
    return result


def waiting_errors(project,device,layout):
    from multires_app.layout.visual_geometry import component_visuals
    from multires_app.layout.adaptive_layout import safe_region
    components=[c for c in project.get('components',[]) if c['role']=='decoration']
    if not components:return []
    source=next((l for l in project.get('sourceDevice',{}).get('layouts',[]) if l['state']==layout['state']),None)
    if not source:return []
    src=component_visuals(project,source);cur=component_visuals(project,layout)
    region=safe_region(project['sourceProfile'],device['profile'],{'logicalViewport':device['viewport']});errors=[]
    for c in components:
        cid=c['id'];t=layout['transforms'].get(cid)
        if c['role']!='decoration' or not t or cid not in src or cid not in cur:continue
        corrected=retain_offscreen(src[cid],cur[cid],t,project['sourceDevice']['viewport'],region)
        if any(abs(corrected[k]-t[k])>.01 for k in ('tx','ty')):
            errors.append({'component':cid,'state':layout['state'],'transform':t,'expected':corrected})
    return errors
