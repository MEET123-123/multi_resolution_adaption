import type { DeviceProfile, Doc, Frame } from '../types.js';
import type { ResolvedNode, ResolvedScene, SolveOptions } from './legacy.js';
import { resolveLegacyDoc } from './legacy.js';
import { inferGroups } from './infer.js';
import { SemanticPlanner, type LayoutPlanner } from './planner.js';
import { contains, intersection, usableRegions } from './geometry.js';
import { densityOf } from '../devices.js';
import type { LayoutExplanation } from './types.js';

function transform(
  node: ResolvedNode,
  from: Frame,
  to: Frame,
  scale: number,
  groupId: string,
  visible = true,
): ResolvedNode {
  return {
    ...node,
    groupId,
    visible: visible && node.visible,
    rect: {
      x: to.x + (node.rect.x - from.x) * scale,
      y: to.y + (node.rect.y - from.y) * scale,
      w: node.rect.w * scale,
      h: node.rect.h * scale,
    },
    text: node.text
      ? {
          ...node.text,
          effectiveSize: node.text.effectiveSize * scale,
          letterSpacing: node.text.letterSpacing * scale,
        }
      : undefined,
    radius: node.radius !== undefined ? node.radius * scale : undefined,
    children: node.children.map((child) =>
      transform(child, from, to, scale, groupId, visible && node.visible),
    ),
  };
}

/** Validate external optimizer/AI proposals before consuming any coordinates. */
function validatePlan(plan: LayoutExplanation): void {
  const ids = new Set(plan.groups.map((g) => g.id));
  const seen = new Set<string>();
  for (const p of plan.placements) {
    if (!ids.has(p.group) || seen.has(p.group))
      throw new Error(`布局策略返回未知或重复的组件组：${p.group}`);
    seen.add(p.group);
    const g = plan.groups.find((item) => item.id === p.group)!;
    if (g.manual || g.position === 'offscreen' || g.role === 'background')
      throw new Error(`布局策略试图覆盖受保护组件组：${p.group}`);
    if (
      ![p.scale, ...Object.values(p.target)].every(Number.isFinite) ||
      p.scale <= 0 ||
      p.target.w <= 0 ||
      p.target.h <= 0
    )
      throw new Error('布局策略返回非有限数或非正尺寸');
    if (
      Math.abs(p.target.w - g.bounds.w * p.scale) > 0.05 ||
      Math.abs(p.target.h - g.bounds.h * p.scale) > 0.05
    )
      throw new Error(`组件组比例被破坏：${p.group}`);
    if (!plan.regions.some((r) => contains(r, p.target)))
      throw new Error(`组件组超出安全区或跨越障碍：${p.group}`);
  }
  for (const g of plan.groups) {
    if (!g.manual && g.position !== 'offscreen' && g.role !== 'background' && !seen.has(g.id))
      throw new Error(`布局策略遗漏组件组：${g.id}`);
  }
  for (let i = 0; i < plan.placements.length; i++) {
    for (const b of plan.placements.slice(i + 1)) {
      const a = plan.placements[i]!;
      const ga = plan.groups.find((g) => g.id === a.group)!;
      const gb = plan.groups.find((g) => g.id === b.group)!;
      if (ga.position === 'overlay' || gb.position === 'overlay') continue;
      if (intersection(a.target, b.target) > 1 && intersection(ga.bounds, gb.bounds) <= 1)
        throw new Error(`重排引入组件组重叠：${a.group} / ${b.group}`);
    }
  }
}

export function resolveAdaptiveDoc(
  doc: Doc,
  device: DeviceProfile,
  options: SolveOptions,
  planner: LayoutPlanner = new SemanticPlanner(),
): ResolvedScene {
  const designDevice: DeviceProfile = {
    id: '_design',
    label: '设计参考画布',
    short: 'design',
    formFactor: 'custom',
    width: doc.meta.design.w,
    height: doc.meta.design.h,
    widthDp: doc.meta.designWidthDp,
    insets: { top: 0, right: 0, bottom: 0, left: 0 },
  };
  const source = resolveLegacyDoc(doc, designDevice, { ignoreVariants: true });
  const baseline = resolveLegacyDoc(doc, device, options);
  const groups = inferGroups(doc, source.nodes, device);
  const request = {
    doc: structuredClone(doc),
    device: structuredClone(device),
    groups: structuredClone(groups),
  };
  const proposal = planner.plan(request);
  const plan = {
    ...proposal,
    groups,
    regions: usableRegions(device, (doc.layout?.marginDp ?? 16) * densityOf(device)),
  };
  let rejected: string | undefined;
  try {
    validatePlan(plan);
  } catch (error) {
    if (!(planner instanceof SemanticPlanner)) throw error;
    rejected = (error as Error).message;
  }
  if (rejected) {
    return {
      ...baseline,
      explanation: {
        ...plan,
        placements: [],
        warnings: [...plan.warnings, `自动布局被拒绝，保留约束布局：${rejected}`],
      },
      warnings: [`自动布局不可行：${rejected}`],
    };
  }
  const base = new Map(baseline.nodes.map((n) => [n.id, n]));
  const placements = new Map(plan.placements.map((p) => [p.group, p]));
  const byMember = new Map(groups.flatMap((g) => g.members.map((id) => [id, g] as const)));
  const nodes = source.nodes.map((node) => {
    const g = byMember.get(node.id);
    if (!g) return base.get(node.id) ?? node;
    if (g.manual) return { ...base.get(node.id)!, role: g.role, groupId: g.id };
    if (g.role === 'background')
      return {
        ...node,
        role: g.role,
        groupId: g.id,
        rect: { x: 0, y: 0, w: device.width, h: device.height },
        image: node.image ? { ...node.image, scaleType: 'centerCrop' as const } : undefined,
      };
    const p = placements.get(g.id);
    if (!p) return { ...base.get(node.id)!, role: g.role, groupId: g.id };
    return { ...transform(node, g.bounds, p.target, p.scale, g.id), role: g.role };
  });
  return { ...baseline, nodes, explanation: plan, warnings: plan.warnings };
}
