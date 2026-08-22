"use client";
import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api, fetchQuote, fetchPaperPortfolio } from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";
import { buildOpenTradeCountMap, openTradeCountKey } from "@/utils/openTradeCount";
import { computeEstimatedUpsidePct, hasValidGenerationBasis, isValidPrice, selectPriceBasis } from "@/utils/priceBasis";
import { evaluateEntryZoneActionability, isQuoteVerifiedComparable, isVerifiedOutsideEntryZone, selectUnverifiedEntryZoneNote } from "@/utils/actionability";
import { getMarketStatus } from "@/utils/marketHours";
import { evaluateSessionFreshness, formatSessionDate, type FreshnessResult } from "@/utils/sessionFreshness";
import { selectPayloadForMarket } from "@/utils/payloadMarketGuard";
import {
  TrendingUp, Clock, AlertCircle, ChevronDown, ChevronUp,
  Loader2, Target, ShieldAlert, Zap, CheckCircle, BarChart2, Activity, FlaskConical, RefreshCw,
} from "lucide-react";
import clsx from "clsx";
import { PaperTradeModal } from "@/components/PaperTradeModal";
import { buildEntryEvidenceFromDailyPick } from "@/utils/entryEvidence";
import { useMarketPreference } from "@/hooks/useMarketPreference";
import { UnsupportedMarketNotice } from "@/components/UnsupportedMarketNotice";
import { INTEGRITY_HOLD_ACTIVE, ValidationIntegrityHold } from "@/components/ValidationIntegrityHold";
import { DataLimitationsNotice } from "@/components/DataLimitationsNotice";

// ── Types ─────────────────────────────────────────────────────────────────────
type ReasonItem = { indicator: string; signal: string; reason: string };
type QualityFactors = { score?: number; sector?: string; piotroski?: number | null; breakdown?: Record<string, number> };
type FactorZScores = { tech?: number; fund?: number; sentiment?: number; quality?: number };

export type Pick = {
  symbol: string; name: string; price: number; target: number;
  stop_loss?: number; entry_low?: number; entry_high?: number;
  risk_reward?: number; confidence: number; tech_score?: number;
  fund_score?: number; sentiment?: string; reasoning: ReasonItem[];
  summary?: string; quality_factors?: QualityFactors; factor_zscores?: FactorZScores;
  combined_alpha?: number; portfolio_weight?: number; regime_label?: string;
  score_band?: string; horizon: string;
  // Phase A1 evidence-gap closure — additive, absent on picks generated
  // before this field existed (legacy cached/historical picks).
  technical_signal?: string | null;
  sentiment_score?: number | null;
  // Release 12A generation-reference provenance (absent on legacy picks)
  generated_at?: string | null;
  generation_reference_price?: number | null;
  generation_reference_source?: string | null;
  generation_reference_price_basis?: string | null;
  generation_reference_as_of?: string | null;
};

// Fix 4 (Phase A1.1 final decision-context correction) — a genuinely
// immutable, copy-based point-in-time snapshot of the Daily Pick decision
// context, captured at the moment the Paper Trade modal is opened. The
// earlier version of this fix (`setFrozenPick(pick)`) stored the SAME
// object reference from the parent Picks list's react-query cache in state
// — that happened to work because react-query replaces rather than mutates
// its cache data on refetch, but the contract shouldn't rely on that never
// changing. This is narrowly typed to only the fields PaperTradeModal (and
// the `buildEntryEvidenceFromDailyPick` evidence builder it feeds) actually
// need — a subset of `Pick`, mirroring `DailyPickEvidenceSource` in
// entryEvidence.ts plus the couple of extra display-only fields (symbol,
// horizon) that helper doesn't need but the modal does.
export type FrozenDailyPickSnapshot = {
  symbol: string;
  price: number;
  horizon: string;
  entry_low?: number;
  entry_high?: number;
  stop_loss?: number;
  target: number;
  confidence: number;
  fund_score?: number;
  sentiment?: string;
  technical_signal?: string | null;
  sentiment_score?: number | null;
  reasoning: ReasonItem[];
  generated_at?: string | null;
};

// Explicit, typed field-by-field copy — deliberately NOT a broad
// `JSON.parse(JSON.stringify(...))` hack. Clones the nested `reasoning`
// array (and its item objects) rather than sharing the source array/object
// references, so a later mutation of the source `pick.reasoning` entries
// (or the parent swapping in a new array for the same symbol) can never
// reach back into an already-open modal's frozen context.
function freezeDailyPickSnapshot(pick: Pick): FrozenDailyPickSnapshot {
  return {
    symbol: pick.symbol,
    price: pick.price,
    horizon: pick.horizon,
    entry_low: pick.entry_low,
    entry_high: pick.entry_high,
    stop_loss: pick.stop_loss,
    target: pick.target,
    confidence: pick.confidence,
    fund_score: pick.fund_score,
    sentiment: pick.sentiment,
    technical_signal: pick.technical_signal ?? null,
    sentiment_score: pick.sentiment_score ?? null,
    reasoning: (pick.reasoning ?? []).map(r => ({ ...r })),
    generated_at: pick.generated_at ?? null,
  };
}

// n_conviction_qualified/n_published/conviction_threshold/max_published_per_horizon
// (feature/daily-picks-conviction-gated-publication) are additive and
// backward-compatible — n_scored/n_buy keep their pre-existing meaning and
// a legacy cached payload without these new fields still renders fine
// (all optional).
type AlphaEngineMeta = {
  ic_weights?: Record<string, number>; regime?: string; n_scored?: number; n_buy?: number; meta_model?: boolean;
  n_conviction_qualified?: number; n_published?: number; conviction_threshold?: number; max_published_per_horizon?: number;
  conviction_semantic?: string;
};
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
  // 3-phase US Daily Picks upgrade (premarket finalizer) — absent on any
  // payload finalized before this feature, or on the IN market (which has
  // no premarket phase). Rendered only when present; never fabricated.
  base_generated_at?: string | null;
  premarket_finalized_at?: string | null;
  premarket_status?: string | null;
  premarket_finalizer_version?: string | null;
  // 2026-07-17 — connects the confidence % shown on each pick to its
  // horizon's real, walk-forward validated track record (see
  // services.validation_engine.get_track_record_summary). One entry per
  // backtested universe (nifty100+midcap for IN, us for US); empty array
  // means no validation run has completed for that horizon yet — never
  // fabricated. Absent entirely on any payload from before this feature.
  historical_track_record?: Record<
    "short" | "medium" | "long",
    { universe: string; beat_benchmark_pct: number | null; buy_hit_rate_pct: number | null;
      n_signals: number | null; run_at: string | null;
      // DP-026 remediation (2026-07-21) — undefined/null on any entry
      // persisted before this session (genuinely legacy, not "false").
      fundamentals_point_in_time?: boolean | null;
      fundamentals_point_in_time_coverage_pct?: number | null }[]
  >;
  // US Daily Picks generation-reliability incident (2026-07-22) — failure-
  // safe publication contract. `picks` above is now ALWAYS either today's
  // genuine successful result or the last genuinely successful prior
  // result (never an empty error stand-in); these fields say which, and
  // give the exact vocabulary for the three states that must never be
  // confused: today's success, stale-but-real last success, and a known
  // terminal failure with nothing usable yet.
  stale?: boolean;
  serving_stale_payload?: boolean;
  last_successful_session_date?: string | null;
  last_attempt_status?: string | null;
  last_attempt_error_category?: string | null;
  message?: string;
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
  { key: "IN" as const, short: "🇮🇳 IN", label: "🇮🇳 NSE India",  currency: "₹", locale: "en-IN", tz: "Asia/Kolkata",     genTime: "2:07 AM IST",   tzLabel: "IST" },
  // This is the US Pre-Open BASE generation schedule only (heavy/full
  // pipeline, see .github/workflows/daily_picks_us.yml) — NOT the separate
  // Premarket Review stage, which runs afterward at ~6:00 AM America/New_York
  // (see .github/workflows/daily_picks_us_premarket.yml and
  // PREMARKET_STATUS_LABEL below). Do not collapse these into one label —
  // see the two-stage badge rendering further down this file.
  { key: "US" as const, short: "🇺🇸 US", label: "🇺🇸 NYSE/NASDAQ", currency: "$", locale: "en-US", tz: "America/New_York", genTime: "10:00 AM Dubai / 11:30 AM IST", tzLabel: "ET" },
];

