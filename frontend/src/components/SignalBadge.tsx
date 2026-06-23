import { Signal } from "@/utils/api";
import clsx from "clsx";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

export function SignalBadge({
  signal,
  confidence,
  size = "md",
}: {
  signal: Signal;
  confidence?: number;
  size?: "sm" | "md" | "lg";
}) {
  const getBuyClasses = (conf?: number) => {
    if (!conf || conf >= 60) return "bg-bull/20 text-bull border border-bull/40";
    if (conf >= 45) return "bg-yellow-500/20 text-yellow-400 border border-yellow-500/40";
    return "bg-gray-500/20 text-gray-400 border border-gray-500/40";
  };

  const classes =
    signal === "BUY"
      ? getBuyClasses(confidence)
      : signal === "SELL"
      ? "bg-bear/20 text-bear border border-bear/40"
      : "bg-neutral/20 text-neutral border border-neutral/40";

  // Always show the backend's actual signal — never relabel it. The backend's
  // BUY/HOLD/SELL bands (composite score >=60 / 45-59 / <45) are the single
  // source of truth; a weak BUY is communicated via muted color (getBuyClasses
  // above), not by silently swapping the word to HOLD, which previously made
  // the same stock look like BUY on one page and HOLD on another.
  const label = signal;

  const Icon =
    label === "BUY"  ? TrendingUp   :
    label === "SELL" ? TrendingDown  :
    Minus;

  if (size === "lg") {
    const isStrong = signal === "BUY" && (confidence ?? 0) >= 60;
    return (
      <div className={clsx("relative inline-flex flex-col items-center gap-1.5 rounded-2xl px-6 py-3", classes)}>
        {isStrong && (
          <span className="absolute inset-0 rounded-2xl animate-pulse opacity-20 bg-bull pointer-events-none" />
        )}
        <div className="flex items-center gap-2">
          <Icon size={20} strokeWidth={2.5} />
          <span className="text-xl font-black tracking-widest">{label}</span>
        </div>
        {confidence !== undefined && (
          <span className="text-xs font-medium opacity-70">{confidence}% confidence</span>
        )}
      </div>
    );
  }

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 font-bold tracking-wider rounded-full",
        size === "sm" && "px-2 py-0.5 text-xs",
        size === "md" && "px-3 py-1 text-sm",
        classes
      )}
    >
      {size === "md" && <Icon size={12} strokeWidth={2.5} />}
      {label}
    </span>
  );
}
