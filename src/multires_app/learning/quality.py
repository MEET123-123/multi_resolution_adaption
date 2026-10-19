"""Dataset grades and missingness, separate from theme layout aesthetics."""
import json
from collections import Counter
from pathlib import Path
from .contract import validate_answer
from .catalog import Catalog
from .dataset import read_samples,read_json
from multires_app.evaluation.core import write_json


def audit(directory,out):
    directory=Path(directory).resolve();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    catalog=Catalog(directory);grades=Counter();semantics=Counter();missing=Counter();total=0;trainable=0;rows=[];elements=0;synthetic=0
    for s in read_samples(directory):
        total+=1;problems=[];review=catalog.sample(s['id']).get('latestReview');grade='C'
        synthetic+=int(s['label']['status']=='synthetic')
        for name in ('sourceImage','targetImage'):
            path=(directory/s[name]).resolve()
            if not path.is_relative_to(directory) or not path.is_file():problems.append({'field':name,'reason':'missing image'});missing[name]+=1
        answer=(review or {}).get('answer',s['label'].get('answer'))
        try:validate_answer(answer,s['request'])
        except (ValueError,TypeError) as exc:problems.append({'field':'target_layout','reason':str(exc)});missing['target_layout']+=1
        if s.get('annotations'):
            annotation=read_json(directory/s['annotations'])
            for e in annotation['elementLevel']['elements']:
                elements+=1
                semantics[e['semantic']['label']]+=1
                if e['visible_bbox'] is None:missing['visible_bbox_not_measured']+=1
                if e['image_path'] is None:missing['image_path_explained']+=1
        if problems or review and review['status']=='rejected':grade='D'
        elif review and review['status']=='approved':
            grade='B' if any(r['status']=='incomplete' for r in s['rendering'].values()) else 'A'
        if grade=='A':trainable+=1
        grades[grade]+=1;rows.append({'sample_id':s['id'],'theme_id':s['sourceSha256'],'scene_id':s['state'],
            'grade':grade,'problems':problems,'target_origin':(review or s['label']).get('origin','human-reviewed' if review else 'weak'),
            'pairing_confidence':1. if review and review.get('pairProvenance') else None})
    with (out/'samples.jsonl').open('w',encoding='utf8') as stream:
        for r in rows:stream.write(json.dumps(r,ensure_ascii=False)+'\n')
    result={'samples':total,'elements':elements,'grades':dict(grades),'syntheticSamples':synthetic,
            'strictTrainableSamples':trainable,'strictTrainableRate':trainable/total if total else None,
            'missingCounts':dict(missing),'semanticCounts':dict(semantics),'automaticRepairRate':None,
            'missingRates':{k:v/(elements if k in {'visible_bbox_not_measured','image_path_explained'} else total) for k,v in missing.items() if (elements if k in {'visible_bbox_not_measured','image_path_explained'} else total)},
            'note':'A=reviewed and complete offline render; B=reviewed but render needs repair; C=weak/unreviewed/synthetic; D=invalid/rejected. Synthetic data needs explicit SFT opt-in; it is not human/native certification.'}
    write_json(out/'quality_report.json',result);return result
