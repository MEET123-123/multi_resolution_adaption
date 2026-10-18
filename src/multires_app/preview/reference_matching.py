"""Local, multi-scale template matching that proposes geometry; never applies it.

Uses Pillow and NumPy. It matches reused raster artwork, not semantic redesigns.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image

from multires_app.preview.layout_refinement import export_request


def _correlation(image,template):
    h,w=image.shape;th,tw=template.shape
    shape=(h+th-1,w+tw-1)
    result=np.fft.irfft2(np.fft.rfft2(image,s=shape)*np.conj(np.fft.rfft2(template,s=shape)),s=shape)
    return result[:h-th+1,:w-tw+1]


def ncc(image,template,mask):
    weight=float(mask.sum())
    if weight<16:raise ValueError('template has too few visible pixels')
    mean=float((template*mask).sum()/weight);center=(template-mean)*mask
    energy=float(((template-mean)**2*mask).sum())
    if energy<1e-5:raise ValueError('flat template cannot be reliably matched')
    numerator=_correlation(image,center)
    sums=_correlation(image,mask);squares=_correlation(image*image,mask)
    denominator=np.sqrt(np.maximum(1e-8,squares-sums*sums/weight)*energy)
    return np.clip(numerator/denominator,-1,1)


def match_template(reference,template,expected_size,scales=(.65,.8,1.,1.2,1.45)):
    """Return best location plus spatially distinct runner-up confidence."""
    image=np.asarray(reference.convert('L'),dtype=np.float64)/255
    candidates=[]
    for factor in scales:
        width=max(4,round(expected_size[0]*factor));height=max(4,round(expected_size[1]*factor))
        if width>=reference.width or height>=reference.height:continue
        bitmap=template.resize((width,height),Image.Resampling.LANCZOS)
        pixels=np.asarray(bitmap.convert('L'),dtype=np.float64)/255
        mask=np.asarray(bitmap.getchannel('A'),dtype=np.float64)/255
        try:scores=ncc(image,pixels,mask)
        except ValueError:continue
        y,x=np.unravel_index(np.argmax(scores),scores.shape);best=float(scores[y,x])
        excluded=scores.copy();excluded[max(0,y-height//2):min(scores.shape[0],y+height//2+1),max(0,x-width//2):min(scores.shape[1],x+width//2+1)]=-1
        runner=float(excluded.max()) if excluded.size else -1.
        candidates.append({'x':int(x),'y':int(y),'w':width,'h':height,'confidence':best,'runnerUp':runner,'scale':factor})
    if not candidates:return None
    best=max(candidates,key=lambda c:c['confidence'])
    # Other scales at the same location are not ambiguous duplicates.
    others=[c['confidence'] for c in candidates if abs(c['x']-best['x'])>best['w']*.5 or abs(c['y']-best['y'])>best['h']*.5]
    best['runnerUp']=max([best['runnerUp'],*others])
    best['peakMargin']=best['confidence']-best['runnerUp']
    return best


def match_reference(project,target,state,image_bytes,asset_root,*,minimum_confidence=.78,minimum_margin=.035,max_components=24):
    if len(image_bytes)>10*1024*1024:raise ValueError('参考图不能超过 10MB')
    request=export_request(project,target,state)
    with Image.open(io.BytesIO(image_bytes)) as original:
        if original.width*original.height>20_000_000:raise ValueError('参考图像素数过大')
        reference_size=original.size
        ratio=original.width/original.height;viewport=request['viewport']
        if abs(ratio/(viewport['width']/viewport['height'])-1)>.025:
            raise ValueError('参考图宽高比与目标画布不一致，请使用不含浏览器边框的完整目标画面')
        factor=min(1.,512/max(original.size));reference=original.convert('RGB').resize((round(original.width*factor),round(original.height*factor)),Image.Resampling.LANCZOS)
    device=next(d for d in project['devices'] if d['profile']['id']==target)
    layout=next(l for l in device['layouts'] if l['state']==state)
    components={c['id']:c for c in request['components']}
    rows={cid:[] for cid in components}
    for row in layout['rows']:
        uid=project['nodeOrder'][row[0]];cid=project['nodeComponents'].get(uid)
        if cid in rows and row[6]>=0:rows[cid].append(row)
    asset_root=Path(asset_root).resolve();matched=[];diagnostics=[];attempted=0
    px_x=reference.width/viewport['width'];px_y=reference.height/viewport['height']
    for cid,comp in components.items():
        if comp['locked']:continue
        options=sorted(rows[cid],key=lambda r:r[3]*r[4]*r[5],reverse=True)
        if not options:diagnostics.append({'id':cid,'reason':'没有可复用的位图模板；文字或重绘元素请手工标 bbox'});continue
        if attempted>=max_components:diagnostics.append({'id':cid,'reason':'达到本次匹配组件预算'});continue
        selected=None
        for row in options:
            name=project['assetOrder'][row[6]];path=(asset_root/'assets'/name).resolve()
            if not path.is_relative_to(asset_root/'assets') or not path.is_file():continue
            try:
                with Image.open(path) as source:
                    bitmap=source.convert('RGBA');crop=bitmap.getchannel('A').getbbox()
                    if not crop:continue
                    selected=(row,bitmap.crop(crop),crop,bitmap.size);break
            except (OSError,ValueError):continue
        if not selected:diagnostics.append({'id':cid,'reason':'位图资源不可读取'});continue
        row,template,crop,native_size=selected;attempted+=1
        t=layout['transforms'][cid]
        raw_x=row[1]+crop[0]/native_size[0]*row[3];raw_y=row[2]+crop[1]/native_size[1]*row[4]
        raw_w=(crop[2]-crop[0])/native_size[0]*row[3];raw_h=(crop[3]-crop[1])/native_size[1]*row[4]
        match=match_template(reference,template,(raw_w*t['sx']*px_x,raw_h*t['sy']*px_y))
        if not match or match['confidence']<minimum_confidence or match['peakMargin']<minimum_margin:
            diagnostics.append({'id':cid,'reason':'低置信度或存在多个相似位置，未提出修改','match':match});continue
        scale=min(match['w']/px_x/raw_w,match['h']/px_y/raw_h)
        tx=match['x']/px_x-raw_x*scale;ty=match['y']/px_y-raw_y*scale
        b=comp['sourceBBox'];bbox={'x':b['x']*scale+tx,'y':b['y']*scale+ty,'w':b['w']*scale,'h':b['h']*scale}
        matched.append({'id':cid,'bbox':bbox,'confidence':match['confidence'],'peakMargin':match['peakMargin']})
    return {**request['responseTemplate'],'origin':'local-reference-image','components':matched,
            'reference':{'sha256':hashlib.sha256(image_bytes).hexdigest(),'size':list(reference_size),'analysisSize':list(reference.size)},
            'matching':{'method':'masked multi-scale normalized cross correlation','minimumConfidence':minimum_confidence,
                        'minimumPeakMargin':minimum_margin,'attempted':attempted,'diagnostics':diagnostics,
                        'scope':'仅匹配复用的位图；未匹配项保持原布局，建议须经过导入校验再应用。'}}
