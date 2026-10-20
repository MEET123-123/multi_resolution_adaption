"""Content-based screenshot pairing and theme retrieval; never use file names as labels."""
from __future__ import annotations
import argparse
import html
import io
import json
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image,ImageOps

from multires_app.preview.automatic_matching import assignment
from multires_app.preview.reference_workflow import save


def features(image,cv):
    image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((640,640))
    return cv.SIFT_create(nfeatures=2200,contrastThreshold=.025).detectAndCompute(cv.cvtColor(np.asarray(image),cv.COLOR_RGB2GRAY),None)


def similarity(a,b,cv):
    if a[1] is None or b[1] is None:return 0.
    good=[p[0] for p in cv.BFMatcher().knnMatch(a[1],b[1],k=2) if len(p)==2 and p[0].distance<.72*p[1].distance]
    # Count distinct target points: repeated glyphs may not inflate similarity.
    count=len({p.trainIdx for p in good})
    return count/max(30.,np.sqrt(len(a[0])*len(b[0])))


def index_pairs(before,after,themes,out):
    import cv2 as cv
    cv.setNumThreads(2);cv.setRNGSeed(31);out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    files=lambda p:sorted(x.resolve() for x in Path(p).iterdir() if x.suffix.lower() in {'.jpg','.jpeg','.png','.webp'})
    left,right=files(before),files(after)
    def read(path):
        with Image.open(path) as im:return features(im,cv)
    af=[read(p) for p in left];bf=[read(p) for p in right]
    scores=[[similarity(a,b,cv) for b in bf] for a in af];selected=assignment(scores,.025)
    # Retrieval is independent of pairing: archive previews identify the theme.
    packages=[]
    for path in sorted(Path(themes).glob('*.hwt')):
        try:
            with zipfile.ZipFile(path) as archive:
                names=[n for n in archive.namelist() if 'preview_unlock' in n.lower() and n.lower().endswith(('.jpg','.png','.jpeg'))][:8]
                for name in names:
                    info=archive.getinfo(name)
                    if info.file_size>8_000_000:continue
                    with Image.open(io.BytesIO(archive.read(name))) as im:
                        if im.width*im.height>12_000_000:continue
                        packages.append((str(path.resolve()),name,features(im,cv)))
        except (OSError,ValueError,zipfile.BadZipFile):continue
    rows=[]
    for i,j in enumerate(selected):
        ranked=sorted(((similarity(af[i],f,cv),p,n) for p,n,f in packages),reverse=True)
        theme_scores={}
        for score,p,n in ranked:
            if p not in theme_scores:theme_scores[p]={'source':p,'score':score,'preview':n}
        alternatives=list(theme_scores.values())[:3]
        margin=scores[i][j]-max([v for k,v in enumerate(scores[i]) if k!=j],default=0) if j is not None else 0
        row={'sourceImage':str(left[i]),'targetImage':str(right[j]) if j is not None else None,
            'pairScore':scores[i][j] if j is not None else 0,'pairMargin':margin,'themeCandidates':alternatives,
            'status':'paired' if j is not None and margin>.005 else 'ambiguous','manualCoordinates':False}
        rows.append(row);save(out/'pairs.json',{'schema':'reference-pairs/1','algorithm':'SIFT retrieval + Hungarian','pairs':rows})
    page='<meta charset="utf-8"><title>前后截图自动配对</title><style>body{font:16px system-ui}section{border-bottom:1px solid #ccc;padding:16px}img{height:320px;max-width:43%;object-fit:contain}pre{white-space:pre-wrap}</style><h1>前后截图内容配对与主题检索</h1>'
    for row in rows:
        page+='<section><img src="'+Path(row['sourceImage']).as_uri()+'">'
        if row['targetImage']:page+='<img src="'+Path(row['targetImage']).as_uri()+'">'
        page+='<pre>'+html.escape(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></section>'
    (out/'report.html').write_text(page,encoding='utf8')
    return {'pairs':len(rows),'paired':sum(r['status']=='paired' for r in rows),'report':str(out/'report.html')}


def run_pairs(index,projects,out,limit=None,retry_failed=False,retry_review=False,reuse_evidence=False):
    """Reuse parsed scripts and render each distinct cached source pose once."""
    import cv2 as cv
    from multires_app.learning.render import render
    from multires_app.preview.reference_workflow import prepare,complete
    cv.setNumThreads(2);out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    rows=json.loads(Path(index).read_text(encoding='utf8'))['pairs'];results=[];catalog={}
    import hashlib
    for path in Path(projects).glob('*/scenes/project.json'):
        project=json.loads(path.read_text(encoding='utf8'));source=Path(project.get('source','')).resolve()
        catalog.setdefault(str(source),[]).append(path.resolve())
        del project
    candidates={}
    for i,row in enumerate(rows[:limit]):
        record=out/f'pair-{i:03d}'/'result.json'
        if record.exists():
            prior=json.loads(record.read_text(encoding='utf8'))
            retry=(retry_failed and prior['status']=='failed') or (retry_review and prior['status']=='needs-review')
            if not retry:results.append(prior);continue
        item={'pair':i,'sourceImage':row['sourceImage'],'targetImage':row['targetImage'],'status':'failed','manualCoordinates':False}
        try:
            if row['status']!='paired':raise ValueError('截图配对存在歧义，未自动反写')
            theme=row['themeCandidates'][0];paths=catalog.get(theme['source'],[])
            if not paths:raise ValueError('没有对应的已提取主题缓存')
            project_path=paths[-1];project=json.loads(project_path.read_text(encoding='utf8'))
            key=str(project_path)
            if key not in candidates:
                candidates[key]=[];seen=set()
                for layout in project['sourceDevice']['layouts']:
                    visible=[r for r in layout['rows'] if r[5]>0 and min(r[3:5])>0]
                    signature=hashlib.sha256(json.dumps(visible,sort_keys=True).encode()).hexdigest()[:16]
                    if signature in seen:continue
                    seen.add(signature);png=out/'state-cache'/hashlib.sha256(key.encode()).hexdigest()[:10]/(signature+'.png')
                    if not png.exists():render(project,project['sourceDevice'],layout,project_path.parent/'assets',png)
                    with Image.open(png) as im:f=features(im,cv)
                    candidates[key].append((layout['state'],f))
                print(f'cached {len(candidates[key])} distinct source states: {project_path.parent.parent.name}',flush=True)
            with Image.open(row['sourceImage']) as im:observed=features(im,cv)
            ranked=sorted([(similarity(observed,f,cv),state) for state,f in candidates[key]],reverse=True)
            score,state=ranked[0]
            item['stateRetrieval']={'state':state,'score':score,'alternatives':ranked[:5],'theme':theme}
            if score<.035:raise ValueError('源截图与缓存场景的视觉证据不足，未反写')
            with Image.open(row['targetImage']) as im:ratio=im.width/im.height
            devices=sorted(project['devices'],key=lambda d:abs(d['viewport']['width']/d['viewport']['height']/ratio-1))
            device=devices[0]
            if abs(device['viewport']['width']/device['viewport']['height']/ratio-1)>.025:raise ValueError('目标图没有同宽高比设备，需建立目标设备配置')
            device_id=device['profile']['id']
            del devices,device,project
            import gc
            gc.collect()
            import uuid
            folder=record.parent/('attempt-'+uuid.uuid4().hex[:8]);prepare(project_path,device_id,state,folder/'request')
            # Use the actual screenshot, preserving real clock/weather data.
            request_file=folder/'request/request.json';request=json.loads(request_file.read_text(encoding='utf8'))
            from multires_app.preview.reference_workflow import digest
            request['sourceImage']=row['sourceImage'];request['sourceSha256']=digest(row['sourceImage']);save(request_file,request)
            replay=None
            if reuse_evidence:
                matches=sorted(record.parent.glob('**/target-matching/*/matches.json'),key=lambda p:p.stat().st_mtime,reverse=True)
                for evidence in matches:
                    answer=json.loads(evidence.read_text(encoding='utf8'))
                    if answer.get('automatic') and answer.get('matches'):replay=evidence.parent;break
            item['workflow']=complete(request_file,row['targetImage'],provider='user-paired-screenshots',replay_job=replay)
            item['status']=item['workflow']['status']
        except Exception as exc:item['error']=str(exc)
        save(record,item);results.append(item);save(out/'results.json',results)
        print(json.dumps({'pair':i,'status':item['status'],'error':item.get('error'),'quality':item.get('workflow',{}).get('quality')},ensure_ascii=False),flush=True)
    page='<meta charset="utf-8"><title>真实截图回归</title><h1>真实前后截图自动回归</h1><p>人工坐标：无。失败与低覆盖案例保留，未计入成功。</p>'
    for item in results:
        page+='<section><h2>Pair '+str(item['pair'])+' · '+html.escape(item['status'])+'</h2>'
        if item.get('workflow',{}).get('directory'):page+='<a href="'+(Path(item['workflow']['directory'])/'report.html').as_uri()+'">完整流程与脚本</a>'
        summary={'quality':item.get('workflow',{}).get('quality'),'stateRetrieval':item.get('stateRetrieval'),
            'error':item.get('error') or item.get('workflow',{}).get('error'),
            'readback':{k:v for k,v in item.get('workflow',{}).get('readback',{}).items() if k not in {'worstNodes','diagnostics'}}}
        page+='<pre style="white-space:pre-wrap">'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre></section>'
    (out/'report.html').write_text(page,encoding='utf8')
    return {'processed':len(results),'statuses':{s:sum(r['status']==s for r in results) for s in {r['status'] for r in results}},'report':str(out/'report.html')}


def summarize(out):
    """Machine-readable failure feedback, with links back to reproducible inputs."""
    out=Path(out).resolve();results=[json.loads(p.read_text(encoding='utf8')) for p in sorted(out.glob('pair-*/result.json'))];cases=[]
    for item in results:
        workflow=item.get('workflow',{});quality=workflow.get('quality',{});error=item.get('error') or workflow.get('error','')
        code=None
        if '源截图' in error:code='source-state-unresolved'
        elif '宽高比' in error:code='viewport-mismatch'
        elif 'animation wrapper' in error:code='shared-animation-scale-conflict'
        elif '没有可靠匹配' in error:code='insufficient-visual-evidence'
        elif workflow.get('readback',{}).get('geometryPassed') is False:code='native-geometry-drift'
        elif error:code='export-or-constraint-rejected'
        elif quality.get('lowFitGroups'):code='target-fit-low'
        elif quality.get('matchedGroups',0)<quality.get('eligibleGroups',0):code='match-coverage-low'
        if code:
            cases.append({'pair':item['pair'],'code':code,'status':item['status'],'error':error,'quality':quality,
                'sourceImage':item['sourceImage'],'targetImage':item['targetImage'],'reproduceRequest':workflow.get('request'),
                'evidenceDirectory':workflow.get('directory'),'state':item.get('stateRetrieval',{}).get('state'),
                'manualCoordinates':False})
    (out/'badcases.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),encoding='utf8')
    counts={s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})}
    summary={'processed':len(results),'statuses':counts,'badcaseTypes':{c:sum(b['code']==c for b in cases) for c in sorted({b['code'] for b in cases})},
        'nativeGeometryPassed':sum(r.get('workflow',{}).get('readback',{}).get('geometryPassed',False) for r in results),
        'nativeDeviceTest':False,'manualCoordinates':False}
    save(out/'summary.json',summary)
    page='<meta charset="utf-8"><title>截图适配回归与反馈</title><style>body{font:16px system-ui;max-width:1200px;margin:30px auto}section{border-top:1px solid #ccc;padding:16px}img{max-height:240px;max-width:45%}pre{white-space:pre-wrap}</style><h1>真实截图适配回归与反馈</h1><p>自动配对与自动适配分别统计；已导出候选仍需满足覆盖和几何回读要求。未进行真机验证。</p><pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre><p><a href="badcases.jsonl">结构化 badcase</a> · <a href="results.json">完整结果</a></p>'
    for item in results:
        page+='<section><h2>Pair '+str(item['pair'])+' · '+html.escape(item['status'])+'</h2><img src="'+Path(item['sourceImage']).as_uri()+'"><img src="'+Path(item['targetImage']).as_uri()+'">'
        workflow=item.get('workflow',{});detail=next((c for c in cases if c['pair']==item['pair']),{'quality':workflow.get('quality')})
        if workflow.get('directory'):page+='<p><a href="'+(Path(workflow['directory'])/'report.html').as_uri()+'">定位、求解与脚本</a></p>'
        page+='<pre>'+html.escape(json.dumps(detail,ensure_ascii=False,indent=2))+'</pre></section>'
    (out/'report.html').write_text(page,encoding='utf8');return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--before',type=Path);parser.add_argument('--after',type=Path);parser.add_argument('--themes',type=Path);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--index',type=Path);parser.add_argument('--projects',type=Path,default=Path('theme/adaptation'));parser.add_argument('--limit',type=int);parser.add_argument('--retry-failed',action='store_true')
    parser.add_argument('--retry-review',action='store_true');parser.add_argument('--reuse-evidence',action='store_true',help='Re-solve previous automatic observations instead of recomputing CV')
    parser.add_argument('--summarize',action='store_true',help='Build compact report and badcases.jsonl from checkpointed results')
    args=parser.parse_args()
    if args.summarize:result=summarize(args.out)
    elif args.index:result=run_pairs(args.index,args.projects,args.out,args.limit,args.retry_failed,args.retry_review,args.reuse_evidence)
    else:
        if not all((args.before,args.after,args.themes)):parser.error('配对需要 --before --after --themes；回归使用 --index')
        result=index_pairs(args.before,args.after,args.themes,args.out)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
