"""Publish the alignment release evidence only after all final audits pass."""
import json
from datetime import datetime,timezone
from pathlib import Path

root=Path('theme/adaptation')
latest_project=max(p.stat().st_mtime for p in root.glob('*/scenes/project.json'))
for name in ('alignment-validation.json','spatial-flow-validation.json','canvas-validation.json','media-validation.json','batch-summary.json'):
    if (root/name).stat().st_mtime<latest_project:raise SystemExit('Audit predates a generated project: '+name)
def read(name):return json.loads((root/name).read_text(encoding='utf-8'))
alignment=read('alignment-validation.json');spatial=read('spatial-flow-validation.json');canvas=read('canvas-validation.json');media=read('media-validation.json')
corner=read('corner-validation.json') if (root/'corner-validation.json').exists() else None
corner_latest=max(((root/r['project']/'scenes/project.json').stat().st_mtime for r in corner['records']),default=0) if corner else 0
if corner and ((root/'corner-validation.json').stat().st_mtime<corner_latest or corner['failures'] or corner['exportFailures']):
    raise SystemExit('Corner audit is stale or failed.')
regression=json.loads(Path('artifacts/reports/quality-results.json').read_text(encoding='utf-8'));packages=read('batch-summary.json')
failures=[alignment['failures'],alignment['containerFailures'],alignment['exportFailures'],spatial['gapFailures'],spatial['requiredCenterFailures'],spatial['exportFailures'],canvas['violations'],media['failures'],media['batchGeometryFailures'],media['batchLiveFailures']]
if any(failures) or alignment['policyStates']!=alignment['states'] or not regression['passed'] or not spatial['sourceHashesUnchanged'] or not media['sourceHashesUnchanged'] or any(p['status']!='validated' or not p['sourceUnchanged'] for p in packages):
    raise SystemExit('Final verification is incomplete or has failures; release report was not updated.')
if alignment['projects']!=spatial['projects'] or alignment['states']!=canvas['states']:raise SystemExit('Audit coverage differs.')
if f"JavaScript syntax checked: {alignment['projects']} previews" not in Path('artifacts/logs/alignment-javascript-final.log').read_text(encoding='utf-8'):
    raise SystemExit('Preview syntax validation has not completed.')
summary={'status':'validated-in-sampled-interpreter','completedAt':datetime.now(timezone.utc).isoformat(),
    'sourcePackages':len(packages),'projects':alignment['projects'],'directions':alignment['devices'],'sampledStates':alignment['states'],
    'sharedAlignmentChecks':alignment['alignmentChecks'],'containerAlignmentChecks':alignment['containerChecks'],'alignmentFailures':0,
    'candidatePackages':media['candidatePackages'],'sourceHashesUnchanged':True,
    'horizontalRelationChecks':spatial['horizontalRelations'],'verticalRelationChecks':spatial['verticalRelations'],'relationFailures':0,
    'strictCenterChecks':spatial['requiredCenterChecks'],'centerFailures':0,'stableResources':canvas['resources'],'canvasFailures':0,'exportFailures':0,
    'mediaElementSnapshots':media['checkedElementSnapshots'],'mediaFailures':0,'mediaSkippedSnapshots':media['skippedSnapshots'],
    'regressionTests':regression['tests'],'previewSyntaxChecks':alignment['projects'],'nativeDeviceTest':False,'decodedPixelTest':False}
