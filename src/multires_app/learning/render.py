"""Reuse the offline painter with bounded image memory and explicit omissions."""
import math
from urllib.parse import unquote, urlsplit
from PIL import ImageDraw
from multires_app.evaluation.report import elements
from multires_app.preview.paint_visibility import raster_snapshot, read_bitmap


def render(project, device, layout, assets, path, annotations=None):
    viewport = device['viewport']; selected = []; omitted = []; pixels = 0
    metas = {unquote(urlsplit(m.get('url', '')).path).removeprefix('assets/'): m for m in project['assets'].values()}
    factor = min(768/viewport['width'], 768/viewport['height'])
    read_bitmap.cache_clear()
    try:
        for e in elements(project, layout):
            b=e['rect']; m=metas.get(e['asset'], {}); size=m.get('width', 0)*m.get('height', 0)
            if not all(math.isfinite(v) for v in b.values()) or min(b['w'], b['h']) < 0:
                omitted.append(e['id']); continue
            if b['x'] >= viewport['width'] or b['y'] >= viewport['height'] or b['x']+b['w'] <= 0 or b['y']+b['h'] <= 0:continue
            if size > 8_000_000 or pixels+size > 24_000_000 or b['w']*b['h']*factor**2 > 4_000_000:
                omitted.append(e['id']); continue
            pixels += size; selected.append(e)
        image, paint = raster_snapshot(selected, viewport, assets, max_size=(768, 768))
        by_key={e['key']:e for e in selected};unsupported=[]
        for key,info in paint.items():
            if info['supported']:continue
            e=by_key[key];node=project['model']['nodes'][e['id']]
            unsupported.append({'node':e['id'],'tag':e['tag'],'status':info['status'],
                'reason':'missing-asset' if e['tag']=='Image' else 'color-expression' if info['status']=='unrendered-color-expression' else 'native-effect',
                'asset':e.get('asset'),'sourceReference':node['attributes'].get('src',node['attributes'].get('srcExp',''))})
        if annotations:
            draw = ImageDraw.Draw(image)
            for alias, b in annotations.items():
                coords=[b['x']*factor, b['y']*factor, (b['x']+b['w'])*factor, (b['y']+b['h'])*factor]
                draw.rectangle(coords, outline='#44ffd0', width=2)
                draw.text((coords[0]+3, coords[1]+2), alias, fill='#ff605e', stroke_width=1)
        path.parent.mkdir(parents=True, exist_ok=True); image.convert('RGB').save(path)
        return {'status': 'incomplete' if omitted or any(not p['supported'] for p in paint.values()) else 'simulated',
                'omitted': len(omitted), 'unsupported':len(unsupported),'unsupportedElements':unsupported,'native': False}
    finally:read_bitmap.cache_clear()
