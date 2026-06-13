import { Signal } from "@/utils/api";
import clsx from "clsx";

const CONFIG: Record<Signal, { label: string; classes: string }> = {
  BUY:  { label: "BUY",  classes: "bg-bull/20 text-bull border border-bull/40" },
  SELL: { label: "SELL", classes: "bg-bear/20 text-bear border border-bear/40" },
  HOLD: { label: "HOLD", classes: "bg-neutral/20 text-neutral border border-neutral/40" },
};

export function SignalBadge({ signal, size = "md" }: { signal: Signal; size?: "sm" | "md" | "lg" }) {
  const { label, classes } = CONFIG[signal];
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
