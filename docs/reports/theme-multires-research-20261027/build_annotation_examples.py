"""Create synthetic format examples. This does not build a real theme dataset."""
from pathlib import Path
import csv
import hashlib
import json
import textwrap
import zipfile
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / '数据集标注结构示例包'
OUT.mkdir(exist_ok=True)
SCHEMA = 'multires-layout/1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write(rel, value):
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def jsonl(rel, rows):
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in rows), encoding='utf-8')


def draw_boxes(rel, boxes, logical_size):
    width = 432 if logical_size[0] == 1080 else 640
    height = round(width * logical_size[1] / logical_size[0])
    im = Image.new('RGB', (width, height + 42), '#edf2f6')
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 13)
    d.text((10, 7), 'SYNTHETIC FORMAT EXAMPLE', fill='#17344a', font=font)
    d.text((10, 23), 'No actual theme / no verified labels', fill='#566979', font=font)
    colors = ['#17344a', '#257d83', '#a66b16', '#6e4a8a']
    for i, (group, box) in enumerate(boxes.items()):
        x, y, w, h = box
        scale = width / logical_size[0]
        xy = (x * scale, 42 + y * scale, (x+w)*scale, 42+(y+h)*scale)
        if xy[1] >= height + 42:
            continue
        d.rectangle(xy, fill='white', outline=colors[i % 4], width=3)
        d.text((xy[0]+6, xy[1]+6), group + ' / schematic only', fill=colors[i % 4], font=font)
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


