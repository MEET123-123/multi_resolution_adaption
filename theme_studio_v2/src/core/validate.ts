import { flatten, type ResolvedNode, type ResolvedScene } from './solve.js';
import { densityOf } from './devices.js';
import { intersection, union } from './layout/geometry.js';

export type Severity = 'error' | 'warn' | 'info';

export interface Issue {
  device: string;
  nodeId: string;
  nodeName: string;
  code: string;
  severity: Severity;
  message: string;
}

const TOUCH_MIN_DP = 44;

function overlaps(a: ResolvedNode, b: ResolvedNode): boolean {
  const ax2 = a.rect.x + a.rect.w;
  const ay2 = a.rect.y + a.rect.h;
  const bx2 = b.rect.x + b.rect.w;
  const by2 = b.rect.y + b.rect.h;
  const w = Math.min(ax2, bx2) - Math.max(a.rect.x, b.rect.x);
  const h = Math.min(ay2, by2) - Math.max(a.rect.y, b.rect.y);
  if (w <= 0 || h <= 0) return false;
  return w * h > 0.12 * Math.min(a.rect.w * a.rect.h, b.rect.w * b.rect.h);
}

/**
 * Quality gates that mirror what a human reviewer checks on a lockscreen:
 * nothing clipped, nothing colliding, nothing too small to tap or read.
 */
export function validateScene(scene: ResolvedScene): Issue[] {
  const issues: Issue[] = [];
  const nodes = flatten(scene.nodes).filter((n) => n.visible);
  const { device } = scene;
  const density = densityOf(device);

  const groups = scene.explanation?.groups ?? [];
  for (let i = 0; i < groups.length; i++) {
    const a = groups[i]!;
    if (a.role === 'background' || a.position === 'overlay' || a.position === 'offscreen') continue;
    const aa = scene.nodes.filter((n) => a.members.includes(n.id) && n.visible).map((n) => n.rect);
    if (!aa.length) continue;
    for (const b of groups.slice(i + 1)) {
      if (b.role === 'background' || b.position === 'overlay' || b.position === 'offscreen')
        continue;
      const bb = scene.nodes
        .filter((n) => b.members.includes(n.id) && n.visible)
        .map((n) => n.rect);
      if (
        bb.length &&
        intersection(a.bounds, b.bounds) <= 1 &&
        intersection(union(aa), union(bb)) > 1
      ) {
        issues.push({
          device: device.id,
          nodeId: a.members[0]!,
          nodeName: a.id,
          code: 'new-group-overlap',
          severity: 'error',
          message: `与 ${b.id} 在适配后产生新的重叠`,
        });
      }
    }
  }

  for (const message of scene.warnings) {
    issues.push({
      device: device.id,
      nodeId: '',
      nodeName: '布局策略',
      code: 'layout-diagnostic',
      severity: message.includes('不可行') ? 'error' : 'warn',
      message,
    });
  }

  for (const n of nodes) {
    const base = { device: device.id, nodeId: n.id, nodeName: n.name };
    const r = n.rect;
    if (![r.x, r.y, r.w, r.h].every(Number.isFinite) || r.w < 0 || r.h < 0) {
      issues.push({
        ...base,
        code: 'invalid-geometry',
        severity: 'error',
        message: '求解结果含无效几何数值',
      });
      continue;
    }
    const group = scene.explanation?.groups.find((g) => g.id === n.groupId);
    if (group?.position === 'offscreen') continue;
    if (n.role !== 'background') {
      for (const feature of device.features ?? []) {
        if ((feature.occluding || feature.separating) && intersection(r, feature.bounds) > 0.5) {
          issues.push({
            ...base,
            code: 'display-feature-overlap',
            severity: 'error',
            message: `与${feature.kind}区域相交`,
          });
        }
      }
      for (const axis of ['W', 'H'] as const) {
        const value = (axis === 'W' ? r.w : r.h) / density;
        const min = n.constraints?.[`min${axis}`];
        const max = n.constraints?.[`max${axis}`];
        if ((min !== undefined && value < min - 0.5) || (max !== undefined && value > max + 0.5)) {
          issues.push({
            ...base,
            code: 'explicit-size-constraint',
            severity: 'error',
            message: `${axis}=${value.toFixed(1)}dp 不满足作者指定的最小/最大尺寸`,
          });
        }
      }
    }

    if (
      r.x < -0.5 ||
      r.y < -0.5 ||
      r.x + r.w > device.width + 0.5 ||
      r.y + r.h > device.height + 0.5
    ) {
      issues.push({
        ...base,
        code: 'out-of-canvas',
        severity: 'error',
        message: `超出画布：x=${Math.round(r.x)} y=${Math.round(r.y)} w=${Math.round(r.w)} h=${Math.round(r.h)}（画布 ${device.width}×${device.height}）`,
      });
    }

    if (n.text) {
      const px = n.text.effectiveSize;
      if (px / density < 11) {
        issues.push({
          ...base,
          code: 'text-too-small',
          severity: 'warn',
          message: `字号 ${px.toFixed(0)}px ≈ ${(px / density).toFixed(1)}dp，低于可读下限 11dp`,
        });
      }
      if (n.text.overflow) {
        issues.push({
          ...base,
          code: 'text-overflow',
          severity: 'error',
          message: `文字在 ${n.text.maxLines} 行内放不下（已缩到 ${px.toFixed(0)}px）`,
        });
      }
    }

    if (n.kind === 'unlocker' || n.kind === 'button') {
      const minPx = TOUCH_MIN_DP * density;
      if (r.h < minPx || r.w < minPx) {
        issues.push({
          ...base,
          code: 'touch-too-small',
          severity: 'warn',
          message: `点击区高度 ${r.h.toFixed(0)}px ≈ ${(r.h / density).toFixed(1)}dp，低于 ${TOUCH_MIN_DP}dp`,
        });
      }
    }
  }

  // Interactive controls must never sit on top of each other: the unlock area
  // swallows taps meant for the camera button when they overlap.
  const interactive = nodes.filter((n) => n.kind === 'unlocker' || n.kind === 'button');
  for (let i = 0; i < interactive.length; i += 1) {
    for (let j = i + 1; j < interactive.length; j += 1) {
      const a = interactive[i]!;
      const b = interactive[j]!;
      if (a.kind === b.kind && a.kind === 'unlocker') continue;
      if (overlaps(a, b)) {
        issues.push({
          device: device.id,
          nodeId: a.id,
          nodeName: a.name,
          code: 'control-overlap',
          severity: 'error',
          message: `与「${b.name}」的点击区重叠，会抢走对方的触摸事件`,
        });
      }
    }
  }

  // Text blocks colliding with each other is the classic multi-resolution bug:
  // at design size they are fine, on a tall screen the clock grows into the date.
  const texts = nodes.filter((n) => n.text !== undefined && n.text.content.trim() !== '');
  for (let i = 0; i < texts.length; i += 1) {
    for (let j = i + 1; j < texts.length; j += 1) {
      if (overlaps(texts[i]!, texts[j]!)) {
        issues.push({
          device: device.id,
          nodeId: texts[i]!.id,
          nodeName: texts[i]!.name,
          code: 'text-collision',
          severity: 'warn',
          message: `与「${texts[j]!.name}」重叠超过 12%`,
        });
      }
    }
  }

  return issues;
}

export function summarize(issues: Issue[]): { errors: number; warns: number; infos: number } {
  return {
    errors: issues.filter((i) => i.severity === 'error').length,
    warns: issues.filter((i) => i.severity === 'warn').length,
    infos: issues.filter((i) => i.severity === 'info').length,
  };
}
