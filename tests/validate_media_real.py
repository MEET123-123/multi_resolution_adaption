"""Cross-phase affine and writeback checks on existing real-theme candidates."""
import copy
import json
import math
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.engine.scene_engine import Runtime,indexed_tree
from multires_app.interaction.interaction_semantics import replay_human_state
from multires_app.pipeline.source_comparison import packed_snapshot
from multires_app.media.media_layout import temporal_ids


def check(path):
    p=json.loads(path.read_text(encoding='utf-8'));model=p['model'];model['assetNames']=list(p['assets'])
    ids=temporal_ids(model);states={s['id']:s for s in p['graph']['states']};checked=0;errors=[];skips=[];references=0;hidden=0
    for device in p['devices']:
        if device['profile']['id'] not in {'tablet-mock-16-10-landscape','dual-fold-mock-inner-portrait'}:continue
        target=device['profile']['id'];xml=ET.parse(path.parent/'scripts'/(target+'.xml')).getroot()
        adapted={**model,'nodes':indexed_tree(xml,model['file']),'root':dict(xml.attrib)}
        adapted['variables']={n['attributes']['name']:n for n in adapted['nodes'].values() if n['tag']=='Var' and n['attributes'].get('name')}
        for index in sorted({0,len(device['layouts'])//2,len(device['layouts'])-1}):
            layout=device['layouts'][index];state=states[layout['state']];vp=device['viewport']
            source=replay_human_state(model,vp['width'],vp['height'],state,p['assets'],verify_hit=False)
            first=None
            for phase in (0,100,200,700,1600):
                rt=copy.deepcopy(source);rt.time_ms=phase;rt.system['time']=phase;rt.cache.clear()
                for playback in rt.state.get('__animations',{}).values():playback['started']=0
                snapshot=packed_snapshot(p,rt,layout['state'],layout['transforms'])
                ref=snapshot.get('mediaLayout')
                if first is None:first=ref
                elif ref!=first:errors.append({'target':target,'state':layout['state'],'phase':phase,'reason':'reference-drift'})
                references+=bool(ref)
                native=Runtime(adapted,vp['width'],vp['height'],copy.deepcopy(rt.state),time_ms=phase);native.system.update(rt.system)
                expected={e['id']:e for e in rt.snapshot(p['assets']) if e['id'] in ids}
                actual={e['id']:e for e in native.snapshot(p['assets']) if e['id'] in ids}
                for uid,e in expected.items():
                    node=model['nodes'][uid]
                    if node['tag'] not in {'Image','Video','Rectangle','Button'}:continue
                    if not e['visible']:hidden+=1;continue
                    t=layout['transforms'].get(p['nodeComponents'].get(uid))
                    if not t:skips.append({'node':uid,'reason':'component-not-in-sampled-layout'});continue
                    if node['tag'] in {'Image','Video'} and e['asset'] not in p['assets']:
                        skips.append({'node':uid,'reason':'unavailable-source-asset'});continue
                    if any(uid+':'+a in rt.issues for a in ('x','y','w','h','srcid','srcExp')):
                        skips.append({'node':uid,'reason':'unresolved-source-expression'});continue
                    a=e['rect'];b=actual[uid]['rect'];checked+=1
                    values={'x':a['x']*t['sx']+t['tx'],'y':a['y']*t['sy']+t['ty'],'w':a['w']*t['sx'],'h':a['h']*t['sy']}
                    for key,value in values.items():
                        if not math.isfinite(b[key]) or abs(value-b[key])>.08:errors.append({'target':target,'state':layout['state'],'phase':phase,'node':uid,'field':key,'expected':value,'actual':b[key]})
    from collections import Counter
    return {'project':path.parent.parent.name,'devices':len(p['devices']),'sourceStates':len(p['graph']['states']),
            'scripts':len(list((path.parent/'scripts').glob('*.xml'))),
            'qualitySelections':dict(Counter(d.get('layoutQuality',{}).get('selection','unknown') for d in p['devices'])),
            'temporalNodes':len(ids),'referenceSnapshots':references,'checkedElementSnapshots':checked,
            'failures':len(errors),'examples':errors[:20],'invisibleExcludedSnapshots':hidden,
            'skippedSnapshots':len(skips),'skipReasons':dict(Counter(r['reason'] for r in skips)),'skipExamples':skips[:8]}


if __name__=='__main__':
    root=Path('theme/adaptation');results=[]
    paths=sorted(root.glob('*/scenes/project.json'))
    if sys.argv[1:]:paths=[p for p in paths if p.parent.parent.name in sys.argv[1:]]
    for path in paths:
        row=check(path);results.append(row);print(json.dumps({k:row[k] for k in ('project','temporalNodes','checkedElementSnapshots','failures')}),flush=True)
    records=json.loads((root/'batch-summary.json').read_text(encoding='utf-8'))
    import hashlib
    report={'policy':'first-frame-affine','samplePhasesMs':[0,100,200,700,1600],
            'scope':'Up to three sampled human states on two representative targets per project. Visible image/video/rectangle/button geometry only; invisible frames and unavailable assets excluded. Interpreter checks, not decoded-pixel or native-device validation.',
            'nativeDeviceTest':False,'decodedPixelTest':False,'sourcePackages':len(records),
            'devicePlans':sum(r['devices'] for r in results),'sourceStates':sum(r['sourceStates'] for r in results),
            'scripts':sum(r['scripts'] for r in results),'candidatePackages':sum(len(list((root/r['slug']/'scenes/packages').glob('*.hwt'))) for r in records),
            'batchGeometryFailures':sum(r.get('validation',{}).get('failures',0) for r in records),
            'batchLiveFailures':sum(r.get('liveValidation',{}).get('failures',0)+sum(v.get('failures',0) for v in r.get('liveValidation',{}).get('variants',[])) for r in records),
            'sourceHashesUnchanged':all(hashlib.sha256(Path(r['source']).read_bytes()).hexdigest()==r['sha256'] for r in records),
            'projects':results,'checkedElementSnapshots':sum(r['checkedElementSnapshots'] for r in results),
            'invisibleExcludedSnapshots':sum(r['invisibleExcludedSnapshots'] for r in results),
            'skippedSnapshots':sum(r['skippedSnapshots'] for r in results),'failures':sum(r['failures'] for r in results)}
    (root/'media-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    raise SystemExit(bool(report['failures']))
