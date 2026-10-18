"""Normalized bbox occupancy comparison, never a pixel/aesthetic guarantee."""
import argparse
import json
import statistics
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.pipeline.scene_clusters import describe_layout,transformed_box,overlap


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=Path('theme/adaptation/visual-similarity-validation.json'))
    parser.add_argument('--all-devices',action='store_true');args=parser.parse_args();results=[]
    for path in sorted(Path('theme/adaptation').glob('*/scenes/project.json')):
        p=json.loads(path.read_text(encoding='utf-8'));source=p['sourceDevice']['viewport'];refs={l['state']:describe_layout(p,l) for l in p['sourceDevice']['layouts']}
        components={c['id']:c for c in p['components']};areas=[];axes=[];oversized=[];missing=0
        for device in p['devices']:
            if not args.all_devices and device['profile']['id'] not in {'tablet-mock-16-10-landscape','dual-fold-mock-inner-portrait'}:continue
            vp=device['viewport']
            for layout in device['layouts']:
                for cid,d in describe_layout(p,layout).items():
                    if components[cid]['role'] not in {'header','footer','content'}:continue
                    ref=refs.get(layout['state'],{}).get(cid,{}).get('bounds');t=layout['transforms'].get(cid)
                    if not ref or not t or min(ref['w'],ref['h'])<=0 or min(d['bounds']['w'],d['bounds']['h'])<=0:missing+=1;continue
                    screen={'x':0,'y':0,'w':source['width'],'h':source['height']}
                    if overlap(ref,screen)<ref['w']*ref['h']-.1 or ref['w']*ref['h']>source['width']*source['height']*.8:continue
                    q=transformed_box(d['bounds'],t)
                    rw=(q['w']/vp['width'])/(ref['w']/source['width']);rh=(q['h']/vp['height'])/(ref['h']/source['height']);area=rw*rh
                    areas.append(area);axes.append(max(rw,rh))
                    if area>1.25+.01 or max(rw,rh)>1.6+.01:
                        oversized.append({'target':device['profile']['id'],'state':layout['state'],'component':cid,
                                          'relativeAreaRatio':round(area,4),'relativeWidthRatio':round(rw,4),'relativeHeightRatio':round(rh,4)})
        row={'project':path.parent.parent.name,'checkedComponents':len(areas),'oversizedSnapshots':len(oversized),
             'medianRelativeAreaRatio':round(statistics.median(areas),4) if areas else None,
             'maximumRelativeAxisRatio':round(max(axes),4) if axes else None,'skippedUnresolved':missing,'reviewExamples':oversized[:12]}
        results.append(row);print(json.dumps({k:v for k,v in row.items() if k!='reviewExamples'}),flush=True)
    report={'scope':'All cached states on two representative targets by default. Same-state component bbox occupancy; full-canvas, intentional source crop and unresolved geometry excluded. Not decoded-pixel or designer acceptance.',
            'areaBudget':1.25,'axisBudget':1.6,'nativeDeviceTest':False,'decodedPixelTest':False,
            'checkedComponents':sum(r['checkedComponents'] for r in results),'oversizedSnapshots':sum(r['oversizedSnapshots'] for r in results),'projects':results}
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
