"""Reuse audit records only for project files untouched since that audit."""
import json
from pathlib import Path


def select_projects(root,args,report_name):
    selected={a for a in args if a not in {'--merge','--only'}};merge='--merge' in args
    paths=sorted(root.glob('*/scenes/project.json'));previous=None
    if selected-set(p.parent.parent.name for p in paths):raise ValueError('unknown audit project')
    if merge:
        if not selected:raise ValueError('--merge requires selected projects')
        report=root/report_name;previous=json.loads(report.read_text(encoding='utf-8'));timestamp=report.stat().st_mtime
        covered={r['project'] for r in previous['records']}
        for path in paths:
            name=path.parent.parent.name
            if name not in selected and (name not in covered or path.stat().st_mtime>timestamp+.001):
                raise ValueError('unchosen project changed since audit: '+name)
    return [p for p in paths if not selected or p.parent.parent.name in selected],previous


def merged_records(records,previous):
    if previous is None:return records
    result={r['project']:r for r in previous['records']};result.update({r['project']:r for r in records})
    return [result[k] for k in sorted(result)]