source_sha = digest({'synthetic_fixture': 'v1.2', 'not_a_theme_package': True})
group_map = {'g0': 'group_datetime', 'g1': 'group_hero', 'g2': 'group_tools', 'g3': 'group_panel'}
node_names = ['node_clock', 'node_date', 'node_hero', 'node_menu', 'node_panel']
roles = ['clock', 'date', 'hero', 'control', 'panel']
owners = ['g0', 'g0', 'g1', 'g2', 'g3']
samples = []
for number, state in enumerate(['home', 'panel_open'], 1):
    sid = f'demo_s{number:03}'
    node_boxes = [[120,240,600,200], [120,450,600,80], [160,700,760,1100], [100,2110,880,160], [100,2500 if state=='home' else 600,880,1400]]
    group_boxes = [[120,240,600,290], node_boxes[2], node_boxes[3], node_boxes[4]]
    request = {'schema': SCHEMA, 'theme': source_sha,
        'sourceViewport': {'width': 1080, 'height': 2400},
        'safeRegion': {'x':80,'y':40,'w':1440,'h':880},
        'targetViewport': {'width':1600,'height':1000},
        'requestKind': 'full-reflow', 'mediaPolicy':'reference-frame affine; preserve timeline',
        'groups': [{'id':g, 'sourceBox':[round(b[0]/1080*1000,3), round(b[1]/2400*1000,3), round(b[2]/1080*1000,3), round(b[3]/2400*1000,3)],
            'roles':[['clock','date'],['hero'],['control'],['panel']][i],
            'memberCount':2 if i==0 else 1, 'container':'rigid','anchor':'center',
            'centerAxes':[], 'edges':{}, 'fixed':False, 'sourceHints':[]}
            for i,(g,b) in enumerate(zip(group_map,group_boxes))]}
    answer = {'schema':SCHEMA,'groups':{'g0':[100,100,400,150], 'g1':[600,100,300,650],
        'g2':[100,780,400,100], 'g3':[100,1100 if state=='home' else 200,800,600]}}
    elements = [{'id':node+'@0','source_node_id':node, 'component_identity':'component_'+node,
        'component':'component_'+node, 'parent':'synthetic_root',
        'tag':['Time','Date','Image','Button','Group'][i], 'attributes':{},
        'sourceBox':dict(zip(['x','y','w','h'],node_boxes[i])),
        'script_bbox':dict(zip(['x','y','w','h'],node_boxes[i])),
        'visible_bbox':None, 'visible_bbox_reason':'synthetic example; no measured visible bounds',
        'alpha':1.0 if i<4 or state=='panel_open' else 0.0,
        'asset':None,'image_path':None,'image_path_reason':'schematic fixture; no theme assets',
        'text':'', 'semantic':{'label':roles[i],'origin':'synthetic example','confidence':None},
        'geometryOrigin':'synthetic example; not script execution or native measurement'}
        for i,node in enumerate(node_names)]
    annotations = {'schema':'multires-annotations/1',
        'elementLevel':{'origin':'synthetic example','elements':elements},
        'semanticLevel':{'origin':'synthetic example','components':[
            {'id':'component_'+node,'component_identity':'component_'+node,'members':[node+'@0'],
             'semanticLabels':[role],'groupRole':role,'groupRoleOrigin':'synthetic example','sourceBounds':None}
            for node,role in zip(node_names,roles)]},
        'compositionLevel':{'origin':'synthetic example','groups':[
            {'id':group_map[g],'members':['component_'+node_names[i] for i,o in enumerate(owners) if o==g],
                'container':{'type':'rigid'},'anchor':{'parent_point':'center'},'confidence':None,
                'sourceBounds':dict(zip(['x','y','w','h'],group_boxes[j]))}
            for j,g in enumerate(group_map)],
            'alignments':[],'distributions':[],'sceneKind':'full-reflow',
            'interactionPath':[],'stateControls':{},'timeMs':0,
            'animationPolicy':'synthetic example; no executed timeline'},
        'unresolved':'All semantics and geometry are synthetic examples; no verification occurred.'}
    features = [{'id':f'e{i}','group':owners[i],'tag':e['tag'],'semantic':roles[i],
        'semanticOrigin':'synthetic example', 'sourceBox':[round(node_boxes[i][0]/1080*1000,3),
            round(node_boxes[i][1]/2400*1000,3),round(node_boxes[i][2]/1080*1000,3),round(node_boxes[i][3]/2400*1000,3)]}
        for i,e in enumerate(elements) if e['alpha']>0]
    layout = {'schema':SCHEMA,'groups':answer['groups'],
        'element_coordinate_system':'source_canvas_normalized_1000',
        'group_coordinate_system':'target_safe_region_normalized_1000',
        'elementLevel':features,'elementCount':len(features),'elementFeaturesTruncated':False}
    sample = {'schema':SCHEMA,'id':sid,'sample_id':sid,'family':'demo_f001','split':'train',
        'theme_id':source_sha,'sourceSha256':source_sha,'scene_id':state,'state':state,
        'device':'demo_landscape','source_resolution':request['sourceViewport'],
        'target_resolution':{'width':1600,'height':1000},
        'coordinate_system':'normalized_1000; source_canvas for elements, target_safe_region for group targets',
        'request':request,'requestKey':digest(request),
        'sourceImage':f'images/{sid}-source.png','targetImage':f'images/{sid}-target.png',
        'rendering':{'source':{'complete':False,'reason':'synthetic diagram, not an engine render'},
                     'target':{'complete':False,'reason':'synthetic diagram, not an engine render'}},
        'annotations':f'annotations/{sid}.json','elementIndex':{f'e{i}':node for i,node in enumerate(node_names)},
        'groupMap':group_map, 'inputLayout':layout,
        'labels':{'script':f'labels/{source_sha}.json','trace':[],'runtimeRows':[],'controls':{},'timeMs':0,'coverage':{}},
        'label':{'status':'weak','origin':'synthetic format example; not an anchor computation or designer ground truth','answer':answer}}
    samples.append(sample)
    write(sample['annotations'], annotations)
    write(f'layouts/{sid}-input.json',{'constraints':request,'layoutJson':layout})
    draw_boxes(sample['sourceImage'],dict(zip(group_map,group_boxes)), (1080,2400))
    region = request['safeRegion']
    target_boxes = {g:[region['x']+b[0]*region['w']/1000,region['y']+b[1]*region['h']/1000,
        b[2]*region['w']/1000,b[3]*region['h']/1000] for g,b in answer['groups'].items()}
    draw_boxes(sample['targetImage'],target_boxes,(1600,1000))

jsonl('samples.jsonl',samples)
write('manifest.json',{'schema':'annotation-example-pack/1','demo_only':True,'training_eligible':False,
    'version':'1.2','sample_count':2,'family_count':1,
    'warning':'虚构格式示例，无真实主题、脚本执行、人工审批或原生证据；不能作为生产库或训练集。',
    'format_scope':'字段示例与现有布局答案协议；扩展关系/轨迹/provenance不属于当前CLI自动产物。'})
write('split-manifest.json',{'demo_f001':'train'})
write(f'labels/{source_sha}.json',{'demo_only':True,'sourceSha256':source_sha,'nodes':{},'reason':'No actual source XML in this format example'})
write('provenance/demo_f001.json',{'schema':'theme-provenance/1','demo_only':True,
    'source_sha256':source_sha,'hash_scope':'synthetic fixture descriptor, not a source package',
    'rights_status':'self-created schematic examples only','review_status':'not_reviewed',
    'native_status':'not_tested','native_evidence':None,'real_package_uri':None})
