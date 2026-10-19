"""A readable overview of a real-theme experiment and its cached evidence."""
import html
import json
from collections import Counter
from pathlib import Path


def build_run_report(directory, *, annotation_url='http://127.0.0.1:8781/', interaction_url='http://127.0.0.1:8782/'):
    root=Path(directory).resolve()
    def read(name,default=None):
        path=root/name
        return json.loads(path.read_text(encoding='utf8')) if path.exists() else default
    inputs=read('inputs.json');prepared=read('prepare-result.json',{})
    samples=[json.loads(line) for line in (root/'dataset/samples.jsonl').read_text(encoding='utf8').splitlines() if line]
    predictions=read('lora-predictions.json',{});base_predictions=read('base-predictions.json',{})
    experiment=read('lora-experiment/results.json',{})
    quality=read('dataset/quality/quality_report.json',{})
    exports=read('delivery-result.json',{})
    deltas=read('proposal-deltas.json',{}).get('samples',[])
    journeys=read('interaction-test.json',{}).get('journeys',[])
    strict=read('sft-reviewed-only/manifest.json',{});weak=read('sft-weak-complete/manifest.json',{})
    esc=lambda v:html.escape(str(v),quote=True)
    link=lambda name,label:'<a href="'+esc(name)+'">'+esc(label)+'</a>'
    by_case={c['id']:c for c in experiment.get('cases',[])}
    case_projects={r['project']:r for r in experiment.get('projects',[])}
    names={r['id']:r['name'] for r in inputs['themes']}
    valid=sum(p.get('status')=='valid' for p in predictions.values())
    base_valid=sum(p.get('status')=='valid' for p in base_predictions.values())
    completed=len(predictions)==len({s['requestKey'] for s in samples})
    metrics=[(str(len(inputs['themes'])),'真实主题包'),(str(sum(r['states'] for r in prepared.get('themes',[]))),'已探索交互状态'),
             (str(len(samples)),'独立布局样本'),(f'{valid}/{len(predictions)}','LoRA 合规输出'+('' if completed else ' · 运行中'))]
    tiles=''.join('<div class="metric"><strong>'+esc(value)+'</strong>'+esc(label)+'</div>' for value,label in metrics)
    table=[]
    for theme in prepared.get('themes',[]):
        slug=theme['id'];count=sum(Path(s['project']).parents[1].name==slug for s in samples)
        complete=sum(all(r['status']!='incomplete' for r in s['rendering'].values()) for s in samples if Path(s['project']).parents[1].name==slug)
        coverage=theme['coverage'];preview=interaction_url.rstrip('/')+'/'+slug+'/scenes/preview.html'
        table.append('<tr><td>'+esc(theme['name'])+'</td><td>'+str(theme['states'])+'</td><td>'+str(theme['components'])+'</td><td>'+str(count)+'</td><td>'+str(complete)+'/'+str(count)+'</td><td>'+link(preview,'原始与适配交互')+'</td></tr>')
    actions={'tap':'点击','double-tap':'双击','long-press':'长按','swipe':'滑动','drag':'拖动'}
    cards=[]
    for s in samples:
        slug=Path(s['project']).parents[1].name;row=predictions.get(s['requestKey'],{});case=by_case.get(s['id'],{})
        trace=[a.get('humanAction',a) for a in s['labels']['trace']]
        journey=' → '.join(actions.get(a.get('kind'),a.get('kind','交互'))+(' '+a['gesture'] if a.get('gesture') not in {None,'center','double','hold'} else '') for a in trace) or '初始场景'
        images=[('原始直板机','dataset/'+s['sourceImage']),('规则算法布局','dataset/'+s['targetImage'])]
        figures=''.join('<figure><figcaption>'+esc(title)+'</figcaption><img loading="lazy" src="'+esc(path)+'" alt="'+esc(title)+'"></figure>' for title,path in images)
        if row.get('status')=='valid' and case:
            figures+=''.join('<figure><figcaption>'+esc(title)+'</figcaption><img loading="lazy" src="lora-experiment/'+esc(case[key])+'" alt="'+esc(title)+'"></figure>' for title,key in [('LoRA 原始提案','raw'),('约束后的候选','candidate')])
        else:
            explanation=('正在执行模型推理' if not row else '模型输出已通过协议检查，正在等待布局评价。' if row.get('status')=='valid' else
                         '模型提案未通过协议检查，保留规则算法布局：'+row.get('error','无有效提案'))
            figures+='<div class="placeholder">'+esc(explanation)+'</div>'
        incomplete=any(r['status']=='incomplete' for r in s['rendering'].values())
        warn='<p class="warn">本样本含包外资源或缺失的系统数据，离线图像不完整；默认不进入训练导出。</p>' if incomplete else ''
        links=' · '.join([link('dataset/layouts/'+s['id']+'-input.json','模型输入 JSON'),link('dataset/'+s['annotations'],'三层标签'),link('dataset/'+s['labels']['script'],'缓存的脚本与交互')])
        if row.get('status')=='valid':links+=' · '+link('lora-predictions-layouts/'+s['id']+'.json','模型输出 JSON')
        detail=json.dumps({'interactionPath':s['labels']['trace'],'rendering':s['rendering'],'model':row,'qualityGate':case_projects.get(s['project'],{}).get('gate')},ensure_ascii=False,indent=2)
        cards.append('<section class="case" data-theme="'+esc(slug)+'" data-device="'+esc(s['device'])+'"><h3>'+esc(names[slug]+' / '+s['device']+' / '+s['state'])+'</h3><p>'+esc(journey)+' · '+str(len(s['request']['groups']))+' 个组件组 · '+esc(s['request']['requestKind'])+'</p>'+warn+'<div class="images">'+figures+'</div><p>'+links+'</p><details><summary>交互路径、模型原文与检查依据</summary><pre>'+esc(detail)+'</pre></details></section>')
    raw=Counter();constrained=Counter();gates=[]
    for p in experiment.get('projects',[]):
        raw.update(p['rawCounts']);constrained.update(p['constrainedCounts']);gates.append(p['gate']['regressionPassed'])
    violations='有效模型提案的原始检查：'+esc(dict(raw))+'；全部选中样本的最终检查（含规则回退）：'+esc(dict(constrained))
    if not experiment:violations='布局评价将在模型推理后生成。'
    delivery=[]
    for r in exports.get('themes',[]):
        preview=link(r['modelPreview'],'模型候选交互')+' · ' if r.get('modelPreview') else ''
        delivery.append('<span>'+esc(r['name'])+'：'+preview+ ' · '.join(link(e['relativePath'],e['target']+' HWT 候选') for e in r.get('packages',[]))+'</span><br>')
    model_progress=f'LoRA 已处理 {len(predictions)}/{len(samples)} 条，完整输出 {valid} 条。'
    if base_predictions:model_progress+=f' 未微调底座：{base_valid}/{len(base_predictions)} 条输出合规（同一对照子集）。'
    if deltas:
        unchanged=sum(r['maxCoordinateChange1000']<=1e-6 for r in deltas)
        model_progress+=f' {unchanged}/{len(deltas)} 条有效输出完全复用了输入组框；输入本身来自原规则算法，不能据此证明模型学会更好的布局。'
    interaction_note=''
    if journeys:interaction_note=f' 已验证 {sum(r["status"]=="passed" for r in journeys)}/{len(journeys)} 条具体连续交互旅程；新场景无预计算提案时回退规则算法。'
    stages=[('1. 原包解析与交互探索','从两份 HWT 重新提取资源与脚本；每套最多 24 状态、深度 4。使用通用 2560×1600 平板画布，覆盖横竖屏。'),
            ('2. 三层标签与审核','缓存元素框、源节点和资源；组件语义；居中、贴边、相对关系及交互路径。'+link(annotation_url,'进入批量标注')),
            ('3. 模型推理',esc(model_progress)+' 本轮复用已完成的 mock LoRA，真实样本尚未作为人工真值训练。'),
            ('4. 约束求解与反馈',violations+' 质量门禁只作本地候选选择；离线图片不代表真机验收。'),
            ('5. 写回与候选交付',(''.join(delivery) or '候选导出审计尚在执行。')+' 模型无效时明确使用规则算法回退；原包不覆盖。'+interaction_note),
            ('6. 数据回流','严格审核模式已导出 '+str(sum(strict.get('counts',{}).values()))+' 条；当前均待人工确认。显式弱标签导出 '+str(sum(weak.get('counts',{}).values()))+' 条，仅供工程检查。训练导出会排除缺失渲染。')]
    pipeline=''.join('<div class="stage"><h3>'+title+'</h3><p>'+body+'</p></div>' for title,body in stages)
    evidence_links=' · '.join(link(name,label) for name,label in [('inputs.json','输入与配置'),('prepare-result.json','解析与探索记录'),('dataset/quality/quality_report.json','数据质检'),('lora-predictions.json','LoRA 原始输出'),('lora-experiment/report.html','详细布局评价'),('lora-experiment/badcases.jsonl','布局 Badcase'),('proposal-deltas.json','输出是否复用输入'),('interaction-test.json','连续交互验证'),('render-unsupported.json','缺失渲染依据'),('run-evidence.json','源包与产物哈希')])
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>初升与叠峰 · 真实主题流程测试</title><style>
*{box-sizing:border-box}body{margin:0;background:#f0f4f8;color:#213449;font:15px/1.65 system-ui}main{max-width:1300px;margin:auto;padding:24px 18px}h1{font-size:28px;line-height:1.3}h2{font-size:21px}h3{font-size:17px}a{color:#175c94}section,.stage{background:white;border:1px solid #dfe7ef;padding:18px;border-radius:12px;margin:16px 0}.metrics,.images{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric{background:#e8f1f6;padding:14px;border-radius:10px}.metric strong{font-size:27px;display:block}.pipeline{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.stage{margin:0}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #e1e6ed}.table{overflow-x:auto}figure{margin:0}figcaption{margin-bottom:8px;font-size:14px}img{width:100%;max-height:440px;object-fit:contain;background:#e8eef4}.warn{background:#fff4dc;color:#755414;padding:10px;border-radius:8px}.placeholder{padding:18px;background:#f2f5f8;border:1px dashed #8ca3b8;grid-column:span 2;display:flex;align-items:center}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto}select{padding:8px;max-width:100%;margin:4px}.muted{color:#5c6c7d}.case[hidden]{display:none}@media(max-width:760px){.images,.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.pipeline{grid-template-columns:1fr}main{padding:18px 10px}section{padding:13px}h1{font-size:24px}}
</style><main><p class="muted">2026-10-20 · 本地真实包流程验证</p><h1>初升 × 叠峰：从主题包到模型布局与脚本候选</h1><p>源渲染图 + 当前布局 JSON → Qwen3-VL mock LoRA → 几何约束 → 评价、写回与问题回流。</p>'''
    page+='<div class="metrics">'+tiles+'</div><section><h2>查看完整流程</h2><div class="pipeline">'+pipeline+'</div></section>'
    page+='<section><h2>两套主题的覆盖范围</h2><div class="table"><table><tr><th>主题</th><th>状态</th><th>组件</th><th>布局样本</th><th>完整离线渲染</th><th>预览</th></tr>'+''.join(table)+'</table></div><p class="warn">24 状态为本次快速验证的探索上限，存在未穷举分支。平板图来自算法候选，不能当作原生双形态设计真值；正式微调仍需人工配对和审核。</p></section>'
    page+='<h2>逐场景对照</h2><label>主题 <select id="theme"><option value="">全部</option>'+''.join('<option value="'+esc(k)+'">'+esc(v)+'</option>' for k,v in names.items())+'</select></label><label>方向 <select id="device"><option value="">全部</option><option value="tablet-portrait">竖屏</option><option value="tablet-landscape">横屏</option></select></label>'+''.join(cards)
    page+='<section><h2>可追溯文件</h2><p>'+evidence_links+'</p><p class="muted">严格训练导出不包含未审核样本；外部照片和天气数据缺失保持未知，不自动补成真实标签。当前模型输出可用与实际设计质量是分别检查的。</p></section></main><script>const themeFilter=document.getElementById("theme"),deviceFilter=document.getElementById("device");for(const select of [themeFilter,deviceFilter])select.onchange=()=>{for(const c of document.querySelectorAll(".case"))c.hidden=!!((themeFilter.value&&c.dataset.theme!==themeFilter.value)||(deviceFilter.value&&c.dataset.device!==deviceFilter.value))}</script></html>'
    (root/'index.html').write_text(page,encoding='utf8')
    return {'report':str(root/'index.html'),'samples':len(samples),'predicted':len(predictions),'valid':valid,'complete':completed,'quality':quality,'gates':gates}
