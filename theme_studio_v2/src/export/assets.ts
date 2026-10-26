import type { Asset, Doc, Node } from '../core/types.js';
import type { ResolvedScene } from '../core/solve.js';
import { safeAssetPath } from '../core/document.js';
import { materialize } from './materialize.js';
import type { ZipEntry } from './zip.js';

export function decodeAsset(asset: Asset): Uint8Array {
  return Uint8Array.from(atob(asset.base64), (c) => c.charCodeAt(0));
}

/** Package real referenced bytes, never replace absent artwork with preview placeholders. */
export function resourceFiles(doc: Doc, scene?: ResolvedScene): ZipEntry[] {
  const assets = new Map((doc.assets ?? []).map((a) => [safeAssetPath(a.path), a]));
  const needed = new Set<string>();
  const missing: string[] = [];
  const visit = (nodes: Node[]): void =>
    nodes.forEach((n) => {
      if (n.visible === false) return;
      if (n.kind === 'image' || (n.kind === 'time' && n.image?.src)) {
        const src = n.image?.src;
        if (!src) missing.push(`${n.id}: 未指定图片`);
        else if (n.kind === 'time') {
          throw new Error(
            `位图时钟 ${n.id} 需要整套数字资源族缩放；当前设计导出请使用文字时钟，已有主题请使用 Python 适配流程。`,
          );
        } else if (assets.has(src)) needed.add(src);
        else missing.push(src);
      }
      visit(n.children ?? []);
    });
  visit(scene ? materialize(doc, scene) : doc.root);
  if (missing.length)
    throw new Error(`缺少交付素材（预览占位图不参与打包）：${missing.slice(0, 12).join('、')}`);
  return [...needed]
    .sort()
    .map((path) => ({ name: `unlock/lockscreen/${path}`, data: decodeAsset(assets.get(path)!) }));
}
