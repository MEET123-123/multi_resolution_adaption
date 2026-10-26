import type { ResolvedNode, ResolvedScene } from '../core/solve.js';
import type { Asset } from '../core/types.js';
import { imageFor } from './images.js';

export interface DrawOptions {
  assets?: Asset[];
  /** Show element outlines (selection aids). */
  debug?: boolean;
  selectedId?: string | null;
}

const FONT_STACK = '"HarmonyOS Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif';

function rgba(hex: string, alpha: number): string {
  const h = hex.replace('#', '');
  const full =
    h.length === 3
      ? h
          .split('')
          .map((c) => c + c)
          .join('')
      : h;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function drawText(ctx: CanvasRenderingContext2D, n: ResolvedNode): void {
  const t = n.text;
  if (!t) return;
  const size = t.effectiveSize;
  const weight = t.weight === 'bold' ? 700 : t.weight === 'medium' ? 500 : 400;
  ctx.save();
  ctx.font = `${weight} ${size}px ${FONT_STACK}`;
  ctx.fillStyle = rgba(t.color, (n.alpha ?? 255) / 255);
  ctx.textBaseline = 'middle';

  const lines: string[] = [];
  let current = '';
  const maxW = n.rect.w;
  for (const ch of t.content) {
    const probe = current + ch;
    if (ctx.measureText(probe).width > maxW && current !== '') {
      lines.push(current);
      current = ch;
      if (lines.length >= t.maxLines) break;
    } else {
      current = probe;
    }
  }
  if (lines.length < t.maxLines && current !== '') lines.push(current);

  const lineH = size * t.lineHeight;
  const totalH = lines.length * lineH;
  let y = n.rect.y + (n.rect.h - totalH) / 2 + lineH / 2;

  for (const line of lines) {
    let x = n.rect.x;
    if (t.align === 'center') {
      ctx.textAlign = 'center';
      x = n.rect.x + n.rect.w / 2;
    } else if (t.align === 'right') {
      ctx.textAlign = 'right';
      x = n.rect.x + n.rect.w;
    } else {
      ctx.textAlign = 'left';
    }
    ctx.fillText(line, x, y);
    y += lineH;
  }
  ctx.restore();
}

function drawNode(ctx: CanvasRenderingContext2D, n: ResolvedNode, options: DrawOptions): void {
  if (!n.visible) return;
  const { x, y, w, h } = n.rect;

  switch (n.kind) {
    case 'image':
      {
        const image = imageFor(n.image?.src, options.assets ?? []);
        ctx.save();
        ctx.globalAlpha = (n.alpha ?? 255) / 255;
        if (image) {
          const crop = n.image?.scaleType === 'centerCrop';
          const fit = n.image?.scaleType === 'contain';
          const s = crop
            ? Math.max(w / image.naturalWidth, h / image.naturalHeight)
            : Math.min(w / image.naturalWidth, h / image.naturalHeight);
          const iw = crop || fit ? image.naturalWidth * s : w;
          const ih = crop || fit ? image.naturalHeight * s : h;
          ctx.beginPath();
          ctx.rect(x, y, w, h);
          ctx.clip();
          ctx.drawImage(image, x + (w - iw) / 2, y + (h - ih) / 2, iw, ih);
        } else {
          roundRect(ctx, x, y, w, h, n.radius ?? 0);
          ctx.fillStyle = rgba(n.fill ?? '#3b4a66', (n.alpha ?? 255) / 255);
          ctx.fill();
          ctx.fillStyle = '#FFFFFF';
          ctx.font = '24px sans-serif';
          ctx.fillText(`缺少素材：${n.image?.src ?? n.id}`, x + 12, y + 32);
        }
        ctx.restore();
      }
      break;
    case 'rect':
      ctx.save();
      roundRect(ctx, x, y, w, h, n.radius ?? 0);
      ctx.fillStyle = rgba(n.fill ?? '#FFFFFF', (n.alpha ?? 255) / 255);
      ctx.fill();
      ctx.restore();
      break;
    case 'text':
    case 'date':
      drawText(ctx, n);
      break;
    case 'time': {
      const size = n.text?.effectiveSize ?? 200;
      ctx.save();
      ctx.font = `700 ${size}px ${FONT_STACK}`;
      ctx.fillStyle = rgba(n.text?.color ?? '#FFFFFF', (n.alpha ?? 255) / 255);
      ctx.textAlign =
        n.text?.align === 'left' ? 'left' : n.text?.align === 'right' ? 'right' : 'center';
      ctx.textBaseline = 'middle';
      const tx = n.text?.align === 'left' ? x : n.text?.align === 'right' ? x + w : x + w / 2;
      ctx.fillText(n.text?.content ?? '09:41', tx, y + h / 2);
      ctx.restore();
      break;
    }
    case 'unlocker':
      // A hot area has no artwork of its own. Designers add a text/image node.
      break;
    case 'button': {
      ctx.save();
      roundRect(ctx, x, y, w, h, n.radius ?? w / 2);
      ctx.fillStyle = rgba(n.fill ?? '#FFFFFF', (n.alpha ?? 255) / 255);
      ctx.fill();
      ctx.restore();
      break;
    }
    case 'group':
      if (n.fill) {
        ctx.save();
        roundRect(ctx, x, y, w, h, n.radius ?? 0);
        ctx.fillStyle = rgba(n.fill, (n.alpha ?? 255) / 255);
        ctx.fill();
        ctx.restore();
      }
      break;
    default:
      break;
  }

  for (const child of n.children) drawNode(ctx, child, options);

  if (options.debug) {
    ctx.save();
    ctx.strokeStyle = 'rgba(90,180,255,0.55)';
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, w, h);
    ctx.restore();
  }
}

export function renderScene(
  ctx: CanvasRenderingContext2D,
  scene: ResolvedScene,
  options: DrawOptions = {},
): void {
  const { width, height } = scene.device;
  ctx.clearRect(0, 0, width, height);
  for (const node of scene.nodes) drawNode(ctx, node, { ...options, assets: scene.assets });
}

/** Draw selection outline + resize handle in device space. */
export function renderSelection(
  ctx: CanvasRenderingContext2D,
  node: ResolvedNode | undefined,
  scale: number,
): void {
  if (!node) return;
  const { x, y, w, h } = node.rect;
  const px = 1 / scale;
  ctx.save();
  ctx.strokeStyle = '#2f7cf6';
  ctx.lineWidth = 1.5 * px;
  ctx.setLineDash([5 * px, 3 * px]);
  ctx.strokeRect(x, y, w, h);
  ctx.setLineDash([]);
  ctx.fillStyle = '#2f7cf6';
  const s = 7 * px;
  ctx.fillRect(x + w - s / 2, y + h - s / 2, s, s);
  ctx.restore();
}
