"""Batch evaluation, badcase attribution and regression budgets over adapted projects.

Read-only over theme/adaptation/*/scenes; writes eval/ only. The evaluator does
not re-run layout: it mines the artifacts each stage already recorded
(validation, layoutQuality, aestheticEvaluation, adaptationReview, coverage,
diagnostics) into one normalized record per project x device, then attributes
every finding to a pipeline stage with a stable code.
"""
import argparse
import collections
import html
import json
import math
import statistics
from pathlib import Path

TAXONOMY = {
    'EXP-01': ('包结构或反写校验失败', 'export', 'validation.json failures>0 或 ZIP 条目/哈希不一致'),
    'INT-01': ('实时交互验证失败', 'export', 'live-validation.json 中手势或返回恢复未通过'),
    'COV-01': ('状态探索触顶截断', 'interaction', 'states 达到 maxStates 且 truncated=true'),
    'COV-02': ('触发器采样不足', 'interaction', 'sampledTriggers / interactiveTriggerCount 偏低'),
    'COV-03': ('手势采样预算耗尽', 'interaction', 'gestureBudgetExceeded=true，后续分支未探索'),
    'GEO-01': ('新增非预期重叠回归', 'layout', '候选 newSourceDisjointOverlaps 高于基线'),
    'GEO-02': ('新增裁切回归', 'layout', '候选 newClippedComponentSnapshots 高于基线'),
    'GEO-03': ('组件超出安全区', 'layout', 'adaptationReview: component exceeds safe area'),
    'GEO-04': ('组件低于最小缩放', 'layout', 'adaptationReview: component below minimum scale'),
    'GEO-05': ('同场景新增重叠', 'layout', 'adaptationReview: new overlap after layout'),
    'GEO-06': ('屏幕关系误差偏高', 'layout', 'screenRelationViolations / 快照 高于阈值'),
    'GEO-07': ('资源缩放偏差偏高', 'media', 'resourceScaleErrorPx / 快照 高于阈值'),
    'GEO-08': ('冻结组件变换漂移', 'layout', 'fixedTransformDrift>0，公共组件被移动'),
    'AES-01': ('低分且无可行改进', 'aesthetic', 'score<80 且 acceptedSteps=0'),
    'AES-02': ('美学维度瓶颈', 'aesthetic', '最大加权损失维度占比过高'),
    'AST-01': ('素材未解析', 'engine', 'diagnostics 中 asset 类告警'),
    'AST-02': ('表达式或动画未解释', 'engine', 'diagnostics 中 srcExp/delay/animation 类告警'),
}

SEVERITY_ORDER = {'P0': 0, 'P1': 1, 'P2': 2}
CAP = {'overlapRate': 0.20, 'clipRate': 0.05, 'safeAreaPerLayout': 40., 'belowScalePerLayout': 30.,
       'relationRate': 0.10, 'scoreGap': 20.}


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def ratio(value, base):
    return float(value) / base if base else 0.


def aspect_bucket(width, height):
    a = max(width, height) / max(min(width, height), 1e-9)
    return '<1.3' if a < 1.3 else '1.3-1.6' if a < 1.6 else '1.6-1.9' if a < 1.9 else '>=1.9'


def orientation(width, height):
    return 'landscape' if width > height else 'portrait' if width < height else 'square'


def weighted_top_loss(evaluation):
    """Return the dominant weighted aesthetic loss so the report names a dimension."""
    if not isinstance(evaluation, dict):
        return None
    losses = evaluation.get('losses') or {}
    available = evaluation.get('available') or {}
    weights = evaluation.get('weights') or {}
    default = {'alignment': 2., 'overlap': 3., 'balance': 1., 'symmetry': .5, 'rhythm': 1.,
               'density': .75, 'hierarchy': 2., 'readability': 1.5, 'backgroundComplexity': .75, 'order': 2.}
    scored = []
    for name, loss in losses.items():
        if not available.get(name):
            continue
        weight = float(weights.get(name, default.get(name, 1.)))
        scored.append((loss * weight, name, float(loss)))
    if not scored:
        return None
    scored.sort(reverse=True)
    total = sum(item[0] for item in scored) or 1e-9
    return {'dimension': scored[0][1], 'loss': round(scored[0][2], 4), 'share': round(scored[0][0] / total, 4)}


