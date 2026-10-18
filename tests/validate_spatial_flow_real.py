"""Audit each cached state on every device, with explicit hard/soft scopes."""
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.layout.visual_geometry import ensure_visual_bounds,with_stable_visuals
from multires_app.pipeline.scene_clusters import describe_layout
from multires_app.layout.spatial_flow import layout_flows,flow_errors
from multires_app.layout.layout_quality import screen_relation_errors
from multires_app.layout.anchor_validation import validate_export
from tests.validation_merge import select_projects,merged_records


def main():
    root=Path('theme/adaptation');records=[]
    paths,previous=select_projects(root,sys.argv[1:],'spatial-flow-validation.json')
    for path in paths:
        p=json.loads(path.read_text(encoding='utf8'));ensure_visual_bounds(p)
        r={'project':path.parent.parent.name,'devices':len(p['devices']),'states':0,'horizontalRelations':0,
           'verticalRelations':0,'gapFailures':0,'requiredCenterChecks':0,'requiredCenterFailures':0,
           'exportFailures':0,'minimumVerticalGap':None,'examples':[]}
        for device in p['devices']:
            try:validate_export(p,device)
            except ValueError as exc:
                r['exportFailures']+=1
                if len(r['examples'])<8:r['examples'].append({'device':device['profile']['id'],'exportError':str(exc)})
            for layout in device['layouts']:
                r['states']+=1;flows=layout_flows(p,device,layout)
                r['horizontalRelations']+=sum(f['axis']=='x' for f in flows)
                r['verticalRelations']+=sum(f['axis']=='y' for f in flows)
                failures=flow_errors(flows,layout['transforms']);r['gapFailures']+=len(failures)
                if failures and len(r['examples'])<8:r['examples'].append({'device':device['profile']['id'],'state':layout['state'],'gap':failures[0]})
                from multires_app.pipeline.scene_clusters import transformed_box
                for f in flows:
                    if f['axis']!='y' or f['first'] not in layout['transforms'] or f['last'] not in layout['transforms']:continue
                    a=transformed_box(f['firstRect'],layout['transforms'][f['first']]);b=transformed_box(f['lastRect'],layout['transforms'][f['last']]);gap=b['y']-a['y']-a['h']
                    r['minimumVerticalGap']=gap if r['minimumVerticalGap'] is None else min(r['minimumVerticalGap'],gap)
                plan=layout.get('adaptationPlan',{})
                strict={**plan,'screenDistributions':[],'groups':[{**g,'screenAttachment':{},'centerAxes':g.get('centerRequiredAxes',[])} for g in plan.get('groups',[])]}
                errors=screen_relation_errors(with_stable_visuals(p,layout,describe_layout(p,layout)),layout['transforms'],strict)
                r['requiredCenterChecks']+=len(errors);r['requiredCenterFailures']+=sum(e>.1 for e in errors)
        if r['minimumVerticalGap'] is not None:r['minimumVerticalGap']=round(r['minimumVerticalGap'],4)
        records.append(r);print(json.dumps(r,ensure_ascii=False),flush=True)
    refreshed=[r['project'] for r in records];records=merged_records(records,previous)
    originals=json.loads((root/'batch-summary.json').read_text(encoding='utf8'))
    report={'scope':'All cached sampled states and 22 device directions per project; source-disjoint component visual bounds, not within-raster segmentation. Original overlaps remain intentional; unsampled states and native devices are outside this audit.',
            'sourcePackages':len(originals),'projects':len(records),'devices':sum(r['devices'] for r in records),'states':sum(r['states'] for r in records),'revalidatedProjects':refreshed,'reuseUnchangedProjectRecords':previous is not None,
            **{key:sum(r[key] for r in records) for key in ['horizontalRelations','verticalRelations','gapFailures','requiredCenterChecks','requiredCenterFailures','exportFailures']},
            'sourceHashesUnchanged':all(hashlib.sha256(Path(r['source']).read_bytes()).hexdigest()==r['sha256'] for r in originals),'records':records}
    (root/'spatial-flow-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    raise SystemExit(bool(report['gapFailures'] or report['requiredCenterFailures'] or report['exportFailures'] or not report['sourceHashesUnchanged']))


if __name__=='__main__':main()
