/** Runtime boundary for JSON imports and plugin calls. Types alone do not validate files. */
import type { DeviceProfile, Doc, Node } from './types.js';

export function safeFilename(name: string): string {
  return (
    name
      .replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')
      .replace(/^\.+|[. ]+$/g, '')
      .slice(0, 80) || 'theme'
  );
}

function finite(value: unknown, label: string, positive = false): asserts value is number {
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    Math.abs(value) > 1e7 ||
    (positive && value <= 0)
  )
    throw new Error(`${label} 必须为${positive ? '正' : '有限'}数值`);
}

export function safeAssetPath(path: string): string {
  if (
    !path ||
    path.includes('\\') ||
    path.startsWith('/') ||
    path.includes(':') ||
    path.split('/').some((p) => p === '.' || p === '..' || p === '') ||
    /[\x00-\x1f]/.test(path)
  )
    throw new Error(`资源路径无效：${path}`);
  return path;
}

export function assertDevice(value: unknown): asserts value is DeviceProfile {
  const d = value as DeviceProfile;
  if (
    !d ||
    typeof d.id !== 'string' ||
    !/^[a-zA-Z0-9_-]{1,80}$/.test(d.id) ||
    typeof d.label !== 'string' ||
    !d.insets
  )
    throw new Error('设备需要有效 id、label 和 insets');
  for (const k of ['width', 'height', 'widthDp'] as const) finite(d[k], `device.${k}`, true);
  for (const k of ['left', 'right', 'top', 'bottom'] as const) {
    finite(d.insets[k], `insets.${k}`);
    if (d.insets[k] < 0) throw new Error('安全区不能为负');
  }
  if (d.insets.left + d.insets.right >= d.width || d.insets.top + d.insets.bottom >= d.height)
    throw new Error('安全区必须留有可用空间');
  for (const f of d.features ?? []) {
    if (!['hinge', 'cutout', 'reserved'].includes(f.kind) || !f.bounds)
      throw new Error('设备障碍格式无效');
    for (const k of ['x', 'y', 'w', 'h'] as const) finite(f.bounds[k], `feature.${k}`);
    if (
      f.bounds.w < 0 ||
      f.bounds.h < 0 ||
      f.bounds.x < 0 ||
      f.bounds.y < 0 ||
      f.bounds.x + f.bounds.w > d.width ||
      f.bounds.y + f.bounds.h > d.height
    )
      throw new Error('设备障碍必须位于画布内');
  }
}

