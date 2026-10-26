import { breakpointOf, DEFAULT_BREAKPOINTS, densityOf, shapeOf } from '../devices.js';
import type {
  AutoLayout,
  ImageStyle,
  Breakpoint,
  DeviceProfile,
  Doc,
  Frame,
  Node,
  NodePatch,
  TextStyle,
  VariantKey,
  SemanticRole,
  Constraints,
  Asset,
} from '../types.js';
import type { LayoutExplanation } from './types.js';

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ResolvedText extends TextStyle {
  /** Font size after auto-shrink, in device px. */
  effectiveSize: number;
  lines: number;
  overflow: boolean;
}

export interface ResolvedNode {
  constraints?: Constraints;
  role?: SemanticRole;
  groupId?: string;
  id: string;
  name: string;
  kind: Node['kind'];
  rect: Rect;
  /** Design-space frame after variant merge, for debugging / constraint badges. */
  design: Frame;
  text?: ResolvedText;
  image?: Node['image'];
  fill?: string;
  radius?: number;
  alpha?: number;
  action?: string;
  visible: boolean;
  autoLayout?: AutoLayout;
  children: ResolvedNode[];
  warnings: string[];
}

export interface ResolvedScene {
  assets?: Asset[];
  explanation?: LayoutExplanation;
  device: DeviceProfile;
  breakpoint: Breakpoint;
  scale: { sx: number; sy: number; uniform: number };
  nodes: ResolvedNode[];
  warnings: string[];
}

export interface SolveOptions {
  /** Force a breakpoint instead of deriving it from the device. */
  breakpoint?: Breakpoint;
  /** Disable variant merging (used by the "base layout" debug view). */
  ignoreVariants?: boolean;
}

const CJK = /[\u3000-\u9fff\uff00-\uffef]/;

