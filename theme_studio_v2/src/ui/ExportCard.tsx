export function ExportCard({
  title,
  desc,
  action,
  onClick,
  disabled = false,
}: {
  title: string;
  desc: string;
  action: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="export-card">
      <div className="export-title">{title}</div>
      <p className="export-desc">{desc}</p>
      <button className="btn small" disabled={disabled} onClick={onClick}>
        {action}
      </button>
    </div>
  );
}