// Premarket Review stage — scheduled for ~6:00 AM America/New_York
// (both EDT and EST candidates converge on the backend's DST-aware
// 6:00-7:30 AM ET acceptance window; see premarket_finalizer.py). This is
// always a truthful *status* label, never a claim that finalization
// happened at exactly this time — the actual completion timestamp is
// rendered separately from data.premarket_finalized_at when present.
const PREMARKET_REVIEW_SCHEDULE_LABEL = "Scheduled for 6:00 AM ET";
const PREMARKET_STATUS_LABEL: Record<string, string> = {
  pending: "Premarket Review Pending",
  completed: "Premarket Review Completed",
  completed_with_limited_premarket_data: "Premarket Review Completed (Limited Data)",
  skipped: "Premarket Review Skipped",
  failed: "Premarket Review Failed",
};
const PREMARKET_STATUS_CLASS: Record<string, string> = {
  pending: "border-dark-border text-gray-500",
  completed: "border-green-500/40 text-green-400",
  completed_with_limited_premarket_data: "border-yellow-500/40 text-yellow-400",
  skipped: "border-dark-border text-gray-500",
  failed: "border-red-500/40 text-red-400",
};

// Canonical horizon wording — see @/utils/horizons's docstring. Kept as a
// local tuple (rather than importing that module directly) because this
// tab list also carries a "key" used for routing/query-state that the
// shared module doesn't need to know about; the `sub` strings themselves
// must stay byte-identical to HORIZON_INFO's `period` values.
const HORIZONS = [
  { key: "short",  label: "Short Term",  sub: "1–5 trading days" },
  { key: "medium", label: "Medium Term", sub: "2–4 weeks"        },
  { key: "long",   label: "Long Term",   sub: "3–6 months"       },
] as const;

// High Conviction filter (legacy-cache fallback only — see `publicationPolicy`
// and the button's own comment below) — surfaces only picks at/above this
// Model Conviction threshold, sorted highest-first. 85 matches the top slice
// users actually see in practice (long-horizon picks routinely reach
// 85-100/100; short/medium rarely do), not an arbitrary round number. Not
// sourced from the backend registry on purpose — it is only ever shown for a
// legacy cached payload that predates that registry.
const HIGH_CONVICTION_THRESHOLD = 85;

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
// Every Daily Pick is already a BUY (this page only ever lists the BUY band),
// so pick.confidence carries the same meaning as everywhere else it appears
// (Stock Detail, Portfolio SignalBadge): how far the composite score sits
// above the 60-point BUY threshold, not "how likely this pick is good."
// Mirrors SignalBadge's own thresholds (>=60 strong, 45-59 moderate, <45
// muted) so a marginal pick doesn't visually read as confident as a strong
// one — color treatment only, no change to the confidence value itself.
// Exported for direct unit testing — no behavior change.
export function confidenceTextColor(confidence: number): string {
  if (confidence >= 60) return "text-green-400";
  if (confidence >= 45) return "text-yellow-400";
  return "text-gray-400";
}

// Exported for direct unit testing — no behavior change.
export function confidenceGradientClass(confidence: number): string {
  if (confidence >= 60) return "bg-gradient-to-r from-green-500 to-emerald-400";
  if (confidence >= 45) return "bg-gradient-to-r from-yellow-500 to-amber-400";
  return "bg-gradient-to-r from-gray-500 to-gray-400";
}

// Exported for direct unit testing — no behavior change from the inline
// template literal this replaces. Carries the horizon the user was already
// on so Stock Detail's resolveInitialTab (see stock/[symbol]/page.tsx) can
// land on the same tab instead of always resetting to Short Term.
export function buildPickStockHref(symbol: string, market: "IN" | "US", horizon: string): string {
  return `/stock/${encodeURIComponent(symbol)}?market=${market}&horizon=${horizon}`;
}

// Conviction-gated publication policy (finding 2, follow-up to commit
// 5a006498) — exported pure functions so the wording/derivation logic can
// be unit tested directly against realistic backend payload shapes
// (valid metadata, legacy/missing metadata, partially-typed metadata)
// instead of only asserting on static source text.
export type PublicationPolicy = {
  maxPublished: number;
  threshold: number;
  nPublished: number;
  nQualified: number;
  // Required (finding 2, corrective follow-up to 0f2bbed8): derivePublicationPolicy
  // now fails the whole payload closed unless the backend supplied a
  // non-empty conviction_semantic string, so a returned PublicationPolicy
  // always has one.
  semantic: string;
};

/**
 * Derives the active conviction-gated publication policy strictly from
 * backend `alpha_engine_meta` for one horizon. Returns null — never a
 * fabricated/default policy — whenever any required field is absent or
 * not the expected type, which is exactly what a pre-deployment legacy
 * cached payload (no publication metadata at all) looks like. Callers
 * must render distinct, truthful copy for the null case rather than
 * claiming the new policy is active.
 */
// Finding 2 (corrective follow-up to 0f2bbed8): `typeof x === "number"` alone
// lets NaN/Infinity/-Infinity/negative/fractional values through — none of
// which a well-formed backend payload should ever produce, but a malformed
// or partially-corrupted cache entry could. Every numeric field must also
// pass Number.isFinite, and the specific invariants the backend's own
// registry guarantees (positive integer cap, 0-100 threshold, non-negative
// integer counts, n_published <= max, n_published <= n_qualified) are
// checked explicitly — any single violation fails the whole payload closed
// (returns null, the same "render neutral legacy-compatible copy" outcome
// as a payload missing the fields entirely). `conviction_semantic` must be
// a non-empty string — the new backend always supplies one.
function _isNonNegativeInteger(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x) && Number.isInteger(x) && x >= 0;
}

export function derivePublicationPolicy(meta: AlphaEngineMeta | null | undefined): PublicationPolicy | null {
  if (meta == null) return null;

  const { max_published_per_horizon, conviction_threshold, n_published, n_conviction_qualified, conviction_semantic } = meta;

  if (!_isNonNegativeInteger(max_published_per_horizon) || max_published_per_horizon <= 0) return null;
  if (typeof conviction_threshold !== "number" || !Number.isFinite(conviction_threshold)) return null;
  if (conviction_threshold < 0 || conviction_threshold > 100) return null;
  if (!_isNonNegativeInteger(n_published)) return null;
  if (!_isNonNegativeInteger(n_conviction_qualified)) return null;
  if (n_published > max_published_per_horizon) return null;
  if (n_published > n_conviction_qualified) return null;
  if (typeof conviction_semantic !== "string" || conviction_semantic.length === 0) return null;

  return {
    maxPublished: max_published_per_horizon,
    threshold: conviction_threshold,
    nPublished: n_published,
    nQualified: n_conviction_qualified,
    semantic: conviction_semantic,
  };
}

/** Header copy for the active horizon — dynamic when policy is active
 * (never hardcodes 3/85), neutral (no specific-number claim) otherwise. */
export function formatPublicationPolicyCopy(policy: PublicationPolicy | null): string {
  return policy
    ? `Up to ${policy.maxPublished} qualified picks per horizon (Model Conviction ≥ ${policy.threshold}/100)`
    : "Qualified BUY picks per horizon";
}