def review_counts(project):
    """Aggregate adaptationReview reasons per device, because they carry per-state evidence."""
    counts = collections.Counter()
    components = collections.Counter()
    for device in project.get('devices', []):
        for layout in device.get('layouts', []):
            for item in layout.get('adaptationReview') or []:
                counts[item.get('reason')] += 1
                if item.get('component'):
                    components[item['component']] += 1
    return counts, components


def device_record(project, slug, device, quality, validation, live):
    profile = device.get('profile') or {}
    viewport = device.get('viewport') or {}
    width, height = float(viewport.get('width') or 0), float(viewport.get('height') or 0)
    layouts = device.get('layouts') or []
    quality = quality or {}
    candidate = quality.get('candidate') or {}
    baseline = quality.get('baseline') or {}
    snapshots = max(int(candidate.get('componentSnapshots') or 0), 1)

    counts = collections.Counter()
    for layout in layouts:
        for item in layout.get('adaptationReview') or []:
            counts[item.get('reason')] += 1
    layout_count = max(len(layouts), 1)

    aesthetic = device.get('aestheticEvaluation') or {}
    score = aesthetic.get('afterScore')
    before = aesthetic.get('beforeScore')
    if not isinstance(score, (int, float)):
        scores = [l['aestheticEvaluation']['after']['score'] for l in layouts
                  if isinstance(l.get('aestheticEvaluation'), dict)
                  and isinstance((l['aestheticEvaluation'].get('after') or {}).get('score'), (int, float))]
        score = statistics.fmean(scores) if scores else None
    if not isinstance(before, (int, float)) and isinstance(score, (int, float)):
        before = score
    top = None
    for layout in layouts:
        evaluation = layout.get('aestheticEvaluation') or {}
        candidate_top = weighted_top_loss(evaluation.get('after') or evaluation.get('before'))
        if candidate_top:
            top = candidate_top
            break
    top = top or weighted_top_loss(aesthetic)

    record = {
        'slug': slug,
        'theme': Path(str(project.get('source') or slug)).name or slug,
        'variant': slug.endswith('-ucd'),
        'device': profile.get('id'),
        'orientation': orientation(width, height),
        'aspect': round(max(width, height) / max(min(width, height), 1e-9), 4),
        'aspectBucket': aspect_bucket(width, height),
        'family': (profile.get('id') or '').split('-')[0],
        'viewport': {'width': width, 'height': height},
        'states': len(layouts),
        'componentSnapshots': snapshots,
        'selection': quality.get('selection'),
        'clipped': int(candidate.get('newClippedComponentSnapshots') or 0),
        'clippedBaseline': int(baseline.get('newClippedComponentSnapshots') or 0),
        'overlaps': int(candidate.get('newSourceDisjointOverlaps') or 0),
        'overlapsBaseline': int(baseline.get('newSourceDisjointOverlaps') or 0),
        'relationViolations': int(candidate.get('screenRelationViolations') or 0),
        'relationErrorPx': float(candidate.get('screenRelationErrorPx') or 0),
        'resourceScaleErrorPx': float(candidate.get('resourceScaleErrorPx') or 0),
        'fixedDrift': int(candidate.get('fixedTransformDrift') or 0),
        'glyphDrift': int(candidate.get('glyphScaleDrift') or 0),
        'score': round(float(score), 3) if isinstance(score, (int, float)) else None,
        'scoreBefore': round(float(before), 3) if isinstance(before, (int, float)) else None,
        'acceptedSteps': len(aesthetic.get('acceptedSteps') or []),
        'optimizerReason': aesthetic.get('reason'),
        'rejected': aesthetic.get('rejected') or {},
        'topLoss': top,
        'review': {k: v for k, v in counts.items()},
        'reviewPerLayout': round(sum(counts.values()) / layout_count, 2),
        'validationFailures': int((validation or {}).get('failures') or 0),
        'skippedSnapshots': int((validation or {}).get('skippedElementSnapshots') or 0),
        'liveFailures': int((live or {}).get('failures') or 0),
    }
    record['overlapRate'] = round(ratio(record['overlaps'], snapshots), 5)
    record['clipRate'] = round(ratio(record['clipped'], snapshots), 5)
    record['relationRate'] = round(ratio(record['relationViolations'], snapshots), 5)
    record['resourceScaleErrorPerSnapshot'] = round(ratio(record['resourceScaleErrorPx'], snapshots), 3)
    record['lowConfidence'] = snapshots < 50 or record['states'] < 5
    return record


