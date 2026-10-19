"""Check exported initial scenes against source interaction-waiting nodes."""
import argparse
import copy
import json
import tempfile
from pathlib import Path

from multires_app.engine.scene_engine import load_model
from multires_app.interaction.interaction_semantics import initialize
from multires_app.layout.canvas_bounds import intersects
from multires_app.media.resource_scaling import manifest_resources


ROOT=Path(__file__).resolve().parents[1]


def validate(path):
    p=json.loads(path.read_text(encoding='utf8'));results=[];failures=[]
    source=p['sourceDevice']['layouts'][0]
    inactive=set(source.get('mediaLayout',{}).get('inactiveSceneNodeIndices',[]))
    ids={p['nodeOrder'][i] for i in inactive}
    with tempfile.TemporaryDirectory(dir=ROOT/'artifacts',prefix='native-animation-') as tmp:
        folder=Path(tmp);manifest=folder/p['model']['file'];manifest.parent.mkdir(parents=True)
        for device in p['devices']:
            script=path.parent/'scripts'/(device['profile']['id']+'.xml')
            manifest.write_bytes(script.read_bytes());model=load_model(folder,p['model']['file'])
            generated=manifest_resources(script)['files'];assets={**p['assets'],**generated}
            model['assetNames']=list(assets)
            inputs=p['model'].get('inputs',{})
            model['inputs']={k:copy.deepcopy(v) for k,v in inputs.items() if k in {'system','variables'}}
            vp=device['viewport'];runtime=initialize(model,vp['width'],vp['height'])
            premature={e['id'] for e in runtime.snapshot(assets) if e['id'] in ids and e['visible'] and intersects(e['rect'],vp)}
            for uid in sorted(premature):failures.append({'target':device['profile']['id'],'node':uid,'reason':'interaction-waiting-node-visible-on-initial-canvas'})
            results.append({'target':device['profile']['id'],'checkedWaitingNodes':len(ids),'prematureNodes':len(premature)})
    return {'project':path.parent.parent.name,'devices':results,'failures':failures,
            'states':len(p['graph']['states']),'boundedExploration':p['graph']['coverage'].get('truncated',False),
            'nativeDeviceTest':False}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='+');args=parser.parse_args()
    paths=sorted((ROOT/'theme/adaptation').glob('*/scenes/project.json'))
    if args.only:paths=[p for p in paths if p.parent.parent.name in args.only]
    results=[]
    for path in paths:
        try:result=validate(path)
        except Exception as exc:result={'project':path.parent.parent.name,'failures':[{'error':str(exc)}]}
        results.append(result);print(json.dumps({'project':result['project'],'failures':len(result['failures'])}),flush=True)
    report={'projects':len(results),'deviceScenes':sum(len(r.get('devices',[])) for r in results),
            'checkedWaitingNodes':sum(d['checkedWaitingNodes'] for r in results for d in r.get('devices',[])),
            'failures':sum(len(r['failures']) for r in results),'nativeDeviceTest':False,'results':results}
    output=ROOT/'artifacts/reports/interaction-animation-native-validation.json'
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    if report['failures']:raise SystemExit(1)


if __name__=='__main__':main()
