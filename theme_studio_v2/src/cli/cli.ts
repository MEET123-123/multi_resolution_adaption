#!/usr/bin/env node
/**
 * Headless entry point — the same engine the studio uses, for batch work.
 *
 *   tsx src/cli/cli.ts demo   --out out/
 *   tsx src/cli/cli.ts verify design.json
 *   tsx src/cli/cli.ts export design.json --out out/ [--mode responsive|baked|both|bundle]
 */
import fs from 'node:fs';
import path from 'node:path';
import { devicesFor } from '../core/devices.js';
import { flatten, resolveDoc } from '../core/solve.js';
import { summarize, validateScene } from '../core/validate.js';
import { exportConversionReport, exportLockscreenXml } from '../export/xml.js';
import { buildPackageFiles } from '../export/hwt.js';
import { exportBundle } from '../export/bundle.js';
import { createAdaptiveDemoDoc } from '../demo/lockscreen.js';
import { parseDocument, safeFilename } from '../core/document.js';
import { exportPreviewHtml } from '../export/html.js';
import type { Doc } from '../core/types.js';

interface Args {
  command: string;
  file?: string;
  out: string;
  mode: string;
}

function parseArgs(argv: string[]): Args {
  const [command = 'help', ...rest] = argv;
  let file: string | undefined;
  let out = 'out';
  let mode = 'both';
  for (let i = 0; i < rest.length; i += 1) {
    const a = rest[i]!;
    if (a === '--out' || a === '-o') out = rest[++i] ?? out;
    else if (a === '--mode' || a === '-m') mode = rest[++i] ?? mode;
    else if (!a.startsWith('-')) file = a;
  }
  return { command, file, out, mode };
}

function loadDoc(file: string): Doc {
  const raw = fs.readFileSync(file, 'utf8');
  return parseDocument(raw);
}

function writeFile(file: string, data: string | Uint8Array): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, data);
}

function cmdVerify(doc: Doc): number {
  let errors = 0;
  console.log(`\n${doc.meta.name} · 设计画布 ${doc.meta.design.w}×${doc.meta.design.h}\n`);
  const scenes = devicesFor(doc, true).map((d) => resolveDoc(doc, d));
  for (const scene of scenes) {
    const issues = validateScene(scene);
    const s = summarize(issues);
    errors += s.errors;
    const flag = s.errors > 0 ? '✗' : s.warns > 0 ? '!' : '✓';
    console.log(
      `${flag} ${scene.device.id.padEnd(18)} ${String(scene.device.width).padStart(4)}×${String(scene.device.height).padEnd(4)} ` +
        `${String(scene.device.widthDp).padStart(4)}dp ${scene.breakpoint.padEnd(9)} ` +
        `sx ${scene.scale.sx.toFixed(3)} sy ${scene.scale.sy.toFixed(3)}  ${flatten(scene.nodes).length} 元素`,
    );
    for (const i of issues)
      console.log(`    [${i.severity}] ${i.code} · ${i.nodeName}：${i.message}`);
  }
  console.log(`\n${exportConversionReport(doc, scenes)}\n`);
  console.log(errors === 0 ? '全部设备无 error' : `${errors} 个 error`);
  return errors === 0 ? 0 : 1;
}

function cmdExport(doc: Doc, out: string, mode: string): void {
  const base = path.resolve(out);
  const scenes = devicesFor(doc, true).map((d) => resolveDoc(doc, d));

  writeFile(path.join(base, 'design.json'), JSON.stringify(doc, null, 2));
  writeFile(path.join(base, 'resolution-report.md'), exportConversionReport(doc, scenes));
  writeFile(path.join(base, 'preview.html'), exportPreviewHtml(doc.meta.name, scenes));

  if (mode === 'responsive' && doc.layout?.mode === 'auto')
    throw new Error('自动重构请使用 --mode baked 或 --mode bundle');
  if (
    doc.layout?.mode !== 'auto' &&
    (mode === 'responsive' || mode === 'both' || mode === 'bundle')
  ) {
    writeFile(
      path.join(base, 'responsive/manifest.xml'),
      exportLockscreenXml(doc, { mode: 'responsive' }),
    );
    for (const f of buildPackageFiles(doc, { mode: 'responsive' })) {
      writeFile(path.join(base, 'hwt-responsive', f.name), f.data);
    }
  }
  if (mode === 'baked' || mode === 'both' || mode === 'bundle') {
    for (const scene of scenes) {
      const target = path.join(base, 'devices', scene.device.id);
      const issues = validateScene(scene);
      writeFile(
        path.join(target, 'layout.json'),
        JSON.stringify({ ...scene, assets: undefined }, null, 2),
      );
      writeFile(path.join(target, 'issues.json'), JSON.stringify(issues, null, 2));
      try {
        for (const file of buildPackageFiles(doc, { mode: 'baked', scene }))
          writeFile(path.join(target, file.name), file.data);
      } catch (error) {
        writeFile(path.join(target, 'needs-review.txt'), (error as Error).message);
        console.warn(`${scene.device.id}: ${(error as Error).message}`);
      }
    }
  }
  if (mode === 'bundle') {
    const { bytes, summary } = exportBundle(doc);
    writeFile(path.join(base, `${safeFilename(doc.meta.name)}-多分辨率.zip`), bytes);
    console.log(`bundle: ${summary}`);
  }
  console.log(`已输出到 ${base}（mode=${mode}）`);
}

function cmdDemo(out: string): void {
  const doc = createAdaptiveDemoDoc();
  const base = path.resolve(out);
  writeFile(path.join(base, 'design.json'), JSON.stringify(doc, null, 2));
  cmdExport(doc, out, 'bundle');
}

function main(): void {
  const args = parseArgs(process.argv.slice(2));
  if (
    ['demo', 'export', 'infer'].includes(args.command) &&
    fs.existsSync(args.out) &&
    fs.readdirSync(args.out).length
  ) {
    throw new Error('输出目录已有文件，请指定新的 --out，避免混入旧版本的脚本。');
  }
  switch (args.command) {
    case 'demo':
      cmdDemo(args.out);
      break;
    case 'verify':
      if (!args.file) throw new Error('用法：verify <design.json>');
      process.exitCode = cmdVerify(loadDoc(args.file));
      break;
    case 'export':
      if (!args.file) throw new Error('用法：export <design.json> --out dir');
      cmdExport(loadDoc(args.file), args.out, args.mode);
      break;
    case 'infer': {
      if (!args.file) throw new Error('用法：infer <design.json> --out dir');
      const doc = loadDoc(args.file);
      const inferred = { ...doc, layout: { ...doc.layout, mode: 'auto' as const } };
      writeFile(path.join(args.out, 'design.json'), JSON.stringify(inferred, null, 2));
      const layouts = devicesFor(inferred, true).map((d) => ({
        device: d.id,
        ...resolveDoc(inferred, d).explanation,
      }));
      writeFile(path.join(args.out, 'inference.json'), JSON.stringify(layouts, null, 2));
      console.log(`已输出自动推断工程和 ${layouts.length} 个设备状态的分组说明到 ${args.out}`);
      break;
    }
    default:
      console.log(`theme-studio-v2 CLI

  demo   --out <dir>                       生成示例文档并完整导出
  verify <design.json>                     逐设备校验，有 error 时退出码 1
  export <design.json> --out <dir> [--mode responsive|baked|both|bundle]
`);
  }
}

main();
