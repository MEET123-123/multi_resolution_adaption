import { describe, expect, it } from 'vitest';
import { DEVICES, breakpointOf, deviceById, shapeOf } from '../src/core/devices.js';
import { flatten, measureText, resolveDoc, wrapText } from '../src/core/solve.js';
import { validateScene } from '../src/core/validate.js';
import { createDemoDoc } from '../src/demo/lockscreen.js';
import type { Doc, Node } from '../src/core/types.js';

const DESIGN = { w: 1080, h: 2340 };

function doc(nodes: Node[]): Doc {
  return {
    version: 2,
    meta: { name: 't', design: DESIGN, designWidthDp: 360, frameRate: 60, scene: 'lockscreen' },
    root: nodes,
  };
}

function box(id: string, x: number, y: number, w: number, h: number, extra: Partial<Node> = {}): Node {
  return {
    id,
    name: id,
    kind: 'rect',
    frame: { x, y, w, h },
    constraints: { h: 'left', v: 'top' },
    sizing: { w: 'fixed', h: 'fixed' },
    ...extra,
  };
}

const phone = deviceById('phone-1080x2340')!;
const tall = deviceById('phone-1080x2400')!;
const tablet = deviceById('tablet-2560x1600')!;

describe('devices', () => {
  it('derives breakpoints from logical width', () => {
    expect(breakpointOf(phone)).toBe('compact');
    expect(breakpointOf(deviceById('tablet-1600x2560')!)).toBe('medium');
    expect(breakpointOf(tablet)).toBe('expanded');
  });

  it('classifies shape, including near-square fold cover', () => {
    expect(shapeOf(deviceById('fold-cover-980')!)).toBe('square');
    expect(shapeOf(deviceById('fold-inner-2120')!)).toBe('landscape');
    expect(shapeOf(phone)).toBe('portrait');
  });
});

describe('solver: anchors', () => {
  it('bottom anchor tracks the real screen bottom instead of a 2160 baseline', () => {
    // bottom gap of 0 in design space: the element must sit flush with the screen bottom
    const d = doc([box('bar', 0, 2140, 1080, 200, { constraints: { h: 'left', v: 'bottom' } })]);
    const a = resolveDoc(d, phone).nodes[0]!;
    const b = resolveDoc(d, tall).nodes[0]!;
    expect(a.rect.y + a.rect.h).toBeCloseTo(phone.height, 3);
    expect(b.rect.y + b.rect.h).toBeCloseTo(tall.height, 3);
    // the legacy formula (`#screen_height-2160+y`) would have left 240px of empty space here
    expect(b.rect.y - a.rect.y).toBeCloseTo(tall.height - phone.height, 3);
  });

  it('fill + scale covers the whole screen on every device', () => {
    const d = doc([
      box('wp', 0, 0, 1080, 2340, {
        constraints: { h: 'scale', v: 'scale' },
        sizing: { w: 'fill', h: 'fill' },
      }),
    ]);
    for (const device of DEVICES) {
      const r = resolveDoc(d, device).nodes[0]!.rect;
      expect(r.w).toBeCloseTo(device.width, 3);
      expect(r.h).toBeCloseTo(device.height, 3);
    }
  });

  it('center anchor keeps the element centred regardless of width', () => {
    const d = doc([box('c', 140, 400, 800, 200, { constraints: { h: 'center', v: 'top' } })]);
    const r = resolveDoc(d, tablet).nodes[0]!.rect;
    expect(r.x + r.w / 2).toBeCloseTo(tablet.width / 2, 3);
  });

  it('right anchor respects the right margin', () => {
    const d = doc([box('r', 780, 100, 200, 60, { constraints: { h: 'right', v: 'top' } })]);
    const r = resolveDoc(d, tablet).nodes[0]!.rect;
    expect(tablet.width - (r.x + r.w)).toBeCloseTo((DESIGN.w - 780 - 200) * (tablet.width / DESIGN.w), 3);
  });
});

