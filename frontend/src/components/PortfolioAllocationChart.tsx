"use client";
import { useState } from "react";
import clsx from "clsx";

interface StockSlice {
  symbol: string;
  value: number;
  signal: string | null;
}

interface SectorSlice {
  sector: string;
  value: number;
}

const PALETTE = [
  "#6366f1", "#22c55e", "#f59e0b", "#3b82f6", "#ec4899",
  "#14b8a6", "#f97316", "#a855f7", "#ef4444", "#84cc16",
];

// Sector is the more useful default view for concentration/diversification
// risk — "am I overweight one sector" matters more than "which single stock
// is biggest," and a by-stock bar with 30+ slivers is unreadable anyway.
// Falls back to by-stock when sector data isn't available yet (still
// computing) or when there's only one distinct sector (grouping would be a
// no-op slice covering the whole bar).
export function PortfolioAllocationChart({
  stockSlices, sectorSlices,
}: { stockSlices: StockSlice[]; sectorSlices: SectorSlice[] }) {
  const withStockValue = stockSlices.filter(s => s.value > 0);
  const withSectorValue = sectorSlices.filter(s => s.value > 0);
  // Any resolved sector value at all — even a single "Other" bucket while
  // the rest are still loading — is enough to show the sector view and its
  // toggle. A stricter ">1 distinct sector" threshold made large India
  // portfolios (many holdings, slow sequential per-symbol sector fetches)
  // look structurally different from small/fast US ones: everything sat
  // in "Other" for a while, so the toggle stayed hidden and it silently
  // fell back to by-stock — the two markets should present the same UI
  // shape (toggle + sector-first default) regardless of how much sector
  // data has resolved so far; "Other" simply shrinks as more arrives.
  const hasSectorData = withSectorValue.length > 0;

  // null = "follow the data" (defaults to sector once sector data arrives,
  // which happens asynchronously after mount as signal queries resolve —
  // a plain useState default would freeze at whatever was true on the
  // FIRST render, before any sector data existed, and never reconsider).
  // Only becomes a fixed "sector"/"stock" once the user explicitly clicks
  // a toggle button, overriding the auto-follow.
  const [mode, setMode] = useState<"sector" | "stock" | null>(null);
  const effectiveMode = mode ?? (hasSectorData ? "sector" : "stock");

  if (withStockValue.length === 0) return null;

  const activeSlices = (effectiveMode === "sector" && hasSectorData
    ? withSectorValue.map(s => ({ label: s.sector, value: s.value }))
    : withStockValue.map(s => ({ label: s.symbol, value: s.value })));

  const total = activeSlices.reduce((s, r) => s + r.value, 0);

  // Signal Distribution always reflects individual holdings, regardless of
  // which grouping the bar/legend above are showing — a per-sector slice
  // has no single signal of its own, so this must not vary with `mode`.
  const buyCount  = withStockValue.filter(s => s.signal === "BUY").length;
  const sellCount = withStockValue.filter(s => s.signal === "SELL").length;
  const holdCount = withStockValue.filter(s => s.signal !== "BUY" && s.signal !== "SELL" && s.signal !== null).length;

  return (
    <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="font-semibold text-sm text-gray-300">Portfolio Allocation</h2>
        {hasSectorData && (
          <div className="flex items-center gap-0.5 bg-dark-bg border border-dark-border rounded-lg p-0.5">
            {(["sector", "stock"] as const).map(m => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={clsx(
                  "px-2.5 py-1 text-xs font-medium rounded-md transition-colors",
                  effectiveMode === m ? "bg-brand-500 text-white" : "text-gray-400 hover:text-white"
                )}
              >
                {m === "sector" ? "By Sector" : "By Stock"}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Stacked bar */}
      <div className="flex h-6 w-full rounded-full overflow-hidden gap-px">
        {activeSlices.map((s, i) => {
          const w = (s.value / total) * 100;
          return (
            <div
              key={s.label}
              className="h-full transition-all duration-300 group relative"
              style={{ width: `${w}%`, backgroundColor: PALETTE[i % PALETTE.length], minWidth: w > 1 ? undefined : 2 }}
              title={`${s.label}: ${w.toFixed(1)}%`}
            />
          );
        })}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {activeSlices.map((s, i) => {
          const w = (s.value / total) * 100;
          return (
            <div key={s.label} className="flex items-center gap-1.5 text-xs">
              <span className="inline-block w-2.5 h-2.5 rounded-sm flex-shrink-0"
                style={{ backgroundColor: PALETTE[i % PALETTE.length] }} />
              <span className={clsx("text-gray-300", effectiveMode === "stock" ? "font-mono font-bold" : "font-semibold")}>{s.label}</span>
              <span className="text-gray-500">{w.toFixed(1)}%</span>
            </div>
          );
        })}
      </div>

      {/* Signal distribution */}
      {(buyCount + sellCount + holdCount) > 0 && (
        <div className="border-t border-dark-border pt-3">
          <p className="text-xs text-gray-500 mb-2">Signal Distribution</p>
          <div className="flex gap-3">
            {buyCount > 0 && (
              <div className="flex items-center gap-1.5 bg-bull/10 border border-bull/30 rounded-lg px-3 py-1.5">
                <span className="w-2 h-2 rounded-full bg-bull" />
                <span className="text-xs font-bold text-bull">{buyCount} BUY</span>
              </div>
            )}
            {holdCount > 0 && (
              <div className="flex items-center gap-1.5 bg-neutral/10 border border-neutral/30 rounded-lg px-3 py-1.5">
                <span className="w-2 h-2 rounded-full bg-neutral" />
                <span className="text-xs font-bold text-neutral">{holdCount} HOLD</span>
              </div>
            )}
            {sellCount > 0 && (
              <div className="flex items-center gap-1.5 bg-bear/10 border border-bear/30 rounded-lg px-3 py-1.5">
                <span className="w-2 h-2 rounded-full bg-bear" />
                <span className="text-xs font-bold text-bear">{sellCount} SELL</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
