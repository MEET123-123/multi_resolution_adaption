"""Uniform resource sizing with explicit normalized screen-occupancy budgets."""
import math
from multires_app.layout.anchor_ir import number


def resource_scale(source_viewport,region,options,target=None):
    rw=region['w']/source_viewport['width'];rh=region['h']/source_viewport['height']
    policy=options.get('resourceScalePolicy','balanced')
    if policy not in {'balanced','width','contain','fixed'}:raise ValueError('resourceScalePolicy must be balanced, width, contain or fixed')
    factor=number(options.get('resourceScaleFactor',1.),'resourceScaleFactor',.001)
    aspect_delta=max(rw/rh,rh/rw)-1
    threshold=number(options.get('similarAspectThreshold',.18),'similarAspectThreshold',0)
    base=(rw if aspect_delta<=threshold else math.sqrt(rw*rh)) if policy=='balanced' else rw if policy=='width' else min(rw,rh) if policy=='contain' else 1.
    scale=base*factor;limits=[]
    axis_limit=number(options.get('maxRelativeAxisScale',1.6),'maxRelativeAxisScale',1.)
    area_limit=number(options.get('maxRelativeAreaScale',1.25),'maxRelativeAreaScale',.01)
    if policy=='balanced' or 'maxRelativeAxisScale' in options:
        limit=axis_limit*min(rw,rh)
        if scale>limit:limits.append('relative width/height occupancy budget');scale=limit
    if policy=='balanced' or 'maxRelativeAreaScale' in options:
        limit=math.sqrt(area_limit*rw*rh)
        if scale>limit:limits.append('relative area occupancy budget');scale=limit
    minimum=number(options.get('minScale',.03),'minScale',.0001)
    # Do not satisfy a crowded scene by making an existing picture effectively
    # disappear. One contain unit is its size in an intact, uniformly fitted
    # copy of the original scene. The scheduler can release inferred freezes
    # and move peers instead of silently shrinking a single resource to 3%.
    minimum_contain=number(options.get('minContainFraction',0.),'minContainFraction',0.)
    if minimum_contain>1:raise ValueError('minContainFraction must be between zero and one')
    minimum=max(minimum,min(rw,rh)*minimum_contain)
    maximum=number(options.get('maxScale',(target or {}).get('adaptation',{}).get('maxScale',max(1.,scale))),'maxScale',minimum)
    if scale<minimum:limits.append('minimum scale overrides occupancy budget')
    if scale>maximum:limits.append('maximum scale')
    scale=min(maximum,max(minimum,scale))
    return {'policy':policy,'scale':scale,'factor':factor,'minScale':minimum,'maxScale':maximum,'minContainFraction':minimum_contain,
            'widthRatio':rw,'heightRatio':rh,'aspectChange':aspect_delta,
            'route':'width-preserve' if policy=='balanced' and aspect_delta<=threshold else 'area-balance' if policy=='balanced' else policy,
            'relativeWidthRatio':scale/rw,'relativeHeightRatio':scale/rh,'relativeAreaRatio':scale*scale/(rw*rh),
            'maxRelativeAxisScale':axis_limit,'maxRelativeAreaScale':area_limit,'limitsApplied':limits,
            'scope':'Uniform geometry and normalized bbox occupancy, not decoded-pixel or aesthetic equivalence.'}
