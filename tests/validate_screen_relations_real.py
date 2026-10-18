"""Audit inferred screen relations in cached real themes, including conflicts."""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.pipeline.scene_clusters import describe_layout
from multires_app.layout.layout_quality import screen_relation_errors


def main():
    root=Path('theme/adaptation');projects=[]
    for path in sorted(root.glob('*/scenes/project.json')):
        p=json.loads(path.read_text(encoding='utf-8'));examples=[];checks=violations=0;max_error=0.;attachments=Counter();rails=Counter();frozen_drift=0
        for device in p['devices']:
            layouts={l['state']:l for l in device['layouts']}
            for layout in device['layouts']:
                plan=layout.get('adaptationPlan',{});base=layouts.get(layout.get('layoutRequest',{}).get('baseState'))
                if base:
                    frozen_drift+=sum(layout['transforms'].get(cid)!=base['transforms'].get(cid) for cid in layout.get('layoutRequest',{}).get('fixedComponents',[]))
                # Reuse carries an exact matrix; audit its inherited relation plan.
                seen=set();current=layout
                while current.get('adaptationPlan',{}).get('route')=='reuse' and current['state'] not in seen:
                    seen.add(current['state']);next_state=current.get('adaptationPlan',{}).get('reusedFrom')
                    if next_state not in layouts:break
                    current=layouts[next_state]
                plan=current.get('adaptationPlan',{})
                for g in plan.get('groups',[]):attachments.update(i['edge'] for i in g.get('screenAttachment',{}).values())
                rails.update(r['mode'] for r in plan.get('screenDistributions',[]))
                errors=screen_relation_errors(describe_layout(p,layout),layout['transforms'],plan)
                checks+=len(errors);bad=[e for e in errors if e>1];violations+=len(bad);max_error=max([max_error,*errors])
                if bad and len(examples)<12:
                    conflicts=[v for v in plan.get('validation',{}).get('soft_violations',[]) if ':screen-' in v['id'] or v['id'].startswith('rail:')]
                    examples.append({'target':device['profile']['id'],'state':layout['state'],'maxErrorPx':max(bad),'conflicts':conflicts[:6]})
        projects.append({'project':path.parent.parent.name,'devices':len(p['devices']),'componentSplits':p.get('screenRelationComponentSplits',[]),
                         'edgeAttachmentSnapshots':dict(attachments),'distributionSnapshots':dict(rails),'relationChecks':checks,
                         'relationViolationsOver1px':violations,'maximumErrorPx':round(max_error,4),'frozenTransformDrift':frozen_drift,'reviewExamples':examples})
    records=json.loads((root/'batch-summary.json').read_text(encoding='utf-8'))
    report={'scope':'All cached reachable states and device profiles. Inferred geometry relations; >1 logical pixel marked for review, not native or pixel acceptance.',
            'sourcePackages':len(records),'sourceHashesUnchanged':all(hashlib.sha256(Path(r['source']).read_bytes()).hexdigest()==r['sha256'] for r in records),
            'projects':projects,'devicePlans':sum(p['devices'] for p in projects),'relationChecks':sum(p['relationChecks'] for p in projects),
            'relationViolationsOver1px':sum(p['relationViolationsOver1px'] for p in projects),'frozenTransformDrift':sum(p['frozenTransformDrift'] for p in projects),
            'splitComponents':sum(len(p['componentSplits']) for p in projects),'nativeDeviceTest':False}
    (root/'screen-relations-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='projects'}))
    raise SystemExit(bool(report['frozenTransformDrift'] or not report['sourceHashesUnchanged']))


if __name__=='__main__':main()
