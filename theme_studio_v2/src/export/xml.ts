import type { DeviceProfile, Doc, Node } from '../core/types.js';
import type { ResolvedNode, ResolvedScene } from '../core/solve.js';
import { flatten } from '../core/solve.js';
import { materialize } from './materialize.js';
import { assertDocument } from '../core/document.js';

export type ExportMode = 'responsive' | 'baked';

export interface XmlExportOptions {
  mode: ExportMode;
  /** Required for `baked`; ignored for `responsive`. */
  scene?: ResolvedScene;
  frameRate?: number;
  /** Emit indented XML (default true). */
  pretty?: boolean;
}

/**
 * A coordinate expressed as a linear form over the two scale variables:
 *   `sx * #ts_sx + sy * #ts_sy + c`
 *
 * Every value the engine can produce is linear in the scale factors, because
 * `#screen_width === #ts_sx * designWidth`. Keeping it linear is what lets
 * auto-layout be summed symbolically, and it collapses to a single number in
 * baked mode (sx = sy = 0).
 */
interface Lin {
  sx: number;
  sy: number;
  c: number;
}

const L = (sx = 0, sy = 0, c = 0): Lin => ({ sx, sy, c });
const add = (a: Lin, b: Lin): Lin => ({ sx: a.sx + b.sx, sy: a.sy + b.sy, c: a.c + b.c });
const sub = (a: Lin, b: Lin): Lin => ({ sx: a.sx - b.sx, sy: a.sy - b.sy, c: a.c - b.c });
const mul = (a: Lin, k: number): Lin => ({ sx: a.sx * k, sy: a.sy * k, c: a.c * k });

const num = (v: number): string => String(Math.round(v * 10000) / 10000);

const esc = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const SX = '#ts_sx';
const SY = '#ts_sy';

interface EmitCtx {
  mode: ExportMode;
  design: { w: number; h: number };
  depth: number;
  pretty: boolean;
  /** Absolute frames for baked mode, keyed by node id. */
  resolved: Map<string, ResolvedNode>;
  /** Width scale, used to bake dp-ish values like letter spacing. */
  sx: number;
}

/** Render a linear form as an engine expression (or a plain number when baked). */
function expr(l: Lin): string {
  const items: Array<{ text: string; neg: boolean }> = [];
  const term = (coef: number, sym: string): void => {
    const body = Math.abs(coef) === 1 ? sym : `${sym}*${num(Math.abs(coef))}`;
    items.push({ text: body, neg: coef < 0 });
  };
  if (l.sx !== 0) term(l.sx, SX);
  if (l.sy !== 0) term(l.sy, SY);
  if (l.c !== 0 || items.length === 0) items.push({ text: num(Math.abs(l.c)), neg: l.c < 0 });

  // Lead with a positive term so the expression reads naturally and never
  // starts with a unary minus.
  const ordered = [...items.filter((i) => !i.neg), ...items.filter((i) => i.neg)];
  return ordered
    .map((item, i) =>
      i === 0
        ? item.neg
          ? `-${item.text}`
          : item.text
        : item.neg
          ? `-${item.text}`
          : `+${item.text}`,
    )
    .join('');
}

/** Emit a full-width / full-height form as the screen variable. */
function dimExpr(l: Lin, design: { w: number; h: number }, axis: 'w' | 'h'): string {
  if (axis === 'w' && l.sx === design.w && l.sy === 0 && l.c === 0) return '#screen_width';
  if (axis === 'h' && l.sy === design.h && l.sx === 0 && l.c === 0) return '#screen_height';
  return expr(l);
}

interface Frame4 {
  x: Lin;
  y: Lin;
  w: Lin;
  h: Lin;
}

const rightGapOf = (f: Node['frame'], design: { w: number }): number => design.w - f.x - f.w;
const bottomGapOf = (f: Node['frame'], design: { h: number }): number => design.h - f.y - f.h;

