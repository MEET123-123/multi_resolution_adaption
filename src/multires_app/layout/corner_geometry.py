"""Corner intent applies to settled source poses; authored motion stays motion."""
from multires_app.pipeline.scene_clusters import box_union,transformed_box


def component_frame(project,layout,cid):
    reference=set(next((c.get('cornerReferenceNodes',[]) for c in project['components'] if c['id']==cid),[]))
    return box_union([dict(zip(('x','y','w','h'),row[1:5])) for row in layout.get('rows',[])
                      if row[5]>0 and project['nodeComponents'].get(project['nodeOrder'][row[0]])==cid and
                      (project['nodeOrder'][row[0]] in reference if reference else
                       project['model']['nodes'][project['nodeOrder'][row[0]]]['tag'] not in {'Group','Button'})])


def at_source_corner(frame,attachment,viewport,tolerance=.01):
    if min(frame['w'],frame['h'])<=0:return False
    x=frame['x'] if attachment['corner']=='bottom-left' else viewport['width']-frame['x']-frame['w']
    y=viewport['height']-frame['y']-frame['h'];m=attachment['sourceMargins']
    return abs(x-m['x'])<=tolerance and abs(y-m['y'])<=tolerance


def corner_errors(project,device,layout,tolerance=.1):
    source=next((l for l in project.get('sourceDevice',{}).get('layouts',[]) if l['state']==layout['state']),None)
    if source is None:return []
    region=layout.get('adaptationPlan',{}).get('safeRegion',{'x':0,'y':0,'w':device['viewport']['width'],'h':device['viewport']['height']});errors=[]
    for c in project['components']:
        a=c.get('cornerAttachment');cid=c['id']
        if not a or cid not in layout['transforms']:continue
        if not at_source_corner(component_frame(project,source,cid),a,project['sourceDevice']['viewport']):continue
        frame=component_frame(project,layout,cid)
        if min(frame['w'],frame['h'])<=0:continue
        t=layout['transforms'][cid];b=transformed_box(frame,t);m=a['sourceMargins']
        x=b['x']-region['x'] if a['corner']=='bottom-left' else region['x']+region['w']-b['x']-b['w'];y=region['y']+region['h']-b['y']-b['h']
        error=max(abs(x-m['x']*t['sx']),abs(y-m['y']*t['sy']))
        if error>tolerance:errors.append({'component':cid,'corner':a['corner'],'residual':error,'target':b})
    return errors