describe('solver: clamps and aspect', () => {
  it('maxW clamps the width and aspect re-derives height without distortion', () => {
    const d = doc([
      box('t', 140, 300, 800, 236, {
        constraints: { h: 'center', v: 'top', maxW: 400, aspect: 0.295 },
      }),
    ]);
    const r = resolveDoc(d, tablet).nodes[0]!.rect;
    // tablet density = 2560 / 1280 = 2 -> 400dp = 800px
    expect(r.w).toBeCloseTo(800, 3);
    expect(r.h).toBeCloseTo(800 * 0.295, 3);
  });

  it('minH wins over aspect and shrinks the width instead of stretching the shape', () => {
    const d = doc([
      box('btn', 100, 2100, 108, 108, {
        constraints: { h: 'left', v: 'bottom', aspect: 1, minH: 60 },
      }),
    ]);
    const r = resolveDoc(d, phone).nodes[0]!.rect;
    expect(r.h).toBeCloseTo(60 * 3, 3); // density 3
    expect(r.w).toBeCloseTo(r.h, 3);
  });
});

describe('solver: auto layout', () => {
  it('stacks children with gap and padding, and hug grows the parent', () => {
    const d = doc([
      box('card', 90, 800, 900, 100, {
        constraints: { h: 'center', v: 'top' },
        sizing: { w: 'fixed', h: 'hug' },
        autoLayout: { dir: 'col', gap: 20, pad: [30, 30, 30, 30], main: 'start', cross: 'stretch' },
        children: [
          box('a', 0, 0, 100, 40, { sizing: { w: 'fill', h: 'hug' } }),
          box('b', 0, 0, 100, 60, { sizing: { w: 'fill', h: 'hug' } }),
        ],
      }),
    ]);
    const scene = resolveDoc(d, phone);
    const card = scene.nodes[0]!;
    expect(card.rect.h).toBeCloseTo(30 + 40 + 20 + 60 + 30, 3);
    const [a, b] = card.children;
    expect(a!.rect.x).toBeCloseTo(card.rect.x + 30, 3);
    expect(a!.rect.w).toBeCloseTo(card.rect.w - 60, 3);
    expect(b!.rect.y).toBeCloseTo(a!.rect.y + a!.rect.h + 20, 3);
  });

  it('hug text height follows wrapped line count', () => {
    const text: Node = {
      ...box('q', 0, 0, 400, 10),
      kind: 'text',
      sizing: { w: 'fixed', h: 'hug' },
      text: {
        content: '所爱隔山河，山河皆可平',
        size: 40,
        color: '#fff',
        weight: 'regular',
        align: 'left',
        lineHeight: 1.4,
        maxLines: 3,
        autoShrink: true,
        letterSpacing: 0,
      },
    };
    const wide = resolveDoc(doc([{ ...text, frame: { x: 0, y: 0, w: 900, h: 10 } }]), phone).nodes[0]!;
    const narrow = resolveDoc(doc([{ ...text, frame: { x: 0, y: 0, w: 200, h: 10 } }]), phone).nodes[0]!;
    expect(narrow.rect.h).toBeGreaterThan(wide.rect.h);
    expect(wide.rect.h).toBeCloseTo(40 * 1.4, 3);
  });
});

describe('solver: variants', () => {
  const variantDoc = doc([
    box('n', 100, 100, 200, 100, {
      variants: {
        expanded: { frame: { x: 400, y: 200 } },
        'compact-square': { frame: { x: 50, y: 50 } },
      },
    }),
  ]);

  it('applies the breakpoint variant', () => {
    const r = resolveDoc(variantDoc, tablet).nodes[0]!;
    expect(r.design.x).toBe(400);
  });

  it('applies the square overlay on top of the breakpoint variant', () => {
    const r = resolveDoc(variantDoc, deviceById('fold-cover-980')!).nodes[0]!;
    expect(r.design.x).toBe(50);
  });

  it('ignores variants when asked, for base-layout comparison', () => {
    const r = resolveDoc(variantDoc, tablet, { ignoreVariants: true }).nodes[0]!;
    expect(r.design.x).toBe(100);
  });

  it('hidden variants drop the node from the layout', () => {
    const d = doc([box('x', 0, 0, 10, 10, { variants: { 'medium-landscape': { hidden: true } } })]);
    const r = resolveDoc(d, deviceById('fold-inner-2120')!).nodes[0]!;
    expect(r.visible).toBe(false);
  });
});

