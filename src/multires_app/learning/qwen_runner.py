"""Native Windows/Linux Qwen3-VL QLoRA smoke/training and layout inference.

Heavy ML imports are lazy and isolated from the original theme environment.
No hosted APIs, no hidden teacher answers during model inference.
"""
import argparse
import importlib.metadata
import json
import math
import os
import importlib
import platform
import random
import sys
import time
from pathlib import Path
from .contract import SCHEMA, SYSTEM, parse_answer, prompt
from multires_app.evaluation.core import write_json


def doctor(out):
    result={'python':sys.version,'executable':sys.executable,'platform':platform.platform(),'packages':{}}
    for package in ('torch','torchvision','transformers','peft','accelerate','bitsandbytes'):
        try:result['packages'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:result['packages'][package]=None
    try:
        import torch
        result.update(cudaAvailable=torch.cuda.is_available(),cudaRuntime=torch.version.cuda)
        if torch.cuda.is_available():
            free,total=torch.cuda.mem_get_info();result['gpu']={'name':torch.cuda.get_device_name(),'freeGiB':free/2**30,'totalGiB':total/2**30}
    except Exception as exc:result['error']=str(exc)
    write_json(out,result);return result


def load(args, training=False):
    if args.loader:
        module,attribute=args.loader.rsplit(':',1)
        return getattr(importlib.import_module(module),attribute)(args,training=training)
    import torch
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration, BitsAndBytesConfig
    if not torch.cuda.is_available():raise RuntimeError('CUDA GPU required; run doctor and install the CUDA torch build')
    local_rank=int(os.environ.get('LOCAL_RANK','0'));torch.cuda.set_device(local_rank)
    dtype=torch.bfloat16 if args.precision=='bf16' or args.precision=='auto' and torch.cuda.is_bf16_supported() else torch.float16
    if dtype==torch.bfloat16 and not torch.cuda.is_bf16_supported():raise ValueError('BF16 unavailable on this GPU; select --precision fp16')
    kwargs={'device_map':{'':local_rank},'dtype':dtype,'attn_implementation':'sdpa','local_files_only':Path(args.model).exists()}
    if not args.no_quant:
        kwargs['quantization_config']=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
            bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=dtype)
    print('Loading Qwen3-VL:',args.model,flush=True)
    model=Qwen3VLForConditionalGeneration.from_pretrained(args.model,**kwargs)
    processor=AutoProcessor.from_pretrained(args.model,local_files_only=Path(args.model).exists())
    processor.image_processor.size={'shortest_edge':4096,'longest_edge':args.max_pixels}
    if training:
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training, TaskType
        if not args.no_quant:model=prepare_model_for_kbit_training(model,use_gradient_checkpointing=True)
        else:
            for parameter in model.parameters():parameter.requires_grad_(False)
            model.enable_input_require_grads()
        if args.adapter:
            from peft import PeftModel
            model=PeftModel.from_pretrained(model,args.adapter,is_trainable=True)
        else:
            model=get_peft_model(model,LoraConfig(r=args.rank,lora_alpha=args.rank*2,lora_dropout=0,
                target_modules=args.target_modules,task_type=TaskType.CAUSAL_LM))
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
        model.config.use_cache=False
    elif args.adapter:
        from peft import PeftModel
        model=PeftModel.from_pretrained(model,args.adapter)
    return model,processor


def messages(example):
    from PIL import Image
    images=iter(example['images']);result=[]
    for message in example['messages']:
        if message['role']!='user':
            result.append({'role':message['role'],'content':[{'type':'text','text':message['content']}]});continue
        content=[];parts=message['content'].split('<image>')
        for i,part in enumerate(parts):
            if i:
                path=next(images)
                with Image.open(path) as im:content.append({'type':'image','image':im.convert('RGB').copy()})
            if part:content.append({'type':'text','text':part})
        result.append({'role':'user','content':content})
    if next(images,None) is not None:raise ValueError('image token count does not match images')
    return result


