"""Re-solve cached scenes after algorithm edits, then repack and validate real themes."""
import argparse
import json
import hashlib
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.pipeline.scene_clusters import schedule_device
from multires_app.pipeline.scene_analysis import attach_analysis
from multires_app.preview.scene_previewer import build_scene_preview
from multires_app.pipeline.scene_pipeline import export_script
from multires_app.tools.refresh_previews import repack_record
from multires_app.tools.batch_convert import write_index


def media_digest(p):
    h=hashlib.sha256()
    for device in [*p['devices'],p.get('sourceDevice',{})]:
        for layout in device.get('layouts',[]):h.update(json.dumps([layout.get('mediaLayout'),[r[:9] for r in layout['rows']]],sort_keys=True,separators=(',',':')).encode())
    return h.digest()


def update(path,media_upgrade=False,exports_only=False,layout_only=False):
    p=json.loads(path.read_text(encoding='utf-8'));before=media_digest(p) if media_upgrade else None
    from multires_app.media.media_layout import refresh_references
    if not exports_only and not layout_only:refresh_references(p)
    else:
        from multires_app.media.media_layout import refresh_media_metadata
        refresh_media_metadata(p)
    from multires_app.media.media_layout import compact_references
    compact_references(p)
    changed=not exports_only and (not media_upgrade or before!=media_digest(p))
    if not changed:print(json.dumps({'mediaUnchanged':path.parent.parent.name}),flush=True)
    if changed:p['anchorIRs']={}
    if changed:
        from multires_app.layout.composition_sections import split_composition_sections
        split_composition_sections(p)
        from multires_app.layout.background_layers import split_background_layers
        split_background_layers(p)
        from multires_app.layout.footer_components import split_footer_components
        split_footer_components(p)
        from multires_app.layout.panel_scaling import infer_panel_scale_families
        infer_panel_scale_families(p)
    algorithm=AnchorThemeLayout();p['algorithm']=algorithm.name;p['algorithmSpec']='anchor'
    if changed:
        for d in p['devices']:
            print(json.dumps({'solving':path.parent.parent.name,'target':d['profile']['id']}),flush=True)
            schedule_device(p,d,algorithm)
    from multires_app.pipeline.layout_storage import compact_layout_storage
    compact_layout_storage(p)
    for k in list(p):
        if k.startswith('_'):p.pop(k)
    attach_analysis(p,path.parent)
    report=json.loads((path.parent/'report.json').read_text(encoding='utf-8'))
    report['algorithm']=algorithm.name;report['layoutQuality']={d['profile']['id']:d.get('layoutQuality') for d in p['devices']}
    report['components']=len(p['components'])
    report['layoutClustering']={d['profile']['id']:d['layoutClusters']['summary'] for d in p['devices']}
    report['exports']=[export_script(p,d['profile']['id'],path.parent/'scripts'/(d['profile']['id']+'.xml')) for d in p['devices']]
    for k in list(p):
        if k.startswith('_'):p.pop(k)
    from multires_app.pipeline.layout_storage import externalize_anchor_evidence
    externalize_anchor_evidence(p)
    temporary=path.with_suffix('.json.solving');temporary.write_text(json.dumps(p,ensure_ascii=False,separators=(',',':')),encoding='utf-8');temporary.replace(path)
    (path.parent/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    write_component_artifacts(p,path.parent)
    build_scene_preview(p,path.parent/'preview.html')
    result={'project':path.parent.parent.name,'quality':{k:sum(d.get('layoutQuality',{}).get('selection')==k for d in p['devices']) for k in ('anchor','baseline-rollback','authored-constraints')}}
    print(json.dumps(result),flush=True);return result


def reports_only(path):
    p=json.loads(path.read_text(encoding='utf-8'))
    from multires_app.media.media_layout import compact_references,refresh_media_metadata
    compact_references(p);refresh_media_metadata(p)
    attach_analysis(p,path.parent);write_component_artifacts(p,path.parent);build_scene_preview(p,path.parent/'preview.html')
    temporary=path.with_suffix('.json.reporting');temporary.write_text(json.dumps(p,ensure_ascii=False,separators=(',',':')),encoding='utf-8');temporary.replace(path)
    return path.parent.parent.name


def write_component_artifacts(project,folder):
    (folder/'components.json').write_text(json.dumps({'components':project['components']},ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'layout-clusters.json').write_text(json.dumps({d['profile']['id']:d['layoutClusters'] for d in project['devices']},ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workers',type=int,choices=[1,2,3,4],default=3);parser.add_argument('--reports-only',action='store_true');parser.add_argument('--media-upgrade',action='store_true');parser.add_argument('--exports-only',action='store_true');parser.add_argument('--layout-only',action='store_true',help='Re-solve existing validated media snapshots without replaying their media references');parser.add_argument('--only',nargs='+');parser.add_argument('--skip-repack',action='store_true');args=parser.parse_args()
    if args.layout_only and (args.media_upgrade or args.exports_only or args.reports_only):parser.error('--layout-only cannot be combined with media/export/report modes')
    root=Path('theme/adaptation');paths=sorted(root.glob('*/scenes/project.json'))
    if args.only:paths=[p for p in paths if p.parent.parent.name in args.only]
    if args.reports_only:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(reports_only,p) for p in paths]):print(json.dumps({'reports':future.result()}),flush=True)
        return
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(update,p,args.media_upgrade,args.exports_only,args.layout_only):p for p in paths};errors=[]
        for future in as_completed(futures):
            try:future.result()
            except Exception as exc:
                error={'project':futures[future].parent.parent.name,'error':str(exc)};errors.append(error);print(json.dumps(error),flush=True)
    (root/'layout-run-errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding='utf8')
    if errors:raise SystemExit(1)
    if args.skip_repack:return
    records=json.loads((root/'batch-summary.json').read_text(encoding='utf-8'));updated=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(repack_record,root,r) for r in records]):updated.append(future.result())
    updated.sort(key=lambda r:r['name']);write_index(root,updated)
    if any(r['status']!='validated' or not r['sourceUnchanged'] for r in updated):raise SystemExit(1)


if __name__=='__main__':main()
