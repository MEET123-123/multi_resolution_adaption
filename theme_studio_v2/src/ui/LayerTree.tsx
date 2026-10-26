import type { Node } from '../core/types.js';

const ICON: Record<Node['kind'], string> = {
  group: '▤',
  image: '▨',
  text: 'T',
  rect: '▢',
  time: '◷',
  date: '◫',
  unlocker: '↑',
  button: '◉',
};

interface Props {
  nodes: Node[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  depth?: number;
}

export function LayerTree({ nodes, selectedId, onSelect, onToggle, depth = 0 }: Props) {
  return (
    <ul className="layer-list">
      {nodes.map((n) => (
        <li key={n.id}>
          <div
            className={`layer-row${selectedId === n.id ? ' selected' : ''}${n.visible === false ? ' hidden' : ''}`}
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => onSelect(n.id)}
          >
            <button
              className="layer-eye"
              onClick={(e) => {
                e.stopPropagation();
                onToggle(n.id);
              }}
              title={n.visible === false ? '显示' : '隐藏'}
            >
              {n.visible === false ? '○' : '●'}
            </button>
            <span className="layer-icon">{ICON[n.kind] ?? '▢'}</span>
            <span className="layer-name">{n.name}</span>
            {n.variants && Object.keys(n.variants).length > 0 && (
              <span className="layer-variant" title={Object.keys(n.variants).join(', ')}>
                {Object.keys(n.variants).length}变体
              </span>
            )}
          </div>
          {n.children && n.children.length > 0 && (
            <LayerTree
              nodes={n.children}
              selectedId={selectedId}
              onSelect={onSelect}
              onToggle={onToggle}
              depth={depth + 1}
            />
          )}
        </li>
      ))}
    </ul>
  );
}
