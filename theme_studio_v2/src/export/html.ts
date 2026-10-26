/** Standalone inspection gallery using solved geometry; not a native-engine screenshot. */
import type { ResolvedNode, ResolvedScene } from '../core/solve.js';
import { wrapText } from '../core/solve.js';
import { summarize, validateScene } from '../core/validate.js';

const esc = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function draw(node: ResolvedNode, scene: ResolvedScene): string {
  if (!node.visible) return '';
  const r = node.rect;
  const opacity = (node.alpha ?? 255) / 255;
  const shapes: string[] = [];
  const a = node.image?.src ? scene.assets?.find((a) => a.path === node.image?.src) : undefined;
  if (node.kind === 'image' && a) {
    const aspect =
      node.image?.scaleType === 'fill'
        ? 'none'
        : node.image?.scaleType === 'centerCrop'
          ? 'xMidYMid slice'
          : 'xMidYMid meet';
    shapes.push(
      `<image x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" opacity="${opacity}" preserveAspectRatio="${aspect}" href="data:${esc(a.mime)};base64,${a.base64}"/>`,
    );
  } else if (node.fill && node.kind !== 'unlocker') {
    shapes.push(
      `<rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="${node.radius ?? 0}" fill="${esc(node.fill)}" opacity="${opacity}"/>`,
    );
  }
  const t = node.text;
  if (t) {
    const x = t.align === 'center' ? r.x + r.w / 2 : t.align === 'right' ? r.x + r.w : r.x;
    const anchor = t.align === 'center' ? 'middle' : t.align === 'right' ? 'end' : 'start';
    const lines = wrapText(t.content, t.effectiveSize, r.w, t)
      .slice(0, t.maxLines)
      .map((l) => l.join(''));
    const lineH = t.effectiveSize * t.lineHeight;
    const y = r.y + (r.h - lines.length * lineH) / 2 + lineH / 2;
    shapes.push(
      `<text fill="${esc(t.color)}" opacity="${opacity}" font-size="${t.effectiveSize}" font-weight="${t.weight === 'bold' ? 700 : t.weight === 'medium' ? 500 : 400}" text-anchor="${anchor}" dominant-baseline="central">${lines.map((line, i) => `<tspan x="${x}" y="${y + i * lineH}">${esc(line)}</tspan>`).join('')}</text>`,
    );
  }
  return shapes.join('') + node.children.map((n) => draw(n, scene)).join('');
}

export function exportPreviewHtml(name: string, scenes: ResolvedScene[]): string {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>${esc(name)}</title><style>
  body{font:14px system-ui,"Microsoft YaHei";background:#f3f5f9;color:#17202e;margin:32px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}
  article{background:white;padding:16px;border-radius:12px}svg{display:block;width:100%;height:330px;background:#e4e8ee}small,p{color:#64748b}h2{font-size:16px}a{color:#2f7cf6}
  </style><h1>${esc(name)}</h1><p>由同一设计自动推断的 ${scenes.length} 个设备/方向。以下是求解几何示意，不是鸿蒙原生截图。时间为设计预览值，导出脚本使用动态时间。</p><main>${scenes
    .map((scene) => {
      const issues = validateScene(scene);
      const s = summarize(issues);
      const d = scene.device;
      return `<article><h2>${esc(d.label)}</h2><small>${d.width} × ${d.height} · ${d.widthDp.toFixed(0)}dp · ${esc(scene.explanation?.composition ?? 'constraints')}</small>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${d.width} ${d.height}">${scene.nodes.map((n) => draw(n, scene)).join('')}${(
      d.features ?? []
    )
      .filter((f) => f.occluding)
      .map(
        (f) =>
          `<rect x="${f.bounds.x}" y="${f.bounds.y}" width="${f.bounds.w}" height="${f.bounds.h}" fill="#111827"/>`,
      )
      .join('')}</svg>
    <p>${s.errors} 错误 / ${s.warns} 警告</p><a href="devices/${d.id}/layout.json">实际布局 JSON</a>${s.errors ? '' : ` · <a href="devices/${d.id}/unlock/lockscreen/manifest.xml">目标脚本</a>`}
    ${issues.length ? `<details><summary>检查信息</summary><ul>${issues.map((i) => `<li>${esc(i.message)}</li>`).join('')}</ul></details>` : ''}</article>`;
    })
    .join('')}</main></html>`;
}