/** Symbolic absolute frame, mirroring `solveRect`. */
function symbolicFrame(node: Node, design: { w: number; h: number }): Frame4 {
  const { frame: f, constraints: c, sizing } = node;

  let w: Lin;
  if (sizing.w === 'fill' && c.h === 'scale') w = L(design.w);
  else w = L(f.w);

  let h: Lin;
  if (c.aspect !== null && c.aspect !== undefined && c.aspect > 0) h = mul(w, c.aspect);
  else if (sizing.h === 'fill' && c.v === 'scale') h = L(0, design.h);
  else h = L(f.h);

  let x: Lin;
  switch (c.h) {
    case 'right':
      x = sub(L(design.w - rightGapOf(f, design)), w);
      break;
    case 'center':
      x = sub(L(f.x + f.w / 2), mul(w, 0.5));
      break;
    default:
      x = L(f.x);
  }

  let y: Lin;
  switch (c.v) {
    case 'bottom':
      y = sub(L(0, design.h - bottomGapOf(f, design)), h);
      break;
    case 'center':
      y = sub(L(0, f.y + f.h / 2), mul(h, 0.5));
      break;
    case 'scale':
      y = L(f.y);
      break;
    default:
      y = L(0, f.y);
  }

  return { x, y, w, h };
}

/** Absolute frames for baked mode, straight from the solver. */
function bakedFrames(
  nodes: ResolvedNode[],
  map = new Map<string, ResolvedNode>(),
): Map<string, ResolvedNode> {
  for (const n of nodes) {
    map.set(n.id, n);
    bakedFrames(n.children, map);
  }
  return map;
}

function bakedFrame(n: ResolvedNode): Frame4 {
  return {
    x: L(0, 0, n.rect.x),
    y: L(0, 0, n.rect.y),
    w: L(0, 0, n.rect.w),
    h: L(0, 0, n.rect.h),
  };
}

/**
 * Absolute frames of auto-layout children, computed by the same rules the
 * solver uses, but kept symbolic so one script serves every screen.
 */
function autoLayoutFrames(
  parent: Node,
  parentFrame: Frame4,
  design: { w: number; h: number },
): Map<string, Frame4> {
  const out = new Map<string, Frame4>();
  const al = parent.autoLayout;
  const kids = parent.children ?? [];
  if (al === undefined || kids.length === 0) return out;

  const padL = L(al.pad[3]);
  const padR = L(al.pad[1]);
  const padT = L(al.pad[0]);
  const padB = L(al.pad[2]);
  const gap = L(al.gap);

  const innerW = sub(sub(parentFrame.w, padL), padR);
  const innerH = sub(sub(parentFrame.h, padT), padB);

  const frames: Frame4[] = [];
  let total = L(0);
  for (const child of kids) {
    const own = symbolicFrame(child, design);
    const w = child.sizing.w === 'fill' ? innerW : own.w;
    const h = child.sizing.h === 'fill' ? innerH : own.h;
    frames.push({ x: L(0), y: L(0), w, h });
    total = add(total, al.dir === 'row' ? w : h);
  }
  total = add(total, mul(gap, Math.max(0, kids.length - 1)));

  let cursor = al.dir === 'row' ? padL : padT;
  const free = sub(al.dir === 'row' ? innerW : innerH, total);
  if (al.main === 'center') cursor = add(cursor, mul(free, 0.5));
  else if (al.main === 'end') cursor = add(cursor, free);
  const spaceBetween =
    al.main === 'spaceBetween' && kids.length > 1 ? mul(free, 1 / (kids.length - 1)) : L(0);

  kids.forEach((child, i) => {
    const f = frames[i]!;
    if (al.dir === 'row') {
      const y =
        al.cross === 'stretch'
          ? add(parentFrame.y, padT)
          : al.cross === 'center'
            ? add(add(parentFrame.y, padT), mul(sub(innerH, f.h), 0.5))
            : al.cross === 'end'
              ? add(add(parentFrame.y, padT), sub(innerH, f.h))
              : add(parentFrame.y, padT);
      out.set(child.id, {
        x: add(parentFrame.x, cursor),
        y,
        w: f.w,
        h: al.cross === 'stretch' ? innerH : f.h,
      });
      cursor = add(add(cursor, f.w), add(gap, spaceBetween));
    } else {
      const x =
        al.cross === 'stretch'
          ? add(parentFrame.x, padL)
          : al.cross === 'center'
            ? add(add(parentFrame.x, padL), mul(sub(innerW, f.w), 0.5))
            : al.cross === 'end'
              ? add(add(parentFrame.x, padL), sub(innerW, f.w))
              : add(parentFrame.x, padL);
      out.set(child.id, {
        x,
        y: add(parentFrame.y, cursor),
        w: al.cross === 'stretch' ? innerW : f.w,
        h: f.h,
      });
      cursor = add(add(cursor, f.h), add(gap, spaceBetween));
    }
  });

  return out;
}