def project_records(path):
    project = load(path)
    if not project:
        return [], {}
    slug = Path(path).parent.parent.name
    anchor = load(Path(path).parent / 'anchor-layout.json', {}) or {}
    quality = anchor.get('devices') or {}
    validation = load(Path(path).parent / 'validation.json', {}) or {}
    live = load(Path(path).parent / 'live-validation.json', {}) or {}
    coverage = (project.get('graph') or {}).get('coverage') or {}
    diagnostics = project.get('diagnostics') or {}
    asset_issues = sum(1 for key in diagnostics if key.endswith(':asset'))
    expression_issues = sum(1 for key, text in diagnostics.items()
                            if not key.endswith(':asset') and any(t in str(text) for t in ('未支持', '未解析', '未识别', '未模拟')))
    context = {'slug': slug, 'coverage': coverage, 'diagnostics': len(diagnostics),
               'assetIssues': asset_issues, 'expressionIssues': expression_issues,
               'validation': validation, 'live': live}
    records = []
    for device in project.get('devices') or []:
        device_id = (device.get('profile') or {}).get('id')
        inner = (quality.get(device_id) or {}).get('quality')
        records.append(device_record(project, slug, device, inner, validation, live))
    return records, context


def project_cases(context):
    """Project-scoped findings: repeating them per device would fake precision."""
    slug = context['slug']
    cases = []

    def add(code, severity, evidence, hint):
        title, stage, rule = TAXONOMY[code]
        cases.append({'code': code, 'title': title, 'stage': stage, 'rule': rule, 'severity': severity,
                      'slug': slug, 'device': None, 'orientation': '-', 'aspectBucket': '-',
                      'evidence': evidence, 'hint': hint, 'riskIndex': 0.})

    if context.get('assetIssues'):
        add('AST-01', 'P1', {'diagnostics': context['assetIssues']},
            'engine/scene_engine.py 的资源解析与 tools/audit_assets.py')
    if context.get('expressionIssues'):
        add('AST-02', 'P2', {'diagnostics': context['expressionIssues']},
            'engine/scene_engine.py 的表达式与动画命令解释')
    coverage = context.get('coverage') or {}
    if coverage.get('gestureBudgetExceeded'):
        add('COV-03', 'P1', {'attempted': coverage.get('attemptedGestureSamples'),
                             'max': coverage.get('maxGestureSamples')},
            'interaction/interaction_semantics.py 的采样策略，先提高去重率再提高预算')
    interactive = coverage.get('interactiveTriggerCount') or 0
    if interactive and (coverage.get('sampledTriggers') or 0) / interactive < 0.6:
        add('COV-02', 'P2', {'sampled': coverage.get('sampledTriggers'), 'interactive': interactive},
            'interaction/interaction_semantics.py 的不可达触发器判定')
    if coverage.get('truncated'):
        add('COV-01', 'P1', {'maxStates': coverage.get('maxStates'),
                             'unexpanded': len(coverage.get('unexpandedStates') or [])},
            'interaction/interaction_semantics.py 的状态探索预算与 scene_clusters 去重')
    return cases