write('relations/demo_s001.json',{'schema':'theme-relations/1','demo_only':True,'sample_id':'demo_s001',
    'evidence_level':'unverified_example','reviewer':None,
    'groups':[{'id':'group_datetime','members':['node_clock','node_date'],'policy':'rigid'}],
    'edges':[{'subject':'node_clock','predicate':'align_x','object':'node_date',
        'space':'source_engine_logical','state':'home','origin':'synthetic example','evidence':None}]})
write('trajectories/demo_t001.json',{'schema':'theme-trajectory/1','demo_only':True,'family_id':'demo_f001','trace_id':'demo_t001',
    'initial':{'state':'home','time_ms':0,'variables':{}},
    'steps':[{'event':{'kind':'tap','target_node_id':'node_menu'},'expected':{'state':'panel_open','panel_visible':True},
        'observed':None,'native_evidence':None,'evidence_level':'unverified_example'}],
    'unexplored':['关闭面板','切换样式后返回','横竖屏切换']})
write('native-evidence/pending.json',{'demo_only':True,'sample_ids':[s['id'] for s in samples],
    'status':'not_tested','device':None,'rom':None,'tool_version':None,'screenshots':[],'conclusion':None})
write('templates/review.template.json',{'schema':SCHEMA,'reviewer':'DEMO_TEMPLATE_REPLACE_WITH_REAL_REVIEWER',
    'reviews':[{'id':s['id'],'requestKey':s['requestKey'],'baseRevision':0,'status':'approved',
        'answer':s['label']['answer'], 'annotationEdits':{
            'elementSemantics':{'e0':'clock'},'groupSemantics':{'g0':'日期时间区'},'sceneIntent':'示例场景意图，需真实审核后填写'},
        'notes':'格式模板，没有发生实际审批。禁止将此文件导入生产数据集。'} for s in samples]})
write('release/dataset-card.json',{'schema':'theme-dataset-card/1','demo_only':True,
    'intended_use':'理解文件结构、标注口径及布局输出协议',
    'excluded_use':['真实训练','主题质量统计','人工或原生验证成绩'],
    'actual_human_reviews':0,'actual_native_checks':0,'limitations':['所有图像为示意框图','无原始XML与主题素材','没有可用训练标签']})
