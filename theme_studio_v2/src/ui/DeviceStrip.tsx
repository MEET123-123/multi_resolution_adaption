import { useEffect, useRef } from 'react';
import { DEVICES } from '../core/devices.js';
import type { ResolvedScene } from '../core/solve.js';
import { renderScene } from '../engine/renderer.js';

interface Props {
  scenes: ResolvedScene[];
  currentId: string;
  issueCount: Record<string, number>;
  onSelect: (id: string) => void;
}

const THUMB_H = 104;

export function DeviceStrip({ scenes, currentId, issueCount, onSelect }: Props) {
  return (
    <div className="device-strip">
      {scenes.map((scene) => (
        <DeviceCard
          key={scene.device.id}
          scene={scene}
          active={scene.device.id === currentId}
          issues={issueCount[scene.device.id] ?? 0}
          onClick={() => onSelect(scene.device.id)}
        />
      ))}
    </div>
  );
}

function DeviceCard({
  scene,
  active,
  issues,
  onClick,
}: {
  scene: ResolvedScene;
  active: boolean;
  issues: number;
  onClick: () => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const { width, height } = scene.device;
  const scale = THUMB_H / height;
  const w = Math.round(width * scale);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(THUMB_H * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${THUMB_H}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(scale * dpr, 0, 0, scale * dpr, 0, 0);
    renderScene(ctx, scene);
  }, [scene, scale, w]);

  return (
    <button
      className={`device-card${active ? ' active' : ''}`}
      onClick={onClick}
      title={scene.device.label}
    >
      <div className="device-thumb" style={{ height: THUMB_H }}>
        <canvas ref={ref} />
      </div>
      <div className="device-meta">
        <span className="device-name">{scene.device.short}</span>
        <span className={`bp bp-${scene.breakpoint}`}>{scene.breakpoint}</span>
      </div>
      <div className="device-foot">
        <span className="device-dp">{scene.device.widthDp}dp</span>
        {issues > 0 ? (
          <span className="device-issues">{issues}</span>
        ) : (
          <span className="device-ok">✓</span>
        )}
      </div>
    </button>
  );
}

export { DEVICES };