def attribute(record, context):
    """Turn one device record into badcase rows. Rules are explicit so they can be tuned."""
    cases = []
    low_sample = record['componentSnapshots'] < 50 or record['states'] < 5

    def add(code, severity, evidence, hint):
        if low_sample and severity == 'P0':
            severity = 'P1'
            evidence = {**evidence, 'lowSample': True}
        title, stage, rule = TAXONOMY[code]
        cases.append({'code': code, 'title': title, 'stage': stage, 'rule': rule,
                      'severity': severity, 'slug': record['slug'], 'device': record['device'],
                      'orientation': record['orientation'], 'aspectBucket': record['aspectBucket'],
                      'lowConfidence': low_sample, 'evidence': evidence, 'hint': hint})

    if record['validationFailures']:
        add('EXP-01', 'P0', {'failures': record['validationFailures']},
            'export/layout_writeback.py 与 pipeline 导出流程；先看 scenes/validation.json examples')
    if record['liveFailures']:
        add('INT-01', 'P0', {'failures': record['liveFailures']},
            'preview/interaction_server.py 的 gesture/back 恢复路径')
    if record['fixedDrift']:
        add('GEO-08', 'P0', {'fixedTransformDrift': record['fixedDrift']},
            'layout_quality.finalize_anchor_device 的 frozenComponents 修复分支')
    if record['overlaps'] > record['overlapsBaseline']:
        delta = record['overlaps'] - record['overlapsBaseline']
        severity = 'P0' if record['overlapRate'] > CAP['overlapRate'] else 'P1'
        add('GEO-01', severity,
            {'candidate': record['overlaps'], 'baseline': record['overlapsBaseline'], 'delta': delta,
             'rate': record['overlapRate']},
            'layout_quality.quality() 的 Pareto 门；当前 only-rollback-on-total-dominance 会放过局部退化')
    if record['clipped'] > record['clippedBaseline']:
        severity = 'P0' if record['clipRate'] > CAP['clipRate'] else 'P1'
        add('GEO-02', severity,
            {'candidate': record['clipped'], 'baseline': record['clippedBaseline'],
             'rate': record['clipRate']},
            'layout/canvas_bounds.py 与 anchor_solver 的安全区投影')
    safe_area = record['review'].get('component exceeds safe area', 0)
    if safe_area:
        per_layout = safe_area / max(record['states'], 1)
        severity = 'P0' if per_layout > 6 else 'P1' if per_layout > 3 else 'P2'
        add('GEO-03', severity, {'count': safe_area, 'perLayout': round(per_layout, 2)},
            'layout/anchor_solver.py 可行域；检查 safeRegion 与不可缩放组件')
    below = record['review'].get('component below minimum scale', 0)
    if below:
        per_layout = below / max(record['states'], 1)
        severity = 'P1' if per_layout > 5 else 'P2'
        add('GEO-04', severity, {'count': below, 'perLayout': round(per_layout, 2)},
            'layout/visual_scaling.py 的最小缩放策略与 resourceScaleFamilies')
    overlap_review = record['review'].get('new overlap after layout', 0)
    if overlap_review:
        per_layout = overlap_review / max(record['states'], 1)
        add('GEO-05', 'P1' if per_layout > 4 else 'P2', {'count': overlap_review, 'perLayout': round(per_layout, 2)},
            'layout/spatial_flow.py 与 composition_layers.py')
    if record['relationRate'] > CAP['relationRate']:
        add('GEO-06', 'P1', {'violations': record['relationViolations'], 'rate': record['relationRate']},
            'layout/screen_relations.py 的 attachment_margin / 分布约束')
    if record['resourceScaleErrorPerSnapshot'] > 100:
        add('GEO-07', 'P2', {'pxPerSnapshot': record['resourceScaleErrorPerSnapshot']},
            'media/resource_scaling.py 与 visual_scaling 的 preferredResourceScale')
    score = record['score']
    if isinstance(score, (int, float)):
        if score < 80 and record['acceptedSteps'] == 0:
            add('AES-01', 'P1' if score >= 70 else 'P0',
                {'score': score, 'reason': record['optimizerReason'], 'rejected': record['rejected']},
                'layout/aesthetic_optimizer.py 的候选生成与拒绝原因；先看 rejected 计数')
        top = record['topLoss']
        if top and top['share'] > 0.45 and top['loss'] > 0.3:
            add('AES-02', 'P1', {'dimension': top['dimension'], 'loss': top['loss'], 'share': top['share']},
                'layout/layout_aesthetics.py 对应维度的损失函数与参考关系推断')
    return cases


