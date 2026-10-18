"""Audit cached stable scenes of every adapted theme and every device."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.layout.canvas_bounds import stable_resources,violations
from tests.validation_merge import select_projects,merged_records


def main():
    records=[]
    root=Path('theme/adaptation');paths,previous=select_projects(root,sys.argv[1:],'canvas-validation.json')
    for path in paths:
        p=json.loads(path.read_text(encoding='utf-8'))
        sources={l['state']:l for l in p.get('sourceDevice',{}).get('layouts',[])}
        record={'project':path.parent.parent.name,'states':0,'resources':0,'violations':0,'examples':[]}
        for d in p['devices']:
            viewport=d['viewport']
            for l in d['layouts']:
                region=l.get('adaptationPlan',{}).get('safeRegion',{'x':0,'y':0,'w':viewport['width'],'h':viewport['height']})
                resources=stable_resources(p,l,sources.get(l['state']))
                errors=violations(resources,l['transforms'],region)
                record['states']+=1;record['resources']+=len(resources);record['violations']+=len(errors)
                if errors and len(record['examples'])<3:record['examples'].append({'device':d['profile']['id'],'state':l['state'],'resource':errors[0]})
        records.append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
    refreshed=[r['project'] for r in records];records=merged_records(records,previous)
    report={'scope':'resolved stable resources intersecting the source canvas; backgrounds use cover; authored offscreen waiting resources and entry/exit animation poses excluded',
            'revalidatedProjects':refreshed,'reuseUnchangedProjectRecords':previous is not None,
            'projects':len(records),'states':sum(r['states'] for r in records),'resources':sum(r['resources'] for r in records),
            'violations':sum(r['violations'] for r in records),'records':records}
    Path('theme/adaptation/canvas-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if report['violations']:raise SystemExit(1)


if __name__=='__main__':main()
