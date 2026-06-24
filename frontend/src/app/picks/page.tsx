"use client";
import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api, fetchQuote } from "@/utils/api";
import {
  TrendingUp, Clock, AlertCircle, ChevronDown, ChevronUp,
  Loader2, Target, ShieldAlert, Zap, CheckCircle, BarChart2, Activity, FlaskConical,
} from "lucide-react";
import clsx from "clsx";
import { PaperTradeModal } from "@/components/PaperTradeModal";
import { useMarketPreference } from "@/hooks/useMarketPreference";

// ── Types ─────────────────────────────────────────────────────────────────────
type ReasonItem = { indicator: string; signal: string; reason: string };
type QualityFactors = { score?: number; sector?: string; piotroski?: number | null; breakdown?: Record<string, number> };
type FactorZScores = { tech?: number; fund?: number; sentiment?: number; quality?: number };

type Pick = {
  symbol: string; name: string; price: number; target: number;
  stop_loss?: number; entry_low?: number; entry_high?: number;
  risk_reward?: number; confidence: number; tech_score?: number;
  fund_score?: number; sentiment?: string; reasoning: ReasonItem[];
  summary?: string; quality_factors?: QualityFactors; factor_zscores?: FactorZScores;
  combined_alpha?: number; portfolio_weight?: number; regime_label?: string;
  score_band?: string; horizon: string;
};

type AlphaEngineMeta = { ic_weights?: Record<string, number>; regime?: string; n_scored?: number; n_buy?: number; meta_model?: boolean };
type GlobalContext = { score?: number; levels?: Record<string, number>; changes?: Record<string, number> };
type DailyPicksResponse = {
  generated_at: string | null;
  market?: "IN" | "US";
  currency?: string;
  picks: { short: Pick[]; medium: Pick[]; long: Pick[] };
  alpha_engine?: Record<string, AlphaEngineMeta>;
  regime?: { label: string; description: string };
  screened_from?: number;
  candidates?: number;
  generating?: boolean;
};

type ValidationResult = {
  available: boolean;
  buy_hit_rate_pct?: number;
  avg_return_on_buy_pct?: number;
  avg_alpha_on_buy_pct?: number;
  sharpe_on_buys?: number;
  beat_benchmark_pct?: number;
  buy_signals?: number;
  total_signals?: number;
  max_drawdown_pct?: number;
  score_buckets?: { score_range: string; count: number; hit_rate_pct: number; avg_return_pct: number }[];
  factor_ic?: Record<string, number | null>;
  run_at?: string;
};

type LivePick = {
  symbol: string; date: string; entry_price: number; score: number; confidence: number;
  return_5d?: number; return_20d?: number; return_60d?: number;
  benchmark_return_5d?: number; benchmark_return_20d?: number; benchmark_return_60d?: number;
};

// ── Constants ─────────────────────────────────────────────────────────────────
const MARKETS = [
  { key: "IN" as const, short: "🇮🇳 IN", label: "🇮🇳 NSE India",  currency: "₹", locale: "en-IN", tz: "Asia/Kolkata",     genTime: "2 AM IST" },
  { key: "US" as const, short: "🇺🇸 US", label: "🇺🇸 NYSE/NASDAQ", currency: "$", locale: "en-US", tz: "America/New_York", genTime: "6:00 PM IST" },
];

const HORIZONS = [
  { key: "short",  label: "Short Term",  sub: "1–5 days"   },
  { key: "medium", label: "Medium Term", sub: "2–4 weeks"  },
  { key: "long",   label: "Long Term",   sub: "3–6 months" },
] as const;

const SIGNAL_COLOR: Record<string, string> = {
  BUY: "text-green-400", BULLISH: "text-green-400",
  SELL: "text-red-400",  BEARISH: "text-red-400",
  HOLD: "text-yellow-400", NEUTRAL: "text-gray-400", INFO: "text-blue-400",
};
const SIGNAL_ICON: Record<string, string> = {
  BUY: "▲", BULLISH: "▲", SELL: "▼", BEARISH: "▼", HOLD: "→", NEUTRAL: "→", INFO: "·",
};
const INDICATOR_GROUP: Record<string, string> = {
  RSI: "Technical", MACD: "Technical", EMA: "Technical", SMA: "Technical",
  Momentum: "Technical", Volume: "Technical", Candlestick: "Technical",
  "Bollinger Bands": "Technical", ATR: "Technical", "Price Level": "Technical",
  Fundamental: "Fundamental", Analyst: "Fundamental",
  "Market Regime": "Market", Global: "Global Macro", Macro: "Global Macro",
  Sentiment: "Sentiment",
  Earnings: "Quality Factors", Ownership: "Quality Factors",
  "Inst. Flow": "Quality Factors", "Rel. Strength": "Quality Factors",
  Sector: "Quality Factors", Valuation: "Quality Factors",
  Risk: "Quality Factors", Liquidity: "Quality Factors",
  "Corp. Actions": "Quality Factors", Quality: "Quality Factors",
};
const GROUP_ORDER = ["Technical", "Fundamental", "Market", "Global Macro", "Sentiment", "Quality Factors", "Other"];
const SCORE_BAND_STYLE: Record<string, string> = {
  "STRONG BUY": "bg-green-500/20 text-green-300 border-green-500/40",
  "BUY":        "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  "HOLD":       "bg-yellow-500/15 text-yellow-400 border-yellow-500/30",
};