def risk_index(record):
    """Triage-only scalar; every term is capped and documented in the report."""
    def norm(value, cap):
        return min(1., max(0., value / cap))
    parts = [0.30 * norm(record['overlapRate'], CAP['overlapRate']),
             0.20 * norm(record['clipRate'], CAP['clipRate']),
             0.20 * norm((80. - record['score']) / CAP['scoreGap'], 1.) if isinstance(record['score'], (int, float)) else 0.,
             0.15 * norm(record['review'].get('component exceeds safe area', 0) / max(record['states'], 1),
                         CAP['safeAreaPerLayout']),
             0.15 * norm(record['relationRate'], CAP['relationRate'])]
    return round(100 * sum(parts), 2)


def aggregate(records, cases):
    by_code = collections.Counter(case['code'] for case in cases)
    devices_by_code = collections.defaultdict(set)
    themes_by_code = collections.defaultdict(set)
    for case in cases:
        if case.get('device'):
            devices_by_code[case['code']].add((case['slug'], case['device']))
        themes_by_code[case['code']].add(case['slug'])

    def slice_stats(key):
        groups = collections.defaultdict(list)
        for record in records:
            groups[key(record)].append(record)
        out = []
        for name, items in sorted(groups.items(), key=lambda kv: str(kv[0])):
            scores = [r['score'] for r in items if isinstance(r['score'], (int, float))]
            out.append({'slice': name, 'devices': len(items),
                        'meanScore': round(statistics.fmean(scores), 2) if scores else None,
                        'minScore': round(min(scores), 2) if scores else None,
                        'meanOverlapRate': round(statistics.fmean([r['overlapRate'] for r in items]), 5),
                        'meanClipRate': round(statistics.fmean([r['clipRate'] for r in items]), 5),
                        'meanRelationRate': round(statistics.fmean([r['relationRate'] for r in items]), 5),
                        'reviewPerLayout': round(statistics.fmean([r['reviewPerLayout'] for r in items]), 2),
                        'overlapRegressions': sum(1 for r in items if r['overlaps'] > r['overlapsBaseline']),
                        'clipRegressions': sum(1 for r in items if r['clipped'] > r['clippedBaseline'])})
        return out

    themes = collections.Counter(record['slug'] for record in records)
    scores = [r['score'] for r in records if isinstance(r['score'], (int, float))]
    return {
        'devices': len(records),
        'projects': len(themes),
        'badcases': len(cases),
        'severity': dict(collections.Counter(case['severity'] for case in cases)),
        'meanScore': round(statistics.fmean(scores), 2) if scores else None,
        'medianScore': round(statistics.median(scores), 2) if scores else None,
        'byCode': [{'code': code, 'title': TAXONOMY[code][0], 'stage': TAXONOMY[code][1],
                    'cases': by_code[code], 'devices': len(devices_by_code[code]),
                    'themes': len(themes_by_code[code])}
                   for code in sorted(by_code, key=lambda c: (-by_code[c], c))],
        'byStage': dict(collections.Counter(case['stage'] for case in cases)),
        'byOrientation': slice_stats(lambda r: r['orientation']),
        'byAspect': slice_stats(lambda r: r['aspectBucket']),
        'byFamily': slice_stats(lambda r: r['family']),
        'byTheme': sorted(slice_stats(lambda r: r['slug']), key=lambda item: (item['meanScore'] is None, item['meanScore'] or 0)),
    }


