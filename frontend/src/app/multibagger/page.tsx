"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchMultibaggerScreen, fetchMultibaggerStatus, MultibaggerScreen, MultibaggerStock } from "@/utils/api";
import { Gem, Wifi, Clock } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";

const SCREENS: { key: MultibaggerScreen; label: string; color: string; desc: string }[] = [
  {
    key: "quality_compounder",
    label: "Quality Compounders",
    color: "text-bull border-bull/40 bg-bull/10",
    desc: "Core portfolio — stable, proven, suitable for 5-10 year holding. Strict on debt, pledge, and profitability.",
  },
  {
    key: "multibagger_discovery",
    label: "Multibagger Discovery",
    color: "text-yellow-400 border-yellow-500/40 bg-yellow-500/10",
    desc: "Future compounders pipeline — midcaps and emerging smallcaps with accelerating growth. Looser on financial history by design.",
  },
  {
    key: "tenbagger_early",
    label: "10-Bagger Early Detection",
    color: "text-blue-400 border-blue-500/40 bg-blue-500/10",
    desc: "Pre-compounder screen — still messy, but improving fast. Catches turnarounds and niche manufacturers before they're obvious.",
  },
];

const METRICS: { key: keyof MultibaggerStock; label: string; fmt: (v: number) => string }[] = [
  { key: "market_cap_cr", label: "Mkt Cap", fmt: (v) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })} Cr` },
  { key: "pe_ratio", label: "P/E", fmt: (v) => `${v.toFixed(1)}×` },
  { key: "roe_pct", label: "ROE", fmt: (v) => `${v.toFixed(1)}%` },
  { key: "roce_pct", label: "ROCE", fmt: (v) => `${v.toFixed(1)}%` },
  { key: "debt_to_equity_pct", label: "D/E", fmt: (v) => `${(v / 100).toFixed(2)}×` },
  { key: "sales_growth_3y_pct", label: "Sales 3Y", fmt: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` },
  { key: "profit_growth_3y_pct", label: "Profit 3Y", fmt: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` },
  { key: "promoter_holding_pct", label: "Promoter", fmt: (v) => `${v.toFixed(1)}%` },
];

export default function MultibaggerPage() {
  const [screen, setScreen] = useState<MultibaggerScreen>("quality_compounder");
  const active = SCREENS.find(s => s.key === screen)!;

  const { data, isLoading } = useQuery({
    queryKey: ["multibagger-screen", screen],
    queryFn: () => fetchMultibaggerScreen(screen),
    staleTime: 60 * 60_000,
    refetchOnWindowFocus: false,
  });

  const { data: status } = useQuery({
    queryKey: ["multibagger-status"],
    queryFn: fetchMultibaggerStatus,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  const lastRefreshed = data?.last_refreshed
    ? new Date(data.last_refreshed).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true })
    : null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <Gem size={22} className="text-brand-500" />
          <div>
            <h1 className="text-2xl font-bold">Multibagger Screen</h1>
            <p className="text-sm text-gray-400 mt-1">Three hard-filter screens — never merge them, that's how you get zero results</p>
          </div>
        </div>
        {status?.running ? (
          <span className="flex items-center gap-1.5 text-xs text-yellow-400 bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-3 py-1.5">
            <Wifi size={12} className="animate-pulse" /> Refreshing fundamentals…
          </span>
        ) : lastRefreshed && (
          <span className="flex items-center gap-1.5 text-xs text-gray-500 bg-dark-card border border-dark-border rounded-lg px-3 py-1.5">
            <Clock size={12} /> Refreshed {lastRefreshed}
          </span>
        )}
      </div>

      {/* Screen selector */}
      <div className="grid sm:grid-cols-3 gap-3">
        {SCREENS.map((s) => (
          <button
            key={s.key}
            onClick={() => setScreen(s.key)}
            className={clsx(
              "text-left rounded-xl border p-4 transition-all",
              screen === s.key ? s.color : "border-dark-border bg-dark-card text-gray-400 hover:border-white/20"
            )}
          >
            <p className="font-bold text-sm mb-1">{s.label}</p>
            <p className="text-xs opacity-80 leading-relaxed">{s.desc}</p>
          </button>
        ))}
      </div>

      {/* Results */}
      <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
        <div className="px-4 py-3 border-b border-dark-border flex items-center justify-between">
          <h2 className="font-semibold text-sm text-gray-300">{active.label}</h2>
          {data && <span className="text-xs text-gray-500">{data.count} match{data.count !== 1 ? "es" : ""}</span>}
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-sm text-gray-500">Loading…</div>
        ) : data?.error ? (
          <div className="p-8 text-center text-sm text-gray-500">
            Screen data not available yet.
            <p className="text-xs text-gray-600 mt-1">{data.error}</p>
          </div>
        ) : !data || data.count === 0 ? (
          <div className="p-8 text-center text-sm text-gray-500">
            No stocks currently pass this screen — try another, or check back after the next nightly refresh.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-dark-border text-xs text-gray-500">
                  <th className="px-4 py-2.5 text-left">Stock</th>
                  {METRICS.map(m => <th key={m.key} className="px-3 py-2.5 text-right whitespace-nowrap">{m.label}</th>)}
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-border">
                {data.results.map((r) => (
                  <tr key={r.symbol} className="hover:bg-white/[0.03]">
                    <td className="px-4 py-2.5">
                      <Link href={`/stock/${r.symbol}?market=IN`} className="font-mono font-bold text-white hover:text-brand-400">
                        {r.symbol}
                      </Link>
                      {r.company_name && <p className="text-[11px] text-gray-500 truncate max-w-[160px]">{r.company_name}</p>}
                    </td>
                    {METRICS.map(m => {
                      const val = r[m.key];
                      return (
                        <td key={m.key} className="px-3 py-2.5 text-right font-mono text-gray-300 whitespace-nowrap">
                          {typeof val === "number" ? m.fmt(val) : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <p className="text-xs text-gray-600 text-center">
        Data sourced from screener.in · Refreshed nightly · Educational research tool only — not investment advice.
        Do your own due diligence before acting on any screen result.
      </p>
    </div>
  );
}
