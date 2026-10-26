import { useEffect, useMemo, useRef, useState } from 'react';
import { devicesFor } from './core/devices.js';
import { activeVariantKeys, mergeVariants, resolveDoc, flatten } from './core/solve.js';
import { validateScene, summarize, type Issue } from './core/validate.js';
import { createAdaptiveDemoDoc } from './demo/lockscreen.js';
import { exportDesign, type ExportAction } from './ui/exportActions.js';
import { parseDocument } from './core/document.js';
import { ResponsivePanel } from './ui/ResponsivePanel.js';
import { IssueList } from './ui/IssueList.js';
import { ExportCard } from './ui/ExportCard.js';
import { loadImages, readImageAsset } from './engine/images.js';
import type { Doc, NodePatch, VariantKey } from './core/types.js';
import { Canvas } from './ui/Canvas.js';
import { LayerTree } from './ui/LayerTree.js';
import { Inspector } from './ui/Inspector.js';
import { DeviceStrip } from './ui/DeviceStrip.js';
import { download, findNode, patchNode, patchFrameFor, patchVariant } from './ui/store.js';

type Tab = 'issues' | 'export' | 'layout';

export default function App() {
  const [doc, setDoc] = useState<Doc>(() => createAdaptiveDemoDoc());
  const [deviceId, setDeviceId] = useState('phone-1080x2340');
  const [selectedId, setSelectedId] = useState<string | null>('clock');
  const [editTarget, setEditTarget] = useState<VariantKey | 'base'>('base');
  const [tab, setTab] = useState<Tab>('issues');
  const [showGuides, setShowGuides] = useState(true);
  const [xmlPreview, setXmlPreview] = useState<string | null>(null);
  const [hint, setHint] = useState('拖动元素改位置 · 拖右下角改尺寸 · 右侧改约束');
  const fileRef = useRef<HTMLInputElement>(null);
  const assetRef = useRef<HTMLInputElement>(null);
  const [assetVersion, setAssetVersion] = useState(0);
  useEffect(() => {
    let active = true;
    loadImages(doc.assets ?? [])
      .then(() => {
        if (active) setAssetVersion((v) => v + 1);
      })
      .catch((e) => {
        if (active) setHint(`素材解码失败：${(e as Error).message}`);
      });
    return () => {
      active = false;
    };
  }, [doc.assets]);

  const devices = useMemo(() => devicesFor(doc, true), [doc]);
  const device = devices.find((d) => d.id === deviceId) ?? devices[1]!;
  const keys = useMemo(() => activeVariantKeys(device, doc.breakpoints), [device, doc.breakpoints]);

  const scenes = useMemo(
    () => devices.map((d) => resolveDoc(doc, d)),
    [doc, devices, assetVersion],
  );
  const scene = useMemo(() => scenes.find((s) => s.device.id === device.id)!, [scenes, device.id]);
  const issues = useMemo(() => validateScene(scene), [scene]);

  const issueCount = useMemo(() => {
    const map: Record<string, number> = {};
    for (const s of scenes) {
      const list = validateScene(s);
      map[s.device.id] = list.filter((i) => i.severity !== 'info').length;
    }
    return map;
  }, [scenes]);

  const selectedBase = selectedId ? findNode(doc.root, selectedId) : undefined;
  const selectedMerged = selectedBase ? mergeVariants(selectedBase, keys) : undefined;

  // Reset the edit target when the selection changes, defaulting to the level
  // that actually drives the current device.
  useEffect(() => {
    if (!selectedBase) return;
    const existing = [...keys].reverse().find((k) => selectedBase.variants?.[k] !== undefined);
    setEditTarget(existing ?? 'base');
  }, [selectedId, deviceId]);

  const insideAutoLayout = (root: Doc['root'], id: string): boolean => {
    const walk = (list: Doc['root'], parentAuto: boolean): boolean => {
      for (const n of list) {
        if (n.id === id) return parentAuto;
        if (n.children) {
          const hit = walk(n.children, n.autoLayout !== undefined);
          if (hit !== false) return hit;
        }
      }
      return false;
    };
    return walk(root, false);
  };

  const onMove = (id: string, dx: number, dy: number): void => {
    if (doc.layout?.mode === 'auto') {
      setHint('自动布局由组件关系求解；请在布局页调整构图，或切换手工约束模式后拖动');
      return;
    }
    if (insideAutoLayout(doc.root, id)) {
      setHint('该元素由父级自动布局定位，请在右侧调整间距/内边距，或改动父级位置');
      return;
    }
    setDoc((d) => {
      const s = resolveDoc(d, device);
      const node = flatten(s.nodes).find((n) => n.id === id);
      if (!node) return d;
      const base = findNode(d.root, id);
      if (base?.locked) return d;
      const k = activeVariantKeys(device, d.breakpoints);
      const driving = mergeVariants(base!, k);
      return {
        ...d,
        root: patchFrameFor(d.root, id, k, {
          x: driving.frame.x + dx / s.scale.sx,
          y: driving.frame.y + dy / (driving.constraints.v === 'scale' ? s.scale.sx : s.scale.sy),
        }),
      };
    });
  };

  const onResize = (id: string, dw: number, dh: number): void => {
    if (insideAutoLayout(doc.root, id)) {
      setHint('自动布局子元素的尺寸由内容或填充决定');
      return;
    }
    setDoc((d) => {
      const s = resolveDoc(d, device);
      const base = findNode(d.root, id);
      if (!base || base.locked) return d;
      const k = activeVariantKeys(device, d.breakpoints);
      const driving = mergeVariants(base, k);
      return {
        ...d,
        root: patchFrameFor(d.root, id, k, {
          w: Math.max(8, driving.frame.w + dw / s.scale.sx),
          h: Math.max(8, driving.frame.h + dh / s.scale.sx),
        }),
      };
    });
  };

  const onPatch = (patch: NodePatch, target: VariantKey | 'base'): void => {
    if (!selectedId) return;
    setDoc((d) => ({
      ...d,
      root:
        target === 'base'
          ? patchNode(d.root, selectedId, patch)
          : patchVariant(d.root, selectedId, target, patch),
    }));
  };

  const onToggle = (id: string): void => {
    setDoc((d) => ({
      ...d,
      root: patchNode(d.root, id, (n) => ({ visible: n.visible === false })),
    }));
  };

  const doExport = (kind: ExportAction): void => {
    try {
      const xml = exportDesign(kind, doc, scene);
      if (xml !== null) setXmlPreview(xml);
    } catch (e) {
      setHint((e as Error).message);
      alert((e as Error).message);
    }
  };

  const importJson = (file: File): void => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = parseDocument(String(reader.result));
        if (parsed.version === 2 && Array.isArray(parsed.root)) {
          setDoc(parsed);
          setSelectedId(parsed.root[0]?.id ?? null);
        } else {
          alert('不是 v2 文档');
        }
      } catch (e) {
        alert(`解析失败：${(e as Error).message}`);
      }
    };
    reader.readAsText(file);
  };

  const totals = summarize(issues);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" />
          <div>
            <div className="brand-title">
              Theme Studio <em>v2</em>
            </div>
            <div className="brand-sub">锁屏 · 响应式主题引擎</div>
          </div>
        </div>

        <div className="topbar-mid">
          <input
            className="doc-name"
            value={doc.meta.name}
            onChange={(e) => setDoc({ ...doc, meta: { ...doc.meta, name: e.target.value } })}
          />
          <span className="doc-canvas">
            画布 {doc.meta.design.w}×{doc.meta.design.h}
          </span>
        </div>

        <div className="topbar-actions">
          <label className="toggle">
            <input
              type="checkbox"
              checked={showGuides}
              onChange={(e) => setShowGuides(e.target.checked)}
            />
            安全区
          </label>
          <button className="btn ghost" onClick={() => fileRef.current?.click()}>
            导入
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json"
            hidden
            onChange={(e) => e.target.files?.[0] && importJson(e.target.files[0])}
          />
          <button className="btn ghost" onClick={() => doExport('json')}>
            保存
          </button>
          <button className="btn ghost" onClick={() => assetRef.current?.click()}>
            素材
          </button>
          <input
            ref={assetRef}
            type="file"
            multiple
            accept="image/png,image/jpeg,image/webp"
            hidden
            onChange={async (e) => {
              try {
                const assets = await Promise.all(
                  Array.from(e.target.files ?? []).map(readImageAsset),
                );
                setDoc((d) => ({
                  ...d,
                  assets: [
                    ...(d.assets ?? []).filter((a) => !assets.some((b) => b.path === a.path)),
                    ...assets,
                  ],
                }));
                setHint(`已导入 ${assets.length} 个素材；在图片节点的资源路径中填写文件名`);
              } catch (error) {
                setHint((error as Error).message);
              }
            }}
          />
          <button className="btn" onClick={() => doExport('preview')}>
            预览脚本
          </button>
          <button className="btn primary" onClick={() => doExport('hwt')}>
            导出 HWT
          </button>
        </div>
      </header>

      <div className="body">
        <aside className="left">
          <div className="panel-head">
            <span>图层</span>
            <span className="panel-hint">{flatten(scene.nodes).length} 个元素</span>
          </div>
          <div className="scroll">
            <LayerTree
              nodes={doc.root}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onToggle={onToggle}
            />
          </div>
        </aside>

        <main className="center">
          <div className="stage-head">
            <div className="stage-info">
              <strong>{device.label}</strong>
              <span className={`bp bp-${scene.breakpoint}`}>{scene.breakpoint}</span>
              <span className="mono">
                sx {scene.scale.sx.toFixed(3)} · sy {scene.scale.sy.toFixed(3)}
              </span>
            </div>
            <div className="stage-keys">
              {keys.map((k) => (
                <span key={k} className="key-badge">
                  {k}
                </span>
              ))}
            </div>
          </div>
          <Canvas
            scene={scene}
            selectedId={selectedId}
            showGuides={showGuides}
            onSelect={setSelectedId}
            onMove={onMove}
            onResize={onResize}
          />
        </main>

        <aside className="right">
          {selectedMerged ? (
            <Inspector
              node={selectedMerged}
              editTarget={editTarget}
              availableKeys={[
                'compact',
                'medium',
                'expanded',
                ...(keys.filter((k) => k !== scene.breakpoint) as VariantKey[]),
              ]}
              onPatch={onPatch}
              onTarget={setEditTarget}
            />
          ) : (
            <div className="empty">在画布或图层里选一个元素</div>
          )}
        </aside>
      </div>

      <section className="bottom">
        <div className="bottom-tabs">
          <button
            className={tab === 'issues' ? 'tab active' : 'tab'}
            onClick={() => setTab('issues')}
          >
            校验
            {totals.errors + totals.warns > 0 && (
              <span className={`tab-count${totals.errors > 0 ? ' error' : ''}`}>
                {totals.errors + totals.warns}
              </span>
            )}
          </button>
          <button
            className={tab === 'export' ? 'tab active' : 'tab'}
            onClick={() => setTab('export')}
          >
            导出
          </button>
          <button
            className={tab === 'layout' ? 'tab active' : 'tab'}
            onClick={() => setTab('layout')}
          >
            布局与设备
          </button>
          <div className="bottom-spacer" />
          <span className="bottom-note">{hint}</span>
        </div>

        {tab === 'layout' ? (
          <ResponsivePanel doc={doc} scene={scene} onChange={setDoc} onDevice={setDeviceId} />
        ) : tab === 'issues' ? (
          <div className="bottom-body">
            <IssueList issues={issues} onSelect={setSelectedId} />
            <div className="device-strip-wrap">
              <DeviceStrip
                scenes={scenes}
                currentId={device.id}
                issueCount={issueCount}
                onSelect={setDeviceId}
              />
            </div>
          </div>
        ) : (
          <div className="export-grid">
            <ExportCard
              title="响应式单文件"
              disabled={doc.layout?.mode === 'auto'}
              desc="仅用于线性约束布局；自动重构请导出逐设备脚本，避免与预览不一致。"
              action="下载 manifest.xml"
              onClick={() => doExport('responsive')}
            />
            <ExportCard
              title={`烘焙到 ${device.short}`}
              desc="把当前设备解算成绝对像素，用于必须逐机型对齐的场景。"
              action="下载烘焙 XML"
              onClick={() => doExport('baked')}
            />
            <ExportCard
              title="锁屏 HWT 包"
              desc="当前设备求解的 XML + 实际素材 + 包描述 + 交付清单；布局错误或缺图会阻止打包。"
              action="下载 .hwt"
              onClick={() => doExport('hwt')}
            />
            <ExportCard
              title="全部机型 ZIP"
              desc="预设及自定义设备的横竖屏脚本、素材和审阅报告；失败的目标保留诊断。"
              action="下载 ZIP"
              onClick={() => doExport('bundle')}
            />
            <ExportCard
              title="工程 JSON"
              desc="完整响应式 IR，包含约束、断点变体与自动布局，便于版本管理与批处理。"
              action="下载 JSON"
              onClick={() => doExport('json')}
            />
          </div>
        )}
      </section>

      {xmlPreview !== null && (
        <div className="modal" onClick={() => setXmlPreview(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <strong>当前设备脚本 · 与布局预览共用求解结果</strong>
              <div>
                <button
                  className="btn ghost"
                  onClick={() => download('manifest.xml', xmlPreview, 'application/xml')}
                >
                  下载
                </button>
                <button className="btn ghost" onClick={() => setXmlPreview(null)}>
                  关闭
                </button>
              </div>
            </div>
            <pre className="code">{xmlPreview}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
