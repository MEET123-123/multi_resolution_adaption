"""Replay a selected numerical-head package without reading target labels.

Produces the existing LearnedAnchor prediction JSON contract. The solver still
owns final constraints. A numeric-head adapter cannot be used with chat infer.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'src'))
from local_finetune import rows,read,save
from optimize_layout import source_audit,head_predictions,batch
from multires_app.learning.geometry_head import VERSION,geometry,decode,make_head
from multires_app.learning.contract import validate_answer


def source_items(samples,bundle):
    import torch
    items=[]
    for original in samples:
        # Explicit source whitelist. Inference never retains label, targetImage,
        # targetTransforms or target-coordinate annotations from the bundle.
        sample={k:original[k] for k in ('id','requestKey','sourceImage','request')}
        image=(bundle/sample['sourceImage']).resolve()
        if not image.is_file() or not image.is_relative_to(bundle.resolve()):raise ValueError('missing or escaped source image')
        evidence_path=(bundle/original['evidence']).resolve()
        if not evidence_path.is_file() or not evidence_path.is_relative_to(bundle.resolve()):raise ValueError('missing or escaped source evidence')
        evidence=read(evidence_path);audit=source_audit(sample,evidence)
        feature,constants,gids=[],[],[]
        for group in sample['request']['groups']:
            f,c=geometry(sample['request'],group);feature.append(f);constants.append(c);gids.append(group['id'])
        items.append({'sample':sample,'gids':gids,'audit':audit,'features':torch.tensor(feature),'constants':torch.tensor(constants)})
    return items


def predict(args):
    import torch
    torch.set_num_threads(4)
    bundle=Path(args.bundle).resolve();run=Path(args.run).resolve();out=Path(args.out)
    if out.exists():raise ValueError('preserve existing predictions; use a new output path')
    selection=read(run/'selection.json')
    if selection['contract']!=VERSION:raise ValueError('unsupported numeric-head package')
    samples=rows(Path(args.samples)) if args.samples else rows(bundle/'samples.jsonl')
    if args.split:samples=[s for s in samples if s.get('split')==args.split]
    if not samples:raise ValueError('no input samples')
    items=source_items(samples,bundle);backend=selection['selected'] if args.backend=='selected' else args.backend
    if backend=='contain':
        boxes=[[decode(c.tolist(),[0,0,0]) for c in item['constants']] for item in items]
    elif backend=='geometry':
        head=make_head(selection['featureCount']);head.load_state_dict(torch.load(run/'geometry-head.pt',weights_only=True,map_location='cpu'))
        boxes=head_predictions(head,items)
    else:
        from multires_app.learning.qwen_runner import load
        adapter=run/'adapter'
        config=SimpleNamespace(model=args.model or selection['model'],loader=None,precision='auto',no_quant=False,
                               adapter=str(adapter),max_pixels=selection['maxPixels'])
        model,processor=load(config,training=False)
        # Match QLoRA training's non-quantized FP32 modules. Leaving visual/LN
        # modules in BF16 would change numerical-head features after reloading.
        from peft import prepare_model_for_kbit_training
        model=prepare_model_for_kbit_training(model,use_gradient_checkpointing=False)
        model.eval()
        head=make_head(selection['featureCount'],selection['hiddenSize']).cuda()
        head.load_state_dict(torch.load(adapter/'numeric-head.pt',weights_only=True));hidden=[]
        with torch.inference_mode():
            for number,item in enumerate(items,1):
                full,pos=batch(item,bundle,processor,selection['maxLength'])
                if args.blank_images:full['pixel_values'].zero_()
                h=model.get_base_model().model(**full,use_cache=False).last_hidden_state[0,pos].detach().float().cpu()
                hidden.append(h);del full
                if number%10==0:print(json.dumps({'embedded':number,'total':len(items),'blankImages':args.blank_images}),flush=True)
        boxes=head_predictions(head,items,hidden)
    predictions={}
    for item,values in zip(items,boxes):
        answer={'schema':item['sample']['request']['schema'], 'groups':{gid:[round(float(v),3) for v in box] for gid,box in zip(item['gids'],values)}}
        validate_answer(answer,item['sample']['request'])
        predictions[item['sample']['requestKey']]={'status':'valid','answer':answer,
            'provider':VERSION+':'+backend,'modelEvidence':backend=='multimodal','blankImageAblation':bool(args.blank_images),
            'fallbackGroups':{gid:a['reason'] for gid,a in item['audit'].items() if not a['learn']}}
    if len(predictions)!=len(items):raise ValueError('duplicate request key')
    save(out,predictions);print(json.dumps({'samples':len(items),'backend':backend,'out':str(out)},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',required=True);parser.add_argument('--run',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--model');parser.add_argument('--samples');parser.add_argument('--split',choices=('train','validation','test'))
    parser.add_argument('--backend',choices=('selected','geometry','contain'),default='selected');parser.add_argument('--blank-images',action='store_true')
    args=parser.parse_args()
    if args.blank_images and args.backend!='selected':parser.error('image ablation requires selected multimodal backend')
    predict(args)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