/** Rough advance width per character, in em. Good enough for layout + tests. */
function advanceOf(ch: string, weight: TextStyle['weight']): number {
  const bold = weight === 'bold' ? 1.04 : weight === 'medium' ? 1.02 : 1;
  if (CJK.test(ch)) return 1 * bold;
  if (ch === ' ') return 0.28 * bold;
  if (/[iIlj.,:;'!|]/.test(ch)) return 0.28 * bold;
  if (/[A-Z0-9]/.test(ch)) return 0.62 * bold;
  return 0.54 * bold;
}

export function measureText(
  text: string,
  size: number,
  style: Pick<TextStyle, 'weight' | 'letterSpacing'>,
): number {
  let w = 0;
  for (const ch of text) w += advanceOf(ch, style.weight) * size + style.letterSpacing;
  return w;
}

/** Greedy wrap that respects explicit newlines. */
export function wrapText(
  text: string,
  size: number,
  maxWidth: number,
  style: Pick<TextStyle, 'weight' | 'letterSpacing'>,
): string[][] {
  const lines: string[][] = [];
  for (const paragraph of text.split('\n')) {
    let line: string[] = [];
    let width = 0;
    for (const ch of paragraph) {
      const adv = advanceOf(ch, style.weight) * size + style.letterSpacing;
      if (width + adv > maxWidth && line.length > 0) {
        lines.push(line);
        line = [];
        width = 0;
      }
      line.push(ch);
      width += adv;
    }
    lines.push(line);
  }
  return lines;
}

function applyPatch(node: Node, patch: NodePatch | undefined): Node {
  if (patch === undefined) return node;
  return {
    ...node,
    frame: { ...node.frame, ...patch.frame },
    constraints: { ...node.constraints, ...patch.constraints },
    sizing: { ...node.sizing, ...patch.sizing },
    autoLayout: patch.autoLayout
      ? { ...(node.autoLayout as AutoLayout), ...patch.autoLayout }
      : node.autoLayout,
    text: patch.text ? { ...(node.text as TextStyle), ...patch.text } : node.text,
    image: patch.image ? { ...(node.image as ImageStyle), ...patch.image } : node.image,
    responsive: patch.responsive ? { ...node.responsive, ...patch.responsive } : node.responsive,
    fill: patch.fill ?? node.fill,
    radius: patch.radius ?? node.radius,
    alpha: patch.alpha ?? node.alpha,
    action: patch.action ?? node.action,
    safeArea: patch.safeArea !== undefined ? patch.safeArea : node.safeArea,
    visible: patch.hidden === true ? false : (patch.visible ?? node.visible),
    children: node.children,
  };
}

function withVariants(node: Node, keys: VariantKey[], ignore: boolean): Node {
  let patched = node;
  if (!ignore) {
    for (const key of keys) patched = applyPatch(patched, patched.variants?.[key]);
  }
  if (patched.children === undefined) return patched;
  return {
    ...patched,
    children: patched.children.map((c) => withVariants(c, keys, ignore)),
  };
}

interface ScaleCtx {
  parentCoordinates?: boolean;
  device: DeviceProfile;
  design: { w: number; h: number };
  sx: number;
  sy: number;
  uniform: number;
  density: number;
}

function clamp(value: number, min: number | undefined, max: number | undefined): number {
  let v = value;
  if (min !== undefined) v = Math.max(v, min);
  if (max !== undefined) v = Math.min(v, max);
  return v;
}

/** Design-space padding of an auto-layout box, converted to device px. */
function pads(node: Node, sx: number): { t: number; r: number; b: number; l: number; gap: number } {
  const al = node.autoLayout;
  if (al === undefined) return { t: 0, r: 0, b: 0, l: 0, gap: 0 };
  return {
    t: al.pad[0] * sx,
    r: al.pad[1] * sx,
    b: al.pad[2] * sx,
    l: al.pad[3] * sx,
    gap: al.gap * sx,
  };
}

/**
 * Height a text node needs at a given width, in device px.
 * Used by `hug` sizing so a card grows with its copy instead of clipping it.
 */
function textHeight(node: Node, width: number, ctx: ScaleCtx): number {
  const t = node.text;
  if (t === undefined) return node.frame.h * ctx.sx;
  const size = t.size * ctx.sx;
  const lines = wrapText(t.content, size, Math.max(1, width), t).length;
  return Math.min(lines, t.maxLines) * size * t.lineHeight;
}

/**
 * Resolve a rect. `availW` overrides the fill width (auto-layout children).
 *
 * Sizes scale with the **width** ratio so shapes keep their proportions.
 * Vertical positions scale with the **height** ratio, which is what makes a
 * bottom-anchored element land on the bottom of a 2400px screen instead of
 * floating at 2160px.
 */
function solveRect(node: Node, ctx: ScaleCtx, availW?: number): Rect {
  const { frame, constraints, sizing } = node;
  const { sx, sy, device, design, density } = ctx;

  const minW = constraints.minW !== undefined ? constraints.minW * density : undefined;
  const maxW = constraints.maxW !== undefined ? constraints.maxW * density : undefined;
  const minH = constraints.minH !== undefined ? constraints.minH * density : undefined;
  const maxH = constraints.maxH !== undefined ? constraints.maxH * density : undefined;

  // ---- width -------------------------------------------------------------
  let w = frame.w * sx;
  if (sizing.w === 'fill') {
    if (availW !== undefined) w = availW;
    else if (constraints.h === 'leftRight')
      w = device.width - frame.x * sx - (design.w - frame.x - frame.w) * sx;
    else if (constraints.h === 'scale') w = device.width;
    else if (constraints.h === 'right')
      w = device.width - (design.w - frame.x - frame.w) * sx - frame.w * sx;
  }
  if (node.autoLayout !== undefined && sizing.w === 'hug' && node.autoLayout.dir === 'row') {
    const p = pads(node, sx);
    const kids = (node.children ?? []).filter((c) => c.visible !== false);
    w =
      p.l +
      p.r +
      kids.reduce((s, c) => s + solveRect(c, ctx).w, 0) +
      p.gap * Math.max(0, kids.length - 1);
  }
  w = clamp(w, minW, maxW);

  // ---- height ------------------------------------------------------------
  let h = frame.h * sx;
  if (sizing.h === 'fill' && constraints.v === 'topBottom') {
    h = device.height - frame.y * sy - (design.h - frame.y - frame.h) * sy;
  } else if (sizing.h === 'fill' && constraints.v === 'scale') {
    h = device.height;
  } else if (sizing.h === 'hug') {
    if (node.autoLayout !== undefined) {
      const p = pads(node, sx);
      const kids = (node.children ?? []).filter((c) => c.visible !== false);
      if (node.autoLayout.dir === 'col') {
        const inner = Math.max(0, w - p.l - p.r);
        h =
          p.t +
          p.b +
          kids.reduce((s, c) => s + solveRect(c, ctx, inner).h, 0) +
          p.gap * Math.max(0, kids.length - 1);
      } else {
        const inner = Math.max(0, w - p.l - p.r);
        h = p.t + p.b + Math.max(0, ...kids.map((c) => solveRect(c, ctx, inner).h));
      }
    } else if (node.text !== undefined) {
      h = textHeight(node, w, ctx);
    }
  }
  h = clamp(h, minH, maxH);

  // Aspect wins over both, and re-derives width when a clamp bites so the
  // shape never distorts.
  if (constraints.aspect !== null && constraints.aspect !== undefined && constraints.aspect > 0) {
    h = w * constraints.aspect;
    const clampedH = clamp(h, minH, maxH);
    if (clampedH !== h) {
      h = clampedH;
      w = h / constraints.aspect;
      w = clamp(w, minW, maxW);
    }
  }

  // ---- x -----------------------------------------------------------------
  let x: number;
  switch (constraints.h) {
    case 'right':
      x = device.width - (design.w - frame.x - frame.w) * sx - w;
      break;
    case 'center': {
      const cx0 = frame.x + frame.w / 2;
      x = device.width / 2 + (cx0 - design.w / 2) * sx - w / 2;
      break;
    }
    default:
      x = frame.x * sx;
  }

  // ---- y -----------------------------------------------------------------
  let y: number;
  switch (constraints.v) {
    case 'bottom':
      y = device.height - (design.h - frame.y - frame.h) * sy - h;
      break;
    case 'center': {
      const cy0 = frame.y + frame.h / 2;
      y = device.height / 2 + (cy0 - design.h / 2) * sy - h / 2;
      break;
    }
    case 'scale':
      y = frame.y * sx;
      break;
    default:
      y = frame.y * sy;
  }

  return { x, y, w, h };
}

function applySafeArea(rect: Rect, node: Node, ctx: ScaleCtx): { rect: Rect; warnings: string[] } {
  const warnings: string[] = [];
  if (!node.safeArea) return { rect, warnings };
  const insets = ctx.device.insets;
  const r = { ...rect };
  if (node.safeArea === 'top' || node.safeArea === 'both') {
    if (r.y < insets.top) {
      warnings.push(`顶部安全区：y 从 ${Math.round(r.y)} 推到 ${Math.round(insets.top)}`);
      r.y = insets.top;
    }
  }
  if (node.safeArea === 'bottom' || node.safeArea === 'both') {
    const limit = ctx.device.height - insets.bottom;
    if (r.y + r.h > limit) {
      warnings.push(`底部安全区：底边从 ${Math.round(r.y + r.h)} 收到 ${Math.round(limit)}`);
      r.y = limit - r.h;
    }
  }
  return { rect: r, warnings };
}

function resolveText(node: Node, rect: Rect, ctx: ScaleCtx): ResolvedText | undefined {
  if (node.text === undefined) return undefined;
  const t = node.text;
  const base = t.size * ctx.sx;
  let size = base;

  // Image-based clocks carry a `text` block only for the `align`/`color`
  // attributes; their metrics come from the digit sprite, so don't wrap them.
  if (node.kind === 'time') {
    return { ...t, effectiveSize: size, lines: 1, overflow: false };
  }

  const limitW = rect.w;
  const fit = (candidate: number): { n: number; fits: boolean } => {
    const wrapped = wrapText(t.content, candidate, Math.max(1, limitW), t);
    const n = wrapped.length;
    const widest = Math.max(...wrapped.map((l) => measureText(l.join(''), candidate, t)), 0);
    const fitsW = widest <= limitW + 0.5;
    const limitH = Math.min(rect.h, t.maxLines * candidate * t.lineHeight);
    return { n, fits: fitsW && n <= t.maxLines && n * candidate * t.lineHeight <= limitH + 0.5 };
  };

  let probe = fit(size);
  if (!probe.fits && t.autoShrink) {
    let lo = size * 0.4;
    let hi = size;
    for (let i = 0; i < 14; i += 1) {
      const mid = (lo + hi) / 2;
      if (fit(mid).fits) lo = mid;
      else hi = mid;
    }
    size = lo;
    probe = fit(size);
  }

  return { ...t, effectiveSize: size, lines: probe.n, overflow: !probe.fits };
}

function layoutChildren(parent: Node, parentRect: Rect, ctx: ScaleCtx): ResolvedNode[] {
  const kids = (parent.children ?? []).filter((c) => c.visible !== false);
  if (kids.length === 0) return [];
  if (parent.autoLayout === undefined) {
    if (!ctx.parentCoordinates) return kids.map((c) => resolveNode(c, ctx, parentRect));
    const local: ScaleCtx = {
      ...ctx,
      design: { w: parent.frame.w, h: parent.frame.h },
      device: {
        ...ctx.device,
        width: parentRect.w,
        height: parentRect.h,
        insets: { top: 0, right: 0, bottom: 0, left: 0 },
      },
      sx: parentRect.w / parent.frame.w,
      sy: parentRect.h / parent.frame.h,
    };
    return kids.map((c) => {
      const r = solveRect(c, local);
      // Keep returned frames absolute; exporters subtract their parent once.
      return resolveNode(c, ctx, parentRect, {
        ...r,
        x: parentRect.x + r.x,
        y: parentRect.y + r.y,
      });
    });
  }

  const al = parent.autoLayout;
  const p = pads(parent, ctx.sx);
  const innerW = Math.max(0, parentRect.w - p.l - p.r);
  const innerH = Math.max(0, parentRect.h - p.t - p.b);

  const measured = kids.map((child) => ({
    child,
    rect: solveRect(child, ctx, innerW),
  }));

  const fills = measured.filter(
    (m) => (al.dir === 'row' ? m.child.sizing.w : m.child.sizing.h) === 'fill',
  );
  if (fills.length) {
    const fixed = measured
      .filter((m) => !fills.includes(m))
      .reduce((s, m) => s + (al.dir === 'row' ? m.rect.w : m.rect.h), 0);
    const share = Math.max(
      0,
      ((al.dir === 'row' ? innerW : innerH) - fixed - p.gap * Math.max(0, measured.length - 1)) /
        fills.length,
    );
    for (const m of fills) {
      if (al.dir === 'row') m.rect = { ...solveRect(m.child, ctx, share), w: share };
      else m.rect.h = share;
    }
  }

  const mainSizes = measured.map((m) => (al.dir === 'row' ? m.rect.w : m.rect.h));
  const mainTotal = mainSizes.reduce((s, v) => s + v, 0) + p.gap * Math.max(0, kids.length - 1);
  const free = (al.dir === 'row' ? innerW : innerH) - mainTotal;

  let cursor = al.dir === 'row' ? p.l : p.t;
  if (al.main === 'center') cursor += free / 2;
  else if (al.main === 'end') cursor += free;
  const spaceBetween =
    al.main === 'spaceBetween' && kids.length > 1 ? Math.max(0, free) / (kids.length - 1) : 0;

  const out: ResolvedNode[] = [];
  for (const { child, rect } of measured) {
    const r: Rect = { ...rect };
    if (al.dir === 'row') {
      r.x = parentRect.x + cursor;
      if (al.cross === 'stretch') {
        r.h = innerH;
        r.y = parentRect.y + p.t;
      }
      if (al.cross === 'center') r.y = parentRect.y + p.t + (innerH - r.h) / 2;
      else if (al.cross === 'end') r.y = parentRect.y + p.t + innerH - r.h;
      else if (al.cross === 'start') r.y = parentRect.y + p.t;
      cursor += r.w + p.gap + spaceBetween;
    } else {
      r.y = parentRect.y + cursor;
      if (al.cross === 'stretch') {
        r.w = innerW;
        r.x = parentRect.x + p.l;
      } else if (al.cross === 'center') r.x = parentRect.x + p.l + (innerW - r.w) / 2;
      else if (al.cross === 'end') r.x = parentRect.x + p.l + innerW - r.w;
      else r.x = parentRect.x + p.l;
      cursor += r.h + p.gap + spaceBetween;
    }
    out.push(resolveNode(child, ctx, parentRect, r));
  }
  return out;
}

function resolveNode(
  node: Node,
  ctx: ScaleCtx,
  parentRect: Rect | undefined,
  preset?: Rect,
): ResolvedNode {
  const warnings: string[] = [];
  let rect = preset ?? solveRect(node, ctx);

  const safe = applySafeArea(rect, node, ctx);
  rect = safe.rect;
  warnings.push(...safe.warnings);

  if (
    rect.x < -0.5 ||
    rect.y < -0.5 ||
    rect.x + rect.w > ctx.device.width + 0.5 ||
    rect.y + rect.h > ctx.device.height + 0.5
  ) {
    warnings.push('超出画布');
  }

  const text = resolveText(node, rect, ctx);
  if (text?.overflow) warnings.push('文字溢出（已自动缩放仍不满足）');

  return {
    id: node.id,
    constraints: node.constraints,
    name: node.name,
    kind: node.kind,
    rect,
    design: node.frame,
    text,
    image: node.image,
    fill: node.fill,
    radius: node.radius !== undefined ? node.radius * ctx.sx : undefined,
    alpha: node.alpha,
    action: node.action,
    visible: node.visible !== false,
    autoLayout: node.autoLayout,
    children: layoutChildren(node, rect, ctx),
    warnings,
  };
}

export function resolveLegacyDoc(
  doc: Doc,
  device: DeviceProfile,
  options: SolveOptions = {},
): ResolvedScene {
  const bp = doc.breakpoints ?? DEFAULT_BREAKPOINTS;
  const breakpoint = options.breakpoint ?? breakpointOf(device, bp);
  const sx = device.width / doc.meta.design.w;
  const sy = device.height / doc.meta.design.h;
  const ctx: ScaleCtx = {
    device,
    design: doc.meta.design,
    sx,
    sy,
    uniform: Math.min(sx, sy),
    density: densityOf(device),
    parentCoordinates: doc.coordinates === 'parent',
  };

  const shape = shapeOf(device);
  const keys: VariantKey[] =
    shape === 'portrait' ? [breakpoint] : [breakpoint, `${breakpoint}-${shape}`];

  const nodes = doc.root.map((n) => {
    const merged = withVariants(n, keys, options.ignoreVariants === true);
    return resolveNode(merged, ctx, undefined);
  });

  const warnings: string[] = [];
  const hasVariant = (key: VariantKey): boolean =>
    doc.root.some((n) => n.variants?.[key] !== undefined);
  if (breakpoint !== 'compact' && !hasVariant(breakpoint)) {
    warnings.push(`${breakpoint} 断点没有定义任何变体，将直接按 compact 布局拉伸`);
  }
  if (shape !== 'portrait' && !hasVariant(`${breakpoint}-${shape}`)) {
    warnings.push(
      `${breakpoint}-${shape} 没有定义${shape === 'square' ? '方形' : '横屏'}变体，沿用竖屏比例`,
    );
  }

  return {
    device,
    assets: doc.assets,
    breakpoint,
    scale: { sx, sy, uniform: ctx.uniform },
    nodes: nodes.map((n) => inheritVisibility(n)),
    warnings,
  };
}

function inheritVisibility(node: ResolvedNode, parentVisible = true): ResolvedNode {
  const visible = parentVisible && node.visible;
  return { ...node, visible, children: node.children.map((c) => inheritVisibility(c, visible)) };
}

export function flatten(nodes: ResolvedNode[]): ResolvedNode[] {
  const out: ResolvedNode[] = [];
  const walk = (list: ResolvedNode[]): void => {
    for (const n of list) {
      out.push(n);
      walk(n.children);
    }
  };
  walk(nodes);
  return out;
}

/** Variant keys that apply to a device, in cascade order. */
export function activeVariantKeys(
  device: DeviceProfile,
  bp: { medium: number; expanded: number } = DEFAULT_BREAKPOINTS,
): VariantKey[] {
  const breakpoint = breakpointOf(device, bp);
  const shape = shapeOf(device);
  return shape === 'portrait' ? [breakpoint] : [breakpoint, `${breakpoint}-${shape}`];
}

/** Public wrapper: merge a node with the given variant cascade. */
export function mergeVariants(node: Node, keys: VariantKey[]): Node {
  return withVariants(node, keys, false);
}
