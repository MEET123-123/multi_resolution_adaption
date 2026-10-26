import type { Breakpoint, DeviceProfile, Doc, ShapeClass } from './types.js';

/**
 * Device catalog.
 *
 * Widths are physical portrait px for phones and physical px for tablets.
 * `widthDp` is the logical width the platform reports; density is derived, so
 * dp-based clamps behave like Android/HarmonyOS.
 */
export const DEVICES: DeviceProfile[] = [
  {
    id: 'phone-legacy',
    label: '1080 × 2160 · 18:9 旧标准',
    short: '1080×2160',
    formFactor: 'phone',
    width: 1080,
    height: 2160,
    widthDp: 360,
    insets: { top: 72, bottom: 48, left: 0, right: 0 },
    note: '旧版 ThemeStudio 的唯一画布，仅用于回归对比。',
  },
  {
    id: 'phone-1080x2340',
    label: '1080 × 2340 · 19.5:9',
    short: '1080×2340',
    formFactor: 'phone-tall',
    width: 1080,
    height: 2340,
    widthDp: 360,
    insets: { top: 84, bottom: 60, left: 0, right: 0 },
  },
  {
    id: 'phone-1080x2400',
    label: '1080 × 2400 · 20:9',
    short: '1080×2400',
    formFactor: 'phone-tall',
    width: 1080,
    height: 2400,
    widthDp: 360,
    insets: { top: 84, bottom: 60, left: 0, right: 0 },
  },
  {
    id: 'phone-1320x2232',
    label: '1320 × 2232 · 宽屏直板',
    short: '1320×2232',
    formFactor: 'phone',
    width: 1320,
    height: 2232,
    widthDp: 440,
    insets: { top: 96, bottom: 64, left: 0, right: 0 },
    note: '宽幅画布示例；不是任何机型的官方参数。',
  },
  {
    id: 'fold-cover-980',
    label: '980 × 980 · 折叠外屏',
    short: '980×980',
    formFactor: 'fold-cover',
    width: 980,
    height: 980,
    widthDp: 327,
    insets: { top: 64, bottom: 48, left: 0, right: 0 },
    note: '近方形画布，纵向约束必须切到 center/scale。',
  },
  {
    id: 'fold-inner-2120',
    label: '2120 × 1320 · 折叠内屏',
    short: '2120×1320',
    formFactor: 'fold-inner',
    width: 2120,
    height: 1320,
    widthDp: 707,
    insets: { top: 48, bottom: 40, left: 64, right: 64 },
    note: '展开态横屏，需要 medium 断点的双栏变体。',
  },
  {
    id: 'tablet-2560x1600',
    label: '2560 × 1600 · 平板横屏',
    short: '2560×1600',
    formFactor: 'tablet-land',
    width: 2560,
    height: 1600,
    widthDp: 1280,
    insets: { top: 56, bottom: 48, left: 96, right: 96 },
    note: 'expanded 断点，内容应限制在 840dp 内容列内。',
  },
  {
    id: 'dual-unfolded-example',
    label: '2160 × 2340 · 双折叠展开示例',
    short: '双折展开',
    formFactor: 'fold-inner',
    width: 2160,
    height: 2340,
    widthDp: 720,
    insets: { top: 84, bottom: 60, left: 0, right: 0 },
    posture: 'flat',
    note: '按照直板机两倍宽度配置的示例，不代表官方机型参数。',
  },
  {
    id: 'tri-unfolded-example',
    label: '3240 × 2340 · 三折展开示例',
    short: '三折展开',
    formFactor: 'tri-fold',
    width: 3240,
    height: 2340,
    widthDp: 1080,
    insets: { top: 84, bottom: 60, left: 0, right: 0 },
    posture: 'flat',
    note: '可配置的三倍宽度示例；折痕不自动视为不可用像素。',
  },
  {
    id: 'wide-fold-example',
    label: '1610 × 1132 · 宽折叠示例',
    short: '宽折叠',
    formFactor: 'wide-fold',
    width: 1610,
    height: 1132,
    widthDp: 600,
    insets: { top: 48, bottom: 48, left: 32, right: 32 },
    posture: 'flat',
    note: '用于 Pura 类宽比例的自定义工作流；尺寸是可替换的测试参数。',
  },
  {
    id: 'book-posture-example',
    label: '2200 × 1800 · 双窗书本姿态示例',
    short: '书本双窗',
    formFactor: 'fold-inner',
    width: 2200,
    height: 1800,
    widthDp: 880,
    insets: { top: 50, bottom: 50, left: 24, right: 24 },
    posture: 'half-open',
    features: [
      {
        kind: 'hinge',
        bounds: { x: 1088, y: 0, w: 24, h: 1800 },
        separating: true,
        occluding: true,
      },
    ],
    note: '刻意包含遮挡中缝的合成示例；普通柔性折痕无需配置遮挡。',
  },
  {
    id: 'tablet-1600x2560',
    label: '1600 × 2560 · 平板竖屏',
    short: '1600×2560',
    formFactor: 'tablet',
    width: 1600,
    height: 2560,
    widthDp: 800,
    insets: { top: 64, bottom: 56, left: 48, right: 48 },
    note: 'medium 断点，宽高比接近直板机但字号需要收敛。',
  },
];

export function rotateDevice(device: DeviceProfile): DeviceProfile {
  const d = device;
  const i = d.insets;
  return {
    ...d,
    id: `${d.id}-rotated`,
    label: `${d.label} · 旋转`,
    short: `${d.height}×${d.width}`,
    width: d.height,
    height: d.width,
    widthDp: d.height / densityOf(d),
    insets: { top: i.left, right: i.top, bottom: i.right, left: i.bottom },
    features: d.features?.map((f) => ({
      ...f,
      bounds: {
        x: d.height - f.bounds.y - f.bounds.h,
        y: f.bounds.x,
        w: f.bounds.h,
        h: f.bounds.w,
      },
    })),
  };
}

/** JSON-authored targets replace catalog entries by id and can use any resolution. */
export function devicesFor(doc: Doc, includeRotations = false): DeviceProfile[] {
  const targets = new Map(DEVICES.map((d) => [d.id, d]));
  for (const d of doc.devices ?? []) targets.set(d.id, d);
  const base = [...targets.values()];
  return includeRotations
    ? [...base, ...base.map(rotateDevice).filter((d) => !targets.has(d.id))]
    : base;
}

export const DEFAULT_BREAKPOINTS = { medium: 600, expanded: 840 };

export function deviceById(id: string): DeviceProfile | undefined {
  return DEVICES.find((d) => d.id === id);
}

/** Aspect-ratio class, the equivalent of a CSS `aspect-ratio` media query. */
export function shapeOf(device: DeviceProfile): ShapeClass {
  const ratio = device.width / device.height;
  if (ratio > 1.15) return 'landscape';
  if (ratio >= 0.87) return 'square';
  return 'portrait';
}

export function densityOf(device: DeviceProfile): number {
  return device.width / device.widthDp;
}

/** Android / HarmonyOS compatible width breakpoints. */
export function breakpointOf(
  device: DeviceProfile,
  bp: { medium: number; expanded: number } = DEFAULT_BREAKPOINTS,
): Breakpoint {
  if (device.widthDp >= bp.expanded) return 'expanded';
  if (device.widthDp >= bp.medium) return 'medium';
  return 'compact';
}

/** Devices grouped for the export report. */
export function familyOf(device: DeviceProfile): 'phone' | 'fold' | 'tablet' {
  if (device.formFactor.includes('fold')) return 'fold';
  if (device.formFactor.startsWith('tablet')) return 'tablet';
  return 'phone';
}
