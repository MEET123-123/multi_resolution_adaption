"""Portable evidence and a local, script-free HTML review queue."""
import csv
import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, unquote
from .core import write_json, digest


def elements(project, layout):
    from multires_app.pipeline.scene_clusters import transformed_box, IDENTITY
    result = []
    for index, row in enumerate(layout['rows']):
        uid = project['nodeOrder'][row[0]]
        node = project['model']['nodes'][uid]
        if node['tag'] == 'Group' or row[5] <= 0:
            continue
        cid = project['nodeComponents'].get(uid)
        t = layout.get('transforms', {}).get(cid, IDENTITY)
        asset = project['assetOrder'][row[6]] if row[6] >= 0 else ''
        meta = project['assets'].get(asset, {})
        # The renderer takes cache paths, not untrusted archive member paths.
        cached = unquote(urlsplit(meta.get('url', '')).path).removeprefix('assets/')
        result.append({'key': uid + '@' + str(index), 'id': uid, 'tag': node['tag'], 'visible': True,
            'rect': transformed_box(dict(zip(('x', 'y', 'w', 'h'), row[1:5])), t),
            'asset': cached, 'text': row[7], 'size': row[8] * t['sy'], 'alpha': row[5],
            'color': node.get('attributes', {}).get('color', '#ffffff'),
            'scaleType': node.get('attributes', {}).get('scaleType', '')})
    return result


def evidence(ctx, row, directory):
    from PIL import ImageDraw
    from multires_app.preview.paint_visibility import raster_snapshot, read_bitmap
    from multires_app.pipeline.scene_clusters import box_union, transformed_box
    p = ctx.project
    d = next(d for d in p['devices'] if d['profile']['id'] == row['device'])
    l = next(l for l in d['layouts'] if l['state'] == row['state'])
    source = next(l for l in p['sourceDevice']['layouts'] if l['state'] == row['state'])
    directory.mkdir(parents=True, exist_ok=True)
    renders = {}
    asset_metadata = {unquote(urlsplit(m.get('url', '')).path).removeprefix('assets/'): m for m in p['assets'].values()}
    # The legacy painter caches full decoded images. A count-only LRU can grow
    # to gigabytes across many themes; an evaluation case has a pixel budget.
    read_bitmap.cache_clear()
    for label, device, layout in [('source', p['sourceDevice'], source), ('target', d, l)]:
        selected, omitted = [], []
        source_pixels, layer_pixels = 0, 0
        vp = device['viewport']
        scale = min(1280/vp['width'], 800/vp['height'])
        import math
        for e in elements(p, layout):
            b = e['rect']
            if not all(math.isfinite(v) for v in b.values()) or min(b['w'], b['h']) < 0:
                omitted.append({'id': e['id'], 'reason': 'invalid geometry'})
                continue
            # Outside items never paint and need no image decode.
            if b['x'] >= vp['width'] or b['y'] >= vp['height'] or b['x']+b['w'] <= 0 or b['y']+b['h'] <= 0:
                continue
            area = b['w'] * b['h'] * scale * scale
            asset = asset_metadata.get(e['asset'], {})
            pixels = asset.get('width', 0) * asset.get('height', 0)
            if area > 8_000_000 or pixels > 8_000_000 or source_pixels+pixels > 24_000_000 or layer_pixels+area > 16_000_000:
                omitted.append({'id': e['id'], 'reason': 'render pixel budget exceeded', 'sourcePixels': pixels, 'projectedPixels': area})
                continue
            source_pixels += pixels
            layer_pixels += area
            selected.append(e)
        image, paint = raster_snapshot(selected, vp, ctx.path.parent / 'assets')
        image.save(directory / (label + '.png'))
        renders[label] = {'unsupported': sum(not r['supported'] for r in paint.values()),
                          'paint': paint, 'omitted': omitted, 'status': 'unknown' if omitted or any(not r['supported'] for r in paint.values()) else 'simulated',
                          'scope': 'offline renderer; not native evidence'}
        read_bitmap.cache_clear()
        if label == 'target':
            overlay = image.copy()
            boxes = ctx.geometry(layout)
            members = set(row['members'])
            for failure in row['evidence'].get('failures', []):
                if isinstance(failure, dict):
                    members.update(failure[k] for k in ('component', 'first', 'last') if failure.get(k) in boxes)
            selected = [transformed_box(boxes[c], layout['transforms'][c]) for c in members
                        if c in boxes and c in layout['transforms']]
            scale = image.width / device['viewport']['width']
            draw = ImageDraw.Draw(overlay)
            for b in selected:
                draw.rectangle([b['x']*scale, b['y']*scale, (b['x']+b['w'])*scale, (b['y']+b['h'])*scale], outline='#ff4655', width=3)
            if row['rule'] == 'GEO.GROUP_CENTER':
                if row['axis'] == 'x':
                    draw.line([image.width/2, 0, image.width/2, image.height], fill='#45d6ce', width=2)
                else:
                    draw.line([0, image.height/2, image.width, image.height/2], fill='#45d6ce', width=2)
            overlay.save(directory / 'overlay.png')
    write_json(directory / 'evidence.json', {'observation': row, 'rendering': renders})
    write_json(directory / 'layout.json', {'source': source, 'target': l})
    return {'source': 'cases/' + row['id'] + '/source.png', 'target': 'cases/' + row['id'] + '/target.png',
            'overlay': 'cases/' + row['id'] + '/overlay.png', 'evidence': 'cases/' + row['id'] + '/evidence.json'}


