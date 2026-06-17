"use client";
import { TrendingUp, TrendingDown } from "lucide-react";
import clsx from "clsx";

// Detect category from reason text — maps known prefixes/keywords to labels
function detectCategory(reason: string): string {
  const r = reason.toLowerCase();
  if (r.includes("obv") || r.includes("macd") || r.includes("rsi") || r.includes("ema") ||
      r.includes("volume") || r.includes("momentum") || r.includes("breakout") ||
      r.includes("support") || r.includes("resistance") || r.includes("moving avg") ||
      r.includes("mfi") || r.includes("bollinger") || r.includes("atr") || r.includes("technical"))
    return "Technical";
  if (r.includes("fii") || r.includes("dii") || r.includes("institutional") ||
      r.includes("promoter") || r.includes("mutual fund") || r.includes("mf ") ||
      r.includes("accumulation") || r.includes("holding") || r.includes("foreign"))
    return "Institutional";
  if (r.includes("pe ") || r.includes("p/e") || r.includes("roe") || r.includes("roce") ||
      r.includes("margin") || r.includes("revenue") || r.includes("profit") ||
      r.includes("debt") || r.includes("earnings") || r.includes("eps") ||
      r.includes("valuation") || r.includes("book value") || r.includes("fundamental"))
    return "Fundamental";
  if (r.includes("sentiment") || r.includes("news") || r.includes("analyst") ||
      r.includes("upgrade") || r.includes("downgrade") || r.includes("target price") ||
      r.includes("consensus") || r.includes("rating"))
    return "Sentiment";
  if (r.includes("sector") || r.includes("nifty") || r.includes("index") ||
      r.includes("market") || r.includes("macro") || r.includes("regime") ||
      r.includes("industry"))
    return "Market";
  if (r.includes("pledge") || r.includes("npa") || r.includes("risk") ||
      r.includes("debt") || r.includes("leverage") || r.includes("liquidity"))
    return "Risk";
  return "Factor";
}

const CATEGORY_STYLE: Record<string, string> = {
  Technical:     "bg-blue-500/15 text-blue-300 border-blue-500/30",
  Institutional: "bg-purple-500/15 text-purple-300 border-purple-500/30",
  Fundamental:   "bg-amber-500/15 text-amber-300 border-amber-500/30",
  Sentiment:     "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
  Market:        "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",
  Risk:          "bg-red-500/15 text-red-300 border-red-500/30",
  Factor:        "bg-gray-500/15 text-gray-300 border-gray-500/30",
};

function ReasonChip({ reason, side }: { reason: string; side: "bull" | "bear" }) {
  const cat = detectCategory(reason);
  return (
    <div className={clsx(
      "rounded-xl border p-3 flex flex-col gap-1.5",
      side === "bull" ? "bg-bull/5 border-bull/20" : "bg-bear/5 border-bear/20"
    )}>
      <div className="flex items-center gap-2">
        <span className={clsx(
          "inline-flex items-center text-[10px] font-semibold tracking-wide px-2 py-0.5 rounded-full border",
          CATEGORY_STYLE[cat]
        )}>
          {cat}
        </span>
      </div>
      <p className="text-sm text-gray-200 leading-snug">{reason}</p>
    </div>
  );
}

export function BullBearCase({
  bull,
  bear,
}: {
  bull: string[];
  bear: string[];
}) {
  if ((!bull || bull.length === 0) && (!bear || bear.length === 0)) return null;

  return (
    <div className="bg-dark-card border border-dark-border rounded-2xl p-6">
      <h2 className="font-bold text-lg mb-4">Bull &amp; Bear Case</h2>
      <div className="grid md:grid-cols-2 gap-6">
        <div>
          <div className="flex items-center gap-2 mb-3 text-bull">
            <TrendingUp size={16} />
            <h3 className="font-semibold text-sm uppercase tracking-wide">Bull Case</h3>
            <span className="ml-auto text-xs text-bull/60 font-mono">{bull.length} signal{bull.length !== 1 ? "s" : ""}</span>
          </div>
          {bull.length > 0 ? (
            <div className="space-y-2">
              {bull.map((s, i) => <ReasonChip key={i} reason={s} side="bull" />)}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No strong positive factors detected.</p>
          )}
        </div>

        <div>
          <div className="flex items-center gap-2 mb-3 text-bear">
            <TrendingDown size={16} />
            <h3 className="font-semibold text-sm uppercase tracking-wide">Bear Case</h3>
            <span className="ml-auto text-xs text-bear/60 font-mono">{bear.length} signal{bear.length !== 1 ? "s" : ""}</span>
          </div>
          {bear.length > 0 ? (
            <div className="space-y-2">
              {bear.map((s, i) => <ReasonChip key={i} reason={s} side="bear" />)}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No significant risk factors detected.</p>
          )}
        </div>
      </div>
      <p className="text-xs text-gray-600 mt-4 pt-3 border-t border-dark-border">
        Each signal is derived from a measured factor value — not generative text.
      </p>
    </div>
  );
}