def compare(records, baseline_path):
    baseline = {f"{r['slug']}::{r['device']}": r for r in load(baseline_path, {}).get('records', [])}
    rows = []
    for record in records:
        key = f"{record['slug']}::{record['device']}"
        old = baseline.get(key)
        if not old:
            continue
        delta = {field: round((record[field] or 0) - (old[field] or 0), 5)
                 for field in ('score', 'overlapRate', 'clipRate', 'relationRate', 'reviewPerLayout')}
        regressions = [field for field, value in delta.items()
                       if (field == 'score' and value < -0.5) or (field != 'score' and value > 1e-6)]
        if regressions:
            rows.append({'key': key, 'delta': delta, 'regressions': regressions})
    return {'baseline': str(baseline_path), 'compared': len(records), 'regressions': rows}


def write_report(out, payload):
    summary = payload['summary']
    esc = html.escape

    def table(headers, rows, cls=''):
        head = ''.join(f'<th>{esc(str(h))}</th>' for h in headers)
        body = ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
        return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    stage_rows = [[esc(code), esc(item['title']), esc(item['stage']), item['cases'], item['devices'], item['themes']]
                  for item in summary['byCode'] for code in [item['code']]]
    slice_rows = [[esc(item['slice']), item['devices'], item['meanScore'], item['minScore'],
                   f"{item['meanOverlapRate']:.4f}", f"{item['meanClipRate']:.4f}",
                   item['reviewPerLayout'], item['overlapRegressions']]
                  for item in summary['byOrientation'] + summary['byAspect']]
    theme_rows = [[esc(item['slice']), item['devices'], item['meanScore'], item['minScore'],
                   item['overlapRegressions'], item['clipRegressions']]
                  for item in summary['byTheme']]
    case_rows = []
    for case in payload['cases'][:200]:
        evidence = esc(json.dumps(case['evidence'], ensure_ascii=False))
        case_rows.append([f"<span class='sev {case['severity']}'>{case['severity']}</span>",
                          esc(case['code']), esc(case['slug']), esc(case['device'] or ''),
                          esc(case['orientation']), f'<code>{evidence}</code>', esc(case['hint'])])
    hot_rows = [[esc(name), count] for name, count in payload['hotComponents'][:15]]
    page = f'''<!doctype html><meta charset="utf-8"><title>批量评测与 badcase 归因</title>
<style>
:root{{--bg:#f6f8fa;--card:#fff;--ink:#16202b;--muted:#5b6b7c;--line:#dbe3ea;--accent:#0f766e}}
*{{box-sizing:border-box}}body{{margin:0;padding:28px 32px;background:var(--bg);color:var(--ink);font:14px/1.55 "Segoe UI",system-ui,sans-serif}}
h1{{font-size:22px;margin:0 0 6px}}h2{{font-size:16px;margin:30px 0 10px}}
p.lead{{color:var(--muted);margin:0 0 20px}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:132px}}
.kpi b{{display:block;font-size:22px;line-height:1.2}}.kpi span{{color:var(--muted);font-size:12px}}
table{{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{background:#eef3f7;font-weight:600;color:#33465a;white-space:nowrap}}
code{{font:12px ui-monospace,Consolas,monospace;color:#0b4f4a;word-break:break-all}}
.sev{{display:inline-block;padding:1px 7px;border-radius:999px;color:#fff;font-size:11px;font-weight:600}}
.P0{{background:#b42318}}.P1{{background:#b54708}}.P2{{background:#475467}}
.note{{color:var(--muted);font-size:12px;margin:8px 0 0}}
</style>
<h1>批量评测与 badcase 归因</h1>
<p class="lead">数据源：{esc(payload['root'])} · 生成时间 {esc(payload['generatedAt'])} · 只读扫描，未重新布局</p>
<div class="kpis">
<div class="kpi"><b>{summary['projects']}</b><span>项目</span></div>
<div class="kpi"><b>{summary['devices']}</b><span>项目×设备</span></div>
<div class="kpi"><b>{summary['meanScore']}</b><span>美学代理均分</span></div>
<div class="kpi"><b>{summary['medianScore']}</b><span>中位数</span></div>
<div class="kpi"><b>{summary['badcases']}</b><span>badcase</span></div>
<div class="kpi"><b>{summary['severity'].get('P0', 0)}</b><span>P0</span></div>
<div class="kpi"><b>{summary['severity'].get('P1', 0)}</b><span>P1</span></div>
</div>
<h2>1. 归因分布</h2>
{table(['代码', '问题', '归属阶段', '命中', '设备', '主题'], stage_rows)}
<p class="note">同一设备可命中多条；命中数用于排序修复优先级，不等于缺陷数。</p>
<h2>2. 切片对比：方向与宽高比</h2>
{table(['切片', '设备', '均分', '最低分', '重叠率', '裁切率', '审查/布局', '重叠回归'], slice_rows)}
<p class="note">聚合均分会掩盖切片差异，任何改动都必须按切片看增量。</p>
<h2>3. 主题排名（升序）</h2>
{table(['主题', '设备', '均分', '最低分', '重叠回归', '裁切回归'], theme_rows)}
<h2>4. Badcase 明细（按严重度与风险指数）</h2>
{table(['级别', '代码', '主题', '设备', '方向', '证据', '建议定位'], case_rows)}
<p class="note">仅展示前 200 条，完整数据见 badcases.jsonl。</p>
<h2>5. 组件热点</h2>
{table(['组件', '审查命中'], hot_rows)}
<p class="note">跨主题高频出现的组件是规则缺口，而不是单主题问题。</p>
'''
    (out / 'report.html').write_text(page, encoding='utf-8')


