"use client";

interface Props {
  entryLow: number;
  entryHigh: number;
  stopLoss: number;
  takeProfit: number;
  currentPrice: number | null;
  signal: "BUY" | "SELL" | "HOLD";
  currency: string;
}

export function TradeLevelVisualizer({
  entryLow, entryHigh, stopLoss, takeProfit, currentPrice, signal, currency,
}: Props) {
  const pad = (takeProfit - stopLoss) * 0.06;
  const min = stopLoss - pad;
  const max = takeProfit + pad;
  const range = max - min;
  if (range <= 0) return null;

  const pct = (v: number) => Math.max(0, Math.min(100, ((v - min) / range) * 100));

  const slPct  = pct(stopLoss);
  const elPct  = pct(entryLow);
  const ehPct  = pct(entryHigh);
  const tpPct  = pct(takeProfit);
  const curPct = currentPrice !== null ? pct(currentPrice) : null;

  const isSell = signal === "SELL";
  const entryFill  = isSell ? "rgba(239,68,68,0.35)"  : "rgba(34,197,94,0.35)";
  const entryBorder = isSell ? "#ef4444" : "#22c55e";

  const fmt = (n: number) => {
    if (n >= 1000) return `${currency}${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
    return `${currency}${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  };

  // Labels to render (deduplicate positions within 8% of each other)
  const labels: { pct: number; text: string; color: string; bold?: boolean }[] = [];
  labels.push({ pct: slPct,  text: fmt(stopLoss),   color: "#ef4444" });
  labels.push({ pct: elPct,  text: fmt(entryLow),   color: entryBorder });
  labels.push({ pct: ehPct,  text: fmt(entryHigh),  color: entryBorder });
  labels.push({ pct: tpPct,  text: fmt(takeProfit), color: "#22c55e" });
  if (curPct !== null && currentPrice !== null)
    labels.push({ pct: curPct, text: fmt(currentPrice), color: "#fff", bold: true });

  // Suppress labels that are too close to a prior one (avoid overlap)
  const shown: typeof labels = [];
  for (const l of labels.sort((a, b) => a.pct - b.pct)) {
    const last = shown[shown.length - 1];
    if (!last || l.pct - last.pct > 9) shown.push(l);
  }

  return (
    <div className="mt-2 mb-4 select-none">
      <p className="text-xs text-gray-500 mb-2 font-medium">Price Level Map</p>

      {/* Track */}
      <div className="relative h-7 rounded-full overflow-visible bg-dark-bg border border-dark-border">
        {/* Left dead zone (below stop loss) */}
        <div className="absolute h-full rounded-l-full bg-bear/10"
          style={{ left: 0, width: `${slPct}%` }} />

        {/* Stop-loss line */}
        <div className="absolute h-full w-px bg-bear/60"
          style={{ left: `${slPct}%` }} />

        {/* Entry zone */}
        <div className="absolute h-full border-x"
          style={{ left: `${elPct}%`, width: `${ehPct - elPct}%`,
            backgroundColor: entryFill, borderColor: entryBorder + "80" }} />

        {/* Upside zone (entry high → take profit) */}
        <div className="absolute h-full bg-bull/10"
          style={{ left: `${ehPct}%`, width: `${tpPct - ehPct}%` }} />

        {/* Take-profit line */}
        <div className="absolute h-full w-px bg-bull/60"
          style={{ left: `${tpPct}%` }} />

        {/* Current price marker */}
        {curPct !== null && (
          <div
            className="absolute top-1/2 z-10 flex items-center justify-center"
            style={{ left: `${curPct}%`, transform: "translate(-50%, -50%)" }}
          >
            <span className="relative flex h-4 w-4">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand-500 opacity-40" />
              <span className="relative inline-flex rounded-full h-4 w-4 bg-brand-500 border-2 border-white shadow" />
            </span>
          </div>
        )}
      </div>

      {/* Price labels */}
      <div className="relative h-5 mt-1">
        {shown.map((l, i) => (
          <span
            key={i}
            className="absolute text-[10px] font-mono whitespace-nowrap"
            style={{
              left: `${l.pct}%`,
              transform: "translateX(-50%)",
              color: l.color,
              fontWeight: l.bold ? 700 : 400,
            }}
          >
            {l.text}
          </span>
        ))}
      </div>

      {/* Zone legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 text-[10px] text-gray-500">
        <span className="flex items-center gap-1">
          <span className="inline-block w-2.5 h-2 rounded-sm bg-bear/40" /> Stop Loss
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-2.5 h-2 rounded-sm" style={{ backgroundColor: entryFill }} />
          {isSell ? "Sell Zone" : "Buy Zone"}
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-2.5 h-2 rounded-sm bg-bull/20" /> Upside
        </span>
        {curPct !== null && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-2 rounded-full bg-brand-500" /> Current Price
          </span>
        )}
      </div>
    </div>
  );
}
