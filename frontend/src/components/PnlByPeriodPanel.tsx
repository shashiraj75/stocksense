"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { BarChart3 } from "lucide-react";
import { fetchPnlByPeriod, type PnlPeriod, type PnlPeriodBucket } from "@/utils/api";

const PERIODS: { key: PnlPeriod; label: string }[] = [
  { key: "day", label: "Daily" },
  { key: "week", label: "Weekly" },
  { key: "month", label: "Monthly" },
  { key: "year", label: "Yearly" },
];

const fmt = (n: number, dec = 0, locale = "en-IN") =>
  n.toLocaleString(locale, { minimumFractionDigits: dec, maximumFractionDigits: dec });

function formatPeriodLabel(isoStart: string, period: PnlPeriod): string {
  const d = new Date(isoStart);
  if (Number.isNaN(d.getTime())) return isoStart;
  if (period === "day") return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  if (period === "week") return `Week of ${d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" })}`;
  if (period === "month") return d.toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
  return String(d.getUTCFullYear());
}

function PnlBucketRow({ bucket, period, market, currency, locale }: {
  bucket: PnlPeriodBucket; period: PnlPeriod; market: "IN" | "US"; currency: string; locale: string;
}) {
  const pnl = market === "IN" ? bucket.in_realized_pnl : bucket.us_realized_pnl_usd;
  const tradeCount = market === "IN" ? bucket.in_trade_count : bucket.us_trade_count;
  if (tradeCount === 0) return null;
  return (
    <tr className="border-b border-dark-border last:border-0 hover:bg-white/[0.02] transition-colors">
      <td className="px-4 py-2.5 text-sm text-gray-300">{formatPeriodLabel(bucket.period_start, period)}</td>
      <td className="px-4 py-2.5 text-xs text-gray-500">{tradeCount} trade{tradeCount !== 1 ? "s" : ""}</td>
      <td className="px-4 py-2.5 text-right">
        <span className={clsx("text-sm font-mono font-bold", pnl > 0 ? "text-bull" : pnl < 0 ? "text-bear" : "text-gray-400")}>
          {pnl >= 0 ? "+" : ""}{currency}{fmt(Math.abs(pnl), 0, locale)}
        </span>
      </td>
    </tr>
  );
}

// P&L broken down by calendar period (Daily/Weekly/Monthly/Yearly), for
// at-a-glance analysis of when realized gains/losses actually happened —
// distinct from the headline "Realized P&L" stat card above, which is a
// single cumulative figure. Lazy-loaded from paper-trading/page.tsx (same
// below-the-fold extraction pattern as PaperTradeHistoryBlock) since it's
// its own independent data fetch, not derived from the already-loaded
// portfolio payload.
export function PnlByPeriodPanel({ market, currency, locale }: {
  market: "IN" | "US"; currency: string; locale: string;
}) {
  const [period, setPeriod] = useState<PnlPeriod>("month");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["paper-trading-pnl-by-period", period],
    queryFn: () => fetchPnlByPeriod(period),
    staleTime: 60_000,
  });

  const buckets = (data?.buckets ?? []).filter(b =>
    (market === "IN" ? b.in_trade_count : b.us_trade_count) > 0
  );

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-dark-border flex-wrap">
        <div className="flex items-center gap-2">
          <BarChart3 size={15} className="text-brand-400" />
          <span className="font-semibold text-sm text-white">P&L by Period</span>
        </div>
        <div className="flex items-center gap-1">
          {PERIODS.map(p => (
            <button
              key={p.key}
              onClick={() => setPeriod(p.key)}
              aria-pressed={period === p.key}
              className={clsx(
                "px-2.5 py-1 text-xs rounded-full border transition-colors",
                period === p.key
                  ? "border-brand-500 text-brand-400 bg-brand-500/10"
                  : "border-dark-border text-gray-500 hover:text-gray-300",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="px-4 py-6 text-center text-xs text-gray-500">Loading…</div>
      ) : isError ? (
        <div className="px-4 py-6 text-center text-xs text-gray-500">Could not load P&L history.</div>
      ) : buckets.length === 0 ? (
        <div className="px-4 py-6 text-center text-xs text-gray-500">
          No closed trades yet for this {PERIODS.find(p => p.key === period)?.label.toLowerCase()} view.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-dark-border text-xs text-gray-500">
                <th className="px-4 py-2 text-left">Period</th>
                <th className="px-4 py-2 text-left">Trades</th>
                <th className="px-4 py-2 text-right">Realized P&L</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map(b => (
                <PnlBucketRow key={b.period_start} bucket={b} period={period} market={market} currency={currency} locale={locale} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default PnlByPeriodPanel;
