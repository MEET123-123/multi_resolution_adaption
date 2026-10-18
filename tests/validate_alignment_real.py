"""Validate shared alignment contracts after optimization and script export."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.layout.spatial_alignment import layout_alignments,alignment_errors
from multires_app.layout.anchor_validation import validate_export
from tests.validation_merge import select_projects,merged_records


def main():
    root=Path('theme/adaptation');records=[]
    paths,previous=select_projects(root,sys.argv[1:],'alignment-validation.json')
    for path in paths:
        p=json.loads(path.read_text(encoding='utf-8'));record={'project':path.parent.parent.name,'devices':len(p['devices']),'states':0,'policyStates':0,'alignmentChecks':0,'failures':0,'containerChecks':0,'containerFailures':0,'exportFailures':[],'examples':[]}
        for d in p['devices']:
            try:validate_export(p,d)
            except Exception as exc:record['exportFailures'].append({'device':d['profile']['id'],'error':str(exc)})
            for l in d['layouts']:
                rs=layout_alignments(p,d,l);errors=alignment_errors(rs,l['transforms'])
                record['states']+=1;record['alignmentChecks']+=len(rs);record['failures']+=len(errors)
                record['policyStates']+=bool(l.get('adaptationPlan',{}).get('spatialAlignmentPolicy'))
                inner=[r for r in layout_alignments(p,d,l,include_container=True) if r.get('scope')=='container']
                inner_errors=alignment_errors(inner,l['transforms'])
                record['containerChecks']+=len(inner);record['containerFailures']+=len(inner_errors)
                if inner_errors and len(record['examples'])<5:record['examples'].append({'device':d['profile']['id'],'state':l['state'],'containerError':inner_errors[0]})
                if errors and len(record['examples'])<5:record['examples'].append({'device':d['profile']['id'],'state':l['state'],'error':errors[0]})
        records.append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
    refreshed=[r['project'] for r in records];records=merged_records(records,previous)
    result={'projects':len(records),'devices':sum(r['devices'] for r in records),'states':sum(r['states'] for r in records),'revalidatedProjects':refreshed,'reuseUnchangedProjectRecords':previous is not None,
            'alignmentChecks':sum(r['alignmentChecks'] for r in records),'failures':sum(r['failures'] for r in records),
            'exportFailures':sum(len(r['exportFailures']) for r in records),'records':records,
            'containerChecks':sum(r['containerChecks'] for r in records),'containerFailures':sum(r['containerFailures'] for r in records),
            'policyStates':sum(r['policyStates'] for r in records),
            'scope':'resolved visible left/right/top/bottom/center lines; persistent native-family relations; source HWT unchanged; no physical device test'}
    (root/'alignment-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='records'}),flush=True)
    if result['failures'] or result['exportFailures'] or result['containerFailures'] or result['policyStates']!=result['states']:raise SystemExit(1)


if __name__=='__main__':main()
