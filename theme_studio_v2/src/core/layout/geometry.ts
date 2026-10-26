import type { DeviceProfile, Frame } from '../types.js';

export function union(rects: Frame[]): Frame {
  if (!rects.length) return { x: 0, y: 0, w: 0, h: 0 };
  const x = Math.min(...rects.map((r) => r.x));
  const y = Math.min(...rects.map((r) => r.y));
  return {
    x,
    y,
    w: Math.max(...rects.map((r) => r.x + r.w)) - x,
    h: Math.max(...rects.map((r) => r.y + r.h)) - y,
  };
}

export function intersection(a: Frame, b: Frame): number {
  return (
    Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) *
    Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y))
  );
}

export function contains(a: Frame, b: Frame, tolerance = 0.5): boolean {
  return (
    b.x >= a.x - tolerance &&
    b.y >= a.y - tolerance &&
    b.x + b.w <= a.x + a.w + tolerance &&
    b.y + b.h <= a.y + a.h + tolerance
  );
}

/** Maximal rectangles after removing physical occlusions. Creases alone do not remove pixels. */
export function usableRegions(device: DeviceProfile, margin: number): Frame[] {
  const i = device.insets;
  let regions: Frame[] = [
    {
      x: i.left + margin,
      y: i.top + margin,
      w: Math.max(0, device.width - i.left - i.right - 2 * margin),
      h: Math.max(0, device.height - i.top - i.bottom - 2 * margin),
    },
  ];
  for (const feature of device.features ?? []) {
    if (!feature.separating && !feature.occluding) continue;
    const b = feature.bounds;
    regions = regions
      .flatMap((r) => {
        // A separating zero-width crease still partitions the available surface.
        const vertical = b.h >= r.h * 0.8;
        const horizontal = b.w >= r.w * 0.8;
        if (vertical && b.x >= r.x && b.x <= r.x + r.w) {
          return [
            { ...r, w: Math.max(0, b.x - r.x - margin / 2) },
            { ...r, x: b.x + b.w + margin / 2, w: Math.max(0, r.x + r.w - b.x - b.w - margin / 2) },
          ];
        }
        if (horizontal && b.y >= r.y && b.y <= r.y + r.h) {
          return [
            { ...r, h: Math.max(0, b.y - r.y - margin / 2) },
            { ...r, y: b.y + b.h + margin / 2, h: Math.max(0, r.y + r.h - b.y - b.h - margin / 2) },
          ];
        }
        if (!intersection(r, b)) return [r];
        return [
          { ...r, h: Math.max(0, b.y - r.y - margin / 2) },
          { ...r, y: b.y + b.h + margin / 2, h: Math.max(0, r.y + r.h - b.y - b.h - margin / 2) },
          { ...r, w: Math.max(0, b.x - r.x - margin / 2) },
          { ...r, x: b.x + b.w + margin / 2, w: Math.max(0, r.x + r.w - b.x - b.w - margin / 2) },
        ];
      })
      .filter((r) => r.w > 1 && r.h > 1);
  }
  // Local cutouts create overlapping maximal rectangles. Keep only disjoint
  // panes; otherwise allocating one component to each could create collisions.
  const selected: Frame[] = [];
  for (const r of regions.sort((a, b) => b.w * b.h - a.w * a.h)) {
    if (!selected.some((s) => intersection(s, r) > 0.5)) selected.push(r);
  }
  return selected.sort((a, b) => a.y - b.y || a.x - b.x);
}
