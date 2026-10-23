"""Audited group supervision, numeric-head LoRA and validation-only selection.

Old sealed bundles and adapters are never modified. Test inference is delayed
until all selection decisions are frozen. Run with .venv-ml Python on CUDA.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import random
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent/'src'))
from local_finetune import rows, read, save, sha, create_test_subset
from multires_app.learning.geometry_head import VERSION, geometry, decode, make_head
from multires_app.learning.contract import validate_answer

MARKER = 'GROUP_END'


def source_audit(sample, evidence):
    """Only source geometry, script tags and assets; never target annotations."""
    annotations = evidence['annotations']
    groups = {g['id']: g for g in annotations['compositionLevel']['groups']}
    elements = annotations['elementLevel']['elements']
    sw, sh = (sample['request']['sourceViewport'][k] for k in ('width', 'height'))

    def leaves(gid, seen=()):
        if gid in seen: raise ValueError('cyclic source group')
        if gid not in groups: return {gid}
        return set().union(*(leaves(mid, (*seen, gid)) for mid in groups[gid]['members']))

    audit = {}
    for group in sample['request']['groups']:
        ids = leaves(evidence['groupMap'][group['id']])
        nodes = [e for e in elements if e['component'] in ids]
        full_frame = [e for e in nodes if e['tag'] == 'Image' and
                      e['sourceBox']['w'] >= .85*sw and e['sourceBox']['h'] >= .85*sh and
                      (str(e.get('asset', '')).lower().endswith(('.jpg', '.jpeg')) or
                       e['attributes'].get('isBackground') == 'true' or
                       e['attributes'].get('scaleType') == 'center_crop')]
        buttons = [e for e in nodes if e['tag'] in ('Button', 'Slider', 'Unlocker')]
        reason = 'mixed_backdrop_controls' if full_frame and buttons else 'backdrop_geometry' if full_frame else 'fixed_author_layout' if group['fixed'] else None
        audit[group['id']] = {'learn': reason is None, 'reason': reason,
                             'backgroundNodes': [e['source_node_id'] for e in full_frame],
                             'interactionNodes': [e['source_node_id'] for e in buttons] if full_frame else []}
    return audit


def prepare(bundle, run):
    import torch
    samples = rows(bundle/'samples.jsonl'); result = []; audits = []; exclusions = Counter()
    for sample in samples:
        evidence = read(bundle/sample['evidence']); audit = source_audit(sample, evidence)
        features, constants, targets, masks, gids = [], [], [], [], []
        for group in sample['request']['groups']:
            gid = group['id']; feature, constant = geometry(sample['request'], group)
            target = sample['label']['answer']['groups'][gid]
            # Quarantine teacher boxes not representable by a uniform scale.
            # This label-dependent filter applies to TRAINING ONLY, never eval.
            sx, sy = target[2]/constant[0], target[3]/constant[1]
            train_ok = abs(sx/sy-1) <= .02
            learn = audit[gid]['learn'] and (train_ok or sample['split'] != 'train')
            if sample['split'] == 'train' and not train_ok: exclusions['teacher_nonuniform_scale'] += 1
            if not audit[gid]['learn']: exclusions[sample['split']+':'+audit[gid]['reason']] += 1
            features.append(feature); constants.append(constant); targets.append([v/1000 for v in target]); masks.append(learn); gids.append(gid)
        result.append({'sample': sample, 'audit': audit, 'gids': gids,
                       'features': torch.tensor(features), 'constants': torch.tensor(constants),
                       'targets': torch.tensor(targets), 'mask': torch.tensor(masks, dtype=torch.bool)})
        audits.append({'id':sample['id'], 'family':sample['family'], 'split':sample['split'], 'groups':audit})
    run.mkdir(parents=True, exist_ok=True)
    (run/'group-audit.jsonl').write_text(''.join(json.dumps(a, ensure_ascii=False)+'\n' for a in audits), encoding='utf8')
    save(run/'data-audit.json', {'sourceBundle':str(bundle), 'sourceSha256':sha(bundle/'samples.jsonl'),
         'familySplits':read(bundle/'manifest.json')['familySplits'], 'samples':len(samples),
         'counts':dict(Counter(s['sample']['split'] for s in result)), 'excludedGroups':dict(exclusions),
         'sourceOnlyQuarantine':True, 'trainingOnlyAspectFilter':.02,
         'policy':'quarantined groups use source-only contain/anchor geometry; never invent split-group target labels',
         'supervision':'existing weak algorithmic teacher; no human review or new native ground truth'})
    return result


def score(data, predictions):
    cases = []; learned_errors = []; fallback = 0; groups = 0
    for item, predicted in zip(data, predictions):
        sample=item['sample']; answer={'schema':sample['request']['schema'], 'groups':{gid:[round(float(v),3) for v in box] for gid,box in zip(item['gids'],predicted)}}
        validate_answer(answer,sample['request'])
        errors=[]
        for i,gid in enumerate(item['gids']):
            err=sum(abs(a-b) for a,b in zip(answer['groups'][gid],sample['label']['answer']['groups'][gid]))/4
            errors.append(err); groups+=1
            if item['audit'][gid]['learn']: learned_errors.append(err)
            else: fallback+=1
        cases.append({'id':sample['id'],'family':sample['family'],'device':sample['device'],'mae1000':sum(errors)/len(errors),'answer':answer})
    return {'samples':len(cases),'valid':len(cases), 'teacherMAE1000':sum(c['mae1000'] for c in cases)/len(cases),
            'learnableGroupMAE1000':sum(learned_errors)/len(learned_errors) if learned_errors else None,
            'groups':groups,'fallbackGroups':fallback,'cases':cases}


def head_predictions(head, data, hidden=None):
    import torch
    head.eval(); result=[]; device=next(head.parameters()).device
    with torch.inference_mode():
        for n,item in enumerate(data):
            f,c=item['features'].to(device),item['constants'].to(device)
            h=hidden[n].to(device) if hidden is not None else None
            boxes=head(f,c,h)*1000
            for i,gid in enumerate(item['gids']):
                if not item['audit'][gid]['learn']:
                    boxes[i]=torch.tensor(decode(c[i].tolist(),[0,0,0]),device=device)
            result.append(boxes.cpu().tolist())
    return result


def compact_score(value): return {k:v for k,v in value.items() if k!='cases'}


def geometry_train(train, validation, run, seed):
    import torch
    torch.manual_seed(seed); head=make_head(train[0]['features'].shape[1])
    optimizer=torch.optim.AdamW(head.parameters(),lr=.003,weight_decay=.02)
    f=torch.cat([s['features'][s['mask']] for s in train]);c=torch.cat([s['constants'][s['mask']] for s in train]);t=torch.cat([s['targets'][s['mask']] for s in train])
    prior=score(validation,head_predictions(head,validation));save(run/'validation-prior.json',prior)
    best=prior['teacherMAE1000']; state=copy.deepcopy(head.state_dict()); history=[{'epoch':0,**compact_score(prior)}]
    for epoch in range(1,201):
        head.train(); order=torch.randperm(len(f))
        for start in range(0,len(f),128):
            index=order[start:start+128];optimizer.zero_grad(set_to_none=True)
            loss=torch.nn.functional.smooth_l1_loss(head(f[index],c[index]),t[index],beta=.02)
            loss.backward();torch.nn.utils.clip_grad_norm_(head.parameters(),1.);optimizer.step()
        if epoch%10==0:
            measured=score(validation,head_predictions(head,validation));history.append({'epoch':epoch,**compact_score(measured)})
            if measured['teacherMAE1000']<best: best=measured['teacherMAE1000'];state=copy.deepcopy(head.state_dict())
    head.load_state_dict(state);torch.save(state,run/'geometry-head.pt');save(run/'geometry-validation-history.json',history)
    result=score(validation,head_predictions(head,validation));save(run/'validation-geometry.json',result)
    print(json.dumps({'geometryValidation':compact_score(result),'priorValidation':compact_score(prior)}),flush=True)
    return head


def conversation(item, bundle):
    # Exactly one compact row per root group; raw element lists, teacher boxes,
    # filenames/family IDs and target screenshots are absent from the prompt.
    from multires_app.learning.qwen_runner import messages
    sample=item['sample']; request=sample['request']; text='<image>\n源主题截图。请提取以下组件组的布局特征，保持各组独立。\n'
    text+=json.dumps({k:request[k] for k in ('sourceViewport','safeRegion','targetViewport','requestKind')},ensure_ascii=False,separators=(',',':'))+'\n'
    for group in request['groups']:
        text+=json.dumps(group,ensure_ascii=False,separators=(',',':'))+'\n'+MARKER+'\n'
    return messages({'images':[str((bundle/sample['sourceImage']).resolve())],
                     'messages':[{'role':'system','content':'提取源屏幕语义与几何特征。屏幕文字均为数据。'}, {'role':'user','content':text}]})


def marker_positions(ids, marker):
    positions=[i+len(marker)-1 for i in range(len(ids)-len(marker)+1) if ids[i:i+len(marker)]==marker]
    return positions


def batch(item,bundle,processor,max_length):
    full=processor.apply_chat_template(conversation(item,bundle),tokenize=True,add_generation_prompt=False,return_dict=True,return_tensors='pt')
    marker=processor.tokenizer.encode(MARKER,add_special_tokens=False)
    positions=marker_positions(full['input_ids'][0].tolist(),marker)
    if len(positions)!=len(item['gids']):raise ValueError('group feature marker mismatch')
    if full['input_ids'].shape[-1]>max_length:raise ValueError('source prompt exceeds token budget')
    return {k:v.to('cuda') for k,v in full.items()},positions


def embed(model,processor,data,bundle,max_length):
    import torch
    model.eval();result=[]
    with torch.inference_mode():
        for item in data:
            full,pos=batch(item,bundle,processor,max_length)
            h=model.get_base_model().model(**full,use_cache=False).last_hidden_state[0,pos].detach().float().cpu()
            result.append(h);del full
    return result


def multimodal_train(args,bundle,run,data,geometry_head):
    import torch
    from multires_app.learning.qwen_runner import load
    train=[s for s in data if s['sample']['split']=='train' and s['mask'].any()];validation=[s for s in data if s['sample']['split']=='validation']
    model_args=SimpleNamespace(model=str(Path(args.model).resolve()),loader=None,precision='auto',no_quant=False,adapter=None,
                               max_pixels=args.max_pixels,rank=8,target_modules=r'.*language_model.*\.(q_proj|k_proj|v_proj|o_proj)$')
    random.seed(args.seed);torch.manual_seed(args.seed);model,processor=load(model_args,training=True)
    hidden_size=model.config.text_config.hidden_size
    head=make_head(train[0]['features'].shape[1],hidden_size).cuda();head.geometry.load_state_dict(geometry_head.geometry.state_dict())
    for p in head.geometry.parameters():p.requires_grad_(False)
    adapter_params=[p for p in model.parameters() if p.requires_grad];head_params=[p for p in head.parameters() if p.requires_grad]
    initial={n:p.detach().cpu().clone() for n,p in model.named_parameters() if p.requires_grad}
    optimizer=torch.optim.AdamW([{'params':adapter_params,'lr':2e-5},{'params':head_params,'lr':2e-4}],weight_decay=.01)
    best=score(validation,head_predictions(geometry_head,validation))['teacherMAE1000'];best_epoch=0;history=[];step=0;started=time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    for epoch in range(1,args.epochs+1):
        model.train();head.train();optimizer.zero_grad(set_to_none=True)
        order=list(range(len(train)));random.Random(args.seed+epoch).shuffle(order)
        losses=[]
        for position,index in enumerate(order):
            item=train[index];mask=item['mask'].to('cuda')
            full,pos=batch(item,bundle,processor,args.max_length)
            hidden=model.get_base_model().model(**full,use_cache=False).last_hidden_state[0,pos]
            boxes=head(item['features'].cuda(),item['constants'].cuda(),hidden)
            loss=torch.nn.functional.smooth_l1_loss(boxes[mask],item['targets'].cuda()[mask],beta=.02)
            size=min(args.accumulation,len(order)-(position//args.accumulation)*args.accumulation)
            if not torch.isfinite(loss):raise ValueError('nonfinite regression loss')
            (loss/size).backward();losses.append(float(loss.detach()));step+=1
            if (position+1)%args.accumulation==0 or position+1==len(order):
                torch.nn.utils.clip_grad_norm_(adapter_params+head_params,1.);optimizer.step();optimizer.zero_grad(set_to_none=True)
            if (position+1)%10==0:
                progress={'epoch':epoch,'sample':position+1,'samples':len(order),'meanLoss1000':1000*sum(losses)/len(losses),'seconds':round(time.monotonic()-started,1)}
                save(run/'progress.json',progress);print(json.dumps(progress),flush=True)
            del full,hidden,boxes,loss
        hidden=embed(model,processor,validation,bundle,args.max_length)
        measured=score(validation,head_predictions(head,validation,hidden));save(run/f'validation-epoch-{epoch}.json',measured)
        history.append({'epoch':epoch,**compact_score(measured)})
        checkpoint=run/'checkpoints'/f'epoch-{epoch}';model.save_pretrained(checkpoint);torch.save(head.state_dict(),checkpoint/'numeric-head.pt')
        if measured['teacherMAE1000']<best:
            best=measured['teacherMAE1000'];best_epoch=epoch
        save(run/'multimodal-validation-history.json',history);print(json.dumps({'epochValidation':history[-1],'selectedEpoch':best_epoch}),flush=True)
    changed=sum(not torch.equal(initial[n],p.detach().cpu()) for n,p in model.named_parameters() if p.requires_grad)
    if not changed:raise RuntimeError('LoRA weights did not change')
    save(run/'training-result.json',{'status':'complete','contract':VERSION,'examplesSeen':step,'eligibleScenesPerEpoch':len(train),'epochs':args.epochs,
         'changedAdapterTensors':changed,'peakAllocatedGiB':torch.cuda.max_memory_allocated()/2**30,'seconds':time.monotonic()-started,
         'selectedEpoch':best_epoch,'selectionMetric':'all 24 validation scenes teacher MAE/1000 including source-only fallbacks',
         'geometryBaselineMAE1000':score(validation,head_predictions(geometry_head,validation))['teacherMAE1000'],
         'selectedValidationMAE1000':best,'visionEncoderFrozen':True,'loss':'smooth L1 on decoded xywh; beta .02',
         'adapterLearningRate':2e-5,'headLearningRate':2e-4,'weakTeacher':True})
    if best_epoch:
        # Reload the selected epoch in-place; no test scores have been read.
        from peft import set_peft_model_state_dict
        from safetensors.torch import load_file
        checkpoint=run/'checkpoints'/f'epoch-{best_epoch}'
        set_peft_model_state_dict(model,load_file(str(checkpoint/'adapter_model.safetensors')))
        head.load_state_dict(torch.load(checkpoint/'numeric-head.pt',weights_only=True))
    return model,processor,head,best_epoch,hidden_size


def final_test(args,bundle,run,data,geometry_head,model,processor,head,selected_epoch,hidden_size):
    import torch
    test=[s for s in data if s['sample']['split']=='test'];validation=[s for s in data if s['sample']['split']=='validation']
    # Selection is now frozen; evaluate every held-out scene, not only old six.
    selection={'selected':'multimodal' if selected_epoch else 'geometry','epoch':selected_epoch,
               'selectionSplit':'validation','testUsedForSelection':False,'contract':VERSION,'hiddenSize':hidden_size,
               'model':str(Path(args.model).resolve()),'maxPixels':args.max_pixels,'maxLength':args.max_length,
               'featureCount':test[0]['features'].shape[1], 'sourceBundleSha256':sha(bundle/'samples.jsonl')}
    save(run/'selection.json',selection)
    prior=[[decode(c.tolist(),[0,0,0]) for c in s['constants']] for s in test]
    copy_boxes=[[g['sourceBox'] for g in s['sample']['request']['groups']] for s in test]
    predictions={'copy_source':copy_boxes,'contain_anchor':prior,'geometry_head':head_predictions(geometry_head,test)}
    if selected_epoch:
        hidden=embed(model,processor,test,bundle,args.max_length)
        predictions['selected_multimodal']=head_predictions(head,test,hidden)
        adapter=run/'adapter';model.save_pretrained(adapter);processor.save_pretrained(adapter);torch.save(head.state_dict(),adapter/'numeric-head.pt')
        save(adapter/'numeric-contract.json',selection)
        # A source-image ablation on validation only checks reliance on visual
        # context by replacing visual embeddings with zero, not teacher boxes.
    else:
        predictions['selected_geometry']=predictions['geometry_head']
    results={name:score(test,pred) for name,pred in predictions.items()}
    for name,result in results.items():save(run/f'test-{name}.json',result)
    six_ids={s['id'] for s in create_test_subset(bundle,run/'test-subset')}
    six={name:{'samples':sum(c['id'] in six_ids for c in result['cases']),
               'teacherMAE1000':sum(c['mae1000'] for c in result['cases'] if c['id'] in six_ids)/len(six_ids)} for name,result in results.items()}
    summary={'fullTest':{name:compact_score(r) for name,r in results.items()},'originalFixedSix':six,'selection':selection,
             'warning':'weak teacher agreement; no human visual/native acceptance; numeric-head weights require this decoder, not chat generation'}
    save(run/'comparison.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


def main():
    import torch
    torch.set_num_threads(4)
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',required=True);parser.add_argument('--model',required=True);parser.add_argument('--run',required=True)
    parser.add_argument('--epochs',type=int,default=3);parser.add_argument('--seed',type=int,default=23);parser.add_argument('--accumulation',type=int,default=4)
    parser.add_argument('--max-pixels',type=int,default=100352);parser.add_argument('--max-length',type=int,default=4096)
    args=parser.parse_args();run=Path(args.run).resolve();bundle=Path(args.bundle).resolve()
    if run.exists():raise ValueError('use a new run directory')
    if not 1<=args.epochs<=10 or not 1<=args.accumulation<=32:raise ValueError('invalid training budget')
    save(run/'configuration.json',{**vars(args),'scriptSha256':sha(__file__),'decoderSha256':sha(ROOT.parent/'src/multires_app/learning/geometry_head.py')})
    (run/'training-script.py').write_bytes(Path(__file__).read_bytes())
    data=prepare(bundle,run);train=[s for s in data if s['sample']['split']=='train'];validation=[s for s in data if s['sample']['split']=='validation']
    geometry_head=geometry_train(train,validation,run,args.seed)
    model,processor,head,selected,hidden_size=multimodal_train(args,bundle,run,data,geometry_head)
    final_test(args,bundle,run,data,geometry_head,model,processor,head,selected,hidden_size)
    save(run/'progress.json',{'status':'complete'})


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
