import type { Asset } from '../core/types.js';

const cache = new Map<string, HTMLImageElement>();
const keyOf = (asset: Asset): string => `${asset.path}:${asset.base64}`;

export async function loadImages(assets: Asset[]): Promise<void> {
  await Promise.all(
    assets.map(async (asset) => {
      const key = keyOf(asset);
      if (cache.has(key)) return;
      const image = new Image();
      image.src = `data:${asset.mime};base64,${asset.base64}`;
      await image.decode();
      cache.set(key, image);
    }),
  );
  const keep = new Set(assets.map(keyOf));
  for (const key of cache.keys()) if (!keep.has(key)) cache.delete(key);
}

export function imageFor(path: string | undefined, assets: Asset[]): HTMLImageElement | undefined {
  const asset = assets.find((a) => a.path === path);
  return asset ? cache.get(keyOf(asset)) : undefined;
}

export function readImageAsset(file: File): Promise<Asset> {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 30 * 1024 * 1024)
    return Promise.reject(new Error('素材限 PNG/JPEG/WebP，单个文件不超过 30MB'));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`读取素材失败：${file.name}`));
    reader.onload = () =>
      resolve({ path: file.name, mime: file.type, base64: String(reader.result).split(',')[1]! });
    reader.readAsDataURL(file);
  });
}
