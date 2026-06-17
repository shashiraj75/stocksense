import clsx from "clsx";

interface Props {
  value: number; // 0-100
  label?: string;
}

export function ConfidenceMeter({ value, label }: Props) {
  const safeValue = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
  const color =
    safeValue >= 70 ? "bg-bull" : safeValue >= 40 ? "bg-neutral" : "bg-bear";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        {label && <p className="text-xs text-gray-400">{label}</p>}
        <span className="text-sm font-mono font-bold text-white">{safeValue}%</span>
      </div>
      <div className="h-2 bg-dark-border rounded-full overflow-hidden">
        <div
          className={clsx("h-full rounded-full transition-all duration-700", color)}
          style={{ width: `${safeValue}%` }}
        />
      </div>
    </div>
  );
}
