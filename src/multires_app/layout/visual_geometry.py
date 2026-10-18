"""Read-only alpha geometry for optical anchors; image canvases stay intact."""
import io
import zipfile
from pathlib import Path
from PIL import Image
from multires_app.pipeline.scene_clusters import box_union
from collections import OrderedDict

_GEOMETRY_CACHE=OrderedDict()
_CACHE_OWNER=None


def ensure_visual_bounds(project):
    if project.get('_visualBoundsReady'):return
    indices=set()
    for d in [project.get('sourceDevice',{}),*project.get('devices',[])]:
        for l in d.get('layouts',[]):
            for row in [*l.get('rows',[]),*l.get('mediaLayout',{}).get('rows',[])]:
                if len(row)>6 and row[6]>=0:indices.add(row[6])
    source=Path(project.get('source',''));archive=None
    try:
        if source.is_file() and zipfile.is_zipfile(source):archive=zipfile.ZipFile(source)
        for index in indices:
            name=project['assetOrder'][index];meta=project['assets'].get(name,{})
            if 'visualBounds' in meta or meta.get('kind')!='image':continue
            try:
                if archive:data=io.BytesIO(archive.read(name))
                elif source.is_dir():data=source/name
                else:continue
                with Image.open(data) as im:
                    if 'A' in im.getbands() or 'transparency' in im.info:
                        bounds=im.convert('RGBA').getchannel('A').point(lambda a:255 if a>=16 else 0).getbbox()
                    else:bounds=(0,0,im.width,im.height)
                    meta['visualBounds']=list(bounds) if bounds else None
                    meta['visualBoundsPolicy']='alpha >= 16; original bitmap retained'
            except (OSError,KeyError,ValueError):continue
    finally:
        if archive:archive.close()
    project['_visualBoundsReady']=True
    _GEOMETRY_CACHE.clear()


def visual_rect(project,row):
    b=dict(zip(('x','y','w','h'),row[1:5]))
    if row[6]<0:return b
    meta=project['assets'].get(project['assetOrder'][row[6]],{})
    if 'visualBounds' not in meta:return b
    bounds=meta['visualBounds']
    if not bounds:return None
    iw,ih=meta.get('width',0),meta.get('height',0)
    if not iw or not ih:return b
    uid=project['nodeOrder'][row[0]];node=project['model']['nodes'][uid]
    sx,sy=b['w']/iw,b['h']/ih;ox,oy=b['x'],b['y']
    if node['attributes'].get('scaleType')=='center_crop':
        sx=sy=max(sx,sy);ox+=(b['w']-iw*sx)/2;oy+=(b['h']-ih*sy)/2
    x=max(b['x'],ox+bounds[0]*sx);y=max(b['y'],oy+bounds[1]*sy)
    right=min(b['x']+b['w'],ox+bounds[2]*sx);bottom=min(b['y']+b['h'],oy+bounds[3]*sy)
    return {'x':x,'y':y,'w':max(0,right-x),'h':max(0,bottom-y)}


def component_visuals(project,layout,canonical=False):
    from multires_app.media.media_layout import layout_rows
    global _CACHE_OWNER
    cache=_GEOMETRY_CACHE
    owner=(id(project.get('assets')),id(project['nodeComponents']))
    if owner!=_CACHE_OWNER:cache.clear();_CACHE_OWNER=owner
    key=(id(layout.get('rows')),id(layout.get('mediaLayout')),id(project['nodeComponents']),canonical)
    if key in cache:return cache[key][-1]
    rows=layout_rows(project,layout) if canonical else layout.get('rows',[])
    followers={uid for c in project['components'] for uid in c.get('layoutFollowers',[])};groups={}
    for row in rows:
        uid=project['nodeOrder'][row[0]];node=project['model']['nodes'][uid];cid=project['nodeComponents'].get(uid)
        if not cid or node['tag'] in {'Group','Button'} or uid in followers or row[5]<=0:continue
        b=visual_rect(project,row)
        if b and min(b['w'],b['h'])>0:groups.setdefault(cid,[]).append(b)
    result={cid:box_union(boxes) for cid,boxes in groups.items()}
    cache[key]=(layout.get('rows'),layout.get('mediaLayout'),project['nodeComponents'],result)
    if len(cache)>768:cache.popitem(last=False)
    return result


def with_stable_visuals(project,layout,descriptions):
    visuals=component_visuals(project,layout)
    return {cid:{**d,'stableVisualBounds':visuals.get(cid,d.get('visualBounds',d['bounds']))} for cid,d in descriptions.items()}


def background_rects(project,layout):
    """The opaque backdrop defines cover, not moving foreground artwork."""
    from multires_app.media.media_layout import layout_rows
    roles={c['id']:c['role'] for c in project['components']};best={};all_rows=layout_rows(project,layout)
    for row in all_rows:
        uid=project['nodeOrder'][row[0]];cid=project['nodeComponents'].get(uid)
        if roles.get(cid)!='background' or row[6]<0 or min(row[3:5])<=0:continue
        name=project['assetOrder'][row[6]];node=project['model']['nodes'][uid]
        if node['tag'] not in {'Image','Video'}:continue
        rank=(int(name.lower().endswith(('.jpg','.jpeg'))),row[3]*row[4])
        if cid not in best or rank>best[cid][0]:best[cid]=(rank,dict(zip(('x','y','w','h'),row[1:5])))
    result={cid:value[1] for cid,value in best.items()}
    for c in project['components']:
        if not c.get('backgroundFrameNodes'):continue
        ids=set(c['backgroundFrameNodes']);boxes=[dict(zip(('x','y','w','h'),row[1:5])) for row in all_rows if project['nodeOrder'][row[0]] in ids]
        frame=box_union(boxes)
        if min(frame['w'],frame['h'])>0:result[c['id']]=frame
    return result


def background_cover(backdrop,reference,viewport,source_viewport):
    from multires_app.layout.adaptive_layout import cover
    result=cover(backdrop,viewport)
    for axis,size,extent in [('x','w',source_viewport['width']),('y','h',source_viewport['height'])]:
        overflow=reference[size]-extent
        fraction=max(0.,min(1.,-reference[axis]/overflow)) if overflow>.1 else .5
        target_extent=viewport['width' if axis=='x' else 'height']
        result['t'+axis]=-fraction*max(0.,backdrop[size]*result['s'+axis]-target_extent)-backdrop[axis]*result['s'+axis]
    return result
