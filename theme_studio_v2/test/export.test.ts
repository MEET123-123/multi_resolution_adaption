import { describe, expect, it } from 'vitest';
import { DEVICES, deviceById } from '../src/core/devices.js';
import { resolveDoc } from '../src/core/solve.js';
import { exportLockscreenXml } from '../src/export/xml.js';
import { buildPackageFiles, packHwt } from '../src/export/hwt.js';
import { exportBundle } from '../src/export/bundle.js';
import { createZip, crc32 } from '../src/export/zip.js';
import { createDemoDoc } from '../src/demo/lockscreen.js';

const demo = createDemoDoc();
const tablet = deviceById('tablet-2560x1600')!;

describe('responsive export', () => {
  const xml = exportLockscreenXml(demo, { mode: 'responsive' });

  it('declares the scale variables used by every coordinate', () => {
    expect(xml).toContain('<Var name="ts_sx" expression="#screen_width/1080"');
    expect(xml).toContain('<Var name="ts_sy" expression="#screen_height/2340"');
    expect(xml).toContain('name="ts_s"');
  });

  it('never emits the legacy 2160 height hack', () => {
    expect(xml).not.toContain('#screen_height-2160');
  });

  it('anchors the unlocker to the bottom with an expression', () => {
    const line = xml.split('\n').find((l) => l.includes('name="上滑解锁"'))!;
    // y is expressed against the height ratio, so it tracks the real bottom
    expect(line).toMatch(/y="#ts_sy\*\d/);
  });

  it('keeps the wallpaper tied to both axes', () => {
    const line = xml.split('\n').find((l) => l.includes('name="壁纸"'))!;
    expect(line).toContain('w="#screen_width"');
    expect(line).toContain('h="#screen_height"');
  });

  it('warns that clamps and variants do not apply to the single-file mode', () => {
    expect(xml).toContain('dp 钳制');
    expect(xml).toContain('断点变体');
  });

  it('is well-formed enough to parse', () => {
    expect(xml.trim().endsWith('</Lockscreen>')).toBe(true);
    expect((xml.match(/<Lockscreen/g) ?? []).length).toBe(1);
    expect((xml.match(/<\/Group>/g) ?? []).length).toBe((xml.match(/<Group/g) ?? []).length);
  });
});

describe('baked export', () => {
  const scene = resolveDoc(demo, tablet);
  const xml = exportLockscreenXml(demo, { mode: 'baked', scene });

  it('uses the device width in the root element', () => {
    expect(xml).toContain('screenWidth="2560"');
  });

  it('writes absolute pixels that match the solved layout', () => {
    const clock = scene.nodes.find((n) => n.id === 'clock')!;
    const line = xml.split('\n').find((l) => l.includes('name="时钟组"'))!;
    expect(line).toContain(`x="${Math.round(clock.rect.x * 10000) / 10000}"`);
    expect(line).toContain(`y="${Math.round(clock.rect.y * 10000) / 10000}"`);
  });

  it('has no expressions left', () => {
    expect(xml).not.toContain('#ts_sx');
    expect(xml).not.toContain('#screen_width');
  });

  it('refuses to run without a scene', () => {
    expect(() => exportLockscreenXml(demo, { mode: 'baked' })).toThrow();
  });
});

describe('hwt package', () => {
  it('contains the three files the theme manager expects', () => {
    const files = buildPackageFiles(demo, { mode: 'responsive', title: 'T' });
    const names = files.map((f) => f.name);
    expect(names).toContain('description.xml');
    expect(names).toContain('unlock/theme.xml');
    expect(names).toContain('unlock/lockscreen/manifest.xml');
  });

  it('does not cap the device height at the design height', () => {
    const files = buildPackageFiles(demo, { mode: 'responsive' });
    const theme = files.find((f) => f.name === 'unlock/theme.xml')!;
    const text = new TextDecoder().decode(theme.data);
    expect(text).toContain('multiResolution="true"');
    expect(text).toContain('<item maxHeight="2340"/>');
  });

  it('produces a valid zip', () => {
    const bytes = packHwt(demo, { mode: 'responsive' });
    expect(bytes[0]).toBe(0x50);
    expect(bytes[1]).toBe(0x4b);
    expect(bytes[2]).toBe(0x03);
    expect(bytes[3]).toBe(0x04);
    // end of central directory signature
    const tail = Array.from(bytes.slice(-22, -18));
    expect(tail).toEqual([0x50, 0x4b, 0x05, 0x06]);
  });
});

describe('zip writer', () => {
  it('computes the standard crc32 for a known payload', () => {
    // "123456789" -> 0xCBF43926
    expect(crc32(new TextEncoder().encode('123456789'))).toBe(0xcbf43926);
  });

  it('records one central directory entry per file', () => {
    const bytes = createZip([
      { name: 'a.txt', data: new TextEncoder().encode('a') },
      { name: 'b/c.txt', data: new TextEncoder().encode('cc') },
    ]);
    const view = new DataView(bytes.buffer);
    const eocd = bytes.length - 22;
    expect(view.getUint32(eocd, true)).toBe(0x06054b50);
    expect(view.getUint16(eocd + 10, true)).toBe(2);
  });
});

describe('bundle export', () => {
  const { bytes, summary } = exportBundle(demo);

  it('covers every catalog device', () => {
    expect(summary).toContain(`${DEVICES.length} 台设备`);
    expect(bytes.length).toBeGreaterThan(2000);
  });

  it('reports zero errors for the demo on all devices', () => {
    expect(summary).toContain(`${DEVICES.length} 台无错误`);
  });
});