def train(args):
    import torch
    directory=Path(args.sft).resolve();manifest=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
    if manifest.get('includeWeak') and not args.allow_weak:raise ValueError('weak labels require --allow-weak; use only for smoke/teacher distillation')
    examples=[json.loads(line) for line in (directory/'train.jsonl').read_text(encoding='utf8').splitlines() if line]
    if not examples:raise ValueError('no training examples; approve labels and export SFT first')
    out=Path(args.out).resolve()
    if out.exists():raise ValueError('use a new adapter output directory')
    world=int(os.environ.get('WORLD_SIZE','1'));rank=int(os.environ.get('RANK','0'));local_rank=int(os.environ.get('LOCAL_RANK','0'))
    if world>1:
        if platform.system()=='Windows':raise ValueError('multi-GPU training recipe targets Linux NCCL; use single GPU on this Windows MVP')
        torch.cuda.set_device(local_rank);torch.distributed.init_process_group('nccl')
    random.seed(args.seed);torch.manual_seed(args.seed);model,processor=load(args,training=True)
    forward_model=torch.nn.parallel.DistributedDataParallel(model,device_ids=[local_rank],find_unused_parameters=False) if world>1 else model
    parameters=[p for p in model.parameters() if p.requires_grad]
    if not parameters:raise RuntimeError('LoRA did not attach to the language model')
    optimizer=torch.optim.AdamW(parameters,lr=args.learning_rate)
    initial={n:p.detach().float().cpu().clone() for n,p in model.named_parameters() if p.requires_grad}
    model.train();torch.cuda.reset_peak_memory_stats();losses=[];lengths=[];start=time.monotonic()
    optimizer.zero_grad(set_to_none=True)
    for micro in range(args.max_steps*args.gradient_accumulation):
        example=examples[(micro*world+rank)%len(examples)];conversation=messages(example)
        prefix=processor.apply_chat_template(conversation[:-1],tokenize=True,add_generation_prompt=True,return_dict=True,return_tensors='pt')
        full=processor.apply_chat_template(conversation,tokenize=True,add_generation_prompt=False,return_dict=True,return_tensors='pt')
        length=prefix['input_ids'].shape[-1];total=full['input_ids'].shape[-1]
        if total>args.max_length:raise ValueError(f'example has {total} tokens > --max-length; reduce scene complexity or raise the GPU budget')
        if not torch.equal(prefix['input_ids'][0],full['input_ids'][0,:length]):raise RuntimeError('assistant mask prefix mismatch')
        labels=full['input_ids'].clone();labels[:,:length]=-100
        if not (labels!=-100).any():raise RuntimeError('no assistant output tokens')
        # Avoid allocating a vocabulary-sized logit tensor for every source JSON
        # token. Keep the last prompt token plus assistant positions; causal
        # shifting still supervises the first answer token and masks the prefix.
        keep=total-length+1
        full={k:v.to('cuda') for k,v in full.items()};full['labels']=labels[:,-keep:].to('cuda');full['logits_to_keep']=keep
        del prefix,labels
        from contextlib import nullcontext
        synchronize=(micro+1)%args.gradient_accumulation==0
        synchronization=forward_model.no_sync() if world>1 and not synchronize else nullcontext()
        with synchronization:
            loss=forward_model(**full).loss
            if not torch.isfinite(loss):raise RuntimeError('nonfinite training loss')
            (loss/args.gradient_accumulation).backward()
        if synchronize:
            grad=torch.nn.utils.clip_grad_norm_(parameters,1.)
            if not torch.isfinite(grad):raise RuntimeError('nonfinite training gradient')
            optimizer.step();optimizer.zero_grad(set_to_none=True)
        losses.append(float(loss.detach()));lengths.append(total)
        if rank==0:print(json.dumps({'microStep':micro+1,'optimizerStep':(micro+1)//args.gradient_accumulation,'loss':losses[-1],'tokens':total},ensure_ascii=False),flush=True)
        del full,loss
    changed=sum(not torch.equal(initial[n],p.detach().float().cpu()) for n,p in model.named_parameters() if p.requires_grad)
    if not changed:raise RuntimeError('optimizer did not change any adapter tensors')
    if rank==0:out.mkdir(parents=True);model.save_pretrained(out);processor.save_pretrained(out)
    result={'model':args.model,'steps':args.max_steps,'losses':losses,'tokens':lengths,'trainableParameters':sum(p.numel() for p in parameters),
        'changedAdapterTensors':changed,'peakAllocatedGiB':torch.cuda.max_memory_allocated()/2**30,
        'seconds':time.monotonic()-start,'weakLabels':manifest.get('includeWeak',False),
        'purpose':'synthetic_pipeline_validation' if manifest.get('includeSynthetic') else 'pipeline_smoke_not_quality_evidence' if manifest.get('includeWeak') else 'supervised_training',
        'syntheticData':manifest.get('includeSynthetic',False),
        'generalization':'not_measured','seed':args.seed,'visionEncoderFrozen':True,
        'worldSize':world,'gradientAccumulation':args.gradient_accumulation,'effectiveBatch':world*args.gradient_accumulation,
        'gpu':torch.cuda.get_device_name(),'precision':args.precision,'validationLoss':None,
        'configuration':{'rank':args.rank,'learningRate':args.learning_rate,'maxPixels':args.max_pixels,
            'maxLength':args.max_length,'quantized':not args.no_quant,'targetModules':args.target_modules}}
    from multires_app.evaluation.input_checks import file_sha
    result['sftManifestSha256']=file_sha(directory/'manifest.json')
    result['sftTrainSha256']=file_sha(directory/'train.jsonl')
    if rank==0:result['adapterSha256']={p.name:file_sha(p) for p in out.glob('*.safetensors')}
    if rank==0:write_json(out/'training-result.json',result)
    if world>1:torch.distributed.barrier();torch.distributed.destroy_process_group()
    return result


def infer(args):
    import torch
    from .dataset import read_samples, reviewed, input_layout
    out=Path(args.out).resolve()
    if out.exists():raise ValueError('use a new prediction file')
    standalone=bool(args.layout_json)
    if standalone:
        from multires_app.evaluation.core import digest
        document=json.loads(Path(args.layout_json).read_text(encoding='utf-8-sig'))
        if not args.image:raise ValueError('--image is required with --layout-json')
        directory=Path(args.image).resolve().parent
        samples=[{'id':'standalone','request':document['constraints'],'requestKey':digest(document['constraints']),
                  'inputLayout':document['layoutJson'],'sourceImage':Path(args.image).resolve().name}]
        if args.input_images!='source':raise ValueError('standalone mode currently accepts one source image')
    else:
        if not args.dataset:raise ValueError('--dataset or --layout-json required')
        directory=Path(args.dataset).resolve()
        samples=[s for s in read_samples(directory) if args.split=='all' or s['split']==args.split][:args.limit]
    labels={} if standalone else reviewed(directory)
    if not samples:raise ValueError('no samples in selected split')
    model,processor=load(args);model.eval();result={};torch.cuda.reset_peak_memory_stats()
    for sample in samples:
        names=[sample['sourceImage']]+([sample['targetImage']] if args.input_images=='source-and-current' else [])
        example={'images':[str(directory/n) for n in names], 'messages':[{'role':'system','content':SYSTEM},
            {'role':'user','content':prompt(sample['request'],input_layout(sample,labels.get(sample['id'])),len(names))}]}
        inputs=processor.apply_chat_template(messages(example),tokenize=True,add_generation_prompt=True,return_dict=True,return_tensors='pt').to('cuda')
        if inputs['input_ids'].shape[-1]+args.max_new_tokens>args.max_length:raise ValueError('inference exceeds token budget')
        start=time.monotonic()
        with torch.inference_mode():output=model.generate(**inputs,max_new_tokens=args.max_new_tokens,do_sample=False,use_cache=True)
        text=processor.decode(output[0,inputs['input_ids'].shape[-1]:],skip_special_tokens=True)
        row={'provider':'Qwen3-VL-LoRA' if args.adapter else 'Qwen3-VL-base','model':args.model,'adapter':args.adapter,
             'modelEvidence':True,'rawText':text,'seconds':time.monotonic()-start,'inputImages':args.input_images,
             'configuration':{'maxPixels':args.max_pixels,'maxLength':args.max_length,'maxNewTokens':args.max_new_tokens,
                 'precision':args.precision,'quantized':not args.no_quant,'doSample':False}}
        try:row.update(status='valid',answer=parse_answer(text,sample['request']))
        except (ValueError,TypeError,KeyError) as exc:row.update(status='invalid',error=str(exc))
        result[sample['requestKey']]=row;write_json(out.with_name(out.stem+'-diagnostics.json') if standalone else out,result)
        if standalone and row['status']=='valid':write_json(out,row['answer'])
        if row['status']=='valid':write_json(out.parent/(out.stem+'-layouts')/(sample['id']+'.json'),row['answer'])
        print(json.dumps({'sample':sample['id'],'status':row['status'],'seconds':row['seconds']}),flush=True)
        del inputs,output
    return {'predictions':len(result),'valid':sum(r['status']=='valid' for r in result.values()),'modelEvidence':True,
            'peakAllocatedGiB':torch.cuda.max_memory_allocated()/2**30}


def main():
    parser=argparse.ArgumentParser(description='Qwen3-VL isolated GPU training/inference')
    subs=parser.add_subparsers(dest='command',required=True);d=subs.add_parser('doctor');d.add_argument('--out',required=True)
    for command in ('train','infer'):
        p=subs.add_parser(command);p.add_argument('--model',required=True);p.add_argument('--adapter');p.add_argument('--out',required=True)
        p.add_argument('--max-pixels',type=int,default=100352);p.add_argument('--max-length',type=int,default=4096);p.add_argument('--no-quant',action='store_true')
        p.add_argument('--loader',help='Optional model backend module:factory(args, training=bool) -> (model, processor)')
        p.add_argument('--precision',choices=['auto','bf16','fp16'],default='auto')
        if command=='train':
            p.add_argument('--sft',required=True);p.add_argument('--max-steps',type=int,default=1);p.add_argument('--rank',type=int,default=8)
            p.add_argument('--learning-rate',type=float,default=2e-4);p.add_argument('--seed',type=int,default=7);p.add_argument('--allow-weak',action='store_true')
            p.add_argument('--gradient-accumulation',type=int,default=1)
            p.add_argument('--target-modules',default=r'.*language_model.*\.(q_proj|k_proj|v_proj|o_proj)$')
        else:
            p.add_argument('--dataset');p.add_argument('--layout-json');p.add_argument('--image');p.add_argument('--split',choices=['all','train','validation','test'],default='test')
            p.add_argument('--limit',type=int,default=1);p.add_argument('--max-new-tokens',type=int,default=512)
            p.add_argument('--input-images',choices=['source','source-and-current'],default='source')
    args=parser.parse_args()
    if args.command=='train' and (not 1<=args.max_steps<=10000 or not 1<=args.rank<=128 or not 1<=args.gradient_accumulation<=128 or not 0<args.learning_rate<.1):parser.error('invalid training budget')
    if args.command!='doctor' and (not 4096<=args.max_pixels<=2_000_000 or not 512<=args.max_length<=32768):parser.error('invalid pixel/token budget')
    result=doctor(args.out) if args.command=='doctor' else train(args) if args.command=='train' else infer(args)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
