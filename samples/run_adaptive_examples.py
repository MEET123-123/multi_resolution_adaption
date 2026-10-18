"""Run both real themes through the reusable policy; no original is overwritten."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from multires_app.tools.batch_convert import source_profile
from multires_app.pipeline.scene_pipeline import create_project
from multires_app.preview.layout_refinement import export_request
from multires_app.tools.release_readiness import assess


def publish_examples(directory):
    from samples.render_layout_comparison import render
    summary=[];links=[]
    for name,slug in [('比邻熊 新拟态','bear'),('线条小狗 夏日出逃','dog')]:
        target=directory/slug;project=json.loads((target/'project.json').read_text(encoding='utf-8'))
        report=json.loads((target/'report.json').read_text(encoding='utf-8'))
        (target/'layout-refinement-request.json').write_text(json.dumps(export_request(project,'tablet-landscape','state-000'),ensure_ascii=False,indent=2),encoding='utf-8')
        readiness=assess(project);(target/'readiness.json').write_text(json.dumps(readiness,ensure_ascii=False,indent=2),encoding='utf-8')
        render(target)
        if len(project['graph']['states'])>1:render(target,1)
        summary.append({'name':name,'sourceProfile':project['sourceProfile'],'project':str((target/'project.json').resolve()),
                        'adaptation':report['adaptation'],'states':report['states'],'coverage':report['coverage'],'review':report['layoutClustering'],
                        'datetimePlacement':{d['profile']['id']:d['layouts'][0].get('adaptationPlan',{}).get('datetimePlacement') for d in project['devices']},
                        'productionReady':False,'nativeDeviceTest':False})
        links.append(f'<section><h2>{name}</h2><p><a href="{slug}/preview.html">原始/目标交互对比</a> · <a href="{slug}/layout-comparison.png">初始画面对比</a> · <a href="{slug}/layout-comparison-state-1.png">首次交互后对比</a></p><p><a href="{slug}/interaction-ir.json">交互规则 JSON</a> · <a href="{slug}/interaction-bindings.json">按钮绑定</a> · <a href="{slug}/scene-analysis.json">场景提取报告</a> · <a href="{slug}/engine-compatibility.json">引擎能力报告</a></p><p><a href="{slug}/layout-refinement-request.json">离线微调请求 JSON</a> · <a href="{slug}/readiness.json">验收报告</a></p><p>布局待复核 {readiness["projectChecks"]["layoutReviewCount"]} 项，绑定待复核 {readiness["projectChecks"]["bindingReviewCount"]} 项；真机未验收。</p></section>')
    (directory/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (directory/'index.html').write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>自适应算法示例</title><style>body{font:16px/1.8 system-ui;margin:36px auto;padding:0 24px;max-width:1050px;color:#243a46;background:#f1f6f8}a{color:#087e83}section{background:white;padding:18px 28px;margin:20px 0;border-radius:12px}h1{font-size:27px}h2{font-size:21px}</style><h1>主题适配与布局微调示例</h1><p>相近比例复用原构图；比例变化大时逐行两列；横屏时间日期搜索左侧/顶部留白。复杂原生脚本按有界探索生成候选，需人工复核。</p>'+''.join(links)+'<p><a href="summary.json">策略、交互覆盖与复核汇总</a></p><p>客户在线服务的本机启动方式见项目根目录 SAAS_READINESS.md。</p>',encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'theme/adaptive-code-demo')
    parser.add_argument('--profiles',type=Path,default=ROOT/'samples/adaptive-profiles.json')
    parser.add_argument('--max-states',type=int,default=8)
    parser.add_argument('--max-depth',type=int,default=2)
    parser.add_argument('--refresh-only',action='store_true',help='只更新已有项目的请求、验收报告与索引')
    args=parser.parse_args()
    if args.refresh_only:
        summary=publish_examples(args.out)
        print(json.dumps({'output':str(args.out.resolve()),'themes':len(summary)},ensure_ascii=False));return
    profiles=json.loads(args.profiles.read_text(encoding='utf-8'))
    args.out.mkdir(parents=True,exist_ok=True)
    inputs=args.out/'inputs.json'
    inputs.write_text(json.dumps({'interaction':{'maxExplorationGestures':256}}),encoding='utf-8')
    for name,slug in [('比邻熊 新拟态','bear'),('线条小狗 夏日出逃','dog')]:
        source=ROOT/'theme'/(name+'.hwt');profile=source_profile(source)
        target=args.out/slug;target.mkdir(parents=True,exist_ok=True)
        profile['adaptation']={'mode':'auto','aspectThreshold':.18}
        (target/'source-profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
        create_project(source,profile,profiles,target,'adaptive',args.max_states,args.max_depth,inputs_file=inputs)
    summary=publish_examples(args.out)
    print(json.dumps({'output':str(args.out.resolve()),'themes':len(summary)},ensure_ascii=False))


if __name__=='__main__':main()
