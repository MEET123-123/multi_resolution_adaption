"""Audit settled corner icons, their hit ownership and all exported targets."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.pipeline.scene_clusters import box_union,transformed_box
from multires_app.layout.anchor_validation import validate_export


def main():
    root=Path('theme/adaptation');records=[]
    for path in sorted(root.glob('*/scenes/project.json')):
        if len(sys.argv)>1 and path.parent.parent.name not in sys.argv[1:]:continue
        p=json.loads(path.read_text(encoding='utf-8'));corners=[c for c in p['components'] if c.get('cornerAttachment')]
        if not corners:continue
        nodes=p['model']['nodes'];source=p['sourceDevice'];sources={l['state']:l for l in source['layouts']}
        r={'project':path.parent.parent.name,'corners':len(corners),'devices':len(p['devices']),'states':0,'stableCornerChecks':0,'edgeChecks':0,'animationOrHiddenSkips':0,'failures':0,'exportFailures':0,'examples':[]}
        def frame(layout,cid):
            from multires_app.layout.corner_geometry import component_frame
            return component_frame(p,layout,cid)
        for d in p['devices']:
            try:validate_export(p,d)
            except ValueError as exc:
                r['exportFailures']+=1
                if len(r['examples'])<5:r['examples'].append({'device':d['profile']['id'],'exportError':str(exc)})
            for l in d['layouts']:
                r['states']+=1;region=l['adaptationPlan']['safeRegion']
                for c in corners:
                    cid=c['id'];reference=c['cornerAttachment'];margin=reference['sourceMargins'];b=frame(sources[l['state']],cid)
                    sx=b['x'] if reference['corner']=='bottom-left' else source['viewport']['width']-b['x']-b['w'];sy=source['viewport']['height']-b['y']-b['h']
                    if min(b['w'],b['h'])<=0 or abs(sx-margin['x'])>.1 or abs(sy-margin['y'])>.1 or cid not in l['transforms']:
                        r['animationOrHiddenSkips']+=1;continue
                    raw=frame(l,cid)
                    if min(raw['w'],raw['h'])<=0:r['animationOrHiddenSkips']+=1;continue
                    t=l['transforms'][cid];q=transformed_box(raw,t)
                    mx=q['x']-region['x'] if reference['corner']=='bottom-left' else region['x']+region['w']-q['x']-q['w'];my=region['y']+region['h']-q['y']-q['h']
                    error=max(abs(mx-margin['x']*t['sx']),abs(my-margin['y']*t['sy']))
                    ownership=all(p['nodeComponents'].get(uid)==cid for uid in c.get('bindingButtons',[]))
                    r['stableCornerChecks']+=1;r['edgeChecks']+=2
                    if error>.1 or not ownership or abs(t['sx']-t['sy'])>1e-8:
                        r['failures']+=1
                        if len(r['examples'])<5:r['examples'].append({'device':d['profile']['id'],'state':l['state'],'corner':reference['corner'],'edgeError':error,'buttonOwnership':ownership,'target':q})
        records.append(r);print(json.dumps(r),flush=True)
    report={'projects':len(records),'devices':sum(r['devices'] for r in records),'states':sum(r['states'] for r in records),
            **{k:sum(r[k] for r in records) for k in ('stableCornerChecks','edgeChecks','animationOrHiddenSkips','failures','exportFailures')},'records':records,
            'scope':'settled raw image frames and owned hit regions; original entry/exit movement is retained; no physical device test'}
    (root/'corner-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k!='records'}),flush=True)
    if report['failures'] or report['exportFailures'] or not report['stableCornerChecks']:raise SystemExit(1)


if __name__=='__main__':main()