// Finding 1 (corrective follow-up to 0f2bbed8): extracted pure function so
// production and tests share exactly one implementation — no test-local
// reimplementation of this logic. `publicationPolicyActive` (NOT the raw
// `highConvictionOnly` toggle alone) gates whether the filter/sort ever
// runs: once the backend conviction-gated publication policy is active for
// a horizon, the list is already <=3 picks, all >=85/100, in the
// backend's own authoritative rank order — a stale `highConvictionOnly`
// state (e.g. left on from before a legacy→policy-active transition on the
// same horizon) must never be allowed to filter or re-sort it. Only a
// legacy payload (`publicationPolicyActive === false`) ever applies the
// client-side filter/sort, exactly as before this fix.
export function computeVisiblePicks(
  picks: Pick[],
  highConvictionOnly: boolean,
  publicationPolicyActive: boolean,
): Pick[] {
  if (publicationPolicyActive || !highConvictionOnly) return picks;
  return [...picks]
    .filter(p => p.confidence >= HIGH_CONVICTION_THRESHOLD)
    .sort((a, b) => b.confidence - a.confidence);
}

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

// ── Historical track record (2026-07-17) ─────────────────────────────────────
// Separate from, and NOT a substitute for, BacktestPanel/LivePerformanceTracker
// below — those remain withheld behind INTEGRITY_HOLD_ACTIVE pending GPI-0
// condition 3 (entry/resolution price-basis reconciliation). This reads a
// different, already-reconciled source: services.validation_engine's own
// walk-forward backtest results, passed straight through GET /api/picks/daily
// as historical_track_record — no separate query, no risk of the market/
// universe-blending defects GPI-0 was about. Renders nothing (not an error
// state) when no validation run has completed for this horizon yet.
function HistoricalTrackRecordSummary({
  horizon, entries, benchmarkLabel,
}: {
  horizon: "short" | "medium" | "long";
  entries?: { universe: string; beat_benchmark_pct: number | null; buy_hit_rate_pct: number | null;
              n_signals: number | null; run_at: string | null;
              fundamentals_point_in_time?: boolean | null;
              fundamentals_point_in_time_coverage_pct?: number | null }[];
  benchmarkLabel: string;
}) {
  if (!entries || entries.length === 0) return null;

  const UNIVERSE_LABEL: Record<string, string> = {
    nifty100: "Large-cap", midcap: "Mid-cap", us: "US",
  };

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <BarChart2 size={16} className="text-brand-400 shrink-0" />
        <p className="text-sm font-semibold text-white">Historical accuracy — {horizon}-term</p>
        <span className="text-xs text-gray-500">from real walk-forward backtests, not this run's picks</span>
      </div>
      {/* DP-026 (2026-07-21 remediation) — the blanket "not point-in-time"
          notice is now shown only when at least one entry genuinely still
          needs it (false or legacy/null). A fully-remediated entry
          (fundamentals_point_in_time === true) gets its own per-entry
          coverage badge below instead — see DataLimitationsNotice's
          docstring: this must never claim remediation for an entry that
          isn't. */}
      {entries.some(e => e.fundamentals_point_in_time !== true) && (
        <DataLimitationsNotice className="mb-3" />
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {entries.map((e) => {
          const beatBenchmark = e.beat_benchmark_pct;
          const lowSample = (e.n_signals ?? 0) < 200;
          const weak = beatBenchmark != null && beatBenchmark < 50;
          const pit = e.fundamentals_point_in_time;
          return (
            <div key={e.universe} className="bg-dark-bg/60 border border-dark-border rounded-lg p-3">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-medium text-gray-300">{UNIVERSE_LABEL[e.universe] ?? e.universe}</span>
                {e.n_signals != null && (
                  <span className="text-[11px] text-gray-500">{e.n_signals.toLocaleString()} backtested signals</span>
                )}
              </div>
              <div className="flex items-baseline gap-3">
                <div>
                  <span className={clsx("text-lg font-bold", weak ? "text-amber-400" : "text-emerald-400")}>
                    {beatBenchmark != null ? `${beatBenchmark.toFixed(1)}%` : "—"}
                  </span>
                  <span className="text-[11px] text-gray-500 ml-1">beat {benchmarkLabel}</span>
                </div>
                {e.buy_hit_rate_pct != null && (
                  <div>
                    <span className="text-sm font-semibold text-gray-300">{e.buy_hit_rate_pct.toFixed(1)}%</span>
                    <span className="text-[11px] text-gray-500 ml-1">hit rate</span>
                  </div>
                )}
              </div>
              {weak && (
                <p className="text-[11px] text-amber-400/80 mt-1.5">
                  Below 50% — this horizon has not shown a reliable edge over {benchmarkLabel} historically.
                </p>
              )}
              {lowSample && (
                <p className="text-[11px] text-gray-500 mt-1">Small sample — treat as directional, not conclusive.</p>
              )}
              {/* DP-026 remediation — per-entry provenance, replacing the
                  blanket warning only for an entry proven genuinely
                  point-in-time. `pit === false` (still contaminated) is
                  already covered by the blanket notice above and gets no
                  duplicate text here; `pit === undefined/null` (legacy,
                  predates data_limitations entirely) gets its own distinct
                  label rather than being silently lumped in with either
                  "remediated" or "known contaminated". */}
              {pit === true && (
                <p className="text-[11px] text-emerald-400/80 mt-1.5">
                  Point-in-time fundamentals coverage: {e.fundamentals_point_in_time_coverage_pct ?? "—"}%
                  {" "}— reconstructed from SEC filings as of each signal's own date.
                </p>
              )}
              {pit == null && (
                <p className="text-[11px] text-gray-500 mt-1.5">
                  Legacy result — predates point-in-time fundamentals tracking; treat as directional only.
                </p>
              )}
            </div>
          );
        })}
      </div>
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

      {/* DP-026 — always rendered, not conditioned on data.data_limitations
          being present (legacy runs predate that field but carry the same
          limitation). See DataLimitationsNotice for the full rationale. */}
      <DataLimitationsNotice />

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

      {/* Priority 2: Observed Historical Reliability by Model Conviction band.
          Renamed from "Signal Strength Calibration" (finding 3, follow-up
          to commit 5a006498): this table shows what actually happened to a
          historical sample of past picks in each Model Conviction band —
          it does NOT convert Model Conviction into a calibrated
          probability, and is deliberately not described as "calibration"
          or "calibrated" to avoid that implication. */}
      {data.score_buckets && data.score_buckets.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
            Observed Historical Reliability by Model Conviction Band
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-dark-border">
                  <th className="text-left py-1.5 pr-3">Model Conviction</th>
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
            These are observed historical outcomes for the displayed sample, by Model Conviction band —
            not a calibrated probability. Small or overlapping/correlated samples in a band can make its
            hit rate an unreliable guide to future results; a higher hit rate here is not a guarantee.
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
// Exported (in addition to being used internally by DailyPicksPage below)
// so Phase A1.1 regression tests can render it directly and simulate a
// parent Picks-list react-query refetch by re-rendering with a new `pick`
// prop, without needing to stand up the full page's data-fetching stack.
export function PickCard({ pick, rank, market, currency, locale, freshness, openTradeCount }: { pick: Pick; rank: number; market: "IN" | "US"; currency: string; locale: string; freshness?: FreshnessResult | null; openTradeCount?: number }) {
  // India Daily Picks session-freshness containment (Phase 0). `freshness`
  // is only ever passed for the IN market — US Daily Picks and any legacy
  // caller that omits the prop render exactly as before this change.
  const isStaleOrUnknown = !!freshness && freshness.freshnessStatus !== "fresh";
  const referenceDateLabel = freshness ? formatSessionDate(freshness.referenceSessionDate, locale) : null;
  const router = useRouter();
  const [expanded, setExpanded] = useState(false);
  const [showPaperTrade, setShowPaperTrade] = useState(false);
  // Phase A1.1 (Production trade 304 correction) — capture ONE immutable
  // copy of the Daily Pick decision context at the moment the modal is
  // opened. `pick` is a prop sourced from the parent Picks list's
  // react-query cache (refetchInterval: 60s/5min — see the query below) and
  // WILL change out from under this component on a background refetch even
  // while the modal stays open and mounted. Without this freeze, the AI
  // Signal pill / persisted evidence built from a live `pick` reference
  // could silently diverge from what the user actually saw when they
  // decided to buy — that's exactly what happened in Production trade 304.
  // `frozenPick` is set once at open time and never re-synced from `pick`
  // while the modal is open; a later re-open re-freezes fresh.
  const [frozenPick, setFrozenPick] = useState<FrozenDailyPickSnapshot | null>(null);

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

  // Wave 0B truthfulness rule: the price the card displays and the upside %
  // next to it must share one basis. Previously the price showed
  // (livePrice ?? pick.price) while upside was always computed from the
  // generation-time pick.price — a live-updating number beside a frozen
  // percentage that silently contradicted it.
  const { basis: priceBasis, price: displayPrice } = selectPriceBasis(livePrice, pick.price);
  const showGenerationPriceLine =
    priceBasis === "current" && isValidPrice(pick.price) && Math.abs(displayPrice! - pick.price) > 0.01;

  // Release 12A: an entry-zone "moved since generation" claim requires PROOF —
  // a provider-timestamped quote newer than the pick, on a compatible price
  // basis, in a known session. Everything else gets a conservative state.
  const marketOpen = getMarketStatus(market).isOpen;
  const actionability = evaluateEntryZoneActionability({
    generationBasis: pick.generation_reference_price_basis,
    generationPrice: pick.generation_reference_price,
    generatedAt: pick.generated_at,
    entryLow: pick.entry_low,
    entryHigh: pick.entry_high,
    quotePrice: livePrice,
    quoteBasis: liveQuote?.quote_price_basis,
    quoteTimestamp: liveQuote?.quote_timestamp,
    marketOpen,
  });
  const verifiedOutside = isVerifiedOutsideEntryZone(actionability);
  const quoteComparable = isQuoteVerifiedComparable(actionability);
  // A quote exists and visibly differs from the generation reference, but its
  // comparability is unproven — say so neutrally, never claim movement.
  const unverifiedQuoteDiffers =
    !quoteComparable && livePrice != null && isValidPrice(pick.price) &&
    Math.abs(livePrice - pick.price) > 0.01 &&
    pick.entry_low != null && pick.entry_high != null;

  // Upside basis: "from current price" only when the quote is proven
  // comparable; otherwise fall back to the frozen generation reference, or
  // suppress when no valid basis exists at all.
  const upsideFromCurrent = quoteComparable && priceBasis === "current";
  const upsideBasisPrice = upsideFromCurrent
    ? displayPrice
    : (isValidPrice(pick.price) ? pick.price : (priceBasis === "current" ? null : displayPrice));
  const upsidePct = computeEstimatedUpsidePct(pick.target, upsideBasisPrice);
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
      <div onClick={() => router.push(buildPickStockHref(pick.symbol, market, pick.horizon))} className="p-4 cursor-pointer flex-1">
        {/* India Daily Picks session-freshness containment (Phase 0) — prominent,
            unmissable warning ahead of everything else on the card. */}
        {isStaleOrUnknown && (
          <div className={clsx("mb-3 flex items-start gap-1.5 text-[11px] rounded-lg px-2.5 py-1.5 border",
            freshness!.freshnessStatus === "stale" ? "text-yellow-400 bg-yellow-500/10 border-yellow-500/30" : "text-gray-400 bg-dark-border/40 border-dark-border")}>
            <AlertCircle size={11} className="shrink-0 mt-0.5" />
            <span>
              {freshness!.freshnessStatus === "stale"
                ? `Price reference is stale — this pick used market data from ${referenceDateLabel}, not the latest completed NSE session.`
                : "Price freshness could not be verified."}
            </span>
          </div>
        )}
        <div className="flex items-start justify-between mb-2">
          <div>
            <span className="font-mono font-bold text-white text-lg group-hover:text-green-400 transition-colors">{pick.symbol}</span>
            <p className="text-xs text-gray-500 mt-0.5 truncate max-w-[200px]">{pick.name}</p>
          </div>
          <div className="text-right">
            <div className="text-sm font-semibold text-white">
              {displayPrice != null ? `${currency}${displayPrice.toLocaleString(locale)}` : "—"}
            </div>
            {isStaleOrUnknown ? (
              isValidPrice(pick.price) && (
                <div className="text-[10px] text-gray-500">
                  Calculation reference: {currency}{pick.price.toLocaleString(locale)} · as of {referenceDateLabel} · {freshness!.freshnessStatus === "stale" ? "stale" : "unverified"}
                </div>
              )
            ) : (
              showGenerationPriceLine && (
                <div className="text-[10px] text-gray-500">was {currency}{pick.price!.toLocaleString(locale)} at generation</div>
              )
            )}
            {isStaleOrUnknown ? (
              <div className="text-[10px] text-gray-500">Estimated upside hidden — not actionable until refreshed</div>
            ) : upsidePct != null ? (
              <div className={clsx("text-xs font-medium", upsidePct >= 0 ? "text-green-400" : "text-yellow-400")}>
                {upsidePct >= 0 ? "+" : ""}{upsidePct.toFixed(1)}% est. upside
                {upsideFromCurrent
                  ? " (from current price)"
                  : showGenerationPriceLine || priceBasis === "generation" ? " (from generation price)" : ""}
              </div>
            ) : (
              <div className="text-[10px] text-gray-500">Estimated upside unavailable</div>
            )}
          </div>
        </div>

        {/* Session-freshness containment: a stale/unknown reference already has
            its own warning above — suppressing these keeps the card from also
            claiming a "verified" before-and-after comparison against an entry
            zone that is itself calculated from an unverified reference. */}
        {verifiedOutside && !isStaleOrUnknown && (
          <div className="mb-3 flex items-center gap-1.5 text-[11px] text-yellow-400 bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-2.5 py-1.5">
            <AlertCircle size={11} className="shrink-0" />
            {actionability === "verified_above_entry_zone"
              ? "Current verified quote is above the entry zone — the original entry may no longer be actionable."
              : "Current verified quote is below the entry zone — reassess before acting."}
          </div>
        )}
        {!verifiedOutside && unverifiedQuoteDiffers && !isStaleOrUnknown && (
          <div className="mb-3 flex items-center gap-1.5 text-[11px] text-gray-400 bg-dark-border/40 rounded-lg px-2.5 py-1.5">
            <AlertCircle size={11} className="shrink-0" />
            {/* Release 12A2: closed-market guidance takes display priority
                over the technical incomparable wording for every
                non-verified state — the internal state is unchanged. */}
            {selectUnverifiedEntryZoneNote(actionability, marketOpen)}
          </div>
        )}

        <div className="mb-3">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>Signal Strength (Model Conviction)</span>
            <span className={clsx("font-medium", confidenceTextColor(pick.confidence))}>{pick.confidence}/100</span>
          </div>
          <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
            <div className={clsx("h-full rounded-full", confidenceGradientClass(pick.confidence))} style={{ width: `${pick.confidence}%` }} />
          </div>
          <p className="text-[10px] text-gray-500 mt-1">
            Signal Strength is not a guaranteed probability of profit.
          </p>
        </div>

        {isStaleOrUnknown && (
          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
            Not actionable until refreshed
          </p>
        )}
        <div className={clsx("grid grid-cols-3 gap-2 mb-3", isStaleOrUnknown && "opacity-50")}>
          <div className={clsx("rounded-lg p-2 text-center", !isStaleOrUnknown && verifiedOutside ? "bg-yellow-500/10 border border-yellow-500/30" : "bg-dark-border/40")}>
            <p className="text-[10px] text-gray-500 mb-0.5">Entry Zone{!isStaleOrUnknown && verifiedOutside && " (passed)"}</p>
            <p className={clsx("text-xs font-mono", !isStaleOrUnknown && verifiedOutside ? "text-yellow-400 line-through" : "text-white")}>
              {isStaleOrUnknown ? "—" : pick.entry_low && pick.entry_high
                ? `${currency}${pick.entry_low.toLocaleString(locale)}–${pick.entry_high.toLocaleString(locale)}`
                : `${currency}${pick.price?.toLocaleString(locale)}`}
            </p>
          </div>
          <div className="bg-green-500/10 rounded-lg p-2 text-center border border-green-500/20">
            <p className="text-[10px] text-gray-500 mb-0.5 flex items-center justify-center gap-1"><Target size={9} />Scenario Target</p>
            <p className="text-xs text-green-400 font-mono font-semibold">{isStaleOrUnknown ? "—" : `${currency}${pick.target?.toLocaleString(locale)}`}</p>
          </div>
          <div className="bg-red-500/10 rounded-lg p-2 text-center border border-red-500/20">
            <p className="text-[10px] text-gray-500 mb-0.5 flex items-center justify-center gap-1"><ShieldAlert size={9} />Stop Loss</p>
            <p className="text-xs text-red-400 font-mono font-semibold">
              {isStaleOrUnknown ? "—" : pick.stop_loss ? `${currency}${pick.stop_loss.toLocaleString(locale)}` : "—"}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs text-gray-500 mb-3">
          {pick.risk_reward && !isStaleOrUnknown && <span>R:R <span className="text-white font-semibold">1:{pick.risk_reward.toFixed(1)}</span></span>}
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

        {/* Backend-generated narrative, frozen into the payload at generation
            time — its "Target ₹X implies Y% upside" figures use the generation
            price, NOT the (possibly live-refreshed) price shown in the header.
            Always label it as such so it can't read as a second, competing
            "current" upside; and when the generation price/target were invalid
            the backend fabricates "₹0.00 / 0% upside" into the sentence, so
            suppress the raw text entirely rather than show a fabricated claim.
            India Daily Picks session-freshness containment (Phase 0 gap
            closure): pick.summary is raw backend prose that can itself
            contain "Target ₹X implies Y% upside" language computed from the
            same stale/unverified reference — the structured fields above are
            already hidden for stale/unknown picks, so this narrative must
            never be the one place that language still leaks through. */}
        {isStaleOrUnknown ? (
          <div className="bg-dark-border/30 rounded-lg p-3 border border-dark-border">
            <p className="text-xs text-gray-500">
              Analysis summary withheld because the price reference is stale or could not be verified. Refresh the pick before reviewing price targets or trade levels.
            </p>
          </div>
        ) : pick.summary && (
          hasValidGenerationBasis(pick.price, pick.target) ? (
            <div className="bg-dark-border/30 rounded-lg p-3 border border-dark-border">
              <p className="text-[10px] text-gray-500 mb-1.5">
                Generated pick summary — target and upside figures below are based on the price at generation, not the current displayed price.
              </p>
              <p className="text-xs text-gray-400 leading-relaxed">{pick.summary}</p>
            </div>
          ) : (
            <div className="bg-dark-border/30 rounded-lg p-3 border border-dark-border">
              <p className="text-xs text-gray-500">Generated target/upside context unavailable.</p>
            </div>
          )
        )}
      </div>

      {/* Action bar */}
      <div className="flex border-t border-dark-border">
        <button
          onClick={(e) => { e.stopPropagation(); if (!isStaleOrUnknown) { setFrozenPick(freezeDailyPickSnapshot(pick)); setShowPaperTrade(true); } }}
          disabled={isStaleOrUnknown}
          title={isStaleOrUnknown ? "Paper Trade is disabled until this pick's price reference is refreshed" : undefined}
          className={clsx("flex items-center gap-1.5 px-4 py-2.5 text-xs transition-colors border-r border-dark-border font-medium",
            isStaleOrUnknown ? "text-gray-600 cursor-not-allowed" : "text-brand-400 hover:text-white hover:bg-brand-500/10")}
        >
          <FlaskConical size={11} /> Paper Trade
        </button>
        {/* Paper Trading Repeat-Buy Awareness (Phase 1) — purely informational
            count of the user's own OPEN Paper Trades for this (market,
            symbol), summed across all horizons. Never shown for zero/loading/
            error/unauthenticated states (see openTradeCount prop contract in
            the parent page), never a warning color, never a new disable
            condition on the button above — repeat Buys remain fully allowed. */}
        {!!openTradeCount && openTradeCount > 0 && (
          <span
            className="flex items-center px-2.5 py-2.5 text-[11px] text-gray-400 border-r border-dark-border shrink-0"
            title="Your existing open Paper Trades for this stock in this market, across all horizons"
          >
            {openTradeCount} open trade{openTradeCount === 1 ? "" : "s"}
          </span>
        )}
        <button onClick={() => setExpanded(e => !e)}
          className="flex-1 flex items-center justify-between px-4 py-2.5 text-xs text-gray-500 hover:text-white hover:bg-dark-border/20 transition-colors">
          <span className="font-medium flex items-center gap-1.5"><Zap size={11} /> Full factor analysis</span>
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>

      {showPaperTrade && frozenPick && frozenPick.price && !isStaleOrUnknown && (
        <PaperTradeModal
          symbol={frozenPick.symbol}
          market={market}
          // Prefer the live/refreshed quote (displayPrice already resolves to
          // livePrice when valid, falling back to the frozen pick's price
          // otherwise — the same selectPriceBasis rule the card itself uses
          // above) so a BUY paper trade executes at the current market
          // price, not the frozen generation-time price. Execution price is
          // intentionally NOT frozen — only the Daily Pick's own
          // recommendation/decision context (below) is. referencePrice lets
          // the modal show that distinction to the user when the two
          // genuinely differ.
          currentPrice={displayPrice ?? frozenPick.price}
          // Phase A1.1 — everything below this point is sourced from
          // `frozenPick`, the immutable copy captured at modal-open time
          // (see the `setFrozenPick(pick)` call above), NOT the live `pick`
          // prop, which can change out from under this component on a
          // background Picks-list refetch while the modal stays open.
          referencePrice={frozenPick.price}
          signal="BUY"
          horizon={frozenPick.horizon}
          currency={currency}
          suggestedStopLoss={frozenPick.stop_loss}
          suggestedTargetPrice={frozenPick.target}
          onClose={() => setShowPaperTrade(false)}
          // Trade Postmortem Evidence Completion, Phase A1 — this is a
          // Daily Pick Buy: evidence is captured from the Daily Pick
          // payload the user actually saw (the frozen snapshot, never a
          // live/refreshed `pick` reference), never from a fresh
          // /api/predictions call, which could describe a different,
          // possibly since-changed recommendation.
          evidenceSource="DAILY_PICK"
          entryEvidenceOverride={buildEntryEvidenceFromDailyPick({
            price: frozenPick.price,
            entry_low: frozenPick.entry_low,
            entry_high: frozenPick.entry_high,
            stop_loss: frozenPick.stop_loss,
            target: frozenPick.target,
            confidence: frozenPick.confidence,
            fund_score: frozenPick.fund_score,
            sentiment: frozenPick.sentiment,
            technical_signal: frozenPick.technical_signal,
            sentiment_score: frozenPick.sentiment_score,
            reasoning: frozenPick.reasoning,
            generated_at: frozenPick.generated_at ?? null,
            horizon: frozenPick.horizon,
          })}
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
            <ScoreBar label="Model Conviction" value={pick.confidence} color={confidenceTextColor(pick.confidence)} />
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
  const [market] = useMarketPreference(["IN", "US"] as const, "IN");
  const [horizon, setHorizon] = useState<"short" | "medium" | "long">("short");
  const [highConvictionOnly, setHighConvictionOnly] = useState(false);
  // Restored for the INTEGRITY_HOLD_ACTIVE === false branch below — always
  // declared (hooks must be called unconditionally) but only read/rendered
  // when the hold is off, so it has no effect while the hold is active.
  const [showTruth, setShowTruth] = useState(false);

  const marketCfg = MARKETS.find(m => m.key === market)!;

  // Paper Trading Repeat-Buy Awareness (Phase 1) — reuses the exact same
  // `["paper-portfolio", userId]` query key / fetchPaperPortfolio contract
  // that PaperTradeModal.tsx and paper-trading/page.tsx already use, so a
  // successful Buy's existing `invalidateQueries({queryKey:
  // ["paper-portfolio"]})` (in PaperTradeModal.tsx) naturally refetches
  // this subscriber too — no new invalidation mechanism needed. One
  // page-level query serves every PickCard below; no per-card/per-symbol
  // fetch. `enabled: !!userId` means unauthenticated users issue zero
  // portfolio requests. No refetchInterval here deliberately — this is an
  // informational count, not a live position tracker; TanStack Query's
  // default staleTime (0) means this observer still triggers its own
  // mount-time fetch/refetch-on-window-focus independent of the other
  // paper-trading page's polling, which is exactly the desired behavior
  // (fresh on page view, updated after a real invalidation) without
  // inheriting that other page's 30s poll.
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const { data: portfolioForCounts } = useQuery({
    queryKey: ["paper-portfolio", userId],
    queryFn: () => fetchPaperPortfolio(userId, user?.email),
    enabled: !!userId,
  });
  const openTradeCountMap = portfolioForCounts ? buildOpenTradeCountMap(portfolioForCounts.open_trades) : null;

  const { data: rawDailyPicksData, isLoading, error: queryError } = useQuery<DailyPicksResponse>({
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

  // Live generation-progress badge. `/api/picks/daily` above only tells us
  // `generating: true/false` — the processed/total counters live on
  // `/api/picks/status`, a separate lightweight endpoint backed by the same
  // daily_picks_jobs row. Only polled while a run is actually in progress
  // (enabled gate below), so idle page loads never pay for it.
  const isGenerating = !!(rawDailyPicksData as any)?.generating;
  const { data: statusData } = useQuery<{ processed?: number | null; total?: number | null; generating?: boolean }>({
    queryKey: ["picks-status", market],
    queryFn: () => api.get(`/api/picks/status?market=${market}`).then(r => r.data),
    enabled: isGenerating,
    refetchInterval: isGenerating ? 15_000 : false,
    staleTime: 10_000, refetchOnWindowFocus: false, retry: 2,
  });

  // Cross-market retained-payload containment bypass fix. `keepPreviousData`
  // above means `rawDailyPicksData` can legitimately still be the OTHER
  // market's payload for one or more renders after `market` state has
  // already changed (the query key changed; the new query hasn't resolved
  // yet). Every `data?.xxx` read below — cards, freshness containment,
  // regime, macro, screened-from count, premarket status — must never
  // trust a payload that doesn't provably belong to the currently selected
  // market. Shadowing `data` here means every existing read downstream
  // (unchanged) is now guarded automatically; nothing after this line ever
  // sees a mismatched-market payload.
  const data = selectPayloadForMarket(rawDailyPicksData, market);
  const isMarketTransitioning = !!rawDailyPicksData && !data;

  const currency = data?.currency ?? marketCfg.currency;
  const picks = data?.picks?.[horizon] ?? [];
  const alphaForHorizon = data?.alpha_engine?.[horizon];
  // Conviction-gated publication policy (finding 2, follow-up to commit
  // 5a006498) — derived via the exported, independently-unit-tested
  // derivePublicationPolicy(); never hardcode 3/85 here. Null for a legacy
  // cached payload (pre-deployment, no publication metadata), so callers
  // render truthful, distinct wording instead of a false "Up to 3 / >=85"
  // claim over what may actually be a 6-pick legacy list. Computed BEFORE
  // `visiblePicks` (finding 1, corrective follow-up to 0f2bbed8) — the
  // stale `highConvictionOnly` toggle state must never be allowed to
  // filter/reorder a backend-published, already conviction-gated list.
  const publicationPolicy = derivePublicationPolicy(alphaForHorizon ?? null);

  // High Conviction filter — legacy-cache fallback only. `computeVisiblePicks`
  // (exported, pure) only ever applies the client-side confidence filter/sort
  // when `publicationPolicy` is null; once the backend policy is active for
  // this horizon, the toggle button itself is hidden (see its own comment
  // below), but the STALE `highConvictionOnly` boolean can still be `true`
  // from before a legacy→active transition (e.g. the same horizon regenerates
  // mid-session) — `computeVisiblePicks` ignores that stale state whenever
  // policy is active, so the authoritative backend rank order is always
  // preserved for an already conviction-gated list, regardless of toggle
  // history.
  const visiblePicks = computeVisiblePicks(picks, highConvictionOnly, publicationPolicy !== null);
  // Derived condition (finding 1) used consistently for both the filter
  // computation above and the legacy-only empty state below — a stale
  // `highConvictionOnly=true` can never present as "the legacy filter is
  // active" once `publicationPolicy` is non-null.
  const legacyHighConvictionFilterActive = publicationPolicy === null && highConvictionOnly;

  // India Daily Picks session-freshness containment (Phase 0). Scoped to IN
  // only — US Daily Picks are untouched by this workstream. Computed once
  // per render for the displayed (current-horizon, current-filter) picks,
  // keyed by symbol so PickCard and the batch notice below use the exact
  // same evaluation.
  const freshnessBySymbol: Record<string, FreshnessResult> | null =
    market === "IN"
      ? (() => {
          const now = new Date();
          const map: Record<string, FreshnessResult> = {};
          for (const pick of visiblePicks) map[pick.symbol] = evaluateSessionFreshness(pick.generation_reference_as_of, now);
          return map;
        })()
      : null;
  const freshnessCounts = freshnessBySymbol
    ? Object.values(freshnessBySymbol).reduce(
        (acc, f) => { acc[f.freshnessStatus]++; return acc; },
        { fresh: 0, stale: 0, unknown: 0 },
      )
    : null;
  // Product Integrity Workstream #001: already explicitly converted to
  // this market's own timezone (correct, predating this workstream) — the
  // only fix needed here is disclosing WHICH timezone, via tzLabel, since
  // the global header clock and this label can legitimately show two
  // different timezones (e.g. ET for the US tab vs IST for the header) and
  // previously neither was labeled, making the gap look like a defect.
  const generatedAt = data?.generated_at
    ? `${new Date(data.generated_at).toLocaleString(marketCfg.locale, {
        timeZone: marketCfg.tz, day: "2-digit", month: "short",
        year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true,
      })} ${marketCfg.tzLabel}` : null;
  // Premarket Review's own actual completion timestamp — never fabricated,
  // never inferred from the 6:00 AM ET schedule; absent whenever the
  // backend hasn't written premarket_finalized_at (no run yet, or a
  // skipped/failed outcome that intentionally leaves it null).
  const premarketFinalizedAt = data?.premarket_finalized_at
    ? `${new Date(data.premarket_finalized_at).toLocaleString(marketCfg.locale, {
        timeZone: marketCfg.tz, day: "2-digit", month: "short",
        year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true,
      })} ${marketCfg.tzLabel}` : null;

  return (
    <div className="space-y-6">
      <UnsupportedMarketNotice supported={["IN", "US"]} />

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
          {/* Original opt-in "Real Accuracy" control — withheld while
              INTEGRITY_HOLD_ACTIVE is true (see ValidationIntegrityHold.tsx),
              restored verbatim once it's false. */}
          {!INTEGRITY_HOLD_ACTIVE && (
            <button onClick={() => setShowTruth(v => !v)}
              className={clsx("flex items-center gap-1.5 text-xs px-3 py-2 rounded-lg border transition-colors",
                showTruth ? "bg-blue-500/20 border-blue-500/40 text-blue-300" : "bg-dark-card border-dark-border text-gray-400 hover:text-white")}>
              <CheckCircle size={12} /> {showTruth ? "Hide" : "Show"} Real Accuracy
            </button>
          )}
          {generatedAt && (() => {
            const ageHours = data?.generated_at ? Math.floor((Date.now() - new Date(data.generated_at).getTime()) / 3_600_000) : 0;
            const isStale = ageHours >= 4;
            // US shows this as the Pre-Open BASE snapshot explicitly — the
            // separate Premarket Review stage (badge below) has its own
            // schedule and its own actual completion timestamp, never
            // conflated with this one. IN has no second stage, so its
            // label is unchanged.
            const label = market === "US" ? "Base generated" : "Updated";
            return (
              // w-full on mobile (its own row instead of squeezing next to
              // the other badges and overflowing the viewport — flex-wrap on
              // the parent only wraps whole items, it doesn't shrink this
              // one's own content-driven width) + items-start/break-words so
              // the timestamp text itself wraps onto a second line rather
              // than forcing horizontal scroll.
              <div className={clsx("flex items-start gap-1.5 text-xs bg-dark-card border rounded-lg px-3 py-2 w-full sm:w-auto",
                isStale ? "border-yellow-500/40 text-yellow-400" : "border-dark-border text-gray-500")}>
                <Clock size={12} className="shrink-0 mt-0.5" />
                <span className="break-words">{label} {generatedAt}{isStale ? ` · ${ageHours}h ago` : ""}</span>
              </div>
            );
          })()}
          {isGenerating && (() => {
            const processed = statusData?.processed;
            const total = statusData?.total;
            // Counters are absent until the backend's counting phase starts
            // (e.g. early in a run) — never fabricate a fraction from
            // partial data, fall back to a plain "Updating…" badge instead.
            const hasCounts = typeof processed === "number" && typeof total === "number" && total > 0;
            const pct = hasCounts ? Math.round((processed! / total!) * 100) : null;
            return (
              <div className="flex items-center gap-1.5 text-xs bg-dark-card border border-blue-500/40 text-blue-300 rounded-lg px-3 py-2 w-full sm:w-auto">
                <RefreshCw size={12} className="shrink-0 animate-spin" />
                <span className="break-words">
                  Updating{hasCounts ? ` — ${processed}/${total} (~${pct}%)` : "…"}
                </span>
              </div>
            );
          })()}
          {/* Premarket Review stage badge — a separate stage from Pre-Open
              base generation above, rendered unconditionally for US (not
              gated on data?.premarket_status being truthy) so "no finalizer
              run yet today" has a truthful, visible "Pending" state instead
              of silently showing nothing. Absent entirely on IN, which has
              no premarket-review stage. The actual premarket_finalized_at
              timestamp is shown only for outcomes where the backend
              actually completed a run (completed/completed_with_limited_
              premarket_data) — never fabricated for pending/skipped/failed. */}
          {market === "US" && (() => {
            const effectiveStatus = data?.premarket_status ?? "pending";
            const isCompletedOutcome = effectiveStatus === "completed"
              || effectiveStatus === "completed_with_limited_premarket_data";
            // Only "pending" is a genuine future-looking state — skipped/failed
            // are terminal outcomes for today's window, so appending a future
            // schedule label there would misleadingly imply an imminent retry.
            const isPending = !isCompletedOutcome && effectiveStatus !== "skipped"
              && effectiveStatus !== "failed";
            return (
              <span className={clsx("shrink-0 whitespace-nowrap text-xs bg-dark-card border rounded-lg px-3 py-2",
                PREMARKET_STATUS_CLASS[effectiveStatus] ?? "border-dark-border text-gray-500")}>
                {PREMARKET_STATUS_LABEL[effectiveStatus] ?? PREMARKET_STATUS_LABEL.pending}
                {isCompletedOutcome && premarketFinalizedAt
                  ? ` · ${premarketFinalizedAt}`
                  : isPending
                    ? ` · ${PREMARKET_REVIEW_SCHEDULE_LABEL}`
                    : ""}
              </span>
            );
          })()}
        </div>
      </div>
      <p className="text-sm text-gray-400">
        {formatPublicationPolicyCopy(publicationPolicy)}
        {" · "}{market === "US" ? "base picks generated" : "generated daily"} at {marketCfg.genTime}
        {market === "US" ? ` · Premarket review ${PREMARKET_REVIEW_SCHEDULE_LABEL.toLowerCase()}, after today's base picks complete` : ""}
        {/* Release 12B coverage truthfulness: real returned count only, never
            a hardcoded number, and never a full-exchange claim. */}
        {data?.screened_from
          ? ` · screened from ${data.screened_from.toLocaleString()} eligible ${market === "IN" ? "NSE" : "US"} stocks in the current quality-filtered universe`
          : ""}
      </p>
      <p className="text-[11px] text-gray-500">
        Coverage is a screened liquid-quality universe, not all {market === "IN" ? "NSE" : "US"}-listed stocks.
      </p>
      {/* DP-035/DP-036: surface the backend's own truthful conviction_semantic
          caveat (not a hardcoded frontend claim) whenever the conviction-gated
          policy is active. DP-035 found the win-rate correlation at this
          threshold is not yet confirmed by a matching backtest for medium/long
          horizon (thin sample). DP-036 ran a real, full-population backtest
          against the correct gate field for SHORT horizon
          specifically and found a definitive negative result — no meaningful
          win-rate lift — so the backend now returns a stronger, distinguishable
          "tested, no lift found" string for short horizon only; medium/long keep
          DP-035's "not yet confirmed" wording unchanged. This component renders
          whichever string the backend sends, per-horizon — no per-horizon
          branching lives here. Absent entirely for a legacy/pre-policy payload
          (publicationPolicy === null), same as the rest of this dynamic copy. */}
      {publicationPolicy && (
        <p className="text-[11px] text-gray-500">{publicationPolicy.semantic}</p>
      )}
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
              <span
                tabIndex={0}
                role="note"
                aria-label={
                  alphaForHorizon.meta_model
                    ? "Meta-model active. Historical market prediction outcomes contributed to ranking this Daily Picks batch. The meta-model is updated only through controlled batch processing. No personal Portfolio, Watchlist, Paper Trade, alert, or user-behaviour data is used."
                    : "IC-weighted ranking active. The trained meta-model was not applied to this Daily Picks batch. Ranking uses validated IC-weighted factor signals instead. No personal Portfolio, Watchlist, Paper Trade, alert, or user-behaviour data is used."
                }
                title={
                  alphaForHorizon.meta_model
                    ? "Historical market prediction outcomes contributed to ranking this Daily Picks batch. The meta-model is updated only through controlled batch processing. No personal Portfolio, Watchlist, Paper Trade, alert, or user-behaviour data is used."
                    : "The trained meta-model was not applied to this Daily Picks batch. Ranking uses validated IC-weighted factor signals instead. No personal Portfolio, Watchlist, Paper Trade, alert, or user-behaviour data is used."
                }
                className={clsx("px-2 py-0.5 rounded font-semibold cursor-help focus:outline-none focus-visible:ring-1 focus-visible:ring-gray-400",
                  alphaForHorizon.meta_model ? "text-green-400 bg-green-500/10" : "text-gray-400 bg-dark-border")}>
                {alphaForHorizon.meta_model ? "✓ Meta-model active" : "IC-weighted ranking active"}
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
      <div className="flex flex-wrap items-center gap-2">
        {HORIZONS.map(({ key, label, sub }) => (
          <button key={key} onClick={() => setHorizon(key)}
            className={clsx("px-4 py-2.5 rounded-xl text-sm font-medium transition-all",
              horizon === key ? "bg-brand-500 text-white shadow-lg shadow-brand-500/20"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white")}>
            {label}
            <span className={clsx("ml-1.5 text-xs", horizon === key ? "text-blue-200" : "text-gray-600")}>({sub})</span>
          </button>
        ))}
        {/* finding 3 (follow-up to commit 5a006498): once the backend
            conviction-gated publication policy is active for this horizon,
            every published pick already clears the same >=85/100 Model
            Conviction bar server-side (n_published <= 3, all >= threshold)
            — a client-side "High Conviction Only >=85%" toggle on top of
            that would be redundant at best and, worse, implies an
            optional/probability-like filter layered on an already-gated
            list. Hidden (not merely relabeled) whenever `publicationPolicy`
            is active; retained, working exactly as before, for a legacy
            cached payload (`publicationPolicy` null) where the underlying
            list can still contain up to 6 picks not conviction-filtered by
            the backend. */}
        {!publicationPolicy && (
          <button onClick={() => setHighConvictionOnly(v => !v)}
            title={`Show only picks with Model Conviction ≥ ${HIGH_CONVICTION_THRESHOLD}/100, sorted highest first`}
            className={clsx("px-4 py-2.5 rounded-xl text-sm font-medium transition-all flex items-center gap-1.5 ml-auto",
              highConvictionOnly ? "bg-emerald-500 text-white shadow-lg shadow-emerald-500/20"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white")}>
            <Zap size={14} />
            High Conviction Only
            <span className={clsx("text-xs", highConvictionOnly ? "text-emerald-100" : "text-gray-600")}>(≥{HIGH_CONVICTION_THRESHOLD}/100)</span>
          </button>
        )}
      </div>

      {/* Product Integrity Workstream — Phase GPI-0, true two-branch hold.
          true  -> ValidationIntegrityHold only; BacktestPanel/
                   LivePerformanceTracker appear in neither branch's JSX
                   here, so their useQuery calls cannot mount/fire — not a
                   CSS-only hide.
          false -> exact pre-containment opt-in interface: the "Show Real
                   Accuracy" control (restored above) plus both panels,
                   each still gated behind the user's own showTruth click,
                   unchanged from before this workstream. See
                   ValidationIntegrityHold.tsx for the removal criteria that
                   must all be true before flipping this flag. */}
      {INTEGRITY_HOLD_ACTIVE ? (
        <ValidationIntegrityHold />
      ) : (
        <>
          {/* Priority 1 + 2: Backtest truth panel (toggle) */}
          {showTruth && <BacktestPanel horizon={horizon} benchmarkLabel={market === "IN" ? "Nifty" : "S&P 500"} />}

          {/* Priority 3: Live performance tracker (toggle) */}
          {showTruth && <LivePerformanceTracker horizon={horizon} currency={currency} locale={marketCfg.locale} benchmarkLabel={market === "IN" ? "Nifty" : "S&P 500"} />}
        </>
      )}

      {/* Historical accuracy — independent of the hold above, always shown
          when data exists (see HistoricalTrackRecordSummary's own comment
          for why this doesn't share GPI-0's defects). */}
      <HistoricalTrackRecordSummary
        horizon={horizon}
        entries={data?.historical_track_record?.[horizon]}
        benchmarkLabel={market === "IN" ? "Nifty" : "S&P 500"}
      />

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

      {/* India Daily Picks session-freshness containment (Phase 0) — page-level
          notice when any currently-displayed India pick is stale or unknown. */}
      {freshnessCounts && (freshnessCounts.stale > 0 || freshnessCounts.unknown > 0) && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle size={18} className="text-yellow-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-yellow-300">
              Some India Daily Picks use unverified or outdated price references. Review the freshness status on each card before using trade levels.
            </p>
            <p className="text-xs text-yellow-400/70 mt-0.5">
              {freshnessCounts.fresh} fresh · {freshnessCounts.stale} stale · {freshnessCounts.unknown} unknown
            </p>
          </div>
        </div>
      )}

      {/* US Daily Picks generation-reliability incident (2026-07-22) —
          failure-safe publication contract, Phase 2. `data` (when present)
          is now ALWAYS a genuinely successful payload — never an empty
          error stand-in — but it may be from an EARLIER session than
          today's if today's generation attempt failed or hasn't completed
          yet. This banner is the one place that says so explicitly,
          distinct from — and must never be confused with — a genuine
          "generation completed, zero qualifying picks today" outcome
          (which keeps generated_at as TODAY and shows the existing
          "No BUY signals found today" copy below, unchanged). */}
      {data?.stale && data?.generated_at && !isLoading && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle size={18} className="text-amber-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-amber-300">
              Today&apos;s {market} generation is delayed. Showing the last successfully completed{" "}
              {market} picks from {data.last_successful_session_date ?? new Date(data.generated_at).toLocaleDateString()}.
            </p>
            {data.last_attempt_status === "failed" && (
              <p className="text-xs text-amber-400/70 mt-0.5">
                Today&apos;s attempt failed{data.last_attempt_error_category ? ` (${data.last_attempt_error_category})` : ""} and is expected to retry automatically.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Picks grid */}
      {isLoading || isMarketTransitioning ? (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(5)].map((_, i) => <div key={i} className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-72" />)}
        </div>
      ) : visiblePicks.length > 0 ? (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {visiblePicks.map((pick, i) => <PickCard key={pick.symbol} pick={pick} rank={i + 1} market={market} currency={currency} locale={marketCfg.locale} freshness={freshnessBySymbol?.[pick.symbol]} openTradeCount={openTradeCountMap?.get(openTradeCountKey(market, pick.symbol))} />)}
        </div>
      ) : legacyHighConvictionFilterActive && picks.length > 0 ? (
        // Distinct from the "no BUY signals at all" empty state below — real
        // picks exist for this horizon, just none clear the confidence bar.
        // Uses the same derived `legacyHighConvictionFilterActive` condition
        // as `visiblePicks` above (finding 1) — a stale `highConvictionOnly`
        // can never trigger this legacy-only empty state once
        // `publicationPolicy` is active.
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <Zap size={40} className="text-gray-600 mb-4" />
          <h3 className="text-lg font-semibold text-gray-300 mb-2">No picks ≥{HIGH_CONVICTION_THRESHOLD}/100 Model Conviction right now</h3>
          <p className="text-sm text-gray-500 max-w-sm">
            {picks.length} {picks.length === 1 ? "pick" : "picks"} available in {HORIZONS.find(h => h.key === horizon)?.label.toLowerCase()}, but none reach {HIGH_CONVICTION_THRESHOLD}/100 Model Conviction today. Try another horizon or turn off the filter.
          </p>
          <button onClick={() => setHighConvictionOnly(false)}
            className="mt-4 px-4 py-2 rounded-lg text-sm font-medium bg-dark-card border border-dark-border text-gray-300 hover:text-white transition-all">
            Show all {picks.length} picks
          </button>
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
                {data?.generated_at
                  ? (data?.stale ? "No BUY signals in the last completed run" : "Generation completed — no stocks met today's qualification criteria")
                  : "Picks not yet generated"}
              </h3>
              <p className="text-sm text-gray-500 max-w-sm">
                {data?.generated_at
                  ? (data?.stale
                      ? `The last successfully completed run (${data.last_successful_session_date ?? "an earlier date"}) found no strong BUY signals across ${market === "IN" ? "NSE" : "US markets"}. Today's run may still be in progress or delayed.`
                      : `The AI didn't find strong BUY signals across ${market === "IN" ? "NSE" : "US markets"} today. Market conditions may be weak — check back tomorrow.`)
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
