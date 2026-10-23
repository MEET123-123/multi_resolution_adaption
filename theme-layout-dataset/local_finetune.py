"""Bounded local QLoRA experiment, validation and reproducible held-out subset."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import random
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'src'))


def read(path): return json.loads(Path(path).read_text(encoding='utf8'))
def rows(path): return [json.loads(line) for line in Path(path).read_text(encoding='utf8').splitlines() if line.strip()]
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp'); tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8'); tmp.replace(path)


def portable_examples(bundle, split):
    result = rows(bundle / (split + '.jsonl'))
    for example in result:
        images = []
        for name in example['images']:
            path = (bundle / name).resolve()
            if not path.is_relative_to(bundle.resolve()) or not path.is_file(): raise ValueError('missing or escaped input image')
            images.append(str(path))
        example['images'] = images
    return result


def epoch_order(count, seed, epoch):
    order = list(range(count)); random.Random(seed + epoch).shuffle(order); return order


def create_test_subset(bundle, out):
    samples = [s for s in rows(bundle / 'samples.jsonl') if s['split'] == 'test']
    selected = {}; families = set()
    for sample in samples:
        key = (sample['family'], sample['device']); families.add(sample['family'])
        # Prefer a small group count and stable initial state, without reading
        # target coordinates or model predictions during subset selection.
        score = (sample['groupCount'], sample['state'], sample['id'])
        if key not in selected or score < selected[key][0]: selected[key] = (score, sample)
    out.mkdir(parents=True, exist_ok=True)
    chosen = [selected[key][1] for key in sorted(selected)]
    for sample in chosen: sample['sourceImage'] = str((bundle / sample['sourceImage']).resolve())
    (out / 'samples.jsonl').write_text(''.join(json.dumps(sample, ensure_ascii=False) + '\n' for sample in chosen), encoding='utf8')
    save(out / 'selection.json', {'policy': 'one lowest-complexity sample per held-out family and device; no prediction/target-based selection',
         'totalTestSamples': len(samples), 'evaluatedSubset': len(chosen), 'families': sorted(families), 'ids': [s['id'] for s in chosen]})
    return chosen


def train(args):
    import torch
    from multires_app.learning.qwen_runner import load, messages
    bundle = Path(args.bundle).resolve(); run = Path(args.run).resolve(); output = run / 'adapter'
    if output.exists() or (run / 'progress.json').exists(): raise ValueError('use a new run directory; existing training is preserved')
    run.mkdir(parents=True, exist_ok=True)
    manifest = read(bundle / 'manifest.json')
    if not manifest.get('includeWeak'): raise ValueError('this local recipe expects explicit algorithm-distillation data')
    examples = portable_examples(bundle, 'train'); validation = portable_examples(bundle, 'validation')
    if not examples or not validation: raise ValueError('train and validation must be nonempty')
    chosen = create_test_subset(bundle, run / 'test-subset')
    model_args = SimpleNamespace(model=str(Path(args.model).resolve()), loader=None, precision='auto', no_quant=False,
        adapter=None, max_pixels=args.max_pixels, rank=8, target_modules=r'.*language_model.*\.(q_proj|k_proj|v_proj|o_proj)$')
    random.seed(args.seed); torch.manual_seed(args.seed)
    save(run / 'configuration.json', {'model': model_args.model, 'bundle': str(bundle), 'trainExamples': len(examples),
         'validationExamples': len(validation), 'testSubset': len(chosen), 'epochs': args.epochs, 'seed': args.seed,
         'learningRate': args.learning_rate, 'gradientAccumulation': args.accumulation, 'maxPixels': args.max_pixels,
         'maxLength': args.max_length, 'loraRank': 8, 'quantization': 'NF4 4bit double quantization',
         'trainSha256': sha(bundle / 'train.jsonl'), 'validationSha256': sha(bundle / 'validation.jsonl'),
         'manifestSha256': sha(bundle / 'manifest.json'), 'scriptSha256': sha(__file__), 'supervision': 'weak algorithmic teacher'})
    model, processor = load(model_args, training=True)
    parameters = [p for p in model.parameters() if p.requires_grad]
    initial = {n:p.detach().float().cpu().clone() for n,p in model.named_parameters() if p.requires_grad}
    if not parameters: raise RuntimeError('no trainable adapter parameters')
    optimizer = torch.optim.AdamW(parameters, lr=args.learning_rate)
    total_steps = math.ceil(len(examples)/args.accumulation)*args.epochs
    warmup = max(1, round(total_steps*.1)); micro = 0; step = 0; records = []; validations = []
    started = time.monotonic(); torch.cuda.reset_peak_memory_stats()

    def batch(example):
        conversation = messages(example)
        prefix = processor.apply_chat_template(conversation[:-1], tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt')
        full = processor.apply_chat_template(conversation, tokenize=True, add_generation_prompt=False, return_dict=True, return_tensors='pt')
        before, total = prefix['input_ids'].shape[-1], full['input_ids'].shape[-1]
        if total > args.max_length: raise ValueError(f'example exceeds token budget: {total}')
        if not torch.equal(prefix['input_ids'][0], full['input_ids'][0,:before]): raise RuntimeError('assistant prefix mismatch')
        labels = full['input_ids'].clone(); labels[:,:before] = -100; keep = total-before+1
        if keep <= 1: raise ValueError('empty assistant supervision')
        full = {k:v.to('cuda') for k,v in full.items()}; full['labels'] = labels[:,-keep:].to('cuda'); full['logits_to_keep'] = keep
        return full, total, total-before

    def validate():
        model.eval(); weighted = 0.; tokens = 0
        with torch.inference_mode():
            for example in validation:
                full, _, count = batch(example); loss = model(**full).loss
                if not torch.isfinite(loss): raise RuntimeError('nonfinite validation loss')
                weighted += float(loss)*count; tokens += count; del full, loss
        model.train(); return {'loss': weighted/tokens, 'assistantTokens': tokens, 'samples': len(validation)}

    print('Validation before training', flush=True)
    before = validate(); validations.append({'epoch':0, **before}); save(run/'validation.json', validations)
    print(json.dumps({'validationBefore':before},ensure_ascii=False),flush=True)
    model.train(); optimizer.zero_grad(set_to_none=True)
    for epoch in range(args.epochs):
        order = epoch_order(len(examples), args.seed, epoch); save(run/f'epoch-{epoch+1}-sample-order.json',order)
        for position, index in enumerate(order):
            micro += 1
            group_start = position-position%args.accumulation
            group_size = min(args.accumulation, len(order)-group_start)
            full, total, count = batch(examples[index]); loss = model(**full).loss
            if not torch.isfinite(loss): raise RuntimeError('nonfinite training loss')
            (loss/group_size).backward(); loss_value = float(loss.detach())
            boundary = (position+1)%args.accumulation==0 or position+1==len(order)
            lr = optimizer.param_groups[0]['lr']
            if boundary:
                step += 1
                lr = args.learning_rate*(step/warmup if step<=warmup else .5*(1+math.cos(math.pi*(step-warmup)/max(1,total_steps-warmup))))
                for group in optimizer.param_groups: group['lr'] = lr
                grad = torch.nn.utils.clip_grad_norm_(parameters,1.)
                if not torch.isfinite(grad): raise RuntimeError('nonfinite adapter gradient')
                optimizer.step(); optimizer.zero_grad(set_to_none=True)
            record = {'epoch':epoch+1, 'microStep':micro, 'optimizerStep':step, 'sampleIndex':index, 'loss':loss_value,
                      'tokens':total, 'assistantTokens':count, 'learningRate':lr, 'seconds':round(time.monotonic()-started,2)}
            records.append(record); save(run/'progress.json', {'status':'training','completedMicroSteps':micro,'totalMicroSteps':len(examples)*args.epochs,'last':record})
            with (run/'training-loss.jsonl').open('a',encoding='utf8') as stream: stream.write(json.dumps(record)+'\n')
            print(json.dumps(record),flush=True); del full,loss
            if boundary and step%10==0:
                checkpoint=run/'checkpoints'/f'step-{step:04d}';model.save_pretrained(checkpoint)
                save(checkpoint/'progress.json',record)
        checkpoint = run/'checkpoints'/f'epoch-{epoch+1}'; model.save_pretrained(checkpoint); processor.save_pretrained(checkpoint)
        result=validate(); validations.append({'epoch':epoch+1,'checkpoint':str(checkpoint),**result}); save(run/'validation.json',validations)
        print(json.dumps({'validationAfterEpoch':epoch+1,**result}),flush=True)
    best = min(validations[1:], key=lambda r:r['loss']); shutil.copytree(best['checkpoint'],output)
    changed=sum(not torch.equal(initial[n],p.detach().float().cpu()) for n,p in model.named_parameters() if p.requires_grad)
    if not changed: raise RuntimeError('adapter weights did not change')
    result={'status':'complete','model':model_args.model,'adapter':str(output),'epochs':args.epochs,'examplesSeen':micro,
            'optimizerSteps':step,'trainableParameters':sum(p.numel() for p in parameters),'changedAdapterTensors':changed,
            'validationBefore':before,'validationAfter':best,'validationRelativeLossReduction':1-best['loss']/before['loss'],
            'first20MeanLoss':sum(r['loss'] for r in records[:20])/min(20,len(records)),
            'last20MeanLoss':sum(r['loss'] for r in records[-20:])/min(20,len(records)),
            'seconds':time.monotonic()-started,'peakAllocatedGiB':torch.cuda.max_memory_allocated()/2**30,
            'gpu':torch.cuda.get_device_name(),'visionEncoderFrozen':True,'weakLabels':True,
            'scope':'one local algorithm-distillation experiment; no human/native quality certification',
            'adapterSha256':{p.name:sha(p) for p in output.glob('*.safetensors')}}
    save(run/'training-result.json',result); save(run/'progress.json',{'status':'complete','completedMicroSteps':micro,'totalMicroSteps':micro})
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',required=True);parser.add_argument('--model',required=True);parser.add_argument('--run',required=True)
    parser.add_argument('--epochs',type=int,default=1);parser.add_argument('--accumulation',type=int,default=4)
    parser.add_argument('--learning-rate',type=float,default=1e-4);parser.add_argument('--max-pixels',type=int,default=100352)
    parser.add_argument('--max-length',type=int,default=4096);parser.add_argument('--seed',type=int,default=23)
    args=parser.parse_args()
    if not 1<=args.epochs<=10 or not 1<=args.accumulation<=32 or not 0<args.learning_rate<=.001: parser.error('invalid training budget')
    train(args)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
