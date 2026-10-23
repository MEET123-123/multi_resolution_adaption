"""Paired base/adapter generation on a fixed held-out subset, then report."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'src'))
from local_finetune import read,rows,save
from multires_app.learning.contract import validate_answer


def measure(samples,predictions):
    cases=[]
    for sample in samples:
        prediction=predictions.get(sample['requestKey'],{})
        case={'id':sample['id'],'family':sample['family'],'device':sample['device'],'valid':False}
        try:
            if prediction.get('status')!='valid':raise ValueError('invalid or missing JSON')
            answer=validate_answer(prediction['answer'],sample['request'])
            diffs=[abs(value-sample['label']['answer']['groups'][gid][i]) for gid,box in answer['groups'].items() for i,value in enumerate(box)]
            case.update(valid=True,mae1000=sum(diffs)/len(diffs))
        except (ValueError,TypeError,KeyError) as exc:case['error']=str(exc)
        cases.append(case)
    good=[c for c in cases if c['valid']]
    return {'samples':len(cases),'valid':len(good),'validRate':len(good)/len(cases) if cases else None,
            'teacherMAE1000':sum(c['mae1000'] for c in good)/len(good) if good else None,'cases':cases}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);args=parser.parse_args();run=Path(args.run).resolve()
    config=read(run/'configuration.json');training=read(run/'training-result.json');subset=run/'test-subset'
    samples=rows(subset/'samples.jsonl');env=os.environ.copy();env['PYTHONPATH']=str(ROOT.parent/'src');env['HF_HUB_OFFLINE']='1';env['PYTHONUNBUFFERED']='1'
    measures={}
    for name,adapter in [('base',None),('lora',str(run/'adapter'))]:
        out=run/(name+'-predictions.json')
        if not out.exists():
            command=[sys.executable,'-m','multires_app.learning.qwen_runner','infer','--model',config['model'],
                     '--dataset',str(subset),'--split','test','--limit',str(len(samples)),'--max-pixels',str(config['maxPixels']),
                     '--max-length',str(config['maxLength']),'--max-new-tokens','1024','--out',str(out)]
            if adapter:command.extend(['--adapter',adapter])
            print('Evaluating '+name,flush=True)
            with (run/(name+'-inference.log')).open('w',encoding='utf8') as log:
                process=subprocess.Popen(command,cwd=ROOT.parent,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace')
                for line in process.stdout:log.write(line);log.flush();print(line,end='',flush=True)
                if process.wait()!=0:raise RuntimeError(name+' inference failed; see saved log')
        predictions=read(out)
        if set(predictions)!={s['requestKey'] for s in samples}:raise RuntimeError(name+' prediction file is incomplete')
        measures[name]=measure(samples,predictions);save(run/(name+'-score.json'),measures[name])
    base={c['id']:c for c in measures['base']['cases']};lora={c['id']:c for c in measures['lora']['cases']}
    common=[identity for identity in base if base[identity]['valid'] and lora[identity]['valid']]
    mean=lambda values:sum(values)/len(values) if values else None
    comparison={'base':{k:v for k,v in measures['base'].items() if k!='cases'},'lora':{k:v for k,v in measures['lora'].items() if k!='cases'},
                'commonValidSamples':len(common),'commonBaseMAE1000':mean([base[i]['mae1000'] for i in common]),
                'commonLoraMAE1000':mean([lora[i]['mae1000'] for i in common]),
                'scope':'6 fixed held-out samples, one per test family/device; teacher agreement only; full test has 55 samples',
                'validationBefore':training['validationBefore']['loss'],'validationAfter':training['validationAfter']['loss']}
    save(run/'comparison.json',comparison)
    before=training['validationBefore']['loss'];after=training['validationAfter']['loss']
    report=(f"# 本地微调实验结果\n\n"
            f"状态：已完成。底座 Qwen3-VL-2B-Instruct，RTX 4060 8GB，4 位 QLoRA，rank 8。\n\n"
            f"训练完整覆盖 140 条样本一轮，共 {training['optimizerSteps']} 次参数更新。视觉编码器冻结。\n"
            f"24 条验证样本的助手 token 加权损失：{before:.6f} → {after:.6f}（相对下降 {1-after/before:.2%}）。\n"
            f"峰值 PyTorch 分配显存：{training['peakAllocatedGiB']:.2f} GiB；训练及验证用时 {training['seconds']:.1f} 秒，不含加载和后续生成。\n\n"
            f"| 固定测试子集 | 底座 | 微调后 |\n|---|---:|---:|\n"
            f"| JSON/组ID合规 | {measures['base']['valid']}/{len(samples)} | {measures['lora']['valid']}/{len(samples)} |\n"
            f"| 各自合规样本 MAE/1000 | {measures['base']['teacherMAE1000']} | {measures['lora']['teacherMAE1000']} |\n"
            f"| 共同合规样本 MAE/1000 | {comparison['commonBaseMAE1000']} | {comparison['commonLoraMAE1000']} |\n\n"
            f"共同合规样本数：{len(common)}。测试预先固定每个测试家族/设备一条，共 6 条；不是全部 55 条测试集。\n"
            f"目标来自算法教师，指标反映教师一致性，不等于人工设计评价或真机验收；未对模型坐标做约束投影。\n\n"
            f"适配器在 `adapter/`，参数和数据哈希在 `configuration.json`，验证结果在 `validation.json`。\n"
            f"底座/微调后的原始生成及逐例结果保存在对应 predictions/score JSON 中。\n")
    (run/'REPORT.md').write_text(report,encoding='utf8');print(json.dumps(comparison,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