describe('solver: safe area', () => {
  it('pushes a top-anchored element below the status bar', () => {
    const d = doc([box('w', 700, 0, 200, 60, { constraints: { h: 'right', v: 'top' }, safeArea: 'top' })]);
    const r = resolveDoc(d, phone).nodes[0]!.rect;
    expect(r.y).toBeGreaterThanOrEqual(phone.insets.top);
  });

  it('lifts a bottom-anchored element above the navigation bar', () => {
    const d = doc([box('s', 90, 2000, 900, 200, { constraints: { h: 'center', v: 'bottom' }, safeArea: 'bottom' })]);
    const r = resolveDoc(d, phone).nodes[0]!.rect;
    expect(r.y + r.h).toBeLessThanOrEqual(phone.height - phone.insets.bottom + 0.001);
  });
});

describe('text metrics', () => {
  it('wraps CJK per character and keeps latin words intact enough', () => {
    const style = { weight: 'regular' as const, letterSpacing: 0 };
    const lines = wrapText('所爱隔山河，山河皆可平', 40, 200, style);
    expect(lines.length).toBeGreaterThan(1);
    expect(measureText('ab', 40, style)).toBeLessThan(measureText('中文', 40, style));
  });
});

describe('demo document', () => {
  it('passes every device without errors or warnings', () => {
    const demo = createDemoDoc();
    for (const device of DEVICES) {
      const scene = resolveDoc(demo, device);
      const issues = validateScene(scene);
      expect(issues.filter((i) => i.severity === 'error'), `${device.id} errors`).toEqual([]);
      expect(issues.filter((i) => i.severity === 'warn'), `${device.id} warns`).toEqual([]);
    }
  });

  it('keeps the unlock control inside the bottom safe area everywhere', () => {
    const demo = createDemoDoc();
    for (const device of DEVICES) {
      const scene = resolveDoc(demo, device);
      const unlocker = flatten(scene.nodes).find((n) => n.id === 'unlocker')!;
      expect(unlocker.rect.y + unlocker.rect.h).toBeLessThanOrEqual(device.height - device.insets.bottom + 0.5);
    }
  });

  it('switches to a two-column layout on expanded screens', () => {
    const demo = createDemoDoc();
    const scene = resolveDoc(demo, tablet);
    const clock = flatten(scene.nodes).find((n) => n.id === 'clock')!;
    const content = flatten(scene.nodes).find((n) => n.id === 'content')!;
    expect(clock.rect.x + clock.rect.w).toBeLessThan(content.rect.x);
  });
});

describe('validator', () => {
  it('reports out-of-canvas elements', () => {
    const d = doc([box('big', 0, 0, 1080, 3000, { constraints: { h: 'scale', v: 'scale' } })]);
    const issues = validateScene(resolveDoc(d, phone));
    expect(issues.some((i) => i.code === 'out-of-canvas')).toBe(true);
  });

  it('reports overlapping text blocks', () => {
    const mk = (id: string, y: number): Node => ({
      ...box(id, 100, y, 800, 120),
      kind: 'text',
      text: {
        content: '重叠测试文字',
        size: 60,
        color: '#fff',
        weight: 'regular',
        align: 'left',
        lineHeight: 1.2,
        maxLines: 1,
        autoShrink: false,
        letterSpacing: 0,
      },
    });
    const issues = validateScene(resolveDoc(doc([mk('a', 300), mk('b', 340)]), phone));
    expect(issues.some((i) => i.code === 'text-collision')).toBe(true);
  });
});
