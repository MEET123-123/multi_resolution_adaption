import type { Doc, Node, DeviceProfile, SemanticRole } from '../types.js';
import type { ResolvedNode } from './legacy.js';
import type { DesignGroup } from './types.js';
import { activeVariantKeys } from './legacy.js';
import { intersection, union } from './geometry.js';

function descendants(node: Node): Node[] {
  return [node, ...(node.children ?? []).flatMap(descendants)];
}

/** Structural signals only. Labels, theme names and product names are never rules. */
export function inferGroups(
  doc: Doc,
  source: ResolvedNode[],
  device: DeviceProfile,
): DesignGroup[] {
  const { w, h } = doc.meta.design;
  const keys = activeVariantKeys(device, doc.breakpoints);
  const roots = doc.root.filter((n) => source.some((s) => s.id === n.id && s.visible));
  const frames = new Map(source.map((n) => [n.id, n.rect]));
  const background = (n: Node): boolean =>
    n.responsive?.role === 'background' ||
    (n.kind === 'image' && n.frame.w >= w * 0.8 && n.frame.h >= h * 0.8);
  const buckets: Node[][] = [];
  for (const n of roots) {
    const matches = buckets.filter((bucket) =>
      bucket.some((other) => {
        if (n.responsive?.group && n.responsive.group === other.responsive?.group) return true;
        if (background(n) || background(other)) return false;
        if (n.responsive?.position === 'offscreen' || other.responsive?.position === 'offscreen')
          return false;
        const a = frames.get(n.id)!;
        const b = frames.get(other.id)!;
        const minArea = Math.min(a.w * a.h, b.w * b.h);
        const bothInteractive =
          ['button', 'unlocker'].includes(n.kind) && ['button', 'unlocker'].includes(other.kind);
        return !bothInteractive && minArea > 0 && intersection(a, b) / minArea > 0.6;
      }),
    );
    if (!matches.length) buckets.push([n]);
    else {
      const first = matches[0]!;
      first.push(n, ...matches.slice(1).flat());
      for (const item of matches.slice(1)) buckets.splice(buckets.indexOf(item), 1);
    }
  }
  return buckets.map((members) => {
    const bounds = union(members.map((n) => frames.get(n.id)!));
    const all = members.flatMap(descendants);
    const explicit = members.find((n) => n.responsive?.role)?.responsive?.role;
    const offscreen =
      bounds.x >= w || bounds.y >= h || bounds.x + bounds.w <= 0 || bounds.y + bounds.h <= 0;
    const footer = members.some((n) => n.constraints.v === 'bottom') || bounds.y > h * 0.78;
    let role: SemanticRole =
      explicit ??
      (members.some(background)
        ? 'background'
        : all.some((n) => n.kind === 'time' || n.kind === 'date')
          ? 'clock'
          : footer
            ? 'footer'
            : bounds.y < h * 0.1 && bounds.w < w * 0.5
              ? 'status'
              : members.some((n) => n.kind === 'image')
                ? 'hero'
                : 'content');
    if (members.some((n) => n.responsive?.position === 'overlay')) role = 'overlay';
    const cx = bounds.x + bounds.w / 2;
    const cy = bounds.y + bounds.h / 2;
    const explicitH = members[0]!.constraints.h;
    const explicitV = members[0]!.constraints.v;
    const hAnchor =
      explicitH === 'center' || Math.abs(cx - w / 2) <= w * 0.025
        ? 'center'
        : explicitH === 'right' || bounds.x > w * 0.65
          ? 'right'
          : explicitH === 'scale' || explicitH === 'leftRight'
            ? 'scale'
            : 'left';
    const vAnchor =
      explicitV === 'center' || Math.abs(cy - h / 2) <= h * 0.025
        ? 'center'
        : footer
          ? 'bottom'
          : explicitV === 'scale' || explicitV === 'topBottom'
            ? 'scale'
            : 'top';
    const manual = members.some((n) =>
      keys.some((k) => {
        const p = n.variants?.[k];
        return p !== undefined && Object.keys(p).length > 0;
      }),
    );
    return {
      id: `group:${members[0]!.id}`,
      members: members.map((n) => n.id),
      bounds,
      role,
      h: hAnchor,
      v: vAnchor,
      manual,
      confidence: explicit ? 1 : role === 'clock' || role === 'background' ? 0.95 : 0.7,
      position:
        members.find((n) => n.responsive?.position)?.responsive?.position ??
        (offscreen
          ? 'offscreen'
          : role === 'overlay'
            ? 'overlay'
            : role === 'status' || role === 'footer'
              ? 'fixed'
              : 'flow'),
      reasons: [
        explicit ? '作者指定语义' : '根据节点种类、层级、源框和边缘关系推断',
        ...(members.length > 1 ? ['源布局重叠的图像与热区保持统一变换'] : []),
        ...(manual ? ['目标断点已有显式变体，保留作者布局'] : []),
      ],
    };
  });
}
