import type { Node, NodePatch, VariantKey } from '../core/types.js';

interface Props {
  node: Node;
  editTarget: VariantKey | 'base';
  availableKeys: VariantKey[];
  onPatch: (patch: NodePatch, target: VariantKey | 'base') => void;
  onTarget: (target: VariantKey | 'base') => void;
}

const H_ANCHORS: Array<[Node['constraints']['h'], string]> = [
  ['left', '左对齐'],
  ['center', '水平居中'],
  ['right', '右对齐'],
  ['leftRight', '左右拉伸'],
  ['scale', '等比铺满'],
];

const V_ANCHORS: Array<[Node['constraints']['v'], string]> = [
  ['top', '顶部'],
  ['center', '垂直居中'],
  ['bottom', '底部'],
  ['topBottom', '上下拉伸'],
  ['scale', '等比铺满'],
];

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <span className="field-body">{children}</span>
    </label>
  );
}

function Num({
  value,
  onChange,
  step = 1,
  min,
}: {
  value: number | undefined;
  onChange: (v: number | undefined) => void;
  step?: number;
  min?: number;
}) {
  return (
    <input
      className="num"
      type="number"
      step={step}
      min={min}
      value={value === undefined || Number.isNaN(value) ? '' : Math.round(value * 100) / 100}
      placeholder="—"
      onChange={(e) => {
        const raw = e.target.value;
        onChange(raw === '' ? undefined : Number(raw));
      }}
    />
  );
}

