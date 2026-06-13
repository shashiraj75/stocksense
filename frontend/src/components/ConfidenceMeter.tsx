import clsx from "clsx";

interface Props {
  value: number; // 0-100
  label?: string;
}

export function ConfidenceMeter({ value, label }: Props) {
  const color =
    value >= 70 ? "bg-bull" : value >= 40 ? "bg-neutral" : "bg-bear";

  return (
    <div className="space-y-1">
      {label && <p className="text-xs text-gray-400">{label}</p>}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-2 bg-dark-border rounded-full overflow-hidden">
          <div
            className={clsx("h-full rounded-full transition-all duration-700", color)}
            style={{ width: `${value}%` }}
          />
        </div>
        <span className="text-sm font-mono font-bold text-white w-10 text-right">
          {value}%
        </span>
      </div>
    </div>
  );
}