with (OUT / 'taxonomy.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.writer(f)
    writer.writerow(['level','label','meaning','required_evidence'])
    for role in ['clock','date','weather','battery','status_text','background','hero','decoration','shortcut','control','panel','container','unknown']:
        writer.writerow(['element_role',role,'一个主角色；保留原始细粒度标签','脚本、全图和状态；交互能力另证'])
    for relation in ['contains','align_x','align_y','equal_gap','rigid','avoid','anchored_to','in_front_of']:
        writer.writerow(['relation',relation,'保存方向、作用域、坐标及容差','结构或设计理由；标注来源和版本'])
    for relation in ['triggers','updates','reveals','hides']:
        writer.writerow(['interaction',relation,'规则预期与执行观测分开','事件、变量、前后状态及回放证据'])

readme = '''# 数据集标注结构示例包 v1.2

这是虚构的格式示例，包含1个家族、2个状态、1个目标配置。没有真实主题XML、原始素材、脚本运行结果、人工批准或真机证据。全部图像为程序绘制的框图，全部初始答案标为weak；不可以作为生产数据集、训练真值或成绩。

## 建议阅读顺序

1. 阅读manifest.json和release/dataset-card.json，确认用途和限制。
2. 看samples.jsonl：一行一个“源状态+目标请求”，requestKey关联约束，g0等仅是样本内别名。
3. 对照annotations与layouts：元素运行实例、源节点、组件和组保留对应；源框与目标框的坐标空间不同。
4. 看relations和trajectories：这是拟增加的sidecar，当前CLI并不自动生成或导入这些schema。
5. 看templates/review.template.json：仅为API格式模板。approved这个字符串用于示范请求格式，不代表已有审批；它未放入reviews.jsonl，禁止导入真实库。
6. 看provenance和native-evidence：未审核、未测试内容保持空值，不能用示例补造证据。
7. 运行 `python validate_examples.py`，只检查示例JSON、文件引用、组ID、requestKey及基本数值范围。

## 标注作业要点

先做20条独立双标校准，再标角色、组、关系、交互及目标。成员重组要重建请求；当前annotationEdits仅支持elementSemantics、groupSemantics、sceneIntent。拖框后先求解、渲染再确认，不能拿未更新底图审批。在线提交用最新baseRevision，离线导入采取单写者与固定版本。

## 坐标

request.groups.sourceBox及inputLayout.elementLevel.sourceBox相对1080×2400源画布归一化到1000；annotations中的sourceBox/script_bbox为源逻辑xywh；目标答案相对safeRegion归一化到1000。target图只画建议框，没有执行等比求解。图像上方42像素为示例说明条，不属于逻辑画布，示例图不能用于几何训练。

## 与现有工程的关系

multires-layout/1答案与review字段按F:/multi-res现有实现整理。samples是格式教学用子集，不保证适合直接导入serve或export-sft；缺少真实project、完整render结果和脚本事实。不会启动服务、执行训练、联网或修改生产目录。完整流程见随附技术文档第7.17至7.25节。

## 生产补齐项

真实源包与哈希口径；设备和引擎版本；完整关键事件与动画采样；授权状态；已审成员和目标脚本；复算后的画面；真实审核身份与时间；原生已测或待测范围；家族去重及冻结划分；实际工时。每次发布固定文件摘要与数据卡。
'''
(OUT / 'README.md').write_text(readme,encoding='utf-8')
validator = '''"""Read-only checks for this synthetic pack; no production import or training."""
from pathlib import Path
import hashlib
import json
import math

root = Path(__file__).resolve().parent
def read(rel):
    return json.loads((root / rel).read_text(encoding='utf-8'))
def require(condition, message):
    if not condition:
        raise ValueError(message)
def digest(obj):
    return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()
def file_ref(rel):
    target = (root / rel).resolve()
    require(target.is_relative_to(root), 'Reference outside pack: '+rel)
    require(target.is_file(), 'Missing referenced file: '+rel)
manifest = read('manifest.json')
require(manifest['demo_only'] is True and manifest['training_eligible'] is False,
    'Pack must remain explicitly synthetic and ineligible for training')
for path in root.rglob('*.json'):
    json.loads(path.read_text(encoding='utf-8'))
samples = [json.loads(line) for line in (root/'samples.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
index = {s['id']:s for s in samples}
require(len(index)==len(samples), 'Duplicate sample id')
family_splits = {}
for s in samples:
    family_splits.setdefault(s['family'],set()).add(s['split'])
    require(s['label']['status']=='weak', 'Example must not become reviewed truth')
    require(s['requestKey']==digest(s['request']), 'requestKey mismatch')
    known={g['id'] for g in s['request']['groups']}
    answer=s['label']['answer']
    require(set(answer)=={'schema','groups'} and answer['schema']=='multires-layout/1','Invalid answer schema')
    require(set(answer['groups'])==known==set(s['groupMap']), 'Group ID mismatch')
    for box in answer['groups'].values():
        require(len(box)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in box),'Invalid xywh')
        require(min(box[2:])>0 and max(abs(v) for v in box)<=8000,'Unbounded box')
    for rel in [s['sourceImage'],s['targetImage'],s['annotations'],s['labels']['script']]:
        file_ref(rel)
    annotation=read(s['annotations'])
    nodes={e['source_node_id'] for e in annotation['elementLevel']['elements']}
    require(set(s['elementIndex'].values())<=nodes,'Missing source node identity')
    require(all(e['id'] in s['elementIndex'] and e['group'] in known for e in s['inputLayout']['elementLevel']), 'Invalid element alias')
require(all(len(v)==1 for v in family_splits.values()),'Family crosses splits')
template=read('templates/review.template.json')
for review in template['reviews']:
    s=index[review['id']]
    require(review['requestKey']==s['requestKey'],'Stale template requestKey')
    edits=review['annotationEdits']
    require(set(edits)<= {'elementSemantics','groupSemantics','sceneIntent'},'Unsupported annotationEdits field')
    require(set(edits.get('elementSemantics',{}))<=set(s['elementIndex']),'Unknown element alias')
    require(set(edits.get('groupSemantics',{}))<=set(s['groupMap']),'Unknown group alias')
trajectory=read('trajectories/demo_t001.json')
require(all(step['observed'] is None and step['native_evidence'] is None for step in trajectory['steps']), 'Unverified example must not contain fabricated observed results')
print(json.dumps({'format_checks':'passed','samples':len(samples),'families':len(family_splits),
    'actual_human_reviews':0,'actual_native_checks':0,'training_eligible':False},ensure_ascii=False))
'''
(OUT / 'validate_examples.py').write_text(validator,encoding='utf-8')
with zipfile.ZipFile(ROOT / '数据集标注结构示例包_v1.2.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
    for path in sorted(OUT.rglob('*')):
        if path.is_file():
            z.write(path, (Path(OUT.name) / path.relative_to(OUT)).as_posix())
print(f'Created {len(list(OUT.rglob("*")))} entries and ZIP')