export function Inspector({ node, editTarget, availableKeys, onPatch, onTarget }: Props) {
  const set = (patch: NodePatch): void => onPatch(patch, editTarget);
  const t = node.text;
  const al = node.autoLayout;
  const c = node.constraints;

  return (
    <div className="inspector">
      <div className="inspector-head">
        <div>
          <div className="inspector-title">{node.name}</div>
          <div className="inspector-sub">
            {node.kind} · {node.id}
          </div>
        </div>
      </div>

      <div className="target-bar">
        <button
          className={editTarget === 'base' ? 'chip active' : 'chip'}
          onClick={() => onTarget('base')}
        >
          基础
        </button>
        {availableKeys.map((k) => (
          <button
            key={k}
            className={editTarget === k ? 'chip active' : 'chip'}
            onClick={() => onTarget(k)}
          >
            {k}
          </button>
        ))}
      </div>

      <section>
        <h4>设计意图</h4>
        <Row label="语义角色">
          <select
            value={node.responsive?.role ?? ''}
            onChange={(e) =>
              set({
                responsive: {
                  role: (e.target.value || undefined) as NonNullable<Node['responsive']>['role'],
                },
              })
            }
          >
            <option value="">自动推断</option>
            <option value="background">背景</option>
            <option value="clock">时间日期组</option>
            <option value="hero">主视觉</option>
            <option value="content">内容</option>
            <option value="footer">底部功能</option>
            <option value="status">顶部状态</option>
            <option value="overlay">弹层</option>
          </select>
        </Row>
        <Row label="排布方式">
          <select
            value={node.responsive?.position ?? ''}
            onChange={(e) =>
              set({
                responsive: {
                  position: (e.target.value || undefined) as NonNullable<
                    Node['responsive']
                  >['position'],
                },
              })
            }
          >
            <option value="">自动推断</option>
            <option value="flow">参与重排</option>
            <option value="fixed">保持锚点</option>
            <option value="overlay">叠层</option>
            <option value="offscreen">屏外候场</option>
          </select>
        </Row>
        <Row label="绑定组">
          <input
            className="text"
            value={node.responsive?.group ?? ''}
            placeholder="图像与热区使用相同组名"
            onChange={(e) => set({ responsive: { group: e.target.value || undefined } })}
          />
        </Row>
        {node.kind === 'image' && (
          <>
            <Row label="素材路径">
              <input
                className="text"
                value={node.image?.src ?? ''}
                onChange={(e) =>
                  set({
                    image: { src: e.target.value, scaleType: node.image?.scaleType ?? 'contain' },
                  })
                }
              />
            </Row>
            <Row label="图片适配">
              <select
                value={node.image?.scaleType ?? 'contain'}
                onChange={(e) => set({ image: { scaleType: e.target.value as 'contain' } })}
              >
                <option value="contain">等比完整显示</option>
                <option value="centerCrop">居中覆盖裁切</option>
                <option value="fill">拉伸</option>
              </select>
            </Row>
          </>
        )}
      </section>

      <section>
        <h4>位置与尺寸</h4>
        <div className="grid2">
          <Row label="X">
            <Num value={node.frame.x} onChange={(v) => set({ frame: { x: v ?? 0 } })} />
          </Row>
          <Row label="Y">
            <Num value={node.frame.y} onChange={(v) => set({ frame: { y: v ?? 0 } })} />
          </Row>
          <Row label="W">
            <Num
              value={node.frame.w}
              onChange={(v) => set({ frame: { w: Math.max(1, v ?? 1) } })}
            />
          </Row>
          <Row label="H">
            <Num
              value={node.frame.h}
              onChange={(v) => set({ frame: { h: Math.max(1, v ?? 1) } })}
            />
          </Row>
        </div>
      </section>

      <section>
        <h4>约束</h4>
        <Row label="水平">
          <select
            value={c.h}
            onChange={(e) =>
              set({ constraints: { h: e.target.value as Node['constraints']['h'] } })
            }
          >
            {H_ANCHORS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Row>
        <Row label="垂直">
          <select
            value={c.v}
            onChange={(e) =>
              set({ constraints: { v: e.target.value as Node['constraints']['v'] } })
            }
          >
            {V_ANCHORS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Row>
        <div className="grid2">
          <Row label="宽">
            <select
              value={node.sizing.w}
              onChange={(e) => set({ sizing: { w: e.target.value as 'fixed' } })}
            >
              <option value="fixed">固定</option>
              <option value="fill">填充</option>
              <option value="hug">适应内容</option>
            </select>
          </Row>
          <Row label="高">
            <select
              value={node.sizing.h}
              onChange={(e) => set({ sizing: { h: e.target.value as 'fixed' } })}
            >
              <option value="fixed">固定</option>
              <option value="fill">填充</option>
              <option value="hug">适应内容</option>
            </select>
          </Row>
        </div>
        <Row label="安全区">
          <select
            value={node.safeArea ?? ''}
            onChange={(e) => set({ safeArea: (e.target.value || null) as Node['safeArea'] })}
          >
            <option value="">不限制</option>
            <option value="top">顶部</option>
            <option value="bottom">底部</option>
            <option value="both">上下</option>
          </select>
        </Row>
        <div className="grid2">
          <Row label="最小宽 dp">
            <Num value={c.minW} onChange={(v) => set({ constraints: { minW: v } })} />
          </Row>
          <Row label="最大宽 dp">
            <Num value={c.maxW} onChange={(v) => set({ constraints: { maxW: v } })} />
          </Row>
          <Row label="最小高 dp">
            <Num value={c.minH} onChange={(v) => set({ constraints: { minH: v } })} />
          </Row>
          <Row label="最大高 dp">
            <Num value={c.maxH} onChange={(v) => set({ constraints: { maxH: v } })} />
          </Row>
        </div>
        <Row label="锁定宽高比">
          <div className="inline">
            <input
              type="checkbox"
              checked={c.aspect !== null && c.aspect !== undefined}
              onChange={(e) =>
                set({
                  constraints: {
                    aspect: e.target.checked
                      ? Number((node.frame.h / node.frame.w).toFixed(4))
                      : null,
                  },
                })
              }
            />
            <Num
              value={c.aspect ?? undefined}
              step={0.01}
              onChange={(v) => set({ constraints: { aspect: v ?? null } })}
            />
          </div>
        </Row>
      </section>

      {t && (
        <section>
          <h4>文字</h4>
          <Row label="内容">
            <textarea
              className="textarea"
              rows={2}
              value={t.content}
              onChange={(e) => set({ text: { content: e.target.value } })}
            />
          </Row>
          <div className="grid2">
            <Row label="字号 px">
              <Num value={t.size} onChange={(v) => set({ text: { size: Math.max(1, v ?? 1) } })} />
            </Row>
            <Row label="颜色">
              <input
                className="color"
                type="color"
                value={t.color.slice(0, 7)}
                onChange={(e) => set({ text: { color: e.target.value } })}
              />
            </Row>
            <Row label="字重">
              <select
                value={t.weight}
                onChange={(e) => set({ text: { weight: e.target.value as 'regular' } })}
              >
                <option value="regular">常规</option>
                <option value="medium">中等</option>
                <option value="bold">加粗</option>
              </select>
            </Row>
            <Row label="对齐">
              <select
                value={t.align}
                onChange={(e) => set({ text: { align: e.target.value as 'left' } })}
              >
                <option value="left">左</option>
                <option value="center">中</option>
                <option value="right">右</option>
              </select>
            </Row>
            <Row label="行高">
              <Num
                value={t.lineHeight}
                step={0.05}
                onChange={(v) => set({ text: { lineHeight: v ?? 1.2 } })}
              />
            </Row>
            <Row label="最大行">
              <Num
                value={t.maxLines}
                min={1}
                onChange={(v) => set({ text: { maxLines: Math.max(1, v ?? 1) } })}
              />
            </Row>
            <Row label="字距">
              <Num
                value={t.letterSpacing}
                onChange={(v) => set({ text: { letterSpacing: v ?? 0 } })}
              />
            </Row>
            <Row label="自动缩字">
              <input
                type="checkbox"
                checked={t.autoShrink}
                onChange={(e) => set({ text: { autoShrink: e.target.checked } })}
              />
            </Row>
          </div>
        </section>
      )}

      {al && (
        <section>
          <h4>自动布局</h4>
          <div className="grid2">
            <Row label="方向">
              <select
                value={al.dir}
                onChange={(e) => set({ autoLayout: { dir: e.target.value as 'row' } })}
              >
                <option value="col">纵向</option>
                <option value="row">横向</option>
              </select>
            </Row>
            <Row label="间距">
              <Num value={al.gap} onChange={(v) => set({ autoLayout: { gap: v ?? 0 } })} />
            </Row>
            <Row label="主轴">
              <select
                value={al.main}
                onChange={(e) => set({ autoLayout: { main: e.target.value as 'start' } })}
              >
                <option value="start">起始</option>
                <option value="center">居中</option>
                <option value="end">末尾</option>
                <option value="spaceBetween">两端</option>
              </select>
            </Row>
            <Row label="交叉轴">
              <select
                value={al.cross}
                onChange={(e) => set({ autoLayout: { cross: e.target.value as 'start' } })}
              >
                <option value="start">起始</option>
                <option value="center">居中</option>
                <option value="end">末尾</option>
                <option value="stretch">拉伸</option>
              </select>
            </Row>
          </div>
          <div className="grid4">
            <Row label="上">
              <Num
                value={al.pad[0]}
                onChange={(v) =>
                  set({ autoLayout: { pad: [v ?? 0, al.pad[1], al.pad[2], al.pad[3]] } })
                }
              />
            </Row>
            <Row label="右">
              <Num
                value={al.pad[1]}
                onChange={(v) =>
                  set({ autoLayout: { pad: [al.pad[0], v ?? 0, al.pad[2], al.pad[3]] } })
                }
              />
            </Row>
            <Row label="下">
              <Num
                value={al.pad[2]}
                onChange={(v) =>
                  set({ autoLayout: { pad: [al.pad[0], al.pad[1], v ?? 0, al.pad[3]] } })
                }
              />
            </Row>
            <Row label="左">
              <Num
                value={al.pad[3]}
                onChange={(v) =>
                  set({ autoLayout: { pad: [al.pad[0], al.pad[1], al.pad[2], v ?? 0] } })
                }
              />
            </Row>
          </div>
        </section>
      )}

      <section>
        <h4>外观</h4>
        <div className="grid2">
          <Row label="填充">
            <input
              className="color"
              type="color"
              value={(node.fill ?? '#ffffff').slice(0, 7)}
              onChange={(e) => set({ fill: e.target.value })}
            />
          </Row>
          <Row label="不透明度">
            <Num value={node.alpha ?? 255} min={0} onChange={(v) => set({ alpha: v ?? 255 })} />
          </Row>
          <Row label="圆角">
            <Num value={node.radius ?? 0} min={0} onChange={(v) => set({ radius: v ?? 0 })} />
          </Row>
        </div>
        {node.kind === 'button' && (
          <Row label="Intent">
            <input
              className="text"
              value={node.action ?? ''}
              placeholder="android.intent.action.DIAL"
              onChange={(e) => set({ action: e.target.value })}
            />
          </Row>
        )}
      </section>
    </div>
  );
}
