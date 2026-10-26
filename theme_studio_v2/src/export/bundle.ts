import { createZip, type ZipEntry } from './zip.js';
import { exportConversionReport, exportLockscreenXml } from './xml.js';
import { devicesFor } from '../core/devices.js';
import { buildPackageFiles } from './hwt.js';
import { resolveDoc } from '../core/solve.js';
import { summarize, validateScene } from '../core/validate.js';
import type { Doc } from '../core/types.js';

const enc = new TextEncoder();
const entry = (name: string, text: string): ZipEntry => ({ name, data: enc.encode(text) });

export interface BundleResult {
  bytes: Uint8Array;
  summary: string;
}

/**
 * One-click multi-resolution conversion: a single archive holding the
 * responsive script, one baked script per device, and the review report that
 * says what still needs a human eye.
 */
export function exportBundle(doc: Doc): BundleResult {
  const files: ZipEntry[] = [];
  const scenes = devicesFor(doc, true).map((d) => resolveDoc(doc, d));

  files.push(entry('design.json', JSON.stringify(doc, null, 2)));
  if (doc.layout?.mode !== 'auto')
    files.push(entry('responsive/manifest.xml', exportLockscreenXml(doc, { mode: 'responsive' })));

  let lines: string[] = [];
  lines.push(`# ${doc.meta.name} · 多分辨率转换报告`);
  lines.push('');
  lines.push(`设计画布：${doc.meta.design.w} × ${doc.meta.design.h}`);
  lines.push(`设备数量：${scenes.length}`);
  lines.push('');
  lines.push('## 解算比例');
  lines.push('');
  lines.push(exportConversionReport(doc, scenes));
  lines.push('');
  lines.push('## 逐机型校验');
  lines.push('');

  for (const scene of scenes) {
    const device = scene.device;
    const issues = validateScene(scene);
    const s = summarize(issues);
    const report: string[] = [];
    try {
      for (const file of buildPackageFiles(doc, { mode: 'baked', scene })) {
        files.push({ ...file, name: `devices/${device.id}/${file.name}` });
      }
      report.push('已生成 XML、包描述、实际引用素材和交付清单；仍需原生验收。');
    } catch (error) {
      report.push(`交付被拒绝：${(error as Error).message}`);
      // Keep diagnostics and the actual solved geometry, not an invalid HWT.
    }
    files.push(
      entry(
        `devices/${device.id}/layout.json`,
        JSON.stringify({ ...scene, assets: undefined }, null, 2),
      ),
    );
    files.push(entry(`devices/${device.id}/issues.json`, JSON.stringify(issues, null, 2)));
    lines.push(`### ${device.id} · ${device.label}`);
    lines.push('');
    lines.push(`- 断点：${scene.breakpoint}（${device.widthDp}dp）`);
    lines.push(`- 缩放：sx ${scene.scale.sx.toFixed(3)} / sy ${scene.scale.sy.toFixed(3)}`);
    lines.push(
      `- 结果：${s.errors === 0 && s.warns === 0 ? '通过' : `${s.errors} 错误 / ${s.warns} 警告`}`,
    );
    for (const i of issues)
      lines.push(`  - [${i.severity}] ${i.code} · ${i.nodeName}：${i.message}`);
    lines.push(...report);
    lines.push('');

    files.push(
      entry(
        `devices/${device.id}/report.md`,
        [...report, ...issues.map((i) => `[${i.severity}] ${i.message}`)].join('\n'),
      ),
    );
  }

  lines.push('## 说明');
  lines.push('');
  lines.push(
    doc.layout?.mode === 'auto'
      ? '- 自动重构以逐设备求解结果为准，没有生成与预览不等价的线性响应式脚本。'
      : '- `responsive/manifest.xml` 仅支持旧版线性缩放，不能等价表达所有断点/内容适应规则。',
  );
  lines.push('- `devices/<机型>/` 是按该机型绝对像素烘焙的版本，平板 / 折叠屏请用这一份。');
  lines.push('- 所有脚本仍需在真机或官方引擎上验收，浏览器预览不能替代真机。');

  files.push(entry('resolution-report.md', lines.join('\n')));

  const ok = scenes.filter((s) => summarize(validateScene(s)).errors === 0).length;
  return {
    bytes: createZip(files),
    summary: `${scenes.length} 台设备 · ${ok} 台无错误 · ${files.length} 个文件`,
  };
}
