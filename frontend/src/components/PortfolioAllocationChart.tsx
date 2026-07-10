"use client";
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
  stockSlices, sectorSlices, unresolvedSectorValue = 0, unresolvedSectorCount = 0, currency = "", mode, onModeChange,
}: {
  stockSlices: StockSlice[];
  // Only ever genuinely-resolved sectors (including the resolved "Other"
  // bucket) — never a synthetic "still loading" entry. See
  // unresolvedSectorValue/Count below for that.
  sectorSlices: SectorSlice[];
  // Value/count of holdings whose sector hasn't resolved yet, tracked
  // separately from sectorSlices so it can never be rendered as if it were
  // a real chart segment (a "Loading… 100%" bar that looks like a complete,
  // real allocation) — surfaced instead as a distinct "Resolving sectors…"
  // line, visually subordinate to the real slices above it.
  unresolvedSectorValue?: number;
  unresolvedSectorCount?: number;
  currency?: string;
  // Lifted from this component so the same toggle also drives the holdings
  // table's grouping (portfolio/page.tsx owns the state) — null still means
  // "follow the data" until the user explicitly clicks a toggle button.
  mode: "sector" | "stock" | null;
  onModeChange: (mode: "sector" | "stock") => void;
}) {
  const withStockValue = stockSlices.filter(s => s.value > 0);
  const withSectorValue = sectorSlices.filter(s => s.value > 0);
  // Any resolved sector value at all — even a single "Other" bucket while
  // the rest are still resolving — is enough to show the sector view and
  // its toggle. A stricter ">1 distinct sector" threshold made large India
  // portfolios (many holdings, slower per-symbol resolution) look
  // structurally different from small/fast US ones: everything sat in
  // "Other" for a while, so the toggle stayed hidden and it silently fell
  // back to by-stock — the two markets should present the same UI shape
  // (toggle + sector-first default) regardless of how much sector data has
  // resolved so far; "Other" simply shrinks as more arrives. Crucially,
  // `sectorSlices` itself never contains an unresolved placeholder (see the
  // prop's own comment), so this can no longer be tricked into thinking
  // "still loading" is real sector data.
  const hasSectorData = withSectorValue.length > 0;

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
                onClick={() => onModeChange(m)}
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

      {/* Resolving sectors — a distinct, visually muted state, never a
          chart segment. Only relevant in sector mode: by-stock allocation
          doesn't depend on sector data at all, so there's nothing to flag
          there. Shown even when hasSectorData is false (nothing real has
          resolved yet) — in that case the bar/legend above are already
          showing By Stock (effectiveMode falls back automatically since
          hasSectorData is false), so this reads as "still working on
          sectors, here's your allocation by stock in the meantime" rather
          than a stalled-looking single "Loading" bar. */}
      {effectiveMode === "sector" && unresolvedSectorCount > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-gray-500">
          <span className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-pulse shrink-0" />
          Resolving sector{unresolvedSectorCount !== 1 ? "s" : ""}… {unresolvedSectorCount} holding{unresolvedSectorCount !== 1 ? "s" : ""}
          {unresolvedSectorValue > 0 && ` · ${currency}${unresolvedSectorValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
        </p>
      )}

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