// ── Small components ──────────────────────────────────────────────────────────
function ScoreBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <div className="flex justify-between text-xs mb-0.5">
        <span className="text-gray-500">{label}</span>
        <span className={`font-medium ${color}`}>{value}%</span>
      </div>
      <div className="h-1 bg-dark-border rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color.replace("text-", "bg-")}`} style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

function Stat({ label, value, sub, good }: { label: string; value: string; sub?: string; good?: boolean }) {
  return (
    <div className="bg-dark-border/40 rounded-xl p-3 text-center">
      <p className="text-[10px] text-gray-500 mb-1">{label}</p>
      <p className={clsx("text-lg font-bold tabular-nums", good === true ? "text-green-400" : good === false ? "text-red-400" : "text-white")}>{value}</p>
      {sub && <p className="text-[11px] text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function TopReasons({ reasoning }: { reasoning: ReasonItem[] }) {
  const priority = reasoning.filter(r => ["BUY", "BULLISH", "BEARISH", "SELL"].includes(r.signal));
  const rest = reasoning.filter(r => !["BUY", "BULLISH", "BEARISH", "SELL"].includes(r.signal));
  const top = [...priority, ...rest].slice(0, 3);
  if (!top.length) return null;
  return (
    <div className="space-y-1.5 mb-3">
      {top.map((r, i) => (
        <div key={i} className="flex items-start gap-2">
          <span className={clsx("text-xs font-bold mt-0.5 flex-shrink-0", SIGNAL_COLOR[r.signal] ?? "text-gray-400")}>
            {SIGNAL_ICON[r.signal] ?? "·"}
          </span>
          <p className="text-xs text-gray-300 leading-relaxed">{r.reason}</p>
        </div>
      ))}
    </div>
  );
}

// ── Priority 1: Backtest Truth Panel ─────────────────────────────────────────
function BacktestPanel({ horizon, benchmarkLabel }: { horizon: string; benchmarkLabel: string }) {
  const { data, isLoading } = useQuery<ValidationResult>({
    queryKey: ["validation", horizon],
    queryFn: () => api.get(`/api/validation/results?horizon=${horizon}`).then(r => r.data),
    staleTime: 60 * 60_000,
    retry: false,
  });

  if (isLoading) return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-24" />
  );
  if (!data?.available || data.buy_hit_rate_pct == null) return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4 text-center">
      <p className="text-xs text-gray-500">No backtest results yet — run validation from the Backtest tab to see real accuracy.</p>
    </div>
  );

  const hitRate = data.buy_hit_rate_pct;
  const hitColor = hitRate >= 60 ? "text-green-400" : hitRate >= 50 ? "text-yellow-400" : "text-red-400";

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart2 size={15} className="text-blue-400" />
          <p className="text-sm font-semibold text-white">Walk-Forward Backtest Results</p>
          <span className="text-xs px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">Real data</span>
        </div>
        {data.run_at && (
          <p className="text-[11px] text-gray-400">
            Run {new Date(data.run_at).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}
          </p>
        )}
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="BUY Hit Rate" value={`${hitRate}%`}
          sub={`${data.buy_signals ?? "—"} signals`} good={hitRate >= 55} />
        <Stat label="Avg Return on BUY" value={data.avg_return_on_buy_pct != null ? `${data.avg_return_on_buy_pct > 0 ? "+" : ""}${data.avg_return_on_buy_pct}%` : "—"}
          good={data.avg_return_on_buy_pct != null ? data.avg_return_on_buy_pct > 0 : undefined} />
        <Stat label={`Alpha vs ${benchmarkLabel}`} value={data.avg_alpha_on_buy_pct != null ? `${data.avg_alpha_on_buy_pct > 0 ? "+" : ""}${data.avg_alpha_on_buy_pct}%` : "—"}
          good={data.avg_alpha_on_buy_pct != null ? data.avg_alpha_on_buy_pct > 0 : undefined} />
        <Stat label="Sharpe Ratio" value={data.sharpe_on_buys != null ? data.sharpe_on_buys.toFixed(2) : "—"}
          good={data.sharpe_on_buys != null ? data.sharpe_on_buys > 0.5 : undefined} />
      </div>

      {/* Priority 2: Confidence Calibration — score bucket table */}
      {data.score_buckets && data.score_buckets.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
            Confidence Score Calibration — Does higher confidence = higher accuracy?
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-dark-border">
                  <th className="text-left py-1.5 pr-3">AI Confidence</th>
                  <th className="text-right py-1.5 pr-3">Signals</th>
                  <th className="text-right py-1.5 pr-3">Hit Rate</th>
                  <th className="text-right py-1.5">Avg Return</th>
                </tr>
              </thead>
              <tbody>
                {data.score_buckets.map(b => {
                  const hr = b.hit_rate_pct;
                  const hrColor = hr >= 65 ? "text-green-400" : hr >= 50 ? "text-yellow-400" : "text-red-400";
                  const retColor = b.avg_return_pct > 0 ? "text-green-400" : "text-red-400";
                  return (
                    <tr key={b.score_range} className="border-b border-dark-border/50">
                      <td className="py-1.5 pr-3 text-gray-300 font-mono">{b.score_range}</td>
                      <td className="py-1.5 pr-3 text-right text-gray-400">{b.count}</td>
                      <td className={clsx("py-1.5 pr-3 text-right font-semibold tabular-nums", hrColor)}>{hr}%</td>
                      <td className={clsx("py-1.5 text-right font-mono tabular-nums", retColor)}>
                        {b.avg_return_pct > 0 ? "+" : ""}{b.avg_return_pct?.toFixed(1)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-gray-400 mt-1.5">
            This table shows the real hit rate for each AI confidence band — not a theoretical score.
            If 80+ confidence picks hit 72% of the time historically, that's a calibrated signal.
          </p>
        </div>
      )}

      {/* Factor IC */}
      {data.factor_ic && (
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Factor Predictive Power (IC)</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.factor_ic).map(([factor, ic]) => {
              if (ic == null) return null;
              const good = ic > 0.03;
              const bad  = ic < -0.01;
              return (
                <div key={factor} className={clsx("flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs",
                  good ? "bg-green-500/10 border-green-500/20" : bad ? "bg-red-500/10 border-red-500/20" : "bg-dark-border/50 border-dark-border")}>
                  <span className="text-gray-400 capitalize">{factor}</span>
                  <span className={clsx("font-mono font-semibold", good ? "text-green-400" : bad ? "text-red-400" : "text-gray-300")}>
                    IC {ic > 0 ? "+" : ""}{ic.toFixed(3)}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="text-[11px] text-gray-400 mt-1.5">IC (Information Coefficient) = correlation between factor score and actual forward return. IC &gt; 0.03 is considered meaningful in quant finance.</p>
        </div>
      )}
    </div>
  );
}

// ── Priority 3: Live Picks Performance Tracker ────────────────────────────────
function LivePerformanceTracker({ horizon, currency, locale, benchmarkLabel }: { horizon: string; currency: string; locale: string; benchmarkLabel: string }) {
  const returnKey = horizon === "short" ? "return_5d" : horizon === "medium" ? "return_20d" : "return_60d";
  const benchKey  = horizon === "short" ? "benchmark_return_5d" : horizon === "medium" ? "benchmark_return_20d" : "benchmark_return_60d";

  const { data, isLoading } = useQuery<{ picks: LivePick[] }>({
    queryKey: ["picks-performance", horizon],
    queryFn: () => api.get(`/api/picks/performance?horizon=${horizon}&window_days=90`).then(r => r.data),
    staleTime: 30 * 60_000,
    retry: false,
  });

  const picks = (data?.picks ?? []).filter(p => (p as any)[returnKey] != null);

  if (isLoading) return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-20" />
  );
  if (!picks.length) return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4 text-center">
      <p className="text-xs text-gray-500">No resolved picks yet for this horizon — results appear once the holding period completes.</p>
    </div>
  );

  const returns   = picks.map(p => (p as any)[returnKey] as number);
  const benchRets = picks.map(p => (p as any)[benchKey] as number ?? 0);
  const avgRet    = returns.reduce((a, b) => a + b, 0) / returns.length;
  const avgBench  = benchRets.reduce((a, b) => a + b, 0) / benchRets.length;
  const winRate   = picks.filter(p => (p as any)[returnKey] > 0).length / picks.length * 100;
  const beatCount = picks.filter((p, i) => (p as any)[returnKey] > benchRets[i]).length;

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Activity size={15} className="text-green-400" />
        <p className="text-sm font-semibold text-white">Live Picks Performance (Last 90 Days)</p>
        <span className="text-xs px-2 py-0.5 rounded bg-green-500/10 text-green-400 border border-green-500/20">Real P&L</span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="Avg Return" value={`${avgRet >= 0 ? "+" : ""}${avgRet.toFixed(1)}%`} good={avgRet > 0} />
        <Stat label={`vs ${benchmarkLabel}`} value={`${(avgRet - avgBench) >= 0 ? "+" : ""}${(avgRet - avgBench).toFixed(1)}%`}
          good={(avgRet - avgBench) > 0} sub="alpha generated" />
        <Stat label="Win Rate" value={`${winRate.toFixed(0)}%`} good={winRate >= 55} sub={`${picks.length} resolved picks`} />
        <Stat label={`Beat ${benchmarkLabel}`} value={`${beatCount}/${picks.length}`}
          good={beatCount / picks.length >= 0.5} sub="picks beat benchmark" />
      </div>

      {/* Per-pick table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-dark-border text-left">
              <th className="py-1.5 pr-3">Symbol</th>
              <th className="py-1.5 pr-3">Date</th>
              <th className="py-1.5 pr-3 text-right">Entry</th>
              <th className="py-1.5 pr-3 text-right">Return</th>
              <th className="py-1.5 text-right">vs {benchmarkLabel}</th>
            </tr>
          </thead>
          <tbody>
            {picks.slice(0, 15).map((p, i) => {
              const ret   = (p as any)[returnKey] as number;
              const bench = (p as any)[benchKey] as number ?? 0;
              const alpha = ret - bench;
              return (
                <tr key={`${p.symbol}-${p.date}`} className="border-b border-dark-border/40 hover:bg-dark-border/20">
                  <td className="py-1.5 pr-3 font-mono font-bold text-white">{p.symbol}</td>
                  <td className="py-1.5 pr-3 text-gray-500">{p.date}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-gray-300">{currency}{p.entry_price?.toLocaleString(locale)}</td>
                  <td className={clsx("py-1.5 pr-3 text-right font-mono font-semibold tabular-nums", ret >= 0 ? "text-green-400" : "text-red-400")}>
                    {ret >= 0 ? "+" : ""}{ret.toFixed(1)}%
                  </td>
                  <td className={clsx("py-1.5 text-right font-mono tabular-nums", alpha >= 0 ? "text-green-400" : "text-red-400")}>
                    {alpha >= 0 ? "+" : ""}{alpha.toFixed(1)}%
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {picks.length > 15 && (
          <p className="text-[11px] text-gray-400 mt-1.5 text-center">Showing 15 of {picks.length} resolved picks</p>
        )}
      </div>
    </div>
  );
}

// ── Pick Card ─────────────────────────────────────────────────────────────────
function PickCard({ pick, rank, market, currency, locale }: { pick: Pick; rank: number; market: "IN" | "US"; currency: string; locale: string }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [showPaperTrade, setShowPaperTrade] = useState(false);
  const upside = pick.price && pick.target
    ? (((pick.target - pick.price) / pick.price) * 100).toFixed(1) : null;

  // Daily Picks are a frozen snapshot from generation time (once or twice
  // daily) — entry zone/target/stop are all computed against pick.price as
  // it stood then. A user viewing this later the same day, or the next day
  // before the next run, can see a stale entry zone the live price has
  // already moved past — misleading for a "1-5 day" short-term call
  // specifically. Fetch the live quote and flag it explicitly instead of
  // silently showing numbers that may no longer be actionable.
  const { data: liveQuote } = useQuery({
    queryKey: ["quote", pick.symbol, market],
    queryFn: () => fetchQuote(pick.symbol, market),
    staleTime: 5 * 60_000,
  });
  const livePrice = liveQuote?.price ?? null;
  const entryZonePassed =
    livePrice != null && pick.entry_low != null && pick.entry_high != null &&
    (livePrice < pick.entry_low || livePrice > pick.entry_high);
  const sector = pick.quality_factors?.sector;
  const piotroski = pick.quality_factors?.piotroski;
  const grouped: Record<string, ReasonItem[]> = {};
  for (const r of pick.reasoning ?? []) {
    const group = INDICATOR_GROUP[r.indicator] ?? "Other";
    if (!grouped[group]) grouped[group] = [];
    grouped[group].push(r);
  }
  const orderedGroups = GROUP_ORDER.filter(g => grouped[g]?.length);

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl overflow-hidden hover:border-green-500/40 transition-all hover:shadow-lg hover:shadow-green-500/5 group flex flex-col">
      {/* Rank + badges row */}
      <div className="flex items-center gap-2 px-4 pt-3 pb-0 flex-wrap">
        <span className="text-xs font-bold text-gray-400">#{rank}</span>
        {pick.score_band && (
          <span className={clsx("text-xs font-bold px-2 py-0.5 rounded border tracking-wide", SCORE_BAND_STYLE[pick.score_band] ?? "bg-gray-500/20 text-gray-400 border-gray-500/30")}>
            {pick.score_band}
          </span>
        )}
        {sector && <span className="text-xs px-2 py-0.5 rounded bg-dark-border text-gray-400">{sector}</span>}
        {pick.sentiment && pick.sentiment !== "NEUTRAL" && (
          <span className={clsx("text-xs px-1.5 py-0.5 rounded border ml-auto",
            pick.sentiment === "BULLISH" ? "bg-green-500/10 text-green-400 border-green-500/20" : "bg-red-500/10 text-red-400 border-red-500/20")}>
            📰 {pick.sentiment === "BULLISH" ? "Bullish News" : "Bearish News"}
          </span>
        )}
      </div>

      {/* Clickable body */}
      <div onClick={() => router.push(`/stock/${encodeURIComponent(pick.symbol)}?market=${market}`)} className="p-4 cursor-pointer flex-1">
        <div className="flex items-start justify-between mb-2">
          <div>
            <span className="font-mono font-bold text-white text-lg group-hover:text-green-400 transition-colors">{pick.symbol}</span>
            <p className="text-xs text-gray-500 mt-0.5 truncate max-w-[200px]">{pick.name}</p>
          </div>
          <div className="text-right">
            <div className="text-sm font-semibold text-white">
              {currency}{(livePrice ?? pick.price)?.toLocaleString(locale)}
            </div>
            {livePrice != null && pick.price != null && Math.abs(livePrice - pick.price) > 0.01 && (
              <div className="text-[10px] text-gray-500">was {currency}{pick.price.toLocaleString(locale)} at generation</div>
            )}
            {upside && <div className="text-xs text-green-400 font-medium">+{upside}% upside</div>}
          </div>
        </div>

        {entryZonePassed && (
          <div className="mb-3 flex items-center gap-1.5 text-[11px] text-yellow-400 bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-2.5 py-1.5">
            <AlertCircle size={11} className="shrink-0" />
            Price has moved {livePrice! > pick.entry_high! ? "above" : "below"} the entry zone since this was generated — may no longer be actionable as shown.
          </div>
        )}

        <div className="mb-3">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>AI Confidence</span>
            <span className="text-white font-medium">{pick.confidence}%</span>
          </div>
          <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
            <div className="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full" style={{ width: `${pick.confidence}%` }} />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 mb-3">
          <div className={clsx("rounded-lg p-2 text-center", entryZonePassed ? "bg-yellow-500/10 border border-yellow-500/30" : "bg-dark-border/40")}>
            <p className="text-[10px] text-gray-500 mb-0.5">Entry Zone{entryZonePassed && " (passed)"}</p>
            <p className={clsx("text-xs font-mono", entryZonePassed ? "text-yellow-400 line-through" : "text-white")}>
              {pick.entry_low && pick.entry_high
                ? `${currency}${pick.entry_low.toLocaleString(locale)}–${pick.entry_high.toLocaleString(locale)}`
                : `${currency}${pick.price?.toLocaleString(locale)}`}
            </p>
          </div>
          <div className="bg-green-500/10 rounded-lg p-2 text-center border border-green-500/20">
            <p className="text-[10px] text-gray-500 mb-0.5 flex items-center justify-center gap-1"><Target size={9} />Target</p>
            <p className="text-xs text-green-400 font-mono font-semibold">{currency}{pick.target?.toLocaleString(locale)}</p>
          </div>
          <div className="bg-red-500/10 rounded-lg p-2 text-center border border-red-500/20">
            <p className="text-[10px] text-gray-500 mb-0.5 flex items-center justify-center gap-1"><ShieldAlert size={9} />Stop Loss</p>
            <p className="text-xs text-red-400 font-mono font-semibold">
              {pick.stop_loss ? `${currency}${pick.stop_loss.toLocaleString(locale)}` : "—"}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs text-gray-500 mb-3">
          {pick.risk_reward && <span>R:R <span className="text-white font-semibold">1:{pick.risk_reward.toFixed(1)}</span></span>}
          {piotroski != null && (
            <span className={clsx("px-1.5 py-0.5 rounded font-semibold",
              piotroski >= 7 ? "bg-green-500/20 text-green-400" : piotroski <= 3 ? "bg-red-500/20 text-red-400" : "bg-yellow-500/20 text-yellow-400")}>
              Piotroski {piotroski}/9
            </span>
          )}
          {pick.portfolio_weight != null && (
            <span
              className="ml-auto"
              title="Suggested weight if buying all of today's picks together as one basket — based on this stock's predicted return relative to the other picks and how correlated they are, not how strong its own BUY signal is on its own."
            >
              Allocation{" "}
              <span className={clsx("font-semibold",
                pick.portfolio_weight === 0 ? "text-gray-500" :
                pick.portfolio_weight >= 0.30 ? "text-green-400" : "text-yellow-400"
              )}>
                {Math.round(pick.portfolio_weight * 100)}%
              </span>
            </span>
          )}
        </div>

        {pick.portfolio_weight === 0 && (
          <div className="mb-3 text-[11px] text-gray-500 bg-dark-border/30 rounded-lg px-2.5 py-1.5">
            0% allocation just means today&apos;s basket optimizer favored the other picks over this one
            — the BUY signal above is unaffected and independent of this number.
          </div>
        )}

        <TopReasons reasoning={pick.reasoning ?? []} />

        {pick.summary && (
          <div className="bg-dark-border/30 rounded-lg p-3 border border-dark-border">
            <p className="text-xs text-gray-400 leading-relaxed">{pick.summary}</p>
          </div>
        )}
      </div>

      {/* Action bar */}
      <div className="flex border-t border-dark-border">
        <button
          onClick={(e) => { e.stopPropagation(); setShowPaperTrade(true); }}
          className="flex items-center gap-1.5 px-4 py-2.5 text-xs text-brand-400 hover:text-white hover:bg-brand-500/10 transition-colors border-r border-dark-border font-medium"
        >
          <FlaskConical size={11} /> Paper Trade
        </button>
        <button onClick={() => setExpanded(e => !e)}
          className="flex-1 flex items-center justify-between px-4 py-2.5 text-xs text-gray-500 hover:text-white hover:bg-dark-border/20 transition-colors">
          <span className="font-medium flex items-center gap-1.5"><Zap size={11} /> Full factor analysis</span>
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>

      {showPaperTrade && pick.price && (
        <PaperTradeModal
          symbol={pick.symbol}
          market={market}
          currentPrice={pick.price}
          signal="BUY"
          horizon={pick.horizon}
          currency={currency}
          suggestedStopLoss={pick.stop_loss}
          suggestedTargetPrice={pick.target}
          onClose={() => setShowPaperTrade(false)}
        />
      )}

      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-dark-border bg-black/20">
          {pick.factor_zscores && (
            <div className="pt-3 space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Universe Rank vs All {market === "IN" ? "NSE" : "US"} Stocks</p>
                {pick.combined_alpha != null && (
                  <span className={clsx("text-xs font-semibold px-2 py-0.5 rounded",
                    pick.combined_alpha > 0.5 ? "bg-green-500/20 text-green-400" : pick.combined_alpha < -0.3 ? "bg-red-500/20 text-red-400" : "bg-yellow-500/20 text-yellow-400")}>
                    α {pick.combined_alpha > 0 ? "+" : ""}{pick.combined_alpha.toFixed(2)}
                  </span>
                )}
              </div>
              {([
                ["tech", "Technical Momentum", "text-blue-400"],
                ["fund", "Fundamentals", "text-purple-400"],
                ["sentiment", "News Sentiment", "text-yellow-400"],
                ["quality", "Quality / ROIC", "text-green-400"],
              ] as [keyof FactorZScores, string, string][]).map(([key, label]) => {
                const z = pick.factor_zscores?.[key];
                if (z == null) return null;
                const pct = Math.round(Math.min(100, Math.max(0, (z + 3) / 6 * 100)));
                const zColor = z > 0.5 ? "text-green-400" : z < -0.5 ? "text-red-400" : "text-yellow-400";
                return (
                  <div key={key} className="space-y-1">
                    <div className="flex justify-between text-xs">
                      <span className="text-gray-400">{label}</span>
                      <span className={`font-mono font-semibold ${zColor}`}>{z > 0 ? "+" : ""}{z.toFixed(2)}σ</span>
                    </div>
                    <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
                      <div className={clsx("h-full rounded-full", z > 0.5 ? "bg-green-500" : z < -0.5 ? "bg-red-500" : "bg-yellow-500")}
                        style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className={pick.factor_zscores ? "space-y-2" : "pt-3 space-y-2"}>
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Absolute Signal Scores</p>
            {pick.tech_score != null && <ScoreBar label="Technical" value={pick.tech_score} color="text-blue-400" />}
            {pick.fund_score != null && <ScoreBar label="Fundamental" value={pick.fund_score} color="text-purple-400" />}
            <ScoreBar label="AI Confidence" value={pick.confidence} color="text-green-400" />
          </div>

          {pick.quality_factors?.breakdown && Object.keys(pick.quality_factors.breakdown).length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Quality Factors</p>
              {([
                ["earnings_revision", "Earnings Revisions"], ["institutional", "Institutional Ownership"],
                ["inst_flow", "Institutional Flows"], ["relative_strength", "Relative Strength"],
                ["sector_strength", "Sector Strength"], ["valuation", "Valuation"],
                ["risk_management", "Risk Management"], ["liquidity", "Liquidity"],
                ["corporate_actions", "Corporate Actions"], ["quality_metrics", "Quality / ROIC"],
              ] as [string, string][]).map(([key, label]) => {
                const val = pick.quality_factors?.breakdown?.[key];
                if (val == null) return null;
                const color = val >= 65 ? "text-green-400" : val <= 40 ? "text-red-400" : "text-yellow-400";
                return <ScoreBar key={key} label={label} value={val} color={color} />;
              })}
            </div>
          )}

          {orderedGroups.length > 0 && (
            <div className="space-y-3">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">All Signals</p>
              {orderedGroups.map(group => (
                <div key={group}>
                  <p className="text-xs font-semibold text-gray-400 mb-1.5">{group}</p>
                  <div className="space-y-1.5">
                    {grouped[group].map((r, i) => (
                      <div key={i} className="flex items-start gap-2">
                        <span className={clsx("text-xs font-bold mt-0.5 flex-shrink-0", SIGNAL_COLOR[r.signal] ?? "text-gray-400")}>
                          {SIGNAL_ICON[r.signal] ?? "·"}
                        </span>
                        <div className="min-w-0">
                          <span className="text-[11px] text-gray-400 mr-1">{r.indicator}</span>
                          <span className="text-xs text-gray-300 leading-relaxed">{r.reason}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function DailyPicksPage() {
  const [market, setMarket] = useMarketPreference(["IN", "US"] as const, "IN");
  const [horizon, setHorizon] = useState<"short" | "medium" | "long">("short");
  const [showTruth, setShowTruth] = useState(false);

  const marketCfg = MARKETS.find(m => m.key === market)!;

  const { data, isLoading, error: queryError } = useQuery<DailyPicksResponse>({
    queryKey: ["daily-picks", market],
    queryFn: () => api.get(`/api/picks/daily?market=${market}`).then(r => r.data),
    // Poll every 60s when generating, every 5 min when idle
    refetchInterval: (query) => (query.state.data as any)?.generating ? 60_000 : 5 * 60_000,
    staleTime: 55_000, refetchOnWindowFocus: false, retry: 3, retryDelay: 8000,
    // Keep showing the previous market's data while the new one loads —
    // without this, switching IN/US briefly nulls `data`, collapsing the
    // "Updated X ago" badge and the regime/results sections, which made
    // the header's right-aligned button cluster visibly jump position.
    placeholderData: keepPreviousData,
  });

  const currency = data?.currency ?? marketCfg.currency;
  const picks = data?.picks?.[horizon] ?? [];
  const generatedAt = data?.generated_at
    ? new Date(data.generated_at).toLocaleString(marketCfg.locale, {
        timeZone: marketCfg.tz, day: "2-digit", month: "short",
        year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true,
      }) : null;
  const alphaForHorizon = data?.alpha_engine?.[horizon];

  return (
    <div className="space-y-6">

      {/* Header — alignment matches Market Heatmap / Market Overview / Screener style */}
      <div className="space-y-1">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <TrendingUp size={24} className="text-green-400 shrink-0" />
          <h1 className="text-2xl font-bold">Daily Stock Picks</h1>
          <span className="text-xs bg-green-500/15 text-green-400 border border-green-500/30 px-2 py-0.5 rounded-full font-semibold shrink-0">
            {marketCfg.label}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0 ml-auto flex-wrap justify-end">
          {/* Market toggle */}
          <div className="flex items-center gap-0.5 overflow-x-auto scrollbar-hide max-w-full bg-dark-card border border-dark-border rounded-lg p-0.5">
            {MARKETS.map(m => (
              <button key={m.key} onClick={() => setMarket(m.key)}
                className={clsx("shrink-0 whitespace-nowrap text-xs px-3 py-1.5 rounded-md font-medium transition-colors",
                  market === m.key ? "bg-brand-500 text-white" : "text-gray-400 hover:text-white")}>
                {m.short}
              </button>
            ))}
          </div>
          {/* Toggle truth panel */}
          <button onClick={() => setShowTruth(v => !v)}
            className={clsx("flex items-center gap-1.5 text-xs px-3 py-2 rounded-lg border transition-colors",
              showTruth ? "bg-blue-500/20 border-blue-500/40 text-blue-300" : "bg-dark-card border-dark-border text-gray-400 hover:text-white")}>
            <CheckCircle size={12} /> {showTruth ? "Hide" : "Show"} Real Accuracy
          </button>
          {generatedAt && (() => {
            const ageHours = data?.generated_at ? Math.floor((Date.now() - new Date(data.generated_at).getTime()) / 3_600_000) : 0;
            const isStale = ageHours >= 4;
            return (
              <div className={clsx("flex items-center gap-1.5 text-xs bg-dark-card border rounded-lg px-3 py-2 flex-shrink-0",
                isStale ? "border-yellow-500/40 text-yellow-400" : "border-dark-border text-gray-500")}>
                <Clock size={12} />
                <span>Updated {generatedAt}{isStale ? ` · ${ageHours}h ago` : ""}</span>
              </div>
            );
          })()}
        </div>
      </div>
      <p className="text-sm text-gray-400">
        Top 6 AI-selected BUY calls per horizon · generated daily at {marketCfg.genTime}
        {data?.screened_from ? ` · screened from ${data.screened_from.toLocaleString()} ${market === "IN" ? "NSE" : "US"} stocks` : ""}
      </p>
      </div>

      {/* Market regime + alpha engine */}
      {(data?.regime || alphaForHorizon) && (
        <div className="bg-dark-card border border-dark-border rounded-xl px-4 py-3 flex flex-wrap items-center gap-3 text-xs">
          {data?.regime && (() => {
            const regimeColors: Record<string, string> = {
              BULL_CALM: "bg-green-500/20 text-green-400 border-green-500/30",
              BULL_VOLATILE: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
              BEAR_CALM: "bg-orange-500/20 text-orange-400 border-orange-500/30",
              BEAR_PANIC: "bg-red-500/20 text-red-400 border-red-500/30",
            };
            const cls = regimeColors[data.regime!.label] || "bg-gray-500/20 text-gray-400 border-gray-500/30";
            return (
              <>
                <span className="text-gray-500 font-medium">Market Regime</span>
                <span className={clsx("px-2 py-0.5 rounded-full border font-semibold", cls)}>
                  {data.regime!.label.replace("_", " ")}
                </span>
                <span className="text-gray-600 hidden sm:inline">{data.regime!.description}</span>
              </>
            );
          })()}
          {alphaForHorizon && (
            <>
              <span className="h-4 w-px bg-dark-border hidden sm:block" />
              <span className="text-gray-500 font-medium">AI Engine</span>
              <span className={clsx("px-2 py-0.5 rounded font-semibold",
                alphaForHorizon.meta_model ? "text-green-400 bg-green-500/10" : "text-gray-400 bg-dark-border")}>
                {alphaForHorizon.meta_model ? "✓ Meta-model active" : "Learning…"}
              </span>
              {alphaForHorizon.n_buy != null && alphaForHorizon.n_scored != null && (
                <span className="text-gray-600">{alphaForHorizon.n_buy} BUY signals from {alphaForHorizon.n_scored} stocks</span>
              )}
            </>
          )}
        </div>
      )}

      {/* Global Macro */}
      {(() => {
        const allPicks = [...(data?.picks?.short ?? []), ...(data?.picks?.medium ?? []), ...(data?.picks?.long ?? [])];
        const ctx = (allPicks[0] as any)?.global_context as GlobalContext | undefined;
        if (!ctx?.levels && !ctx?.changes) return null;
        const l = ctx.levels ?? {}; const c = ctx.changes ?? {};
        const macroItems = [
          { label: "S&P 500", value: c.sp500 != null ? `${c.sp500 > 0 ? "+" : ""}${c.sp500.toFixed(1)}%` : null, pos: (c.sp500 ?? 0) >= 0 },
          { label: "NASDAQ",  value: c.nasdaq != null ? `${c.nasdaq > 0 ? "+" : ""}${c.nasdaq.toFixed(1)}%` : null, pos: (c.nasdaq ?? 0) >= 0 },
          { label: "Brent",   value: c.crude_brent != null ? `${c.crude_brent > 0 ? "+" : ""}${c.crude_brent.toFixed(1)}%` : null, pos: (c.crude_brent ?? 0) <= 0 },
          { label: "Gold",    value: c.gold != null ? `${c.gold > 0 ? "+" : ""}${c.gold.toFixed(1)}%` : null, pos: true },
          { label: "USD/INR", value: l.usdinr != null ? `₹${l.usdinr.toFixed(1)}` : null, pos: true },
          { label: "VIX",     value: l.vix != null ? l.vix.toFixed(1) : null, pos: (l.vix ?? 99) < 20 },
          { label: "US 10Y",  value: l.us10y != null ? `${l.us10y.toFixed(2)}%` : null, pos: (l.us10y ?? 99) < 4.5 },
        ].filter(i => i.value !== null);
        if (!macroItems.length) return null;
        return (
          <div className="bg-dark-card border border-dark-border rounded-xl p-4">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">🌍 Global Macro at Pick Generation</p>
            <div className="flex flex-wrap gap-2">
              {macroItems.map(({ label, value, pos }) => (
                <div key={label} className="flex items-center gap-1.5 bg-dark-border/50 rounded-lg px-3 py-1.5">
                  <span className="text-xs text-gray-500">{label}</span>
                  <span className={clsx("text-xs font-bold font-mono", pos ? "text-green-400" : "text-red-400")}>{value}</span>
                </div>
              ))}
              {ctx.score != null && (
                <div className="flex items-center gap-1.5 bg-dark-border/50 rounded-lg px-3 py-1.5">
                  <span className="text-xs text-gray-500">Macro</span>
                  <span className={clsx("text-xs font-bold", ctx.score >= 55 ? "text-green-400" : ctx.score <= 45 ? "text-red-400" : "text-yellow-400")}>
                    {ctx.score >= 55 ? "Supportive" : ctx.score <= 45 ? "Headwind" : "Neutral"}
                  </span>
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {/* Horizon tabs */}
      <div className="flex gap-2">
        {HORIZONS.map(({ key, label, sub }) => (
          <button key={key} onClick={() => setHorizon(key)}
            className={clsx("px-4 py-2.5 rounded-xl text-sm font-medium transition-all",
              horizon === key ? "bg-brand-500 text-white shadow-lg shadow-brand-500/20"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white")}>
            {label}
            <span className={clsx("ml-1.5 text-xs", horizon === key ? "text-blue-200" : "text-gray-600")}>({sub})</span>
          </button>
        ))}
      </div>

      {/* Priority 1 + 2: Backtest truth panel (toggle) */}
      {showTruth && <BacktestPanel horizon={horizon} benchmarkLabel={market === "IN" ? "Nifty" : "S&P 500"} />}

      {/* Priority 3: Live performance tracker (toggle) */}
      {showTruth && <LivePerformanceTracker horizon={horizon} currency={currency} locale={marketCfg.locale} benchmarkLabel={market === "IN" ? "Nifty" : "S&P 500"} />}

      {/* Loading */}
      {isLoading && (
        <div className="bg-blue-500/10 border border-blue-500/30 rounded-xl p-4 flex items-start gap-3">
          <Loader2 size={18} className="text-blue-400 mt-0.5 animate-spin shrink-0" />
          <div>
            <p className="text-sm font-semibold text-blue-300">Waking up the AI engine…</p>
            <p className="text-xs text-blue-400/70 mt-0.5">Our free-tier server starts cold — this can take 30–60 seconds.</p>
          </div>
        </div>
      )}
      {queryError && !isLoading && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-red-300">Couldn't reach the prediction server</p>
            <p className="text-xs text-red-400/70 mt-0.5">The server may still be warming up. Please refresh in 30 seconds.</p>
          </div>
        </div>
      )}

      {/* Picks grid */}
      {isLoading ? (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(5)].map((_, i) => <div key={i} className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-72" />)}
        </div>
      ) : picks.length > 0 ? (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {picks.map((pick, i) => <PickCard key={pick.symbol} pick={pick} rank={i + 1} market={market} currency={currency} locale={marketCfg.locale} />)}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          {/* Only show the big blocking spinner when no data has ever been
              generated yet — if generated_at already exists, a concurrent
              background refresh shouldn't hide already-valid results
              (including a legitimate "0 BUY signals" outcome). */}
          {(data as any)?.generating && !data?.generated_at ? (
            <>
              <Loader2 size={40} className="text-brand-400 mb-4 animate-spin" />
              <h3 className="text-lg font-semibold text-gray-300 mb-2">Generating picks…</h3>
              <p className="text-sm text-gray-500 max-w-sm">
                The AI is bulk-scanning all {market === "IN" ? "NSE-listed" : "US-listed"} stocks, then running deep analysis on top momentum candidates.
                This takes about 15 minutes. Page auto-refreshes every minute.
              </p>
            </>
          ) : (
            <>
              <AlertCircle size={40} className="text-gray-600 mb-4" />
              <h3 className="text-lg font-semibold text-gray-300 mb-2">
                {data?.generated_at ? "No BUY signals found today" : "Picks not yet generated"}
              </h3>
              <p className="text-sm text-gray-500 max-w-sm">
                {data?.generated_at
                  ? `The AI didn't find strong BUY signals across ${market === "IN" ? "NSE" : "US markets"} today. Market conditions may be weak — check back tomorrow.`
                  : `Daily picks are generated at ${marketCfg.genTime} on market days. Check back then.`}
              </p>
              {(data as any)?.generating && data?.generated_at && (
                <p className="text-xs text-gray-600 mt-3 flex items-center gap-1.5">
                  <Loader2 size={12} className="animate-spin" /> Refreshing in the background…
                </p>
              )}
            </>
          )}
        </div>
      )}

      <div className="bg-dark-card border border-dark-border rounded-xl p-4 text-center space-y-1">
        <p className="text-xs font-semibold text-gray-400">Disclaimer</p>
        <p className="text-xs text-gray-500">
          StockSense360 picks are AI-generated signals for <strong className="text-gray-400">educational and research purposes only</strong>.
          They do not constitute financial advice. Past accuracy is not a guarantee of future results.
          Always consult a {market === "IN" ? "SEBI-registered" : "licensed"} investment advisor before trading.
        </p>
      </div>
    </div>
  );
}