function pad(ctx: EmitCtx): string {
  return ctx.pretty ? '  '.repeat(ctx.depth) : '';
}

function attrs(pairs: Array<[string, string | number | undefined]>): string {
  return pairs
    .filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => ` ${k}="${esc(String(v))}"`)
    .join('');
}

function textSize(node: Node, ctx: EmitCtx): string {
  const base = node.text?.size ?? 40;
  if (ctx.mode === 'baked') {
    const resolved = ctx.resolved.get(node.id);
    return num(resolved?.text?.effectiveSize ?? base);
  }
  return expr(L(base));
}

function emitNode(node: Node, ctx: EmitCtx, abs: Frame4, parentAbs: Frame4 | null): string[] {
  if (node.visible === false) return [];
  const p = pad(ctx);
  const name = node.name.replace(/"/g, '');
  const alpha = node.alpha !== undefined ? num(node.alpha) : undefined;

  // The runtime accumulates child coordinates onto the parent origin, so every
  // node is written relative to its parent box.
  const alignment = ['text', 'date', 'time'].includes(node.kind) ? node.text?.align : undefined;
  const anchorX =
    alignment === 'center'
      ? add(abs.x, mul(abs.w, 0.5))
      : alignment === 'right'
        ? add(abs.x, abs.w)
        : abs.x;
  const X = expr(parentAbs ? sub(anchorX, parentAbs.x) : anchorX);
  const Y = expr(parentAbs ? sub(abs.y, parentAbs.y) : abs.y);
  const W = dimExpr(abs.w, ctx.design, 'w');
  const H = dimExpr(abs.h, ctx.design, 'h');

  switch (node.kind) {
    case 'group': {
      // When the group carries a fill, the alpha belongs to the background
      // rectangle; putting it on the group too would fade the text twice.
      const head = `${p}<Group${attrs([
        ['name', name],
        ['x', X],
        ['y', Y],
        ['alpha', node.fill ? undefined : alpha],
      ])}>`;
      const kids: string[] = [];
      if (node.fill) {
        kids.push(
          `${p}  <Rectangle${attrs([
            ['name', `${name}-bg`],
            ['x', '0'],
            ['y', '0'],
            ['w', W],
            ['h', H],
            [
              'cornerRadius',
              node.radius !== undefined ? `${num(node.radius)},${num(node.radius)}` : undefined,
            ],
            ['fillColor', node.fill],
            ['alpha', alpha],
          ])}/>`,
        );
      }
      ctx.depth += 1;
      const childFrames = childFrameMap(node, abs, ctx);
      for (const child of node.children ?? []) {
        const frame = childFrames.get(child.id) ?? symbolicFrame(child, ctx.design);
        kids.push(...emitNode(child, ctx, frame, abs));
      }
      ctx.depth -= 1;
      return [head, ...kids, `${p}</Group>`];
    }
    case 'image':
      return [
        `${p}<Image${attrs([
          ['name', name],
          ['x', X],
          ['y', Y],
          ['w', W],
          ['h', H],
          ['src', node.image?.src ?? `${name}.png`],
          ['scaleType', node.image?.scaleType === 'contain' ? 'inside' : 'fill'],
          ['alpha', alpha],
        ])}/>`,
      ];
    case 'text':
      return [
        `${p}<Text${attrs([
          ['name', name],
          ['x', X],
          ['y', Y],
          ['w', W],
          ['h', H],
          ['size', textSize(node, ctx)],
          ['color', node.text?.color ?? '#FFFFFF'],
          ['textalign', node.text?.align ?? 'left'],
          ['align', node.text?.align ?? 'left'],
          ['bold', node.text?.weight === 'bold' ? 'true' : undefined],
          [
            'letterSpacing',
            node.text?.letterSpacing
              ? ctx.mode === 'baked'
                ? num(node.text.letterSpacing * ctx.sx)
                : expr(L(node.text.letterSpacing))
              : undefined,
          ],
          ['autoLineFeed', (node.text?.maxLines ?? 1) > 1 ? 'true' : undefined],
          ['alpha', alpha],
          ['text', node.text?.content ?? ''],
        ])}/>`,
      ];
    case 'time':
      if (!node.image?.src)
        return [
          `${p}<DateTime${attrs([
            ['name', name],
            ['x', X],
            ['y', Y],
            ['w', W],
            ['h', H],
            ['format', 'HH:mm'],
            ['size', textSize(node, ctx)],
            ['color', node.text?.color ?? '#FFFFFF'],
            ['align', node.text?.align ?? 'center'],
            ['alpha', alpha],
          ])}/>`,
        ];
      return [
        `${p}<Time${attrs([
          ['name', name],
          ['x', X],
          ['y', Y],
          ['w', W],
          ['h', H],
          ['src', node.image?.src ?? 'time.png'],
          ['align', node.text?.align ?? 'center'],
          ['alpha', alpha],
        ])}/>`,
      ];
    case 'rect':
      return [
        `${p}<Rectangle${attrs([
          ['name', name],
          ['x', X],
          ['y', Y],
          ['w', W],
          ['h', H],
          [
            'cornerRadius',
            node.radius !== undefined ? `${num(node.radius)},${num(node.radius)}` : undefined,
          ],
          ['fillColor', node.fill ?? '#FFFFFF'],
          ['alpha', alpha],
        ])}/>`,
      ];
    case 'unlocker':
      return [
        `${p}<Button${attrs([
          ['name', name],
          ['x', X],
          ['y', Y],
          ['w', W],
          ['h', H],
        ])}>`,
        `${p}  <Trigger action="up">`,
        `${p}    <UnlockerCommand/>`,
        `${p}  </Trigger>`,
        `${p}</Button>`,
      ];
    case 'button':
      return [
        ...(node.fill
          ? [
              `${p}<Rectangle${attrs([
                ['name', `${name}-surface`],
                ['x', X],
                ['y', Y],
                ['w', W],
                ['h', H],
                [
                  'cornerRadius',
                  node.radius !== undefined ? `${num(node.radius)},${num(node.radius)}` : undefined,
                ],
                ['fillColor', node.fill],
                ['alpha', alpha],
              ])}/>`,
            ]
          : []),
        `${p}<Button${attrs([
          ['name', name],
          ['x', X],
          ['y', Y],
          ['w', W],
          ['h', H],
        ])}>`,
        `${p}  <Trigger action="tap">`,
        node.action
          ? `${p}    <IntentCommand action="${esc(node.action)}"/>`
          : `${p}    <!-- 未配置 Intent -->`,
        `${p}  </Trigger>`,
        `${p}</Button>`,
      ];
    case 'date':
      return [
        `${p}<DateTime${attrs([
          ['name', name],
          ['x', X],
          ['y', Y],
          ['w', W],
          ['h', H],
          ['size', textSize(node, ctx)],
          ['color', node.text?.color ?? '#FFFFFF'],
          ['align', node.text?.align ?? 'center'],
          ['alpha', alpha],
          ['format', 'M月d日 EEEE'],
        ])}/>`,
      ];
  }
}

/** Absolute frames for a parent's children, honouring auto layout. */
function childFrameMap(parent: Node, parentFrame: Frame4, ctx: EmitCtx): Map<string, Frame4> {
  if (ctx.mode === 'baked') {
    const out = new Map<string, Frame4>();
    const resolved = ctx.resolved.get(parent.id);
    for (const child of resolved?.children ?? []) out.set(child.id, bakedFrame(child));
    return out;
  }
  if (parent.autoLayout !== undefined) return autoLayoutFrames(parent, parentFrame, ctx.design);
  const out = new Map<string, Frame4>();
  for (const child of parent.children ?? []) out.set(child.id, symbolicFrame(child, ctx.design));
  return out;
}

function rootFrame(node: Node, ctx: EmitCtx): Frame4 {
  if (ctx.mode === 'baked') {
    const resolved = ctx.resolved.get(node.id);
    if (resolved) return bakedFrame(resolved);
  }
  return symbolicFrame(node, ctx.design);
}

/** Emit a HarmonyOS lockscreen script. */
export function exportLockscreenXml(doc: Doc, options: XmlExportOptions): string {
  assertDocument(doc);
  const { mode, scene } = options;
  if (mode === 'responsive' && doc.layout?.mode === 'auto') {
    throw new Error(
      '自动重构包含分区、设备安全区和语义推断，无法无损编译成现有线性表达式。请导出当前设备 XML 或全部设备包。',
    );
  }
  if (mode === 'baked' && scene === undefined) {
    throw new Error('baked 模式需要传入已求解的 scene');
  }
  const device: DeviceProfile | undefined = scene?.device;
  const ctx: EmitCtx = {
    mode,
    design: doc.meta.design,
    depth: 1,
    pretty: options.pretty !== false,
    resolved: scene ? bakedFrames(scene.nodes) : new Map(),
    sx: device ? device.width / doc.meta.design.w : 1,
  };

  const screenWidth = mode === 'baked' && device ? device.width : doc.meta.design.w;
  const frameRate = options.frameRate ?? doc.meta.frameRate;

  const header: string[] = ['<?xml version="1.0" encoding="utf-8"?>'];
  if (mode === 'responsive') {
    const all = flattenNodes(doc.root);
    const clamped = all.filter(
      (n) => n.constraints.maxW !== undefined || n.constraints.minH !== undefined,
    ).length;
    const variantNodes = all.filter((n) => n.variants !== undefined).length;
    if (clamped > 0 || variantNodes > 0) {
      header.push(
        `<!-- 注意：本文件为等比缩放版本（${clamped} 个 dp 钳制、${variantNodes} 个断点变体在此模式下不生效）。` +
          `平板 / 折叠屏请使用「按机型烘焙」或「导出全部机型」的产物。 -->`,
      );
    }
  }
  header.push(
    `<!-- 由 theme-studio-v2 生成 · ${mode === 'responsive' ? '响应式单文件' : `按 ${esc(device?.label ?? '').replace(/--/g, '- -')} 烘焙`} · 设计画布 ${doc.meta.design.w}×${doc.meta.design.h} -->`,
  );
  header.push(`<Lockscreen version="1" frameRate="${frameRate}" screenWidth="${screenWidth}">`);

  const vars: string[] = [];
  if (mode === 'responsive') {
    vars.push('  <!-- 响应式基准：宽度比、高度比、等比（取小） -->');
    vars.push(
      `  <Var name="ts_sx" expression="#screen_width/${doc.meta.design.w}" const="true" />`,
    );
    vars.push(
      `  <Var name="ts_sy" expression="#screen_height/${doc.meta.design.h}" const="true" />`,
    );
    vars.push(
      `  <Var name="ts_s" expression="min(#screen_width/${doc.meta.design.w},#screen_height/${doc.meta.design.h})" const="true" />`,
    );
  }

  const nodes = mode === 'baked' && scene ? materialize(doc, scene) : doc.root;
  const body = nodes.flatMap((n) => emitNode(n, ctx, rootFrame(n, ctx), null));
  return [...header, ...vars, ...body, '</Lockscreen>', ''].join('\n');
}

function flattenNodes(nodes: Node[]): Node[] {
  const out: Node[] = [];
  const go = (list: Node[]): void => {
    for (const n of list) {
      out.push(n);
      if (n.children) go(n.children);
    }
  };
  go(nodes);
  return out;
}

/** Human-readable table of every device the document was checked against. */
export function exportConversionReport(doc: Doc, scenes: ResolvedScene[]): string {
  const rows = scenes.map((s) => {
    const d = s.device;
    const uniform = Math.min(d.width / doc.meta.design.w, d.height / doc.meta.design.h);
    return [
      d.id,
      `${d.width}×${d.height}`,
      `${d.widthDp}dp`,
      s.breakpoint,
      s.scale.sx.toFixed(3),
      s.scale.sy.toFixed(3),
      uniform.toFixed(3),
    ].join(' | ');
  });
  return [
    '| 设备 | 分辨率 | 逻辑宽 | 断点 | sx | sy | 等比 |',
    '|---|---|---|---|---|---|---|',
    ...rows.map((r) => `| ${r} |`),
  ].join('\n');
}

export function collectNodes(doc: Doc): Node[] {
  return flattenNodes(doc.root);
}

export { flatten };
