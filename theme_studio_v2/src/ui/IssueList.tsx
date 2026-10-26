import type { Issue } from '../core/validate.js';

export function IssueList({
  issues,
  onSelect,
}: {
  issues: Issue[];
  onSelect: (id: string) => void;
}) {
  if (!issues.length)
    return <div className="issues-empty">当前设备未检出布局错误；仍需官方引擎和真机验收。</div>;
  return (
    <ul className="issues">
      {issues.map((i, idx) => (
        <li
          key={`${i.code}-${i.nodeId}-${idx}`}
          className={`issue ${i.severity}`}
          onClick={() => onSelect(i.nodeId)}
        >
          <span className="issue-dot" />
          <span className="issue-code">{i.code}</span>
          <span className="issue-msg">
            <b>{i.nodeName}</b> {i.message}
          </span>
        </li>
      ))}
    </ul>
  );
}
