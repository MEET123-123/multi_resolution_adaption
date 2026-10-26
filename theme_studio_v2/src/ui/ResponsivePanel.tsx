import { useState } from 'react';
import type { Composition, DeviceProfile, Doc } from '../core/types.js';
import type { ResolvedScene } from '../core/solve.js';
import { assertDevice } from '../core/document.js';

interface Props {
  doc: Doc;
  scene: ResolvedScene;
  onChange: (doc: Doc) => void;
  onDevice: (id: string) => void;
}

export const COMPOSITIONS: Array<[Composition, string]> = [
  ['auto', '自动选择构图'],
  ['preserve', '保留整体构图'],
  ['stack', '上下弹性分布'],
  ['split', '左右主辅分区'],
  ['grid', '组件卡片栅格'],
  ['focus-dock', '主视觉与底部功能区'],
];

const example: DeviceProfile = {
  id: 'custom-wide',
  label: '自定义宽屏',
  short: '自定义',
  formFactor: 'custom',
  width: 2400,
  height: 1600,
  widthDp: 960,
  insets: { top: 48, right: 48, bottom: 48, left: 48 },
  posture: 'flat',
};

export function ResponsivePanel({ doc, scene, onChange, onDevice }: Props) {
  const [profile, setProfile] = useState(JSON.stringify(example, null, 2));
  const [error, setError] = useState('');
  const plan = scene.explanation;
  const add = (): void => {
    try {
      const value: unknown = JSON.parse(profile);
      assertDevice(value);
      onChange({
        ...doc,
        devices: [...(doc.devices ?? []).filter((d) => d.id !== value.id), value],
      });
      onDevice(value.id);
      setError('');
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="responsive-panel">
      <section>
        <h4>设计关系与构图</h4>
        <p>显式设备变体优先；自动推断仅重排独立组件组，图片、文字和热区随组统一变换。</p>
        <label>
          布局方式{' '}
          <select
            value={doc.layout?.mode ?? 'constraints'}
            onChange={(e) =>
              onChange({
                ...doc,
                layout: { ...doc.layout, mode: e.target.value as 'auto' | 'constraints' },
              })
            }
          >
            <option value="auto">设计关系自动推断</option>
            <option value="constraints">手工约束与变体</option>
          </select>
        </label>
        <label>
          构图{' '}
          <select
            value={doc.layout?.composition ?? 'auto'}
            onChange={(e) =>
              onChange({
                ...doc,
                layout: { ...doc.layout, mode: 'auto', composition: e.target.value as Composition },
              })
            }
          >
            {COMPOSITIONS.map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {plan ? (
          <>
            <p>
              {plan.algorithm} · {plan.composition} · {plan.regions.length} 个可用区域
            </p>
            <ul>
              {plan.groups.map((g) => (
                <li key={g.id}>
                  <b>{g.members.join(' + ')}</b> · {g.role} · {g.h}/{g.v} ·{' '}
                  {Math.round(g.confidence * 100)}%<small>{g.reasons.join('；')}</small>
                </li>
              ))}
            </ul>
            {plan.warnings.map((w, i) => (
              <p className="error-text" key={i}>
                {w}
              </p>
            ))}
          </>
        ) : (
          <p>启用自动推断后查看分组、锚点和规划原因。</p>
        )}
      </section>
      <section>
        <h4>任意分辨率与折叠姿态</h4>
        <p>
          width/height 为像素；widthDp 决定密度；insets 为像素。features
          可声明中缝、挖孔和保留区。目录内示例尺寸不是官方机型参数。
        </p>
        <textarea
          aria-label="设备配置 JSON"
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
          spellCheck={false}
        />
        <button className="btn" onClick={add}>
          添加 / 更新设备并预览
        </button>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
      </section>
    </div>
  );
}
