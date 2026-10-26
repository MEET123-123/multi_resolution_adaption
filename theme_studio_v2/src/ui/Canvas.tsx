import { useEffect, useRef, useState } from 'react';
import { flatten, type ResolvedNode, type ResolvedScene } from '../core/solve.js';
import { renderScene, renderSelection } from '../engine/renderer.js';

interface Props {
  scene: ResolvedScene;
  selectedId: string | null;
  showGuides: boolean;
  onSelect: (id: string | null) => void;
  onMove: (id: string, dx: number, dy: number) => void;
  onResize: (id: string, dw: number, dh: number) => void;
}

type DragState =
  | { mode: 'move'; id: string; lastX: number; lastY: number }
  | { mode: 'resize'; id: string; lastX: number; lastY: number }
  | null;

const HANDLE = 18;

export function Canvas({ scene, selectedId, showGuides, onSelect, onMove, onResize }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState(0.3);
  const dragRef = useRef<DragState>(null);

  const { width, height } = scene.device;

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = (): void => {
      const availW = el.clientWidth - 48;
      const availH = el.clientHeight - 48;
      const s = Math.min(availW / width, availH / height);
      setScale(Math.max(0.08, Math.min(s, 1.2)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [width, height]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * scale * dpr);
    canvas.height = Math.round(height * scale * dpr);
    canvas.style.width = `${width * scale}px`;
    canvas.style.height = `${height * scale}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(scale * dpr, 0, 0, scale * dpr, 0, 0);
    renderScene(ctx, scene, { selectedId });
    if (showGuides) {
      drawSafeArea(ctx, width, height, scene);
    }
    if (selectedId) {
      const node = flatten(scene.nodes).find((n) => n.id === selectedId);
      renderSelection(ctx, node, scale);
    }
  }, [scene, scale, selectedId, showGuides, width, height]);

  const hitTest = (px: number, py: number): ResolvedNode | undefined => {
    const nodes = flatten(scene.nodes).reverse();
    return nodes.find((n) => {
      if (!n.visible) return false;
      const { x, y, w, h } = n.rect;
      return px >= x && px <= x + w && py >= y && py <= y + h;
    });
  };

  const local = (e: React.PointerEvent): { x: number; y: number } => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return { x: (e.clientX - rect.left) / scale, y: (e.clientY - rect.top) / scale };
  };

  const onPointerDown = (e: React.PointerEvent): void => {
    const p = local(e);
    const selected = selectedId ? flatten(scene.nodes).find((n) => n.id === selectedId) : undefined;
    if (selected) {
      const { x, y, w, h } = selected.rect;
      if (Math.abs(p.x - (x + w)) < HANDLE / scale && Math.abs(p.y - (y + h)) < HANDLE / scale) {
        dragRef.current = { mode: 'resize', id: selected.id, lastX: p.x, lastY: p.y };
        (e.target as Element).setPointerCapture(e.pointerId);
        return;
      }
    }
    const hit = hitTest(p.x, p.y);
    onSelect(hit?.id ?? null);
    if (hit) {
      dragRef.current = { mode: 'move', id: hit.id, lastX: p.x, lastY: p.y };
      (e.target as Element).setPointerCapture(e.pointerId);
    }
  };

  const onPointerMove = (e: React.PointerEvent): void => {
    const drag = dragRef.current;
    if (!drag) return;
    const p = local(e);
    const dx = p.x - drag.lastX;
    const dy = p.y - drag.lastY;
    drag.lastX = p.x;
    drag.lastY = p.y;
    if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) return;
    if (drag.mode === 'move') onMove(drag.id, dx, dy);
    else onResize(drag.id, dx, dy);
  };

  const onPointerUp = (e: React.PointerEvent): void => {
    dragRef.current = null;
    (e.target as Element).releasePointerCapture?.(e.pointerId);
  };

  return (
    <div className="canvas-wrap" ref={wrapRef}>
      <div className="canvas-stage" style={{ width: width * scale, height: height * scale }}>
        <canvas
          ref={canvasRef}
          className="stage-canvas"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
        />
        <div className="stage-badge">
          {width} × {height}
        </div>
      </div>
    </div>
  );
}

function drawSafeArea(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  scene: ResolvedScene,
): void {
  const { top, bottom } = scene.device.insets;
  ctx.save();
  ctx.fillStyle = 'rgba(47,124,246,0.10)';
  ctx.fillRect(0, 0, w, top);
  ctx.fillRect(0, h - bottom, w, bottom);
  ctx.strokeStyle = 'rgba(47,124,246,0.45)';
  ctx.setLineDash([6, 4]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, top);
  ctx.lineTo(w, top);
  ctx.moveTo(0, h - bottom);
  ctx.lineTo(w, h - bottom);
  ctx.stroke();
  ctx.restore();
}
