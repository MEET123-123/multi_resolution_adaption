"""Synthetic coordinate-repair task; never represented as designer annotations."""
import copy
import json
import shutil
from pathlib import Path
from PIL import Image
from multires_app.evaluation.core import digest,write_json
from multires_app.pipeline.scene_pipeline import create_project
from .dataset import build,read_samples,read_json
from .render import render


def generate(out,themes=6):
    out=Path(out).resolve()
    if out.exists():raise ValueError('use a new mock workspace')
    out.mkdir(parents=True);projects=[];families={}
    colors=['#15334b','#39265b','#244f48','#4b302b','#24385c','#3c424b']
    profiles={'profiles':[{'id':'tablet','resolutionPx':{'width':1600,'height':1000},'orientations':['portrait','landscape'],'baseOrientation':'landscape'}]}
    for i in range(themes):
        folder=out/'raw'/('mock-'+str(i));lock=folder/'unlock'/'lockscreen';lock.mkdir(parents=True)
        # All artwork and labels are generated here. No real theme package or
        # designer reference is read by this generator.
        y=300+i*15
        Image.new('RGB',(1080,2400),colors[i%len(colors)]).save(lock/'background.png')
        xml=f'''<Lockscreen screenWidth="1080" screenHeight="2400">
<Image name="background" x="0" y="0" w="1080" h="2400" src="background.png"/>
<Text name="clock" x="330" y="{y}" w="420" h="160" size="100" text="00:41" color="#ffffff"/>
<Text name="date" x="400" y="{y+200}" w="280" h="80" size="38" text="11 / 13" color="#dddddd"/>
<Text name="weather" x="{100+i*8}" y="1100" w="260" h="100" size="46" text="22 C" color="#a7e6ff"/>
<Text name="flashlight" x="50" y="2220" w="100" h="100" size="54" text="F" color="#ffffff"/>
<Text name="camera" x="930" y="2220" w="100" h="100" size="54" text="C" color="#ffffff"/>
</Lockscreen>'''
        (lock/'manifest.xml').write_text(xml,encoding='utf8')
        project_path=out/'projects'/('mock-'+str(i))/'scenes'
        create_project(folder,{'id':'mock-phone','resolutionPx':{'width':1080,'height':2400}},profiles,project_path,
                       max_states=1,max_depth=0)
        projects.append(project_path/'project.json')
        split='train' if i<themes-2 else 'validation' if i==themes-2 else 'test'
        families['mock-'+str(i)]={'family':'synthetic-family-'+str(i),'split':split}
    directory=out/'dataset'
    manifest=build(projects,directory,max_states=1,targets=['tablet-portrait','tablet-landscape'],family_overrides=families)
    rows=[]
    for sample in read_samples(directory):
        answer=copy.deepcopy(sample['label']['answer'])
        # Deliberately perturb the editable layout, retaining the deterministic
        # anchor-engine reference as the synthetic correction target.
        p=read_json(sample['project']);d=next(d for d in p['devices'] if d['profile']['id']==sample['device'])
        layout=d['layouts'][0];groups={g['id']:g for g in layout['adaptationPlan']['groups']}
        def leaves(gid):return [cid for m in groups[gid]['members'] for cid in (leaves(m) if m in groups else [m])]
        for j,(alias,b) in enumerate(sample['inputLayout']['groups'].items()):
            dx=80 if j%2 else -80;dy=45 if j%2 else -45
            b[0]+=dx;b[1]+=dy
            for cid in leaves(sample['groupMap'][alias]):
                t=layout['transforms'][cid];t['tx']+=dx*sample['request']['safeRegion']['w']/1000;t['ty']+=dy*sample['request']['safeRegion']['h']/1000
        reference='references/'+sample['id']+'-synthetic-target.png';(directory/'references').mkdir(exist_ok=True)
        shutil.copyfile(directory/sample['targetImage'],directory/reference)
        sample['targetReferenceImage']=reference;sample['targetImage']='images/'+sample['id']+'-current.png'
        render(p,d,layout,Path(sample['project']).parent/'assets',directory/sample['targetImage'])
        sample['label']={'status':'synthetic','origin':'generated mock; deterministic anchor reference; no human design label','answer':answer}
        sample['syntheticTask']='repair known coordinate perturbations; engineering feasibility only'
        sample['dataMode']='mock';rows.append(sample)
        write_json(directory/('layouts/'+sample['id']+'-input.json'),{'constraints':sample['request'],'layoutJson':sample['inputLayout']})
    with (directory/'samples.jsonl').open('w',encoding='utf8') as stream:
        for s in rows:stream.write(json.dumps(s,ensure_ascii=False,allow_nan=False)+'\n')
    manifest.update(dataMode='mock',labelCounts={'synthetic':len(rows)},syntheticTask='coordinate repair; no real-theme generalization evidence')
    write_json(directory/'manifest.json',manifest)
    from .review_ui import build_review
    build_review(directory,rows)
    from .quality import audit
    audit(directory,directory/'quality')
    return manifest