def run(root, out, only=None):
    root = Path(root)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = sorted(root.glob('*/scenes/project.json'))
    if only:
        paths = [p for p in paths if any(name in str(p.parent.parent.name) for name in only)]
    records, cases, hot = [], [], collections.Counter()
    for path in paths:
        items, context = project_records(path)
        for item in items:
            item['riskIndex'] = risk_index(item)
            records.append(item)
            for case in attribute(item, context):
                case['riskIndex'] = item['riskIndex']
                cases.append(case)
        cases.extend(project_cases(context))
        _, components = review_counts(load(path) or {})
        hot.update(components)
    cases.sort(key=lambda case: (SEVERITY_ORDER.get(case['severity'], 9), -case.get('riskIndex', 0), case['code']))
    summary = aggregate(records, cases)
    payload = {'generatedAt': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
               'root': str(root.resolve()), 'summary': summary, 'records': records,
               'cases': cases, 'hotComponents': hot.most_common(50)}
    (out / 'scorecard.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'badcases.jsonl').write_text(
        '\n'.join(json.dumps(case, ensure_ascii=False) for case in cases), encoding='utf-8')
    (out / 'attribution.json').write_text(
        json.dumps({key: summary[key] for key in ('byCode', 'byStage', 'byOrientation', 'byAspect', 'byFamily', 'byTheme')},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    write_report(out, payload)
    return summary


def main():
    parser = argparse.ArgumentParser(description='批量评测、badcase 归因与回归预算；只读 theme/adaptation，写出 eval/')
    parser.add_argument('--root', type=Path, default=Path('theme/adaptation'))
    parser.add_argument('--out', type=Path, default=Path('eval'))
    parser.add_argument('--only', nargs='*', help='只评测 slug 包含这些字符串的项目')
    parser.add_argument('--compare', type=Path, help='与上一次 scorecard.json 比较，输出回归项')
    args = parser.parse_args()
    summary = run(args.root, args.out, args.only)
    if args.compare:
        payload = load(args.out / 'scorecard.json', {}) or {}
        result = compare(payload.get('records', []), args.compare)
        (args.out / 'regression.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"regressions: {len(result['regressions'])} / {result['compared']} devices")
    print(json.dumps({'projects': summary['projects'], 'devices': summary['devices'],
                      'meanScore': summary['meanScore'], 'badcases': summary['badcases'],
                      'severity': summary['severity'],
                      'topCodes': [(item['code'], item['cases']) for item in summary['byCode'][:6]]},
                     ensure_ascii=False))
    print(f'report: {(Path(args.out) / "report.html").resolve()}')


if __name__ == '__main__':
    main()