if corner:summary.update(cornerProjects=corner['projects'],cornerDirections=corner['devices'],stableCornerChecks=corner['stableCornerChecks'],cornerEdgeChecks=corner['edgeChecks'],cornerFailures=0,authoredMotionOrHiddenCornerSkips=corner['animationOrHiddenSkips'])
for name in ('alignment-release-summary.json','layout-final-summary.json'):(root/name).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
content=f'''# 对齐关系修复与全量验证

## 修复内容

- 根据原始画布的可见内容识别共用的左、右、上、下边线及中心线，跨组件组统一求解。
- 自动行列保留原来的起边、中心、末边对齐及透明边距；无共同对齐线时保留原偏移。
- 多帧采用持续成立的对齐证据，重复对齐线去重；公共组件先锁定，冲突时扩大推断的局部重排范围。
- 美学调整、布局复用和导出都检查对齐，避免后处理再次造成错位。
- 左下、右下的独立快捷按钮分别使用角落锚点，图片与点击区域同步；上方时间与底部按钮按上下区域约束，避免角落展开时把时间拉偏。

## 最终结果

| 检查 | 覆盖 | 失败 |
| --- | ---: | ---: |
| 原始主题包 | {summary['sourcePackages']} | 0 |
| 项目 / 设备方向 | {summary['projects']} / {summary['directions']} | 0 |
| 缓存场景 | {summary['sampledStates']:,} | 0 |
| 跨组对齐关系 | {summary['sharedAlignmentChecks']:,} | 0 |
| 容器内对齐关系 | {summary['containerAlignmentChecks']:,} | 0 |
| 水平 / 垂直分离关系 | {summary['horizontalRelationChecks']:,} / {summary['verticalRelationChecks']:,} | 0 |
| 稳定资源画布范围 | {summary['stableResources']:,} | 0 |
| 自动回归测试 | {summary['regressionTests']} | 0 |
| 候选 HWT | {summary['candidatePackages']} | 0 |

媒体和交互跨时刻复核 {summary['mediaElementSnapshots']:,} 次，失败 0；另有 {summary['mediaSkippedSnapshots']} 次因素材不可用或表达式不可解析而跳过，未计为通过。预览脚本语法检查 {summary['previewSyntaxChecks']} 个项目通过。源 HWT 摘要校验一致。

以上为已采样场景的离线解释器、几何与导出验证，未进行原生设备和解码像素验收。源位图内部的多项图形保持原素材结构。

## 查看结果

- 预览：<http://127.0.0.1:8766/>
- 脚本：`theme/adaptation/<主题编号>/scenes/scripts/`
- 候选包：`theme/adaptation/<主题编号>/scenes/packages/`
- 对齐复核：`theme/adaptation/alignment-validation.json`
- 汇总：`theme/adaptation/alignment-release-summary.json`
- 完整求解依据：各项目 `scenes/anchor-layout.json`

自动排布和阈值说明见 [LAYOUT_RELATIONS.md](../guides/LAYOUT_RELATIONS.md)。自定义布局接口保持 `layout_scene(...) → {{组件编号: {{sx, sy, tx, ty}}}}`。
'''
if alignment.get('reuseUnchangedProjectRecords'):
    content+='\n本轮重新计算并复核 '+str(len(alignment['revalidatedProjects']))+' 个变化项目，其余项目沿用先前逐项目复核结果；复用前校验项目文件未在复核后发生修改。汇总覆盖全部 '+str(alignment['projects'])+' 个项目。\n'
if corner:
    content+=f"\n角落专项：{corner['projects']} 个项目、{corner['devices']} 个设备方向，{corner['stableCornerChecks']:,} 次稳定位置检查、{corner['edgeChecks']:,} 条边距检查，失败 0；{corner['animationOrHiddenSkips']} 次原作者动画或隐藏姿态未作为固定角落位置检查。\n"
    details=f'''# 底部手电筒、相机角落锚点修复

两个底部快捷入口此前被当成宽组整体缩放、居中，宽屏上组内距离随缩放收拢。现分离为独立组件，保持原图片和按钮绑定，分别依附左下角、右下角。

- 左侧画框：`x = 安全区左边 + 原左边距 × 资源缩放率`。
- 右侧画框：`right = 安全区右边 - 原右边距 × 资源缩放率`。
- 底部画框：`bottom = 安全区底边 - 原下边距 × 资源缩放率`。
- 图片保持等比缩放；点击区域使用同一组件变换。
- 用已到位的画框确定锚点，保留原入场、离场动画；原作者改变布局的交互场景释放角落规则，返回时恢复。
- 时间与角落按钮按上下区域保持分离，不建立错误的左右邻居关系，也不以远距离间距重新推动上方时间。

## 验证

角落专项覆盖 {corner['projects']} 个真实主题项目，复核 {corner['devices']} 个设备方向、{corner['states']:,} 个缓存场景。完成 {corner['stableCornerChecks']:,} 次稳定位置检查、{corner['edgeChecks']:,} 条边距检查，图片与点击区域归属、等比缩放及导出失败均为 0。另有 {corner['animationOrHiddenSkips']} 次动画或隐藏姿态保留原行为，未作为固定角落位置检查。

完整回归 {regression['tests']} 项通过。对应候选包已重打包并经过脚本几何和交互抽查。全局布局与媒体结果见 [ALIGNMENT_RESULTS.md](ALIGNMENT_RESULTS.md)。以上为离线采样复核，未在原生设备上验收。

叠峰平板横屏的逻辑画布为 1080 × 675，图标等比缩放至 70 × 70；手电筒左边距 39.5、相机右边距 40.5、底边距均为 33 个逻辑像素。

预览：<http://127.0.0.1:8766/theme-3470136cc2/scenes/preview.html>。候选包位置保持为 `theme/adaptation/<主题编号>/scenes/packages/`。原始 HWT 不覆盖。
'''
    Path('docs/reports/CORNER_ANCHORS_RESULTS.md').write_text(details,encoding='utf-8')
Path('docs/reports/ALIGNMENT_RESULTS.md').write_text(content,encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False),flush=True)
