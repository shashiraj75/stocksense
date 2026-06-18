"use client";

interface Slice {
  symbol: string;
  value: number;
  signal: string | null;
}

const PALETTE = [
  "#6366f1", "#22c55e", "#f59e0b", "#3b82f6", "#ec4899",
  "#14b8a6", "#f97316", "#a855f7", "#ef4444", "#84cc16",
];

export function PortfolioAllocationChart({ slices }: { slices: Slice[] }) {
  const withValue = slices.filter(s => s.value > 0);
  if (withValue.length === 0) return null;

  const total = withValue.reduce((s, r) => s + r.value, 0);

  const buyCount  = withValue.filter(s => s.signal === "BUY").length;
  const sellCount = withValue.filter(s => s.signal === "SELL").length;
  const holdCount = withValue.filter(s => s.signal !== "BUY" && s.signal !== "SELL" && s.signal !== null).length;

  return (
    <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-4">
      <h2 className="font-semibold text-sm text-gray-300">Portfolio Allocation</h2>

      {/* Stacked bar */}
      <div className="flex h-6 w-full rounded-full overflow-hidden gap-px">
        {withValue.map((s, i) => {
          const w = (s.value / total) * 100;
          return (
            <div
              key={s.symbol}
              className="h-full transition-all duration-300 group relative"
              style={{ width: `${w}%`, backgroundColor: PALETTE[i % PALETTE.length], minWidth: w > 1 ? undefined : 2 }}
              title={`${s.symbol}: ${w.toFixed(1)}%`}
            />
          );
        })}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {withValue.map((s, i) => {
          const w = (s.value / total) * 100;
          return (
            <div key={s.symbol} className="flex items-center gap-1.5 text-xs">
              <span className="inline-block w-2.5 h-2.5 rounded-sm flex-shrink-0"
                style={{ backgroundColor: PALETTE[i % PALETTE.length] }} />
              <span className="text-gray-300 font-mono font-bold">{s.symbol}</span>
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
