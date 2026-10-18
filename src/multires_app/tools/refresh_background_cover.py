"""Refresh backdrop cover without rerunning foreground layout or animation."""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from multires_app.layout.visual_geometry import background_rects,background_cover
from multires_app.pipeline.scene_pipeline import export_script,state_guard
from multires_app.preview.scene_previewer import build_scene_preview


def update(path):
    p=json.loads(path.read_text(encoding='utf8'))
    from multires_app.layout.background_layers import split_background_layers
    split_background_layers(p)
    source={l['state']:background_rects(p,l) for l in p['sourceDevice']['layouts']}
    states={s['id']:s for s in p['graph']['states']};count=0
    bg_ids={c['id'] for c in p['components'] if c['role']=='background'}
    for device in p['devices']:
        layouts={l['state']:l for l in device['layouts']};parents={};candidates={}
        guards={l['state']:state_guard(p,states[l['state']],device) for l in device['layouts']}
        def find(key):
            parents.setdefault(key,key)
            if parents[key]!=key:parents[key]=find(parents[key])
            return parents[key]
        for layout in device['layouts']:
            base=layouts.get(layout.get('layoutRequest',{}).get('baseState'))
            if not base:continue
            for cid in set(layout.get('layoutRequest',{}).get('fixedComponents',[]))&bg_ids:
                left=find((guards[layout['state']],cid));right=find((guards[base['state']],cid));parents[left]=right
        for layout in device['layouts']:
            for cid,b in background_rects(p,layout).items():
                key=find((guards[layout['state']],cid))
                candidates.setdefault(key,background_cover(b,source.get(layout['state'],{}).get(cid,b),device['viewport'],p['sourceDevice']['viewport']))
        for layout in device['layouts']:
            for cid in bg_ids:
                key=find((guards[layout['state']],cid))
                if key in candidates:layout['transforms'][cid]=dict(candidates[key]);count+=1
        device['backgroundCoverPolicy']='primary-backdrop-per-axis-source-crop'
    report=json.loads((path.parent/'report.json').read_text(encoding='utf8'))
    report['exports']=[export_script(p,d['profile']['id'],path.parent/'scripts'/(d['profile']['id']+'.xml')) for d in p['devices']]
    p['backgroundCoverPolicy']='primary-backdrop-per-axis-source-crop'
    from multires_app.pipeline.layout_storage import compact_layout_storage
    compact_layout_storage(p)
    for key in list(p):
        if key.startswith('_'):p.pop(key)
    temp=path.with_suffix('.json.background');temp.write_text(json.dumps(p,ensure_ascii=False,separators=(',',':')),encoding='utf8');temp.replace(path)
    (path.parent/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (path.parent/'components.json').write_text(json.dumps({'components':p['components']},ensure_ascii=False,indent=2),encoding='utf8')
    build_scene_preview(p,path.parent/'preview.html')
    print(json.dumps({'backgrounds':path.parent.parent.name,'snapshots':count}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='+');args=parser.parse_args()
    paths=[p for p in Path('theme/adaptation').glob('*/scenes/project.json') if not args.only or p.parent.parent.name in args.only]
    with ProcessPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(update,p) for p in paths]):future.result()


if __name__=='__main__':
    main()
