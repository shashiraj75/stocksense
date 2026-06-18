"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import { TrendingUp, Clock, AlertCircle, ChevronDown, ChevronUp, Loader2, Target, ShieldAlert, Zap } from "lucide-react";
import clsx from "clsx";

type ReasonItem = { indicator: string; signal: string; reason: string };

type QualityFactors = {
  score?: number;
  sector?: string;
  piotroski?: number | null;
  breakdown?: Record<string, number>;
};

type FactorZScores = {
  tech?: number;
  fund?: number;
  sentiment?: number;
  quality?: number;
};

type Pick = {
  symbol: string;
  name: string;
  price: number;
  target: number;
  stop_loss?: number;
  entry_low?: number;
  entry_high?: number;
  risk_reward?: number;
  confidence: number;
  tech_score?: number;
  fund_score?: number;
  sentiment?: string;
  reasoning: ReasonItem[];
  summary?: string;
  quality_factors?: QualityFactors;
  factor_zscores?: FactorZScores;
  combined_alpha?: number;
  meta_alpha?: number | null;
  ranking_alpha?: number;
  portfolio_weight?: number;
  regime_label?: string;
  score_band?: string;
  horizon: string;
};

type AlphaEngineMeta = {
  ic_weights?: Record<string, number>;
  regime?: string;
  n_scored?: number;
  n_buy?: number;
  meta_model?: boolean;
};

type GlobalContext = {
  score?: number;
  levels?: Record<string, number>;
  changes?: Record<string, number>;
};

type DailyPicksResponse = {
  generated_at: string | null;
  picks: { short: Pick[]; medium: Pick[]; long: Pick[] };
  alpha_engine?: Record<string, AlphaEngineMeta>;
  regime?: { label: string; description: string };
  message?: string;
};

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
  BUY: "▲", BULLISH: "▲",
  SELL: "▼", BEARISH: "▼",
  HOLD: "→", NEUTRAL: "→", INFO: "·",
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

// Top 3 most impactful reason items — shown inline on the card
function TopReasons({ reasoning }: { reasoning: ReasonItem[] }) {
  // Prefer BULLISH/BUY signals first, then INFO, skip NEUTRAL fillers
  const priority = reasoning.filter(r => ["BUY", "BULLISH", "BEARISH", "SELL"].includes(r.signal));
  const rest = reasoning.filter(r => !["BUY", "BULLISH", "BEARISH", "SELL"].includes(r.signal));
  const top = [...priority, ...rest].slice(0, 3);
  if (!top.length) return null;
  return (
    <div className="space-y-1.5 mb-3">
      {top.map((r, i) => (
        <div key={i} className="flex items-start gap-2">
          <span className={clsx("text-xs font-bold mt-0.5 flex-shrink-0 tabular-nums", SIGNAL_COLOR[r.signal] ?? "text-gray-400")}>
            {SIGNAL_ICON[r.signal] ?? "·"}
          </span>
          <p className="text-xs text-gray-300 leading-relaxed">{r.reason}</p>
        </div>
      ))}
    </div>
  );
}

