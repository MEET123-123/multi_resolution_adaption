import type { Composition, DeviceProfile, Doc, Frame } from '../types.js';
import type { DesignGroup, GroupPlacement, LayoutExplanation } from './types.js';
import { densityOf, shapeOf } from '../devices.js';
import { contains, intersection, usableRegions } from './geometry.js';

export interface PlannerInput {
  doc: Doc;
  device: DeviceProfile;
  groups: DesignGroup[];
}
/** Replace this interface to connect an optimizer or an AI planner. IDs remain stable. */
export interface LayoutPlanner {
  name: string;
  plan(input: PlannerInput): LayoutExplanation;
}

function centered(g: DesignGroup, box: Frame, limit: number, reason: string): GroupPlacement {
  const scale = Math.max(
    0.0001,
    Math.min(limit, box.w / Math.max(1, g.bounds.w), box.h / Math.max(1, g.bounds.h)),
  );
  const w = g.bounds.w * scale;
  const h = g.bounds.h * scale;
  return {
    group: g.id,
    source: g.bounds,
    target: { x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h },
    scale,
    reason,
  };
}

/** Pack semantic groups without separating their children, images and hit areas. */
function pack(
  groups: DesignGroup[],
  region: Frame,
  gap: number,
  limit: number,
  grid: boolean,
): GroupPlacement[] {
  if (!groups.length) return [];
  const cols = grid
    ? Math.max(1, Math.min(groups.length, Math.floor(region.w / Math.max(1, region.h / 2))))
    : 1;
  const rows = Math.ceil(groups.length / cols);
  const width = Math.max(1, (region.w - gap * (cols - 1)) / cols);
  const height = Math.max(1, (region.h - gap * (rows - 1)) / rows);
  if (cols === 1) {
    // Preserve the relative visual mass of source groups; equal-height cells
    // make a date/clock tiny next to a full notification card.
    const total = groups.reduce((s, g) => s + g.bounds.h, 0);
    const available = Math.max(1, region.h - gap * (groups.length - 1));
    const common = Math.min(
      limit,
      available / Math.max(1, total),
      ...groups.map((g) => region.w / Math.max(1, g.bounds.w)),
    );
    const totalH = total * common + gap * (groups.length - 1);
    let y = region.y + Math.max(0, (region.h - totalH) / 2);
    return groups.map((g) => {
      const result = centered(
        g,
        { x: region.x, y, w: region.w, h: g.bounds.h * common },
        common,
        '组内刚性缩放；纵向间距随可用高度调整',
      );
      y += result.target.h + gap;
      return result;
    });
  }
  return groups.map((g, i) =>
    centered(
      g,
      {
        x: region.x + (i % cols) * (width + gap),
        y: region.y + Math.floor(i / cols) * (height + gap),
        w: width,
        h: height,
      },
      limit,
      '可用栅格内按阅读顺序放置组件组',
    ),
  );
}

export class SemanticPlanner implements LayoutPlanner {
  name = 'semantic-groups-v1';

