import { Signal } from "@/utils/api";
import clsx from "clsx";

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
    if (!conf || conf >= 60) return "bg-bull/20 text-bull border border-bull/40";          // strong green
    if (conf >= 45) return "bg-yellow-500/20 text-yellow-400 border border-yellow-500/40"; // amber — moderate
    return "bg-gray-500/20 text-gray-400 border border-gray-500/40";                       // gray — weak
  };

  const classes =
    signal === "BUY"
      ? getBuyClasses(confidence)
      : signal === "SELL"
      ? "bg-bear/20 text-bear border border-bear/40"
      : "bg-neutral/20 text-neutral border border-neutral/40";

  const label =
    signal === "BUY" && confidence !== undefined && confidence < 45
      ? "HOLD"    // downgrade to HOLD when conviction is too low
      : signal;

  return (
    <span
      className={clsx(
        "inline-flex items-center font-bold tracking-wider rounded-full",
        size === "sm" && "px-2 py-0.5 text-xs",
        size === "md" && "px-3 py-1 text-sm",
        size === "lg" && "px-5 py-2 text-lg",
        classes
      )}
    >
      {label}
    </span>
  );
}