export function assertDocument(value: unknown): asserts value is Doc {
  const d = value as Doc;
  if (
    !d ||
    d.version !== 2 ||
    !d.meta ||
    !d.meta.design ||
    !Array.isArray(d.root) ||
    typeof d.meta.name !== 'string'
  )
    throw new Error('不是有效的 v2 设计文档');
  for (const n of [d.meta.design.w, d.meta.design.h, d.meta.designWidthDp, d.meta.frameRate])
    finite(n, '设计画布/密度/帧率', true);
  if (d.layout) {
    if (!['auto', 'constraints'].includes(d.layout.mode)) throw new Error('未知布局模式');
    if (
      d.layout.composition &&
      !['auto', 'preserve', 'stack', 'split', 'grid', 'focus-dock'].includes(d.layout.composition)
    )
      throw new Error('未知构图策略');
    for (const n of [d.layout.gapDp, d.layout.marginDp, d.layout.maxScale])
      if (n !== undefined) finite(n, '布局参数');
    if ((d.layout.gapDp ?? 0) < 0 || (d.layout.marginDp ?? 0) < 0 || (d.layout.maxScale ?? 1) <= 0)
      throw new Error('布局间距不能为负，最大缩放必须为正');
  }
  const ids = new Set<string>();
  const objects = new Set<Node>();
  const visit = (n: Node, depth: number): void => {
    if (!n || depth > 32 || ids.size >= 4096 || objects.has(n))
      throw new Error('节点层级过深、数量过大或包含循环引用');
    objects.add(n);
    if (typeof n.id !== 'string' || !n.id || ids.has(n.id) || typeof n.name !== 'string')
      throw new Error('节点 id 必须唯一，且必须有名称');
    ids.add(n.id);
    if (
      !['group', 'image', 'text', 'rect', 'time', 'date', 'unlocker', 'button'].includes(n.kind) ||
      !n.frame ||
      !n.constraints ||
      !n.sizing
    )
      throw new Error(`${n.id} 节点缺少类型、几何或约束`);
    for (const k of ['x', 'y', 'w', 'h'] as const)
      finite(n.frame[k], `${n.id}.${k}`, k === 'w' || k === 'h');
    if (
      !['left', 'right', 'leftRight', 'center', 'scale'].includes(n.constraints.h) ||
      !['top', 'bottom', 'topBottom', 'center', 'scale'].includes(n.constraints.v)
    )
      throw new Error(`${n.id} 锚点无效`);
    if (
      !['fixed', 'fill', 'hug'].includes(n.sizing.w) ||
      !['fixed', 'fill', 'hug'].includes(n.sizing.h)
    )
      throw new Error(`${n.id} 尺寸策略无效`);
    for (const axis of ['W', 'H'] as const) {
      const min = n.constraints[`min${axis}`];
      const max = n.constraints[`max${axis}`];
      if (min !== undefined) finite(min, `${n.id}.min${axis}`, true);
      if (max !== undefined) finite(max, `${n.id}.max${axis}`, true);
      if (min !== undefined && max !== undefined && min > max)
        throw new Error(`${n.id} 最小尺寸大于最大尺寸`);
    }
    if (n.text) {
      if (typeof n.text.content !== 'string') throw new Error('文字内容必须是字符串');
      finite(n.text.size, '字号', true);
      finite(n.text.lineHeight, '行高', true);
      finite(n.text.maxLines, '行数', true);
    }
    if (n.autoLayout) {
      if (
        !['row', 'col'].includes(n.autoLayout.dir) ||
        !Array.isArray(n.autoLayout.pad) ||
        n.autoLayout.pad.length !== 4
      )
        throw new Error('自动布局需要方向和四个内边距');
      for (const v of [...n.autoLayout.pad, n.autoLayout.gap]) {
        finite(v, '自动布局间距');
        if (v < 0) throw new Error('自动布局间距不能为负');
      }
    }
    if (n.image?.src) safeAssetPath(n.image.src);
    for (const [key, p] of Object.entries(n.variants ?? {})) {
      if (
        !/^(compact|medium|expanded)(-(landscape|square))?$/.test(key) ||
        !p ||
        typeof p !== 'object' ||
        Array.isArray(p) ||
        p.variants
      )
        throw new Error(`${n.id} 的断点变体无效`);
      for (const [axis, v] of Object.entries(p.frame ?? {})) {
        if (!['x', 'y', 'w', 'h'].includes(axis)) throw new Error('未知变体几何字段');
        finite(v, `${n.id}.${key}.${axis}`, axis === 'w' || axis === 'h');
      }
      if (
        p.constraints?.h &&
        !['left', 'right', 'leftRight', 'center', 'scale'].includes(p.constraints.h)
      )
        throw new Error('变体水平锚点无效');
      if (
        p.constraints?.v &&
        !['top', 'bottom', 'topBottom', 'center', 'scale'].includes(p.constraints.v)
      )
        throw new Error('变体竖直锚点无效');
      for (const [k, v] of Object.entries(p.constraints ?? {}))
        if (!['h', 'v'].includes(k) && v !== null) finite(v, `变体约束.${k}`, true);
      if (p.image?.src) safeAssetPath(p.image.src);
      if (p.text?.size !== undefined) finite(p.text.size, '变体字号', true);
      if (p.text?.content !== undefined && typeof p.text.content !== 'string')
        throw new Error('变体文字无效');
      if (p.autoLayout?.pad) {
        if (!Array.isArray(p.autoLayout.pad) || p.autoLayout.pad.length !== 4)
          throw new Error('变体内边距需要四个数值');
        p.autoLayout.pad.forEach((v) => finite(v, '变体内边距'));
      }
    }
    if (n.children !== undefined && !Array.isArray(n.children))
      throw new Error('children 必须为数组');
    n.children?.forEach((c) => visit(c, depth + 1));
  };
  d.root.forEach((n) => visit(n, 0));
  if (d.devices && (!Array.isArray(d.devices) || d.devices.length > 64))
    throw new Error('设备列表最多 64 项');
  d.devices?.forEach(assertDevice);
  if (new Set(d.devices?.map((device) => device.id)).size !== (d.devices?.length ?? 0))
    throw new Error('设备 id 重复');
  const paths = new Set<string>();
  let bytes = 0;
  for (const a of d.assets ?? []) {
    const path = safeAssetPath(a.path);
    if (
      paths.has(path) ||
      typeof a.base64 !== 'string' ||
      !/^[A-Za-z0-9+/]*={0,2}$/.test(a.base64) ||
      a.base64.length % 4
    )
      throw new Error('素材路径重复或 base64 无效');
    if (typeof a.mime !== 'string' || !/^image\/(png|jpeg|webp)$/.test(a.mime))
      throw new Error('MVP 素材仅支持 PNG/JPEG/WebP');
    paths.add(path);
    bytes += a.base64.length * 0.75;
  }
  if (bytes > 100 * 1024 * 1024) throw new Error('设计内嵌素材超过 100MB');
}

export function parseDocument(text: string): Doc {
  const doc: unknown = JSON.parse(text);
  assertDocument(doc);
  return doc;
}
