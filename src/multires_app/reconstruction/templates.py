"""Tablet composition vocabulary. Rectangles are normalized to the safe area."""
import math

TEMPLATES = {
    'center-stage': {'name': '中心主视觉', 'zones': {'header':[.18,.03,.64,.20], 'main':[.16,.26,.68,.57], 'support':[.03,.28,.12,.53], 'dock':[.12,.86,.76,.11]}},
    'hero-support': {'name': '左右主辅', 'zones': {'header':[.03,.04,.42,.22], 'main':[.03,.29,.55,.65], 'support':[.62,.04,.35,.76], 'dock':[.62,.84,.35,.12]}},
    'triptych': {'name': '三分区', 'zones': {'header':[.04,.04,.22,.24], 'main':[.29,.06,.43,.78], 'support':[.76,.05,.20,.79], 'dock':[.06,.88,.88,.09]}},
    'card-grid': {'name': '卡片网格', 'zones': {'header':[.03,.03,.94,.19], 'main':[.03,.26,.60,.68], 'support':[.67,.26,.30,.68], 'dock':[.03,.96,.94,.03]}},
    'hero-dock': {'name': '主视觉加底栏', 'zones': {'header':[.04,.03,.32,.24], 'main':[.39,.03,.57,.67], 'support':[.04,.31,.31,.39], 'dock':[.04,.76,.92,.20]}},
    'side-panel': {'name': '侧边设置面板', 'zones': {'header':[.03,.03,.57,.20], 'main':[.03,.27,.57,.55], 'support':[.65,.04,.32,.92], 'dock':[.03,.86,.57,.10]}},
}


def catalog():
    return [{'id':k, **v} for k,v in TEMPLATES.items()]


def propose(request, template=None):
    """Deterministic offline provider; inferred roles are explicitly not labels."""
    groups=request['groups'];movable=[g for g in groups if not g['locked']]
    template=template or ('card-grid' if len(movable)>=8 else 'hero-support')
    if template not in TEMPLATES:raise ValueError('未知构图模板')
    remaining=sorted(movable,key=lambda g:(-g['sourceBox']['w']*g['sourceBox']['h'],g['id']))
    primary=next((g['id'] for g in remaining if not set(g['roles'])&{'datetime','clock','date','time','header','footer','popup','dialog','overlay'}),None)
    assignments=[]
    for index,g in enumerate(movable):
        roles=set(g['roles'])
        zone=('header' if roles&{'datetime','clock','date','time','header'} else
              'dock' if roles&{'footer','shortcuts'} else 'main' if g['id']==primary else 'support')
        assignments.append({'id':g['id'],'zone':zone,'order':index})
    return {'schema':'layout-reconstruction-advice/1','inputDigest':request['inputDigest'],
            'candidates':[{'template':template,'confidence':1.,'rationale':'离线规则候选，用于验证接口与完整流程；未调用模型。',
                           'groups':assignments,'relations':[]}]}


def placements(request, advice):
    """Pack existing rigid groups in template zones; no new controls or pixels."""
    region=request['safeRegion'];definitions={g['id']:g for g in request['groups']};output={}
    for zone,normalized in TEMPLATES[advice['template']]['zones'].items():
        items=sorted([a for a in advice['groups'] if a['zone']==zone],key=lambda a:(a['order'],a['id']))
        if not items:continue
        x,y,w,h=normalized;x=region['x']+x*region['w'];y=region['y']+y*region['h'];w*=region['w'];h*=region['h']
        gap=min(w,h)*.045;best=None
        for columns in range(1,len(items)+1):
            rows=math.ceil(len(items)/columns);cw=(w-gap*(columns-1))/columns;ch=(h-gap*(rows-1))/rows
            if min(cw,ch)<=0:continue
            scales=[min(cw/definitions[a['id']].get('footprint',definitions[a['id']]['sourceBox'])['w'],ch/definitions[a['id']].get('footprint',definitions[a['id']]['sourceBox'])['h']) for a in items]
            score=(min(scales),sum(scales),-(columns*rows-len(items)))
            if best is None or score>best[0]:best=(score,columns,cw,ch)
        if best is None:raise ValueError('分区内没有可行的网格')
        _,columns,cw,ch=best
        for index,a in enumerate(items):
            g=definitions[a['id']];f=g.get('footprint',g['sourceBox']);visual=g['sourceBox']
            scale=min(cw/f['w'],ch/f['h']);left=x+(index%columns)*(cw+gap)+(cw-f['w']*scale)/2;top=y+(index//columns)*(ch+gap)+(ch-f['h']*scale)/2
            output[a['id']]={'bbox':{'x':left+(visual['x']-f['x'])*scale,'y':top+(visual['y']-f['y'])*scale,'w':visual['w']*scale,'h':visual['h']*scale},
                              'confidence':advice['confidence']}
    return output