def build_report(out, records, manifest):
    groups = defaultdict(list)
    counts = Counter()
    total = 0
    traces = {}
    import sqlite3
    import uuid
    index_path = out / ('cases-index-' + uuid.uuid4().hex + '.sqlite3')
    index = sqlite3.connect(index_path)
    index.execute('CREATE TABLE cases (id TEXT PRIMARY KEY, offset INTEGER NOT NULL)')
    offsets = []
    with (out / 'observations.jsonl').open('w', encoding='utf8') as observations, (out / 'badcases.jsonl').open('w', encoding='utf8') as badcases, (out / 'metrics.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        fields = ['id', 'project', 'device', 'state', 'rule', 'status', 'severity', 'confidence', 'residual']
        writer = csv.DictWriter(stream, fields, extrasaction='ignore')
        writer.writeheader()
        for row in records:
            counts[row['status']] += 1
            total += 1
            trace = row.get('trace', [])
            trace_id = digest(trace)
            traces[trace_id] = trace
            # Shared reproduction data is stored once, not once per passing
            # component. This avoids multi-gigabyte decoded batch reports.
            saved = {**row, 'traceRef': trace_id, 'trace': trace if row['status'] in {'fail', 'review', 'unknown'} else []}
            line = json.dumps(saved, ensure_ascii=False, allow_nan=False)
            offsets.append((row['id'], observations.tell()))
            observations.write(line + '\n')
            if len(offsets) >= 1000:
                index.executemany('INSERT OR REPLACE INTO cases VALUES (?, ?)', offsets)
                offsets.clear()
            writer.writerow(row)
            if row['status'] in {'fail', 'review', 'unknown'}:
                badcases.write(line + '\n')
                light = {k: row[k] for k in ('id', 'project', 'rule', 'status', 'severity', 'confidence', 'residual')}
                groups[row['cluster']].append(light)
    write_json(out / 'repro.json', {'traces': traces})
    index.executemany('INSERT OR REPLACE INTO cases VALUES (?, ?)', offsets)
    index.execute('CREATE TABLE metadata (size INTEGER, mtime INTEGER)')
    stat = (out / 'observations.jsonl').stat()
    index.execute('INSERT INTO metadata VALUES (?, ?)', (stat.st_size, stat.st_mtime_ns))
    index.commit()
    index.close()
    index_path.replace(out / 'cases.sqlite3')
    # The remaining passes read the compact JSONL, keeping only cluster keys
    # and a bounded HTML queue in memory.
    from .workflow import iter_records
    clusters = []
    for cid, rows in groups.items():
        ranked = sorted(rows, key=lambda r: (r['status'] != 'fail', r['severity'], -r['confidence'], -r['residual'], r['id']))
        representatives = [ranked[0]]
        themes = {ranked[0]['project']}
        for row in ranked[1:]:
            if row['project'] not in themes and len(representatives) < 3:
                representatives.append(row)
                themes.add(row['project'])
        clusters.append({'id': cid, 'rule': ranked[0]['rule'], 'rootCauseStatus': 'suspected',
            'instances': len(rows), 'themes': len({r['project'] for r in rows}),
            'representatives': [r['id'] for r in representatives], 'caseIds': sorted(r['id'] for r in rows)})
    clusters.sort(key=lambda c: (-c['themes'], -c['instances'], c['id']))
    write_json(out / 'clusters.json', clusters)
    write_json(out / 'manifest.json', manifest)
    summary = {'schema': manifest['schema'], 'checks': total, 'statuses': dict(counts),
        'clusters': len(clusters), 'nativeDevicePassRate': 'not_measured', 'precision': 'not_measured',
        'recall': 'not_measured', 'f1': 'not_measured', 'sourceUnchanged': manifest.get('sourceUnchanged'),
        'scope': 'offline geometry, relation and simulated interaction evidence'}
    write_json(out / 'summary.json', summary)
    import heapq
    light_rows = (r for rows in groups.values() for r in rows)
    selected = heapq.nsmallest(500, light_rows, key=lambda r: (r['status'] != 'fail', r['severity'], -r['confidence'], r['id']))
    representative_ids = list(dict.fromkeys(r for c in clusters for r in c['representatives']))[:500]
    wanted = list(dict.fromkeys([*representative_ids, *(r['id'] for r in selected)]))[:500]
    ranks = {case: i for i, case in enumerate(wanted)}
    cases = [r for r in iter_records(out) if r['id'] in ranks]
    esc = html.escape
    case_rows = []
    for row in sorted(cases, key=lambda r: ranks[r['id']]):
        artifacts = row.get('artifacts', {})
        images = ''.join(f'<a href="{esc(artifacts[k])}"><img width="160" loading="lazy" src="{esc(artifacts[k])}"></a>'
                         for k in ('source', 'overlay') if k in artifacts)
        case_rows.append('<tr><td>' + esc(row['status'] + ' / ' + row['severity']) + '</td><td>' +
            esc(row['project'] + '\n' + row['device'] + '\n' + row['state']) + '</td><td>' +
            esc(row['rule']) + '<br><code>' + esc(row['id']) + '</code></td><td>' + images +
            '</td><td><details><summary>证据与复现操作</summary><pre>' + esc(json.dumps({'trace': row['trace'],
            'evidence': row['evidence']}, ensure_ascii=False, indent=2)) + '</pre></details></td></tr>')
    body = '<!doctype html><meta charset="utf-8"><title>适配反馈闭环</title><style>body{font:14px/1.6 system-ui;padding:24px;color:#183044;background:#f5f7fa}table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;border:1px solid #dbe3ed;vertical-align:top;text-align:left;white-space:pre-line}pre{white-space:pre-wrap;max-width:620px}img{margin:4px}</style>'
    body += '<h1>适配反馈闭环</h1><p>FAIL：确定失败 · REVIEW：待复核 · UNKNOWN：证据不足。原生设备验证：not_measured。</p>'
    body += '<p>' + esc(json.dumps(summary, ensure_ascii=False)) + '</p><p>展示前 500 项，完整结果见 JSONL/CSV。标注可通过 feedback-loop review 保存；固定回归通过 freeze 导出。原图与目标图为离线模拟，跨分辨率不做整图像素差。</p>'
    body += '<table><tr><th>判定</th><th>主题 / 设备 / 状态</th><th>规则 / Case ID</th><th>原始 / 标注目标</th><th>证据</th></tr>' + ''.join(case_rows) + '</table>'
    (out / 'report.html').write_text(body, encoding='utf8')
    return summary