function PickCard({ pick, rank }: { pick: Pick; rank: number }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);

  const upside = pick.price && pick.target
    ? (((pick.target - pick.price) / pick.price) * 100).toFixed(1)
    : null;

  const sector = pick.quality_factors?.sector;
  const piotroski = pick.quality_factors?.piotroski;

  // Group reasoning by category for expanded section
  const grouped: Record<string, ReasonItem[]> = {};
  for (const r of pick.reasoning ?? []) {
    const group = INDICATOR_GROUP[r.indicator] ?? "Other";
    if (!grouped[group]) grouped[group] = [];
    grouped[group].push(r);
  }
  const orderedGroups = GROUP_ORDER.filter(g => grouped[g]?.length);

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl overflow-hidden hover:border-green-500/40 transition-all hover:shadow-lg hover:shadow-green-500/5 group flex flex-col">

      {/* Rank ribbon */}
      <div className="flex items-center gap-3 px-4 pt-3 pb-0">
        <span className="text-xs font-bold text-gray-600">#{rank}</span>
        {/* Score band */}
        {pick.score_band && (
          <span className={clsx("text-xs font-bold px-2 py-0.5 rounded border tracking-wide", SCORE_BAND_STYLE[pick.score_band] ?? "bg-gray-500/20 text-gray-400 border-gray-500/30")}>
            {pick.score_band}
          </span>
        )}
        {/* Sector tag */}
        {sector && (
          <span className="text-xs px-2 py-0.5 rounded bg-dark-border text-gray-400">{sector}</span>
        )}
        {/* Sentiment badge */}
        {pick.sentiment && pick.sentiment !== "NEUTRAL" && (
          <span className={clsx("text-xs px-1.5 py-0.5 rounded border ml-auto",
            pick.sentiment === "BULLISH" ? "bg-green-500/10 text-green-400 border-green-500/20" : "bg-red-500/10 text-red-400 border-red-500/20")}>
            📰 {pick.sentiment === "BULLISH" ? "Bullish News" : "Bearish News"}
          </span>
        )}
      </div>

      {/* Main card body — clickable */}
      <div
        onClick={() => router.push(`/stock/${pick.symbol}?market=IN`)}
        className="p-4 cursor-pointer flex-1"
      >
        {/* Symbol + price row */}
        <div className="flex items-start justify-between mb-2">
          <div>
            <span className="font-mono font-bold text-white text-lg group-hover:text-green-400 transition-colors">
              {pick.symbol}
            </span>
            <p className="text-xs text-gray-500 mt-0.5 truncate max-w-[200px]">{pick.name}</p>
          </div>
          <div className="text-right">
            <div className="text-sm font-semibold text-white">₹{pick.price?.toLocaleString("en-IN")}</div>
            {upside && <div className="text-xs text-green-400 font-medium">+{upside}% upside</div>}
          </div>
        </div>

        {/* AI Confidence bar */}
        <div className="mb-3">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>AI Confidence</span>
            <span className="text-white font-medium">{pick.confidence}%</span>
          </div>
          <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
            <div className="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full"
              style={{ width: `${pick.confidence}%` }} />
          </div>
        </div>

        {/* Price targets row */}
        <div className="grid grid-cols-3 gap-2 mb-3">
          <div className="bg-dark-border/40 rounded-lg p-2 text-center">
            <p className="text-[10px] text-gray-500 mb-0.5">Entry Zone</p>
            <p className="text-xs text-white font-mono">
              {pick.entry_low && pick.entry_high
                ? `₹${pick.entry_low.toLocaleString("en-IN")}–${pick.entry_high.toLocaleString("en-IN")}`
                : `₹${pick.price?.toLocaleString("en-IN")}`}
            </p>
          </div>
          <div className="bg-green-500/10 rounded-lg p-2 text-center border border-green-500/20">
            <p className="text-[10px] text-gray-500 mb-0.5 flex items-center justify-center gap-1"><Target size={9} />Target</p>
            <p className="text-xs text-green-400 font-mono font-semibold">₹{pick.target?.toLocaleString("en-IN")}</p>
          </div>
          <div className="bg-red-500/10 rounded-lg p-2 text-center border border-red-500/20">
            <p className="text-[10px] text-gray-500 mb-0.5 flex items-center justify-center gap-1"><ShieldAlert size={9} />Stop Loss</p>
            <p className="text-xs text-red-400 font-mono font-semibold">
              {pick.stop_loss ? `₹${pick.stop_loss.toLocaleString("en-IN")}` : "—"}
            </p>
          </div>
        </div>

        {/* Risk:reward + Piotroski inline */}
        <div className="flex items-center gap-3 text-xs text-gray-500 mb-3">
          {pick.risk_reward && (
            <span>R:R <span className="text-white font-semibold">1:{pick.risk_reward.toFixed(1)}</span></span>
          )}
          {piotroski != null && (
            <span className={clsx("px-1.5 py-0.5 rounded font-semibold",
              piotroski >= 7 ? "bg-green-500/20 text-green-400" : piotroski <= 3 ? "bg-red-500/20 text-red-400" : "bg-yellow-500/20 text-yellow-400")}>
              Piotroski {piotroski}/9
            </span>
          )}
          {pick.portfolio_weight != null && (
            <span className="ml-auto">
              Allocation <span className={clsx("font-semibold", pick.portfolio_weight >= 0.30 ? "text-green-400" : "text-yellow-400")}>
                {Math.round(pick.portfolio_weight * 100)}%
              </span>
            </span>
          )}
        </div>

        {/* Top 3 key reasons — visible without expanding */}
        <TopReasons reasoning={pick.reasoning ?? []} />

        {/* AI Summary */}
        {pick.summary && (
          <div className="bg-dark-border/30 rounded-lg p-3 border border-dark-border">
            <p className="text-xs text-gray-400 leading-relaxed">{pick.summary}</p>
          </div>
        )}
      </div>

      {/* Expand toggle */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between px-4 py-2.5 border-t border-dark-border text-xs text-gray-500 hover:text-white hover:bg-dark-border/20 transition-colors"
      >
        <span className="font-medium flex items-center gap-1.5"><Zap size={11} /> Full factor analysis</span>
        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-dark-border bg-black/20">

          {/* Universe rank z-scores */}
          {pick.factor_zscores && (
            <div className="pt-3 space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Universe Rank vs Nifty 100</p>
                {pick.combined_alpha != null && (
                  <span className={clsx("text-xs font-semibold px-2 py-0.5 rounded",
                    pick.combined_alpha > 0.5 ? "bg-green-500/20 text-green-400" : pick.combined_alpha < -0.3 ? "bg-red-500/20 text-red-400" : "bg-yellow-500/20 text-yellow-400")}>
                    α {pick.combined_alpha > 0 ? "+" : ""}{pick.combined_alpha.toFixed(2)}
                  </span>
                )}
              </div>
              <p className="text-xs text-gray-500">Standard deviations (σ) above/below the Nifty 100 average</p>
              {([
                ["tech",      "Technical Momentum", "text-blue-400"],
                ["fund",      "Fundamentals",       "text-purple-400"],
                ["sentiment", "News Sentiment",     "text-yellow-400"],
                ["quality",   "Quality / ROIC",     "text-green-400"],
              ] as [keyof FactorZScores, string, string][]).map(([key, label, color]) => {
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
                      <div className={clsx("h-full rounded-full transition-all", z > 0.5 ? "bg-green-500" : z < -0.5 ? "bg-red-500" : "bg-yellow-500")}
                        style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Core signal scores */}
          <div className={pick.factor_zscores ? "space-y-2" : "pt-3 space-y-2"}>
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Absolute Signal Scores</p>
            {pick.tech_score != null && <ScoreBar label="Technical" value={pick.tech_score} color="text-blue-400" />}
            {pick.fund_score != null && <ScoreBar label="Fundamental" value={pick.fund_score} color="text-purple-400" />}
            <ScoreBar label="AI Confidence" value={pick.confidence} color="text-green-400" />
          </div>

          {/* Quality factors breakdown */}
          {pick.quality_factors?.breakdown && Object.keys(pick.quality_factors.breakdown).length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Quality Factors</p>
              {([
                ["earnings_revision", "Earnings Revisions"],
                ["institutional",     "Institutional Ownership"],
                ["inst_flow",         "Institutional Flows"],
                ["relative_strength", "Relative Strength"],
                ["sector_strength",   "Sector Strength"],
                ["valuation",         "Valuation"],
                ["risk_management",   "Risk Management"],
                ["liquidity",         "Liquidity"],
                ["corporate_actions", "Corporate Actions"],
                ["quality_metrics",   "Quality / ROIC"],
              ] as [string, string][]).map(([key, label]) => {
                const val = pick.quality_factors?.breakdown?.[key];
                if (val == null) return null;
                const color = val >= 65 ? "text-green-400" : val <= 40 ? "text-red-400" : "text-yellow-400";
                return <ScoreBar key={key} label={label} value={val} color={color} />;
              })}
            </div>
          )}

          {/* Full grouped reasoning — all signals */}
          {orderedGroups.length > 0 && (
            <div className="space-y-3">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">All Signals</p>
              {orderedGroups.map(group => (
                <div key={group}>
                  <p className="text-xs font-semibold text-gray-600 mb-1.5">{group}</p>
                  <div className="space-y-1.5">
                    {grouped[group].map((r, i) => (
                      <div key={i} className="flex items-start gap-2">
                        <span className={clsx("text-xs font-bold mt-0.5 flex-shrink-0", SIGNAL_COLOR[r.signal] ?? "text-gray-400")}>
                          {SIGNAL_ICON[r.signal] ?? "·"}
                        </span>
                        <div className="min-w-0">
                          <span className="text-[10px] text-gray-600 mr-1">{r.indicator}</span>
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

export default function DailyPicksPage() {
  const [horizon, setHorizon] = useState<"short" | "medium" | "long">("short");

  const { data, isLoading, error: queryError } = useQuery<DailyPicksResponse>({
    queryKey: ["daily-picks"],
    queryFn: () => api.get("/api/picks/daily").then(r => r.data),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    retry: 3,
    retryDelay: 8000,
  });

  const picks = data?.picks?.[horizon] ?? [];
  const generatedAt = data?.generated_at
    ? new Date(data.generated_at).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit", hour12: true,
      })
    : null;

  const alphaForHorizon = data?.alpha_engine?.[horizon];

  return (
    <div className="space-y-6 max-w-4xl mx-auto">

      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <TrendingUp size={24} className="text-green-400" />
            <h1 className="text-2xl font-bold">Daily Stock Picks</h1>
            <span className="text-xs bg-green-500/15 text-green-400 border border-green-500/30 px-2 py-0.5 rounded-full font-semibold">
              🇮🇳 NSE India
            </span>
          </div>
          <p className="text-sm text-gray-400">
            Top 5 AI-selected BUY calls from Nifty 100 — refreshed every market day at 9 AM IST
          </p>
        </div>
        {generatedAt && (() => {
          const genMs = data?.generated_at ? new Date(data.generated_at).getTime() : 0;
          const ageHours = genMs ? Math.floor((Date.now() - genMs) / 3_600_000) : 0;
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

      {/* Market regime + alpha engine — compact single row */}
      {(data?.regime || alphaForHorizon) && (
        <div className="bg-dark-card border border-dark-border rounded-xl px-4 py-3 flex flex-wrap items-center gap-3 text-xs">
          {data?.regime && (() => {
            const regimeColors: Record<string, string> = {
              BULL_CALM:     "bg-green-500/20 text-green-400 border-green-500/30",
              BULL_VOLATILE: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
              BEAR_CALM:     "bg-orange-500/20 text-orange-400 border-orange-500/30",
              BEAR_PANIC:    "bg-red-500/20 text-red-400 border-red-500/30",
            };
            const cls = regimeColors[data.regime.label] || "bg-gray-500/20 text-gray-400 border-gray-500/30";
            return (
              <>
                <span className="text-gray-500 font-medium">Market Regime</span>
                <span className={clsx("px-2 py-0.5 rounded-full border font-semibold", cls)}>
                  {data.regime.label.replace("_", " ")}
                </span>
                <span className="text-gray-600 hidden sm:inline">{data.regime.description}</span>
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
                <span className="text-gray-600">{alphaForHorizon.n_buy} BUY signals from {alphaForHorizon.n_scored} stocks scored</span>
              )}
            </>
          )}
        </div>
      )}

      {/* Global Macro Snapshot */}
      {(() => {
        const allPicks = [...(data?.picks?.short ?? []), ...(data?.picks?.medium ?? []), ...(data?.picks?.long ?? [])];
        const ctx = (allPicks[0] as any)?.global_context as GlobalContext | undefined;
        if (!ctx?.levels && !ctx?.changes) return null;
        const l = ctx.levels ?? {};
        const c = ctx.changes ?? {};
        const macroItems = [
          { label: "S&P 500",    value: c.sp500 != null      ? `${c.sp500 > 0 ? "+" : ""}${c.sp500.toFixed(1)}%`       : null, pos: (c.sp500 ?? 0) >= 0 },
          { label: "NASDAQ",     value: c.nasdaq != null     ? `${c.nasdaq > 0 ? "+" : ""}${c.nasdaq.toFixed(1)}%`     : null, pos: (c.nasdaq ?? 0) >= 0 },
          { label: "Brent",      value: c.crude_brent != null ? `${c.crude_brent > 0 ? "+" : ""}${c.crude_brent.toFixed(1)}%` : null, pos: (c.crude_brent ?? 0) <= 0 },
          { label: "Gold",       value: c.gold != null       ? `${c.gold > 0 ? "+" : ""}${c.gold.toFixed(1)}%`         : null, pos: true },
          { label: "USD/INR",    value: l.usdinr != null     ? `₹${l.usdinr.toFixed(1)}`                               : null, pos: true },
          { label: "VIX",        value: l.vix != null        ? l.vix.toFixed(1)                                        : null, pos: (l.vix ?? 99) < 20 },
          { label: "US 10Y",     value: l.us10y != null      ? `${l.us10y.toFixed(2)}%`                                : null, pos: (l.us10y ?? 99) < 4.5 },
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
          <button
            key={key}
            onClick={() => setHorizon(key)}
            className={clsx("px-4 py-2.5 rounded-xl text-sm font-medium transition-all",
              horizon === key
                ? "bg-brand-500 text-white shadow-lg shadow-brand-500/20"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white"
            )}
          >
            {label}
            <span className={clsx("ml-1.5 text-xs", horizon === key ? "text-blue-200" : "text-gray-600")}>
              ({sub})
            </span>
          </button>
        ))}
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="bg-blue-500/10 border border-blue-500/30 rounded-xl p-4 flex items-start gap-3">
          <Loader2 size={18} className="text-blue-400 mt-0.5 animate-spin shrink-0" />
          <div>
            <p className="text-sm font-semibold text-blue-300">Waking up the AI engine…</p>
            <p className="text-xs text-blue-400/70 mt-0.5">
              Our free-tier server starts cold — this can take 30–60 seconds. Hang tight while we fetch and score Nifty 100 stocks.
            </p>
          </div>
        </div>
      )}

      {/* Error */}
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
        <div className="grid md:grid-cols-2 gap-4">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-72" />
          ))}
        </div>
      ) : picks.length > 0 ? (
        <div className="grid md:grid-cols-2 gap-4">
          {picks.map((pick, i) => (
            <PickCard key={pick.symbol} pick={pick} rank={i + 1} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <AlertCircle size={40} className="text-gray-600 mb-4" />
          <h3 className="text-lg font-semibold text-gray-300 mb-2">
            {data?.generated_at ? "No BUY signals found today" : "Picks not yet generated"}
          </h3>
          <p className="text-sm text-gray-500 max-w-sm">
            {data?.generated_at
              ? "The AI didn't find strong BUY signals in Nifty 100 today. Market conditions may be weak — check back tomorrow."
              : "Daily picks are generated at 9 AM IST on market days. Check back after the market opens."}
          </p>
        </div>
      )}

      <div className="bg-dark-card border border-dark-border rounded-xl p-4 text-center space-y-1">
        <p className="text-xs font-semibold text-gray-400">Disclaimer</p>
        <p className="text-xs text-gray-500">
          StockSense picks are AI-generated signals for <strong className="text-gray-400">educational and research purposes only</strong>.
          They do not constitute financial advice or a recommendation to buy or sell any security.
          Past model accuracy is not a guarantee of future results. Always consult a SEBI-registered investment advisor before trading.
        </p>
      </div>
    </div>
  );
}
