"use client";
import { useState, Fragment } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { fetchMultibaggerScreen, fetchMultibaggerStatus, MultibaggerScreen, MultibaggerStock } from "@/utils/api";
import { Gem, Wifi, Clock, ChevronDown, ChevronUp, Flame, AlertTriangle, Check, X } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";

const VERDICT: Record<string, { label: string; color: string }> = {
  strong_buy: { label: "Strong Buy", color: "text-bull bg-bull/10 border-bull/30" },
  watchlist: { label: "Watchlist", color: "text-yellow-400 bg-yellow-500/10 border-yellow-500/30" },
  avoid: { label: "Avoid", color: "text-bear bg-bear/10 border-bear/30" },
};

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

const METRICS_BY_MARKET: Record<"IN" | "US", { key: keyof MultibaggerStock; label: string; fmt: (v: number) => string }[]> = {
  IN: [
    { key: "market_cap_cr", label: "Mkt Cap", fmt: (v) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })} Cr` },
    { key: "pe_ratio", label: "P/E", fmt: (v) => `${v.toFixed(1)}×` },
    { key: "roe_pct", label: "ROE", fmt: (v) => `${v.toFixed(1)}%` },
    { key: "roce_pct", label: "ROCE", fmt: (v) => `${v.toFixed(1)}%` },
    { key: "debt_to_equity_pct", label: "D/E", fmt: (v) => `${(v / 100).toFixed(2)}×` },
    { key: "sales_growth_3y_pct", label: "Sales 3Y", fmt: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` },
    { key: "profit_growth_3y_pct", label: "Profit 3Y", fmt: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` },
    { key: "promoter_holding_pct", label: "Promoter", fmt: (v) => `${v.toFixed(1)}%` },
  ],
  US: [
    { key: "market_cap_usd_m", label: "Mkt Cap", fmt: (v) => v >= 1000 ? `$${(v / 1000).toFixed(1)}B` : `$${v.toFixed(0)}M` },
    { key: "pe_ratio", label: "P/E", fmt: (v) => `${v.toFixed(1)}×` },
    { key: "roe_pct", label: "ROE", fmt: (v) => `${v.toFixed(1)}%` },
    { key: "roce_pct", label: "ROCE", fmt: (v) => `${v.toFixed(1)}%` },
    { key: "debt_to_equity_pct", label: "D/E", fmt: (v) => `${(v / 100).toFixed(2)}×` },
    { key: "sales_growth_3y_pct", label: "Sales 3Y", fmt: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` },
    { key: "profit_growth_3y_pct", label: "Profit 3Y", fmt: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` },
    { key: "insider_holding_pct", label: "Insider", fmt: (v) => `${v.toFixed(1)}%` },
  ],
};

export default function MultibaggerPage() {
  const [market, setMarket] = useState<"IN" | "US">("IN");
  const [screen, setScreen] = useState<MultibaggerScreen>("quality_compounder");
  const [expanded, setExpanded] = useState<string | null>(null);
  const active = SCREENS.find(s => s.key === screen)!;
  const METRICS = METRICS_BY_MARKET[market];

  const { data, isLoading } = useQuery({
    queryKey: ["multibagger-screen", screen, market],
    queryFn: () => fetchMultibaggerScreen(screen, market),
    staleTime: 60 * 60_000,
    refetchOnWindowFocus: false,
    // Keep the previous market/screen's results on screen while the new
    // ones load, instead of briefly going undefined — same header-jump
    // fix applied to Daily Picks for the identical IN/US toggle pattern.
    placeholderData: keepPreviousData,
  });

  const { data: status } = useQuery({
    queryKey: ["multibagger-status", market],
    queryFn: () => fetchMultibaggerStatus(market),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    placeholderData: keepPreviousData,
  });

  const lastRefreshed = data?.last_refreshed
    ? new Date(data.last_refreshed).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true })
    : null;

  return (
    <div className="space-y-6">
      {/* Header — alignment matches Daily Picks / Market Heatmap / Screener style */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Gem size={22} className="text-brand-500 shrink-0" />
          <div>
            <h1 className="text-2xl font-bold">Multibagger Screen</h1>
            <p className="text-sm text-gray-400 mt-1">
              Three hard-filter screens — never merge them, that's how you get zero results
              {" · refreshed nightly at " + (market === "IN" ? "10:30 PM IST" : "7:30 AM IST")}
              {status?.last_summary?.total ? ` · screened from ${status.last_summary.total.toLocaleString()} ${market === "IN" ? "NSE" : "US"} stocks` : ""}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap">
          <div className="flex items-center gap-0.5 bg-dark-card border border-dark-border rounded-lg p-0.5">
            {(["IN", "US"] as const).map(m => (
              <button key={m} onClick={() => setMarket(m)}
                className={clsx("px-3 py-1.5 rounded-md text-xs font-medium transition-colors",
                  market === m ? "bg-brand-500 text-white" : "text-gray-400 hover:text-white")}>
                {m === "IN" ? "🇮🇳 IN" : "🇺🇸 US"}
              </button>
            ))}
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
        <div className="px-4 py-3 border-b border-dark-border flex items-center justify-between flex-wrap gap-2">
          <h2 className="font-semibold text-sm text-gray-300">{active.label}</h2>
          {data && data.count > 0 && (
            <span className="text-xs text-gray-500">
              {data.count} match{data.count !== 1 ? "es" : ""} ·{" "}
              <span className="text-brand-400">{data.results.filter(r => r.shortlisted).length} shortlisted</span> (top ~20%)
            </span>
          )}
        </div>
        <p className="px-4 pt-3 text-[11px] text-gray-500 leading-relaxed">
          Score is a transparent rule-based checklist ({data?.results[0]?.scorecard.max_score ?? (market === "IN" ? 12 : 10)} fundamentals checks{market === "US" ? " — no promoter pledge or 5Y growth, neither exists for US filings" : ""}) — separate from, and not the same as, the AI signal shown on each stock's own page.
          Click a row for the full breakdown. Verdict downgrades to Avoid if any Anti-Loss red flag is present, regardless of score.
        </p>

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
                  <th className="px-3 py-2.5 text-left whitespace-nowrap">Verdict</th>
                  <th className="px-3 py-2.5 text-right whitespace-nowrap">Score</th>
                  {METRICS.map(m => <th key={m.key} className="px-3 py-2.5 text-right whitespace-nowrap">{m.label}</th>)}
                  <th className="px-3 py-2.5"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-border">
                {data.results.map((r, i) => {
                  const showShortlistDivider = i > 0 && r.shortlisted === false && data.results[i - 1].shortlisted === true;
                  const v = VERDICT[r.scorecard.verdict];
                  const isOpen = expanded === r.symbol;
                  return (
                    <Fragment key={r.symbol}>
                      {showShortlistDivider && (
                        <tr key={`divider-${r.symbol}`}>
                          <td colSpan={METRICS.length + 4} className="px-4 py-1.5 bg-dark-bg text-[11px] text-gray-500 uppercase tracking-wide">
                            Other matches (outside the top 20% shortlist)
                          </td>
                        </tr>
                      )}
                      <tr
                        key={r.symbol}
                        onClick={() => setExpanded(isOpen ? null : r.symbol)}
                        className={clsx("hover:bg-white/[0.03] cursor-pointer", r.shortlisted && "bg-brand-500/[0.04]")}
                      >
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-1.5">
                            {r.shortlisted && <Flame size={12} className="text-orange-400 shrink-0" />}
                            <Link href={`/stock/${r.symbol}?market=${market}`} onClick={(e) => e.stopPropagation()} className="font-mono font-bold text-white hover:text-brand-400">
                              {r.symbol}
                            </Link>
                          </div>
                          {r.company_name && <p className="text-[11px] text-gray-500 truncate max-w-[160px]">{r.company_name}</p>}
                        </td>
                        <td className="px-3 py-2.5">
                          <span className={clsx("inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap", v.color)}>
                            {r.scorecard.red_flags.length > 0 && <AlertTriangle size={10} />}
                            {v.label}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-gray-300 whitespace-nowrap">
                          {r.scorecard.score}/{r.scorecard.max_score}
                        </td>
                        {METRICS.map(m => {
                          const val = r[m.key];
                          return (
                            <td key={m.key} className="px-3 py-2.5 text-right font-mono text-gray-300 whitespace-nowrap">
                              {typeof val === "number" ? m.fmt(val) : "—"}
                            </td>
                          );
                        })}
                        <td className="px-3 py-2.5 text-gray-500">
                          {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr key={`detail-${r.symbol}`}>
                          <td colSpan={METRICS.length + 4} className="px-4 py-4 bg-dark-bg">
                            <div className="grid sm:grid-cols-2 gap-4">
                              <div>
                                <p className="text-xs font-semibold text-gray-400 mb-2">Scorecard checklist</p>
                                <ul className="space-y-1">
                                  {r.scorecard.checks.map((c, ci) => (
                                    <li key={ci} className="flex items-start gap-1.5 text-xs">
                                      {c.passed
                                        ? <Check size={13} className="text-bull shrink-0 mt-0.5" />
                                        : <X size={13} className="text-gray-600 shrink-0 mt-0.5" />}
                                      <span className={c.passed ? "text-gray-300" : "text-gray-600"}>{c.label}</span>
                                    </li>
                                  ))}
                                </ul>
                              </div>
                              {r.scorecard.red_flags.length > 0 && (
                                <div>
                                  <p className="text-xs font-semibold text-bear mb-2 flex items-center gap-1.5">
                                    <AlertTriangle size={13} /> Anti-loss red flags
                                  </p>
                                  <ul className="space-y-1">
                                    {r.scorecard.red_flags.map((f, fi) => (
                                      <li key={fi} className="text-xs text-red-300">{f}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <p className="text-xs text-gray-600 text-center">
        Data sourced from {market === "IN" ? "screener.in" : "Yahoo Finance"} · Refreshed nightly · Educational research tool only — not investment advice.
        Do your own due diligence before acting on any screen result.
      </p>
    </div>
  );
}
