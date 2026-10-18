"""Reproducible anchor evidence and same-scene legacy A/B, without visual claims."""
import copy
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from multires_app.layout.adaptive_layout import AdaptiveThemeLayout
from multires_app.pipeline.scene_clusters import schedule_device,describe_layout,transformed_box,overlap


def metrics(project,device):
    checked=outside=collisions=drift=0;scales=[]
    components={c['id']:c for c in project['components']};layouts={l['state']:l for l in device['layouts']}
    vp=device['viewport'];screen={'x':0,'y':0,'w':vp['width'],'h':vp['height']}
    refs={l['state']:describe_layout(project,l) for l in project['sourceDevice']['layouts']}
    for layout in device['layouts']:
        desc=describe_layout(project,layout);placed=[]
        for cid,d in desc.items():
            if components[cid]['role'] in {'background','gesture-layer','interaction-layer','decoration','popup','overlay','dialog'}:continue
            b=d['bounds'];t=layout['transforms'].get(cid)
            if not t or min(b['w'],b['h'])<=0:continue
            q=transformed_box(b,t);checked+=1;scales.append(min(t['sx'],t['sy']))
            source=refs.get(layout['state'],{}).get(cid,{}).get('bounds',b)
            sw=project['sourceDevice']['viewport'];source_screen={'x':0,'y':0,'w':sw['width'],'h':sw['height']}
            source_inside=overlap(source,source_screen)>=source['w']*source['h']-.1
            if source_inside and overlap(q,screen)<q['w']*q['h']-.1:outside+=1
            for other,obox,oref in placed:
                area=min(q['w']*q['h'],obox['w']*obox['h'])
                if overlap(source,oref)<1e-6 and overlap(q,obox)>.05*area:collisions+=1
            placed.append((cid,q,source))
        request=layout.get('layoutRequest',{});base=layouts.get(request.get('baseState'))
        if base and request.get('kind') in {'local-reflow','overlay'}:
            drift+=sum(layout['transforms'].get(cid)!=base['transforms'].get(cid) for cid in request.get('fixedComponents',[]))
    return {'componentSnapshots':checked,'newClippedComponentSnapshots':outside,'newSourceDisjointOverlaps':collisions,
            'fixedTransformDrift':drift,'minimumScale':min(scales,default=1),
            'interpretation':'Component bbox proxies, not pixel segmentation or designer intent; original crop and modal overlays excluded.'}


def check(path):
    start=time.monotonic();p=json.loads(path.read_text(encoding='utf-8'));rows=[]
    source_digest=hashlib.sha256(Path(p['source']).read_bytes()).hexdigest()
    for device in p['devices']:
        if device['profile']['id'] not in {'tablet-mock-16-10-landscape','dual-fold-mock-inner-portrait'}:continue
        candidate=metrics(p,device);baseline_device=copy.deepcopy(device)
        baseline_device.pop('manualComponentAnchors',None)
        baseline={**p,'devices':[baseline_device],'anchorIRs':{}}
        try:
            schedule_device(baseline,baseline_device,AdaptiveThemeLayout());old=metrics(baseline,baseline_device)
        except ValueError as exc:old={'error':str(exc)}
        rows.append({'target':device['profile']['id'],'states':len(device['layouts']),'anchor':candidate,'adaptiveBaseline':old,
                     'qualityGate':device.get('layoutQuality',{})})
    return {'project':path.parent.parent.name,'algorithm':p['algorithm'],'states':len(p['graph']['states']),
            'sourceUnchanged':hashlib.sha256(Path(p['source']).read_bytes()).hexdigest()==source_digest,
            'targets':rows,'seconds':round(time.monotonic()-start,3)}


def main():
    root=ROOT/'theme/adaptation';records=json.loads((root/'batch-summary.json').read_text(encoding='utf-8'))
    paths=sorted(root.glob('*/scenes/project.json'));results=[]
    with ProcessPoolExecutor(max_workers=2) as pool:
        for result in as_completed([pool.submit(check,p) for p in paths]):
            row=result.result();results.append(row);print(json.dumps({'ab':row['project'],'seconds':row['seconds']}),flush=True)
    ab={'scope':'Same cached source scenes and components, all sampled states on two representative targets per project.',
        'nativeDeviceTest':False,'designerGoldLabelsAvailable':False,'projects':sorted(results,key=lambda r:r['project'])}
    (root/'anchor-ab-report.json').write_text(json.dumps(ab,ensure_ascii=False,indent=2),encoding='utf-8')
    totals={'sourcePackages':len(records),'projects':len(paths),'devices':0,'states':0,'scripts':0,'candidatePackages':0,
            'qualitySelections':{},
            'geometryFailures':sum(r.get('validation',{}).get('failures',0) for r in records),
            'sourceHashesUnchanged':all(hashlib.sha256(Path(r['source']).read_bytes()).hexdigest()==r['sha256'] for r in records),
            'liveGestureFailures':sum(r.get('liveValidation',{}).get('failures',0)+sum(v.get('failures',0) for v in r.get('liveValidation',{}).get('variants',[])) for r in records),
            'checkedElementSnapshots':sum(r.get('validation',{}).get('checkedElementSnapshots',0) for r in records),
            'skippedElementSnapshots':sum(r.get('validation',{}).get('skippedElementSnapshots',0) for r in records),
            'nativeDeviceTest':False,'visualAcceptance':False,'themes':[{k:r.get(k) for k in ('name','status','sourceUnchanged')} for r in records]}
    for path in paths:
        p=json.loads(path.read_text(encoding='utf-8'));totals['states']+=len(p['graph']['states']);totals['devices']+=len(p['devices'])
        for d in p['devices']:
            selection=d.get('layoutQuality',{}).get('selection','unknown');totals['qualitySelections'][selection]=totals['qualitySelections'].get(selection,0)+1
        totals['scripts']+=len(list((path.parent/'scripts').glob('*.xml')))
        if not path.parent.parent.name.endswith('-ucd'):totals['candidatePackages']+=len(list((path.parent/'packages').glob('*.hwt')))
        if p['algorithm']!='anchor-theme-layout-v1':raise ValueError('not adapted by anchor: '+str(path))
    totals['scope']='All candidate XML parsed and patch-audited; representative packaged geometry samples, frozen invariants and two live gestures per project. Native glyph estimates and unexpanded branches remain unverified.'
    (root/'anchor-validation-summary.json').write_text(json.dumps(totals,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in totals.items() if k!='themes'},ensure_ascii=True))
    if totals['geometryFailures'] or totals['liveGestureFailures'] or not totals['sourceHashesUnchanged']:raise SystemExit(1)


if __name__=='__main__':main()