  plan({ doc, device, groups }: PlannerInput): LayoutExplanation {
    const density = densityOf(device);
    const unit = density / (doc.meta.design.w / doc.meta.designWidthDp);
    const margin = (doc.layout?.marginDp ?? 16) * density;
    const gap = (doc.layout?.gapDp ?? 12) * density;
    const limit = unit * (doc.layout?.maxScale ?? 1.35);
    const regions = usableRegions(device, margin);
    const warnings: string[] = [];
    const placements: GroupPlacement[] = [];
    let composition: Composition = doc.layout?.composition ?? 'auto';
    if (composition === 'auto') {
      composition =
        regions.length > 1
          ? 'split'
          : shapeOf(device) === 'landscape' && device.widthDp >= 600
            ? 'split'
            : device.widthDp >= 840 && groups.filter((g) => g.role === 'content').length >= 3
              ? 'grid'
              : 'stack';
    }
    if (!regions.length)
      return {
        algorithm: this.name,
        composition,
        groups,
        regions,
        placements,
        warnings: ['安全区与排除区域没有可用空间'],
      };
    const safe = regions.reduce((a, b) => (a.w * a.h >= b.w * b.h ? a : b));
    const source = doc.meta.design;
    const fixed = groups.filter((g) => g.position === 'fixed' && !g.manual);
    for (const g of fixed) {
      // On a book/tabletop posture a control belongs to one physical pane.
      const pane = regions.length > 1 ? regions[g.h === 'right' ? regions.length - 1 : 0]! : safe;
      const p = centered(
        g,
        pane,
        Math.min(limit, unit),
        '保持源布局的居中或边缘锚点；短画布优先压缩组外留白',
      );
      const b = g.bounds;
      const edgeGapScale = Math.min(p.scale, device.width / source.w, device.height / source.h);
      const left = b.x * edgeGapScale;
      const right = (source.w - b.x - b.w) * edgeGapScale;
      // Compact height consumes elastic whitespace before shrinking artwork.
      // Keep all members of a group rigid; only its external top/bottom gap changes.
      const verticalGapScale = Math.min(p.scale, device.height / source.h);
      const top = b.y * verticalGapScale;
      const bottom = (source.h - b.y - b.h) * verticalGapScale;
      p.target.x =
        g.h === 'center'
          ? pane.x + (pane.w - p.target.w) / 2
          : g.h === 'right'
            ? pane.x + pane.w - p.target.w - Math.max(0, right - margin)
            : pane.x + Math.max(0, left - margin);
      p.target.y =
        g.v === 'bottom'
          ? pane.y + pane.h - p.target.h - Math.max(0, bottom - margin)
          : g.v === 'center'
            ? pane.y + (pane.h - p.target.h) / 2
            : pane.y + Math.max(0, top - margin);
      p.target.x = Math.max(pane.x, Math.min(pane.x + pane.w - p.target.w, p.target.x));
      p.target.y = Math.max(pane.y, Math.min(pane.y + pane.h - p.target.h, p.target.y));
      placements.push(p);
    }
    // Keep corner anchors; move an originally higher dock above them when
    // asymmetric insets leave insufficient horizontal room.
    const footerPlacements = placements.filter(
      (p) => groups.find((g) => g.id === p.group)?.role === 'footer',
    );
    for (const upper of footerPlacements) {
      for (const lower of footerPlacements) {
        if (
          upper.source.y + upper.source.h > lower.source.y + 1 ||
          intersection(upper.target, lower.target) <= 1
        )
          continue;
        upper.target.y = lower.target.y - Math.min(gap, 6 * density) - upper.target.h;
        upper.reason += '；避让角落控件并保留源上下顺序';
      }
    }
    let flow = groups.filter((g) => g.position === 'flow' && !g.manual && g.role !== 'background');
    flow = flow.sort((a, b) =>
      a.role === 'clock'
        ? -1
        : b.role === 'clock'
          ? 1
          : a.bounds.y - b.bounds.y || a.bounds.x - b.bounds.x,
    );
    const contentRegions = regions.map((region) => {
      const footers = placements.filter(
        (p) =>
          groups.find((g) => g.id === p.group)?.role === 'footer' && contains(region, p.target),
      );
      const statuses = placements.filter(
        (p) =>
          groups.find((g) => g.id === p.group)?.role === 'status' && contains(region, p.target),
      );
      const bottom = footers.length
        ? Math.min(...footers.map((p) => p.target.y)) - gap
        : region.y + region.h;
      const top = statuses.length
        ? Math.max(...statuses.map((p) => p.target.y + p.target.h)) + gap
        : region.y;
      return { ...region, y: top, h: Math.max(1, bottom - top) };
    });
    if (composition === 'preserve') {
      const sourceGroup = {
        id: '_source',
        bounds: { x: 0, y: 0, w: source.w, h: source.h },
      } as DesignGroup;
      const p = centered(sourceGroup, safe, limit, '保持源整体构图');
      placements.push(
        ...flow.map((g) => ({
          group: g.id,
          source: g.bounds,
          scale: p.scale,
          reason: p.reason,
          target: {
            x: p.target.x + g.bounds.x * p.scale,
            y: p.target.y + g.bounds.y * p.scale,
            w: g.bounds.w * p.scale,
            h: g.bounds.h * p.scale,
          },
        })),
      );
    } else if (contentRegions.length > 1) {
      const lanes = contentRegions.map(() => [] as DesignGroup[]);
      flow.forEach((g, i) => lanes[i % lanes.length]!.push(g));
      contentRegions.forEach((r, i) =>
        placements.push(...pack(lanes[i]!, r, gap, limit, composition === 'grid')),
      );
    } else if (composition === 'split' && flow.length > 1) {
      const r = contentRegions[0]!;
      const left = flow.filter((g) => g.role === 'clock' || g.role === 'hero');
      const right = flow.filter((g) => !left.includes(g));
      if (!left.length || !right.length) {
        const n = Math.ceil(flow.length / 2);
        left.splice(0, left.length, ...flow.slice(0, n));
        right.splice(0, right.length, ...flow.slice(n));
      }
      const width = Math.max(1, (r.w - gap) / 2);
      placements.push(
        ...pack(left, { ...r, w: width }, gap, limit, false),
        ...pack(right, { ...r, x: r.x + width + gap, w: width }, gap, limit, false),
      );
    } else if (composition === 'focus-dock' && flow.length > 1) {
      const r = contentRegions[0]!;
      const dockH = r.h * 0.32;
      placements.push(
        ...pack(flow.slice(0, 1), { ...r, h: Math.max(1, r.h - dockH - gap) }, gap, limit, false),
        ...pack(flow.slice(1), { ...r, y: r.y + r.h - dockH, h: dockH }, gap, limit, true),
      );
    } else placements.push(...pack(flow, contentRegions[0]!, gap, limit, composition === 'grid'));
    for (const g of groups.filter((g) => g.position === 'overlay' && !g.manual)) {
      placements.push(centered(g, safe, limit, '弹层保持为独立叠层，不参与稳定场景排布'));
    }
    for (const p of placements) {
      if (!regions.some((r) => contains(r, p.target))) warnings.push(`${p.group} 无法放入安全区域`);
      if (p.scale < unit * 0.55)
        warnings.push(`${p.group} 空间不足，缩放低于建议值；建议提供更紧凑的设计变体`);
    }
    return { algorithm: this.name, composition, groups, placements, regions, warnings };
  }
}
