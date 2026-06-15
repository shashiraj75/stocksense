"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import { TrendingUp, Clock, AlertCircle, ChevronDown, ChevronUp, Loader2 } from "lucide-react";

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

function PickCard({ pick }: { pick: Pick }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);

  const upside = pick.price && pick.target
    ? (((pick.target - pick.price) / pick.price) * 100).toFixed(1)
    : null;

  // Group reasoning by category
  const grouped: Record<string, ReasonItem[]> = {};
  for (const r of pick.reasoning ?? []) {
    const group = INDICATOR_GROUP[r.indicator] ?? "Other";
    if (!grouped[group]) grouped[group] = [];
    grouped[group].push(r);
  }

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl overflow-hidden hover:border-green-500/50 transition-all hover:shadow-lg hover:shadow-green-500/5 group">
      {/* Clickable header — navigates to stock page */}
      <div
        onClick={() => router.push(`/stock/${pick.symbol}?market=IN`)}
        className="p-4 cursor-pointer"
      >
        {/* Top row: symbol + price */}
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono font-bold text-white text-lg group-hover:text-green-400 transition-colors">
                {pick.symbol}
              </span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/15 text-green-400 font-semibold border border-green-500/30">
                BUY
              </span>
              {pick.sentiment && pick.sentiment !== "NEUTRAL" && (
                <span className={`text-xs px-1.5 py-0.5 rounded border ${pick.sentiment === "BULLISH" ? "bg-green-500/10 text-green-400 border-green-500/20" : "bg-red-500/10 text-red-400 border-red-500/20"}`}>
                  {pick.sentiment === "BULLISH" ? "📰 Bullish News" : "📰 Bearish News"}
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500 mt-0.5 truncate max-w-[200px]">{pick.name}</p>
          </div>
          <div className="text-right">
            <div className="text-sm font-semibold text-white">₹{pick.price?.toLocaleString("en-IN")}</div>
            {upside && <div className="text-xs text-green-400 font-medium">+{upside}% upside</div>}
          </div>
        </div>

        {/* AI Confidence + portfolio weight row */}
        <div className="mb-3 space-y-2">
          <div>
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>AI Confidence</span>
              <span className="text-white font-medium">{pick.confidence}%</span>
            </div>
            <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
              <div className="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full"
                style={{ width: `${pick.confidence}%` }} />
            </div>
          </div>
          {pick.portfolio_weight != null && (
            <div>
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>Suggested Allocation</span>
                <span className={`font-semibold ${pick.portfolio_weight >= 0.30 ? "text-green-400" : "text-yellow-400"}`}>
                  {Math.round(pick.portfolio_weight * 100)}%
                </span>
              </div>
              <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
                <div className="h-full bg-gradient-to-r from-blue-500 to-indigo-400 rounded-full"
                  style={{ width: `${Math.round(pick.portfolio_weight * 100)}%` }} />
              </div>
            </div>
          )}
        </div>

        {/* Price targets row */}
        <div className="grid grid-cols-3 gap-2 mb-3">
          <div className="bg-dark-border/40 rounded-lg p-2 text-center">
            <p className="text-xs text-gray-500 mb-0.5">Entry Zone</p>
            <p className="text-xs text-white font-mono">
              {pick.entry_low && pick.entry_high
                ? `₹${pick.entry_low.toLocaleString("en-IN")}–${pick.entry_high.toLocaleString("en-IN")}`
                : `₹${pick.price?.toLocaleString("en-IN")}`}
            </p>
          </div>
          <div className="bg-green-500/10 rounded-lg p-2 text-center border border-green-500/20">
            <p className="text-xs text-gray-500 mb-0.5">Target</p>
            <p className="text-xs text-green-400 font-mono font-semibold">₹{pick.target?.toLocaleString("en-IN")}</p>
          </div>
          <div className="bg-red-500/10 rounded-lg p-2 text-center border border-red-500/20">
            <p className="text-xs text-gray-500 mb-0.5">Stop Loss</p>
            <p className="text-xs text-red-400 font-mono font-semibold">
              {pick.stop_loss ? `₹${pick.stop_loss.toLocaleString("en-IN")}` : "—"}
            </p>
          </div>
        </div>

        {pick.risk_reward && (
          <div className="flex items-center gap-1 text-xs text-gray-500 mb-3">
            <span>Risk:Reward</span>
            <span className="text-white font-semibold">1 : {pick.risk_reward.toFixed(1)}</span>
          </div>
        )}

        {/* Smart Summary */}
        {pick.summary && (
          <div className="bg-dark-border/30 rounded-lg p-3 mb-1 border border-dark-border">
            <p className="text-xs text-gray-300 leading-relaxed">{pick.summary}</p>
          </div>
        )}
      </div>

      {/* Expandable deep reasoning */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between px-4 py-2.5 border-t border-dark-border text-xs text-gray-500 hover:text-white hover:bg-dark-border/20 transition-colors"
      >
        <span className="font-medium">View detailed analysis</span>
        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-dark-border bg-black/20">
          {/* Cross-sectional ranking (z-scores) */}
          {pick.factor_zscores && (
            <div className="pt-3 space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Universe Rank (vs Nifty 100)</p>
                {pick.combined_alpha != null && (
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded ${pick.combined_alpha > 0.5 ? "bg-green-500/20 text-green-400" : pick.combined_alpha < -0.3 ? "bg-red-500/20 text-red-400" : "bg-yellow-500/20 text-yellow-400"}`}>
                    α {pick.combined_alpha > 0 ? "+" : ""}{pick.combined_alpha.toFixed(2)}
                  </span>
                )}
              </div>
              <p className="text-xs text-gray-500">How this stock ranks vs the full Nifty 100 universe on each factor (σ = standard deviations above/below average)</p>
              {([
                ["tech",      "Technical Momentum", "text-blue-400"],
                ["fund",      "Fundamentals",        "text-purple-400"],
                ["sentiment", "News Sentiment",      "text-yellow-400"],
                ["quality",   "Quality / ROIC",      "text-green-400"],
              ] as [keyof FactorZScores, string, string][]).map(([key, label, color]) => {
                const z = pick.factor_zscores?.[key];
                if (z == null) return null;
                // Map z-score (-3 to +3) → 0–100 for bar display
                const pct = Math.round(Math.min(100, Math.max(0, (z + 3) / 6 * 100)));
                const zColor = z > 0.5 ? "text-green-400" : z < -0.5 ? "text-red-400" : "text-yellow-400";
                return (
                  <div key={key} className="space-y-1">
                    <div className="flex justify-between text-xs">
                      <span className="text-gray-400">{label}</span>
                      <span className={`font-mono font-semibold ${zColor}`}>{z > 0 ? "+" : ""}{z.toFixed(2)}σ</span>
                    </div>
                    <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
                      <div className={`h-full rounded-full transition-all ${z > 0.5 ? "bg-green-500" : z < -0.5 ? "bg-red-500" : "bg-yellow-500"}`} style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Core signal scores */}
          <div className={pick.factor_zscores ? "space-y-2" : "pt-3 space-y-2"}>
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Core Signal Scores (Absolute)</p>
            {pick.tech_score != null && <ScoreBar label="Technical" value={pick.tech_score} color="text-blue-400" />}
            {pick.fund_score != null && <ScoreBar label="Fundamental" value={pick.fund_score} color="text-purple-400" />}
            <ScoreBar label="AI Confidence" value={pick.confidence} color="text-green-400" />
          </div>

          {/* Quality Factor Radar */}
          {pick.quality_factors?.breakdown && Object.keys(pick.quality_factors.breakdown).length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Professional Quality Factors</p>
                <div className="flex items-center gap-2 text-xs">
                  {pick.quality_factors.sector && (
                    <span className="px-1.5 py-0.5 bg-dark-border rounded text-gray-400">{pick.quality_factors.sector}</span>
                  )}
                  {pick.quality_factors.piotroski != null && (
                    <span className={`px-1.5 py-0.5 rounded font-semibold ${pick.quality_factors.piotroski >= 7 ? "bg-green-500/20 text-green-400" : pick.quality_factors.piotroski <= 3 ? "bg-red-500/20 text-red-400" : "bg-yellow-500/20 text-yellow-400"}`}>
                      Piotroski {pick.quality_factors.piotroski}/9
                    </span>
                  )}
                </div>
              </div>
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

          {/* Grouped reasoning */}
          {Object.entries(grouped).map(([group, items]) => (
            <div key={group}>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">{group}</p>
              <div className="space-y-1.5">
                {items.map((r, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <span className={`text-xs font-semibold mt-0.5 flex-shrink-0 w-16 ${SIGNAL_COLOR[r.signal] ?? "text-gray-400"}`}>
                      {r.signal}
                    </span>
                    <p className="text-xs text-gray-300 leading-relaxed">{r.reason}</p>
                  </div>
                ))}
              </div>
            </div>
          ))}
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

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between">
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
        {generatedAt && (
          <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-dark-card border border-dark-border rounded-lg px-3 py-2 flex-shrink-0">
            <Clock size={12} />
            <span>Updated {generatedAt}</span>
          </div>
        )}
      </div>

      {/* Global Macro Snapshot — sourced from first pick's global_context */}
      {(() => {
        const allPicks = [...(data?.picks?.short ?? []), ...(data?.picks?.medium ?? []), ...(data?.picks?.long ?? [])];
        const ctx = (allPicks[0] as any)?.global_context as GlobalContext | undefined;
        if (!ctx?.levels && !ctx?.changes) return null;
        const l = ctx.levels ?? {};
        const c = ctx.changes ?? {};
        const macroItems = [
          { label: "S&P 500", value: c.sp500 != null ? `${c.sp500 > 0 ? "+" : ""}${c.sp500.toFixed(1)}%` : null, positive: (c.sp500 ?? 0) >= 0 },
          { label: "NASDAQ", value: c.nasdaq != null ? `${c.nasdaq > 0 ? "+" : ""}${c.nasdaq.toFixed(1)}%` : null, positive: (c.nasdaq ?? 0) >= 0 },
          { label: "Brent Crude", value: c.crude_brent != null ? `${c.crude_brent > 0 ? "+" : ""}${c.crude_brent.toFixed(1)}%` : null, positive: (c.crude_brent ?? 0) <= 0 },
          { label: "Gold", value: c.gold != null ? `${c.gold > 0 ? "+" : ""}${c.gold.toFixed(1)}%` : null, positive: true },
          { label: "USD/INR", value: l.usdinr != null ? `₹${l.usdinr.toFixed(1)}` : null, positive: true },
          { label: "VIX", value: l.vix != null ? l.vix.toFixed(1) : null, positive: (l.vix ?? 99) < 20 },
          { label: "DXY", value: l.dxy != null ? l.dxy.toFixed(1) : null, positive: (l.dxy ?? 999) < 102 },
          { label: "US 10Y", value: l.us10y != null ? `${l.us10y.toFixed(2)}%` : null, positive: (l.us10y ?? 99) < 4.5 },
        ].filter(i => i.value !== null);
        if (!macroItems.length) return null;
        return (
          <div className="bg-dark-card border border-dark-border rounded-xl p-4">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">🌍 Global Macro Snapshot (at time of pick generation)</p>
            <div className="flex flex-wrap gap-3">
              {macroItems.map(({ label, value, positive }) => (
                <div key={label} className="flex flex-col items-center bg-dark-border/40 rounded-lg px-3 py-2 min-w-[70px]">
                  <span className="text-xs text-gray-500 mb-0.5">{label}</span>
                  <span className={`text-xs font-bold font-mono ${positive ? "text-green-400" : "text-red-400"}`}>{value}</span>
                </div>
              ))}
            </div>
            {ctx.score != null && (
              <div className="mt-3 flex items-center gap-2">
                <span className="text-xs text-gray-500">Global Macro Score</span>
                <div className="flex-1 h-1.5 bg-dark-border rounded-full overflow-hidden max-w-[120px]">
                  <div className={`h-full rounded-full ${ctx.score >= 55 ? "bg-green-500" : ctx.score <= 45 ? "bg-red-500" : "bg-yellow-500"}`}
                    style={{ width: `${ctx.score}%` }} />
                </div>
                <span className={`text-xs font-semibold ${ctx.score >= 55 ? "text-green-400" : ctx.score <= 45 ? "text-red-400" : "text-yellow-400"}`}>
                  {ctx.score >= 55 ? "Supportive" : ctx.score <= 45 ? "Headwind" : "Neutral"} ({ctx.score}/100)
                </span>
              </div>
            )}
          </div>
        );
      })()}

      {/* Alpha Engine Status Banner */}
      {data?.alpha_engine && data?.regime && (
        <div className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">🧠 Learning Alpha Engine</p>
            <div className="flex items-center gap-2">
              {(() => {
                const regimeColors: Record<string, string> = {
                  BULL_CALM:     "bg-green-500/20 text-green-400 border-green-500/30",
                  BULL_VOLATILE: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
                  BEAR_CALM:     "bg-orange-500/20 text-orange-400 border-orange-500/30",
                  BEAR_PANIC:    "bg-red-500/20 text-red-400 border-red-500/30",
                };
                const label = data.regime!.label;
                const cls = regimeColors[label] || "bg-gray-500/20 text-gray-400 border-gray-500/30";
                return (
                  <span className={`text-xs px-2 py-0.5 rounded-full border font-semibold ${cls}`}>
                    {label.replace("_", " ")}
                  </span>
                );
              })()}
            </div>
          </div>
          <p className="text-xs text-gray-500">{data.regime.description}</p>
          <div className="flex flex-wrap gap-4">
            {(["short", "medium", "long"] as const).map(h => {
              const meta = data.alpha_engine![h];
              if (!meta) return null;
              const hasMetaModel = meta.meta_model;
              return (
                <div key={h} className="space-y-1.5 min-w-[140px]">
                  <p className="text-xs font-semibold text-gray-400 capitalize">{h}-term IC weights</p>
                  {Object.entries(meta.ic_weights ?? {}).map(([factor, weight]) => (
                    <div key={factor} className="flex items-center gap-2">
                      <span className="text-xs text-gray-500 w-16 capitalize">{factor}</span>
                      <div className="flex-1 h-1 bg-dark-border rounded-full overflow-hidden">
                        <div className="h-full bg-blue-500 rounded-full" style={{ width: `${Math.round((weight as number) * 100)}%` }} />
                      </div>
                      <span className="text-xs text-gray-400 font-mono w-8 text-right">{Math.round((weight as number) * 100)}%</span>
                    </div>
                  ))}
                  <p className={`text-xs mt-1 ${hasMetaModel ? "text-green-400" : "text-gray-500"}`}>
                    {hasMetaModel ? "✓ Meta-model active" : "◻ IC-alpha (learning…)"}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Horizon tabs */}
      <div className="flex gap-2">
        {HORIZONS.map(({ key, label, sub }) => (
          <button
            key={key}
            onClick={() => setHorizon(key)}
            className={`px-4 py-2.5 rounded-xl text-sm font-medium transition-all ${
              horizon === key
                ? "bg-brand-500 text-white shadow-lg shadow-brand-500/20"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white"
            }`}
          >
            {label}
            <span className={`ml-1.5 text-xs ${horizon === key ? "text-blue-200" : "text-gray-600"}`}>
              ({sub})
            </span>
          </button>
        ))}
      </div>

      {/* Cold-start banner */}
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

      {/* Server error */}
      {queryError && !isLoading && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-red-300">Couldn't reach the prediction server</p>
            <p className="text-xs text-red-400/70 mt-0.5">The server may still be warming up. Please refresh in 30 seconds.</p>
          </div>
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="grid md:grid-cols-2 gap-4">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-64" />
          ))}
        </div>
      ) : picks.length > 0 ? (
        <div className="grid md:grid-cols-2 gap-4">
          {picks.map((pick) => (
            <PickCard key={pick.symbol} pick={pick} />
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
