import axios from "axios";
import { supabase } from "@/lib/supabase";

export const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  timeout: 120000,  // 120s — Render prediction can take 90s+ under load
});

// Security Remediation Sprint #001 (fixes the Mini Security Audit's C-1
// finding): the backend's Portfolio/Watchlist/Alerts/Terms-Acceptance
// endpoints now require a verified Supabase JWT and check it matches the
// `user_id` in the request — previously no token was ever sent. Attaching it
// here, once, on the shared axios instance covers every caller (portfolio,
// watchlist, alerts pages, acceptTerms/getTermsStatus, importPortfolioHoldings)
// without touching each call site individually.
api.interceptors.request.use(async (config) => {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export type Market = "US" | "IN";
export type Horizon = "short" | "medium" | "long";
export type Signal = "BUY" | "HOLD" | "SELL";

export interface StockQuote {
  symbol: string;
  market: Market;
  price: number;
  prev_close: number;
  change: number;
  change_pct: number;
  volume: number;
  market_cap: number;
  fifty_two_week_high: number;
  fifty_two_week_low: number;
  open?: number;
  high?: number;
  low?: number;
  company_name?: string;
  // Release 12A quote provenance (additive; absent on older cached payloads)
  quote_source?: string | null;
  quote_price_basis?: string | null;
  quote_timestamp?: string | null;
}

export interface OHLCVBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// Recommendation Consolidation Intelligence (Epic 005) — the additive,
// read-only "Evidence Summary" field. Defined per Sprint #011's contract
// spec: every field below is the exact backend shape (no frontend-invented
// replacement), `contract_version` is the only field this client currently
// understands (SUPPORTED_RCI_CONTRACT_VERSION), and the whole object is
// optional/absent whenever the Railway flag is disabled, the backend's own
// error-isolation path omits it, or an older API response predates RCI.
// `coverage_notices` / `unresolved_risk_flags` / `material_warnings` are
// confirmed (Sprint #011, direct pipeline execution) to be bare strings
// with NO stable identifier — unlike `conflicts`, which has `conflict_id`.
// Do not add any text-matching de-duplication beyond a single response.
export const SUPPORTED_RCI_CONTRACT_VERSION = 1;

export type RciThesisState = "supported" | "mixed" | "conflicted" | "insufficient_evidence";
export type RciExplanationConfidenceCategory = "high" | "moderate" | "low";

export interface RciConflict {
  conflict_id: string;
  headline: string;
  narrative: string;
  supporting_engines: string[];
  opposing_engines: string[];
  severity: string;
}

export interface RecommendationConsolidation {
  contract_version: number;
  snapshot_id: string;
  computed_at: string;
  is_snapshot: boolean;
  thesis_state: RciThesisState;
  engine_agreement: string;
  conflicts: RciConflict[];
  coverage_notices: string[];
  supporting_evidence: string[];
  opposing_evidence: string[];
  active_gates: string[];
  unresolved_risk_flags: string[];
  material_warnings: string[];
  evidence_completeness_pct: number | null;
  explanation_confidence_category: RciExplanationConfidenceCategory;
  narrative: string;
  engine_versions_used: Record<string, string | null>;
}

// AI Equity Research Analyst — Phase 2's additive, read-only "Research
// Report" field (Epic 008B). Defined per the Phase 3 UI/Copy spec: every
// field below is the exact backend shape from report_assembler.py (no
// frontend-invented replacement), and the whole object is optional/absent
// whenever RESEARCH_ANALYST_V2_ENABLED is disabled in Railway (default),
// the backend's own fail-open composer omits it on any failure, or an
// older API response predates Phase 2. A section's `entries` and `text`
// are not mutually exclusive states to branch on with an if/else — render
// `entries` when present and ALWAYS render `text` too when it is non-null
// (e.g. `disclaimer` has both); only `entries: []` combined with a
// non-null `text` is the honest-absence case.
export const SUPPORTED_RESEARCH_REPORT_CONTRACT_VERSION = "1.0";

export type ResearchReportSectionId =
  | "executive_snapshot"
  | "current_signal_context"
  | "key_evidence"
  | "risks_and_invalidation"
  | "data_availability"
  | "disclaimer";

export interface ResearchReportEntry {
  label: string;
  value: unknown;
  evidence_ids: string[];
  provenance_ref: string;
  owner?: string;
  display_value?: string;
}

export interface ResearchReportSection {
  section_id: ResearchReportSectionId;
  title: string;
  entries: ResearchReportEntry[];
  evidence_ids: string[];
  text: string | null;
}

export interface ResearchReport {
  report_contract_version: string;
  snapshot_id: string;
  snapshot_hash: string;
  scope: { symbol: string; market: Market; exchange: string; currency: string; horizon: Horizon };
  generated_at: string | null;
  data_as_of: string | null;
  overall_status: "COMPLETE" | "PARTIAL" | "STALE" | "UNAVAILABLE" | "INVALID";
  sections: ResearchReportSection[];
  disclosures: string[];
}

export interface Prediction {
  symbol: string;
  market: Market;
  horizon: Horizon;
  signal: Signal;
  confidence: number;
  current_price: number;
  target_price: number;
  generated_at?: string;
  reasoning: { indicator: string; signal: string; reason: string }[];
  technical: { overall: Signal; rsi: number; macd_diff: number };
  fundamental_score: { score: number; reasons: string[] };
  sentiment_score: { score: number; label: string; bullish: number; bearish: number };
  market_regime?: { trend: string; score_adj: number; reason: string };
  // AI-suggested stop-loss/take-profit/entry-range levels — present on the
  // live /api/predictions response but not previously declared here (every
  // call site read it via an `as any` cast). Typed properly since Stage 2
  // needs it to build entry-evidence payloads without an unsafe cast.
  trade_levels?: { stop_loss?: number | null; take_profit?: number | null; entry_low?: number | null; entry_high?: number | null };
  // Optional per Sprint #011 §3A — absent today in every production response
  // since RCI_LIVE_STOCK_ANALYSIS_ENABLED is disabled in Railway.
  recommendation_consolidation?: RecommendationConsolidation;
  // Optional per Epic 008B Phase 2/3 — absent today in every production
  // response since RESEARCH_ANALYST_V2_ENABLED is disabled in Railway.
  research_report?: ResearchReport;
}

export interface NewsArticle {
  title: string;
  source: string;
  url: string;
  published_at: string;
  description: string;
  sentiment?: { label: string; score: number };
  // Backend-computed freshness verdict (Wave 0C) — the single source of
  // truth for whether this article counts toward current sentiment.
  // Optional: absent on payloads cached before the annotation shipped.
  sentiment_eligible?: boolean;
  eligibility_reason?: "fresh" | "stale" | "invalid_date" | "future_date";
  // Backend-computed company-relevance verdict (Wave 0D1) — fresh AND
  // primarily about this company. Optional: absent on older cached payloads.
  company_sentiment_eligible?: boolean;
  relevance_class?: string;
  relevance_reason?: string;
}

// ─── API calls ────────────────────────────────────────────────

// Per Sprint #011 §3A/§3C: absent, null, malformed, incomplete, or an
// unsupported future contract version must all degrade to "do not render" —
// never an error state. `narrative` is treated as the one load-bearing
// field; its absence (or a non-array on any evidence list) means the object
// is unusable. This is the ONLY place that decides whether Evidence Summary
// renders at all — callers never need their own absence/validity checks.
export function getValidRecommendationConsolidation(
  prediction: Prediction | null | undefined,
): RecommendationConsolidation | null {
  const rci = prediction?.recommendation_consolidation;
  if (!rci || typeof rci !== "object") return null;
  if (rci.contract_version !== SUPPORTED_RCI_CONTRACT_VERSION) return null;
  if (typeof rci.narrative !== "string" || !rci.narrative) return null;
  const isStringArray = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");
  if (
    !isStringArray(rci.coverage_notices) ||
    !isStringArray(rci.supporting_evidence) ||
    !isStringArray(rci.opposing_evidence) ||
    !isStringArray(rci.active_gates) ||
    !isStringArray(rci.unresolved_risk_flags) ||
    !isStringArray(rci.material_warnings) ||
    !Array.isArray(rci.conflicts)
  ) {
    return null;
  }
  return rci;
}

// Phase 3 UI/Copy spec's single decision point (mirrors
// getValidRecommendationConsolidation exactly): absent, null, malformed,
// incomplete, or an unsupported future contract version must all degrade
// to "do not render" — never an error state. `sections` is the one
// load-bearing field; a report with a non-array/empty `sections` or the
// wrong `report_contract_version` is unusable. Callers never need their
// own absence/validity checks.
export function getValidResearchReport(
  prediction: Prediction | null | undefined,
): ResearchReport | null {
  const report = prediction?.research_report;
  if (!report || typeof report !== "object") return null;
  if (report.report_contract_version !== SUPPORTED_RESEARCH_REPORT_CONTRACT_VERSION) return null;
  if (!Array.isArray(report.sections) || report.sections.length === 0) return null;
  const isValidSection = (s: unknown): s is ResearchReportSection =>
    !!s && typeof s === "object" &&
    typeof (s as ResearchReportSection).section_id === "string" &&
    Array.isArray((s as ResearchReportSection).entries) &&
    Array.isArray((s as ResearchReportSection).evidence_ids);
  if (!report.sections.every(isValidSection)) return null;
  return report;
}

export const fetchQuote = (symbol: string, market: Market) =>
  api.get<StockQuote>(`/api/stocks/quote/${symbol}`, { params: { market } }).then((r) => r.data);

// Raw, unmodified passthrough of screener.in's (IN) / yfinance's (US) own
// sector/industry classification — see sectorDisplay.ts for why both
// fields are needed (screener.in's own peer-breadcrumb sometimes files a
// stock under a broad sector like "Consumer Services" while `industry`
// says something more specific and useful, e.g. "Wellness").
export interface RawSectorInfo {
  sector: string | null;
  industry: string | null;
}

// Batch sector lookup for Portfolio's allocation view — sourced from the
// nightly-refreshed stock_fundamentals_cache table, NOT the per-symbol
// signal/prediction pipeline (fetchSignalSummary's `sector` field). A
// portfolio's sector breakdown used to be gated on every holding's full AI
// signal resolving (staggered, and slow on a cold cache), so the
// allocation chart could sit on a misleading "Loading… 100%" bar for as
// long as that took even though this data has been sitting in a cache
// table the whole time. One call for the whole holdings list, resolves in
// one page-load's round trip regardless of portfolio size.
export const fetchSectorsBatch = (symbols: string[], market: Market) =>
  symbols.length === 0
    ? Promise.resolve({} as Record<string, RawSectorInfo>)
    : api
        .get<{ sectors: Record<string, RawSectorInfo> }>("/api/stocks/sectors", {
          params: { symbols: symbols.join(","), market },
        })
        .then((r) => r.data.sectors);

export const fetchOHLCV = (symbol: string, market: Market, period = "1y", interval = "1d") =>
  api
    .get<{ data: OHLCVBar[] }>(`/api/stocks/ohlcv/${symbol}`, { params: { market, period, interval } })
    .then((r) => r.data);

// Typed error for anything a prediction-backed endpoint can fail with —
// callers (Stock Detail) branch on `.code`/`.status` to render a distinct
// unsupported-symbol vs temporarily-unavailable state, instead of the
// generic Error a bare `throw new Error(...)` gave no way to distinguish.
export class PredictionError extends Error {
  code: string;
  status: number;
  symbol?: string;
  market?: string;
  constructor(message: string, opts: { code: string; status: number; symbol?: string; market?: string }) {
    super(message);
    this.name = "PredictionError";
    this.code = opts.code;
    this.status = opts.status;
    this.symbol = opts.symbol;
    this.market = opts.market;
  }
}

const VALID_SIGNALS = new Set(["BUY", "HOLD", "SELL"]);

// Runtime guard for a genuinely successful, fully-populated Prediction body
// — not just an HTTP 200. Backend cache entries that failed (unsupported
// symbol, provider outage, insufficient history) used to be served as a
// plain 200 `{error: "..."}` body, indistinguishable at the transport
// level from a real prediction; a blind `as Prediction` cast let that
// object's `undefined` signal/confidence fall through to a fabricated
// "HOLD" with a blank confidence bar on Stock Detail. This is the single
// point that decides whether a 200 body counts as a valid Prediction.
export function isValidPrediction(body: unknown): body is Prediction {
  if (!body || typeof body !== "object") return false;
  const b = body as Record<string, unknown>;
  if ("error" in b) return false;
  if (typeof b.signal !== "string" || !VALID_SIGNALS.has(b.signal)) return false;
  if (typeof b.confidence !== "number" || !Number.isFinite(b.confidence)) return false;
  if (typeof b.symbol !== "string" || !b.symbol) return false;
  if (typeof b.market !== "string" || !b.market) return false;
  if (typeof b.horizon !== "string" || !b.horizon) return false;
  if (typeof b.current_price !== "number" || !Number.isFinite(b.current_price)) return false;
  return true;
}

// SignalSummary's own contract already treats `signal`/`confidence: null`
// as a legitimate "nothing cached yet" state (Portfolio's non-blocking
// dash) — so this only rejects the shapes that were never valid at all
// (an `error` body, or a missing/malformed identifying field), not a
// genuinely-null-but-present signal.
export function isValidSignalSummaryBody(body: unknown): body is SignalSummary {
  if (!body || typeof body !== "object") return false;
  const b = body as Record<string, unknown>;
  if ("error" in b) return false;
  if (typeof b.symbol !== "string" || !b.symbol) return false;
  if (typeof b.market !== "string" || !b.market) return false;
  if (typeof b.horizon !== "string" || !b.horizon) return false;
  if (b.signal !== null && typeof b.signal !== "string") return false;
  if (b.confidence !== null && typeof b.confidence !== "number") return false;
  return true;
}

// Shared 202-poll loop for the prediction-backed endpoints (/api/predictions/
// {symbol} and its Sprint 011 signal-only variant) — one polling contract,
// not two copies that could drift.
const pollPredictionEndpoint = async <T>(
  url: string,
  params: { market: Market; horizon: Horizon },
  onComputing: (() => void) | undefined,
  validate: (body: unknown) => body is T,
): Promise<T> => {
  // Poll up to 180 s (36 × 5 s) for background computation to complete
  for (let attempt = 0; attempt < 36; attempt++) {
    let res;
    try {
      res = await api.get<T | { status: string; retry_after?: number }>(url, {
        params,
        validateStatus: (s) => s === 200 || s === 202,
      });
    } catch (e: any) {
      // A structured 404/503 (SYMBOL_NOT_SUPPORTED / DATA_PROVIDER_UNAVAILABLE)
      // fails axios's validateStatus above and lands here as a thrown error
      // — surface it as a typed PredictionError so the caller can tell
      // "unsupported symbol" apart from "temporarily unavailable" instead
      // of both collapsing into the same generic failure.
      const respErr = e?.response?.data?.error;
      if (respErr && typeof respErr === "object") {
        throw new PredictionError(respErr.message || "Prediction unavailable.", {
          code: respErr.code || "PREDICTION_UNAVAILABLE",
          status: e.response.status,
          symbol: respErr.symbol,
          market: respErr.market,
        });
      }
      throw e;
    }
    if (res.status === 200) {
      if (!validate(res.data)) {
        // Defensive fallback — should no longer be reachable now that the
        // backend translates cached errors to a non-200 above, but a 200
        // body that still fails runtime validation (malformed/incomplete)
        // must never be treated as a real prediction either.
        const bodyErr = (res.data as any)?.error;
        throw new PredictionError(
          typeof bodyErr === "string" ? bodyErr : "Prediction data is invalid or incomplete.",
          { code: "INVALID_PREDICTION_RESPONSE", status: 200 },
        );
      }
      return res.data;
    }
    // Check for error field returned in a 202 response body
    if ((res.data as any)?.error) throw new Error((res.data as any).error);
    // 202 = computing in background — notify caller and wait
    if (attempt === 0) onComputing?.();
    const serverDelay = (res.data as { retry_after?: number }).retry_after ?? 5;
    // Most predictions finish in 3-8s server-side, but every poll attempt
    // waited the server's full suggested 5s regardless of how close it
    // actually was — pure dead time on top of real compute time, which
    // compounds badly across staggered batches on a large portfolio. Poll
    // faster for the first few attempts; fall back to the server's own
    // pacing afterward in case something is genuinely slower than typical.
    const delay = (attempt < 4 ? Math.min(2, serverDelay) : serverDelay) * 1000;
    await new Promise((r) => setTimeout(r, delay));
  }
  throw new Error("Prediction timed out after 120 s");
};

export const fetchPrediction = (
  symbol: string,
  market: Market,
  horizon: Horizon,
  onComputing?: () => void,
): Promise<Prediction> =>
  pollPredictionEndpoint<Prediction>(`/api/predictions/${symbol}`, { market, horizon }, onComputing, isValidPrediction);

// Sprint 011 (§20.1) — badge-only prediction summary. Served from the same
// backend prediction cache as fetchPrediction (same signal/confidence values,
// same 202-then-poll contract), but the payload is just these five fields, so
// pages that only render a SignalBadge (Portfolio's per-holding column) don't
// pull the full multi-engine payload per row.
export interface SignalSummary {
  symbol: string;
  market: Market;
  horizon: Horizon;
  signal: string | null;
  confidence: number | null;
  // Additive (Portfolio's sector-wise allocation view) — reused from the
  // same prediction's already-computed quality_factors.sector, no new
  // provider call. Null when unavailable.
  sector?: string | null;
}

export const fetchSignalSummary = (
  symbol: string,
  market: Market,
  horizon: Horizon,
): Promise<SignalSummary> =>
  pollPredictionEndpoint<SignalSummary>(`/api/predictions/${symbol}/signal`, { market, horizon }, undefined, isValidSignalSummaryBody);

export interface CachedSignal {
  signal: string | null;
  confidence: number | null;
  // false = nothing computed for this symbol/market/horizon yet — a real,
  // possibly-permanent state for a rarely-viewed holding, not "still
  // loading." Callers must render this as calm/non-blocking, never as an
  // endless spinner.
  cached: boolean;
}

// Cache-only counterpart to fetchSignalSummary, for Portfolio specifically.
// fetchSignalSummary's 202-then-poll contract is right for a single stock
// page (compute it, the user is looking at exactly this one symbol) but
// wrong for a holdings list: calling it once per holding meant every
// holding could independently kick off a real prediction computation and
// poll for up to 3 minutes on a cold cache, long after prices/P&L had
// already resolved — the whole page felt stuck. This reads the exact same
// server-side cache and NEVER triggers a computation; a symbol with
// nothing cached comes back immediately with `cached: false` instead of
// a 202/pending state. One request for the whole holdings list, not one
// per holding.
export const fetchCachedSignalsBatch = (
  symbols: string[],
  market: Market,
  horizon: Horizon,
): Promise<Record<string, CachedSignal>> =>
  symbols.length === 0
    ? Promise.resolve({})
    : api
        .get<{ signals: Record<string, CachedSignal> }>("/api/predictions/signals/cached-batch", {
          params: { symbols: symbols.join(","), market, horizon },
        })
        .then((r) => r.data.signals);

export const fetchNews = (symbol: string, market: Market) =>
  api
    .get<{
      articles: NewsArticle[];
      total_article_count?: number;
      eligible_article_count?: number;
      historical_article_count?: number;
      company_specific_article_count?: number;
      contextual_article_count?: number;
      current_company_news_event_count?: number;
      duplicate_company_news_article_count?: number;
    }>(`/api/news/${symbol}`, { params: { market } })
    .then((r) => r.data);

type Mover = { symbol: string; price: number; change_pct: number; name?: string };
export const fetchTopMovers = (market: Market) =>
  api
    .get<{ movers: Mover[]; gainers: Mover[]; losers: Mover[]; market_open: boolean }>("/api/screener/top-movers", {
      params: { market },
    })
    .then((r) => r.data);

export const searchStocks = (q: string, market: Market | "ALL" = "ALL") =>
  api.get<{ symbol: string; name: string }[]>("/api/stocks/search", { params: { q, market } }).then((r) => r.data);

export interface IndexQuote {
  symbol: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  change_pts: number | null;
}

export const fetchIndices = (market: Market | "CRYPTO") =>
  api.get<{ indices: IndexQuote[] }>("/api/stocks/indices", { params: { market } }).then((r) => r.data);

export const fetchFactorAttribution = (symbol: string, market: Market, horizon: Horizon) =>
  api
    .get(`/api/stocks/${symbol}/factor-attribution`, { params: { market, horizon } })
    .then((r) => r.data);

export interface ScoreHistoryPoint {
  date: string;
  composite_score: number | null;
  quality_score: number | null;
  growth_score: number | null;
  valuation_score: number | null;
  technical_score: number | null;
  sentiment_score: number | null;
  risk_score: number | null;
  confidence_score: number | null;
  signal: string | null;
}

export const fetchScoreHistory = (symbol: string, market: Market, horizon: Horizon, days = 90) =>
  api
    .get<{ symbol: string; market: string; horizon: string; window_days: number; points: ScoreHistoryPoint[] }>(
      `/api/stocks/${symbol}/score-history`,
      { params: { market, horizon, days } }
    )
    .then((r) => r.data);

// ─── Paper Trading ────────────────────────────────────────────────────────────

export type TradeManagementMode = "manual" | "auto" | "ai_assisted";
export type ExitReason = "STOP_LOSS" | "TARGET_HIT" | "MANUAL" | null;

export interface PaperTrade {
  id: number;
  symbol: string;
  market: Market;
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  stop_loss: number | null;
  target_price: number | null;
  status: "OPEN" | "CLOSED";
  signal: string;
  horizon: string;
  opened_at: string;
  closed_at: string | null;
  invested: number;
  realized_pnl?: number;
  trade_management_mode: TradeManagementMode;
  exit_reason: ExitReason;
}

// Server-computed (never recomputed client-side) per-horizon closed-trade
// metrics — see backend/api/routers/paper_trading.py's
// _summarize_closed_bucket for the exact Target Hit Rate definition
// (conclusive TARGET_HIT/STOP_LOSS outcomes only, never total closed trades
// or P&L sign).
export interface ClosedTradeHorizonSummary {
  closed_trade_count: number;
  // Win Rate — the same canonical definition as the top-level Win Rate
  // stat card: realized P&L > 0 is a win, exactly 0 is break-even (never a
  // win), denominator is every closed trade in this bucket. Independent of
  // Target Hit Rate below — never merge the two.
  win_trades_count: number;
  win_rate_pct: number | null;
  break_even_count: number;
  target_hit_count: number;
  stop_loss_count: number;
  conclusive_count: number;
  other_count: number;
  target_hit_rate_pct: number | null;
  conclusive_rate_pct: number | null;
  net_realized_pnl: number;
  avg_realized_return_pct: number | null;
}

export interface ClosedTradeSummaryByMarket {
  short: ClosedTradeHorizonSummary;
  medium: ClosedTradeHorizonSummary;
  long: ClosedTradeHorizonSummary;
  // Present only when at least one closed trade in this market has a
  // stored horizon outside short/medium/long (e.g. a legacy row) — absent
  // entirely, not a zero-count bucket, when no such trade exists.
  unclassified?: ClosedTradeHorizonSummary;
}

// The authoritative, backend-assembled Trade History payload for one
// horizon bucket — `summary` and `latest_trades` are always derived from
// the exact same server-side list (see _closed_history_bucket in
// paper_trading.py), so they can never silently disagree. The frontend
// must render this verbatim; it must not re-derive `summary` from
// `latest_trades` (which is deliberately truncated to 5) or re-sort/group
// trades itself.
export interface ClosedTradeHorizonBucket {
  summary: ClosedTradeHorizonSummary;
  latest_trades: PaperTrade[];
  earlier_trade_count: number;
}

export interface ClosedTradeHistoryByMarket {
  short: ClosedTradeHorizonBucket;
  medium: ClosedTradeHorizonBucket;
  long: ClosedTradeHorizonBucket;
  // Present only when at least one closed trade in this market has a
  // stored horizon outside short/medium/long — absent entirely otherwise.
  unclassified?: ClosedTradeHorizonBucket;
}

export type ClosedHistoryHorizonKey = keyof ClosedTradeHistoryByMarket;

// Compact, backend-computed aggregate for the overall Trade History
// headline and win-rate stat card — never derived by summing/filtering
// individual trade rows client-side. See _build_modern_closed_trade_data /
// _overview_from_closed_trades in paper_trading.py.
export interface ClosedTradeOverview {
  closed_trade_count: number;
  win_trades_count: number;
  win_rate_pct: number | null;
  total_invested: number;
}

export interface PaperPortfolio {
  user_id: string;
  cash: number;
  cash_usd: number;
  starting_cash: number;
  starting_cash_usd: number;
  open_trades: PaperTrade[];
  // Only present when the request explicitly asked for it
  // (include_full_closed_trades=true) — the modern Paper Trading page
  // requests include_full_closed_trades=false and must not read this;
  // it renders the overall headline from closed_trade_overview_by_market
  // and Trade History's sections from closed_trade_history_by_horizon.
  closed_trades?: PaperTrade[];
  total_realized_pnl: number;
  total_realized_pnl_usd: number;
  closed_trade_summary: { IN: ClosedTradeSummaryByMarket; US: ClosedTradeSummaryByMarket };
  closed_trade_history_by_horizon: { IN: ClosedTradeHistoryByMarket; US: ClosedTradeHistoryByMarket };
  closed_trade_overview_by_market: { IN: ClosedTradeOverview; US: ClosedTradeOverview };
  email_notifications_enabled: boolean;
}

// The modern Paper Trading page always passes include_full_closed_trades:
// false — the legacy full `closed_trades` array is never fetched or
// transmitted for its normal render path. Omitting the parameter (or
// passing true) preserves the original, unbounded-history response shape
// for any other/legacy consumer.
export const fetchPaperPortfolio = (userId: string, email?: string | null) =>
  api.get<PaperPortfolio>("/api/paper-trading/portfolio", {
    params: { user_id: userId, email: email ?? undefined, include_full_closed_trades: false },
  }).then((r) => r.data);

export interface OlderClosedTradesCursor {
  before_closed_at: string;
  before_id: number;
}

export interface OlderClosedTradesResponse {
  market: Market;
  horizon: string;
  trades: PaperTrade[];
  has_more: boolean;
  next_cursor: OlderClosedTradesCursor | null;
}

// Lazy, bounded retrieval for a single horizon's "Show N earlier closed
// trades" control — called only when the user expands it, never prefetched.
// Pass the previous page's `next_cursor` fields to fetch the next page.
export const fetchOlderClosedTrades = (
  market: Market,
  horizon: ClosedHistoryHorizonKey,
  cursor?: OlderClosedTradesCursor | null,
  limit = 10,
) =>
  api.get<OlderClosedTradesResponse>("/api/paper-trading/closed-trades/older", {
    params: {
      market, horizon, limit,
      before_closed_at: cursor?.before_closed_at,
      before_id: cursor?.before_id,
    },
  }).then((r) => r.data);

// Trade Postmortem Engine, Stage 2 — matches backend's EvidenceSource enum
// exactly (services/postmortem/entry_snapshot.py). "MANUAL" is the safe
// default when the caller doesn't know why a trade was opened; a real
// trade with no recommendation behind it is a legitimate MANUAL trade, not
// an error.
export type EvidenceSource = "MANUAL" | "SCREENER" | "DAILY_PICK" | "RESEARCH";

// Mirrors backend's EntryEvidenceRequest field-for-field — every field is
// CLIENT_REPORTED evidence (see entry_snapshot.py's module docstring), not
// SERVER_VERIFIED. Omitting a field (or the whole object) is fully
// supported; only send what's actually known, never a fabricated default.
export interface EntryEvidencePayload {
  recommendation_signal?: string | null;
  recommendation_generated_at?: string | null;
  recommendation_reference_price?: number | null;
  recommendation_entry_low?: number | null;
  recommendation_entry_high?: number | null;
  recommended_stop_loss?: number | null;
  recommended_target_price?: number | null;
  confidence_score?: number | null;
  technical_signal?: string | null;
  technical_rsi?: number | null;
  technical_macd_diff?: number | null;
  fundamental_score?: number | null;
  sentiment_score?: number | null;
  sentiment_label?: string | null;
  market_regime_trend?: string | null;
  market_regime_score_adj?: number | null;
  market_regime_reason?: string | null;
  recommendation_reasoning?: { indicator: string; signal: string; reason: string }[] | null;
  daily_pick_run_id?: string | null;
  daily_pick_rank?: number | null;
  model_version?: string | null;
}

export interface PlacePaperBuyResponse {
  message: string;
  trade_id: number;
  symbol: string;
  market: Market;
  quantity: number;
  entry_price: number;
  cost: number;
  remaining_cash: number;
  entry_evidence_captured: boolean;
  snapshot_schema_version: string;
  evidence_source: EvidenceSource;
  evidence_completeness: "LIMITED" | "PARTIAL" | "COMPLETE";
  available_evidence_fields: string[];
  missing_evidence_fields: string[];
  // Migration-verification hardening gate, Part 7 — whether the backend's
  // durable exactly-once guarantee actually applied to this request (false
  // when idempotency_key was omitted).
  idempotency_enforced: boolean;
}

// Migration-verification hardening gate, Part 7 — thrown by placePaperBuy
// for the two idempotency-specific 409 responses, so callers can
// distinguish them from an ordinary API error without re-parsing
// `error.response.data.detail` themselves.
export class BuyIdempotencyConflictError extends Error {
  errorCode: "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST" | "BUY_ALREADY_IN_PROGRESS";
  constructor(errorCode: "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST" | "BUY_ALREADY_IN_PROGRESS", message: string) {
    super(message);
    this.name = "BuyIdempotencyConflictError";
    this.errorCode = errorCode;
  }
}

export const placePaperBuy = (data: {
  user_id: string; symbol: string; market: Market;
  quantity: number; price: number; signal?: string; horizon?: string;
  stop_loss?: number | null; target_price?: number | null; email?: string | null;
  trade_management_mode?: TradeManagementMode;
  evidence_source?: EvidenceSource;
  entry_evidence?: EntryEvidencePayload | null;
  // Migration-verification hardening gate, Part 7 — client-generated,
  // reused verbatim for every retry of the SAME logical Buy decision (see
  // PaperTradeModal's useRef-based generation). Omit entirely for the
  // pre-idempotency backward-compatible path (no exactly-once guarantee).
  idempotency_key?: string;
}): Promise<PlacePaperBuyResponse> =>
  api.post("/api/paper-trading/buy", data).then((r) => r.data).catch((error) => {
    const detail = error?.response?.data?.detail;
    if (error?.response?.status === 409 && detail?.error_code) {
      throw new BuyIdempotencyConflictError(detail.error_code, detail.message ?? "This Buy request could not be completed.");
    }
    throw error;
  });

export const closePaperTrade = (tradeId: number, userId: string, price: number, exitReason?: Exclude<ExitReason, null>) =>
  api.post(`/api/paper-trading/sell/${tradeId}`, { user_id: userId, price, exit_reason: exitReason ?? null }).then((r) => r.data);

export const resetPaperPortfolio = (userId: string, market: Market | "ALL" = "ALL") =>
  api.post("/api/paper-trading/reset", null, { params: { user_id: userId, market } }).then((r) => r.data);

export const editPaperTrade = (tradeId: number, userId: string, stopLoss: number | null, targetPrice: number | null, entryPrice?: number | null) =>
  api.patch(`/api/paper-trading/trade/${tradeId}`, { user_id: userId, stop_loss: stopLoss, target_price: targetPrice, entry_price: entryPrice ?? null }).then((r) => r.data);

// Only "manual"/"auto" are acceptable here — "ai_assisted" has no backend
// behavior yet and this endpoint rejects it outright (see ManagementModeRequest).
export const updatePaperTradeManagementMode = (tradeId: number, userId: string, mode: Exclude<TradeManagementMode, "ai_assisted">) =>
  api.patch(`/api/paper-trading/trades/${tradeId}/management-mode`, { user_id: userId, trade_management_mode: mode }).then((r) => r.data);

export const updatePaperTradeNotificationPreference = (userId: string, enabled: boolean) =>
  api.patch("/api/paper-trading/notifications", { user_id: userId, email_notifications_enabled: enabled }).then((r) => r.data);

// Trade Postmortem Engine, Phase 1 — GET /postmortem/{trade_id}'s response
// shape (backend/api/routers/paper_trading.py's PostmortemResponse). Never
// previously typed here since no frontend caller existed until the Daily
// Trade Postmortem Report; the daily response embeds one of these per trade.
export interface PostmortemResponse {
  schema_version: string;
  trade_id: number;
  status: string;
  outcome: "WIN" | "LOSS" | "BREAKEVEN" | "INDETERMINATE";
  realized_pnl_abs: number | null;
  realized_pnl_pct: number | null;
  holding_duration_seconds: number | null;
  exit_mechanism: "TARGET_HIT" | "STOP_LOSS" | "MANUAL" | "UNKNOWN";
  exit_mechanism_raw: string | null;
  trade_management_mode: string;
  auto_close_timing_evidence: "NOT_APPLICABLE" | "CLIENT_REPORTED_UNVERIFIED" | "SERVER_VERIFIED";
  evidence_completeness: "LIMITED" | "PARTIAL" | "COMPLETE";
  available_evidence_fields: string[];
  missing_evidence_fields: string[];
  target_distance_at_exit_pct: number | null;
  stop_distance_at_exit_pct: number | null;
  calculation_version: string;
  warnings: string[];
  snapshot_schema_version: string | null;
  evidence_source: string | null;
  verification_levels: Record<string, string> | null;
}

// Trade Postmortem Engine, Sprint 1 — mirrors backend's
// services/postmortem/evidence.py. Replaces the Stage 0 prototype's
// SignalDirectionAgreement/RootCauseCategory model outright: that model
// derived "the signal worked/failed" from the trade's own final P&L sign
// (outcome-circular reasoning) and always forced exactly one root cause.
// Neither survives Sprint 1 — see evidence_attribution.py's module
// docstring for the full audit finding. INSUFFICIENT_EVIDENCE is expected
// to appear often; that is this module working as designed, not a gap to
// paper over.
export type EvidenceClass =
  | "MECHANICALLY_VERIFIED" | "DIRECTLY_OBSERVED" | "EVIDENCE_SUPPORTED"
  | "CONFLICTING_EVIDENCE" | "INSUFFICIENT_EVIDENCE";

// Not a statistical probability — a qualitative label for how much the
// cited evidence supports the claim text. Never render this as a percent.
export type ConfidenceBand = "HIGH" | "MODERATE" | "LOW" | "NOT_ASSESSABLE";

export interface EvidenceItem {
  evidence_id: string;
  category: string;
  name: string;
  value: unknown;
  units: string | null;
  observation_timestamp: string | null;
  source: string;
  source_type: "SERVER_STORED" | "SERVER_DERIVED" | "CLIENT_REPORTED" | "APPROVED_EXTERNAL_SOURCE" | "UNAVAILABLE";
  verification_level: "MECHANICALLY_VERIFIED" | "DIRECTLY_OBSERVED" | "CLIENT_REPORTED" | "UNVERIFIED" | "UNAVAILABLE";
  freshness_status: "POINT_IN_TIME_VALID" | "STALE" | "NOT_APPLICABLE" | "UNKNOWN";
  limitations: string[];
}

export interface PostmortemClaim {
  claim_id: string;
  report_section: string;
  factor: string;
  claim_text: string;
  evidence_class: EvidenceClass;
  confidence_band: ConfidenceBand;
  supporting_evidence_ids: string[];
  opposing_evidence_ids: string[];
  missing_evidence: string[];
  contradiction_flags: string[];
  rule_id: string;
  rule_version: string;
  limitations: string[];
}

export type SignalStatus =
  | "WORKED_AS_EXPECTED" | "PARTIALLY_WORKED" | "WEAKENED" | "REVERSED" | "INVALIDATED"
  | "NOT_TESTABLE" | "INSUFFICIENT_EVIDENCE";

export interface SignalEvaluation {
  signal_id: string;
  signal_name: string;
  expected_interpretation: string | null;
  entry_evidence_id: string | null;
  comparison_evidence_ids: string[];
  status: SignalStatus;
  evidence_class: EvidenceClass;
  confidence_band: ConfidenceBand;
  explanation_claim_id: string;
  limitations: string[];
}

export type ContributorCategory =
  | "STOCK_SELECTION" | "ENTRY_TIMING" | "POSITION_MANAGEMENT" | "EXIT_LOGIC" | "MARKET_CONDITIONS"
  | "SECTOR_CONDITIONS" | "VOLATILITY" | "LIQUIDITY" | "NEWS_OR_EVENT" | "PRICE_NOISE" | "ADMINISTRATIVE_ACTION";

export type SupportLevel =
  | "STRONGLY_SUPPORTED" | "SUPPORTED" | "WEAKLY_SUPPORTED" | "CONFLICTED" | "NOT_SUPPORTED" | "NOT_ASSESSABLE";

export interface ContributorAssessment {
  category: ContributorCategory;
  support_level: SupportLevel;
  evidence_class: EvidenceClass;
  confidence_band: ConfidenceBand;
  supporting_evidence_ids: string[];
  opposing_evidence_ids: string[];
  claim_id: string;
  limitations: string[];
}

export type ThesisVerdict =
  | "CORRECT" | "PARTIALLY_CORRECT" | "EARLY" | "LATE" | "POORLY_TIMED"
  | "INVALIDATED" | "UNSUPPORTED" | "NOT_ASSESSABLE";

export interface EvidenceAttribution {
  evidence_items: EvidenceItem[];
  claims: PostmortemClaim[];
  signal_scorecard: SignalEvaluation[];
  contributor_assessments: ContributorAssessment[];
  // Null whenever no single category clears the "strongly supported, no
  // competing category" bar — an expected, honest result. Always check
  // primary_contributor_claim_id for the claim explaining why, even when
  // primary_contributor itself is null.
  primary_contributor: ContributorCategory | null;
  primary_contributor_claim_id: string | null;
  thesis_verdict: ThesisVerdict;
  thesis_verdict_claim_id: string;
  rule_registry_version: string;
  calculation_version: string;
  warnings: string[];
}

export interface DailyTradePostmortem {
  trade_id: number;
  symbol: string;
  market: Market;
  postmortem: PostmortemResponse;
  attribution: EvidenceAttribution;
}

export interface DailyPostmortemSummary {
  date: string;
  market: Market | "ALL";
  trade_count: number;
  win_count: number;
  loss_count: number;
  breakeven_count: number;
  indeterminate_count: number;
  // `null` only when EVERY trade that day lacks a valid realized P&L
  // (never coerced to 0 — see pnl_excluded_trade_count for how many were
  // excluded from this sum).
  total_realized_pnl_abs: number | null;
  pnl_excluded_trade_count: number;
  // Only STRONGLY_SUPPORTED/SUPPORTED contributor occurrences — labeled
  // "most frequently supported," never "root cause" (Sprint 1, Stage 13).
  recurring_supported_contributors: Record<string, number>;
  recurring_conflicting_contributors: Record<string, number>;
  recurring_not_assessable_count: Record<string, number>;
  trades_with_no_supported_contributor: number;
}

export interface DailyPostmortemReport {
  schema_version: string;
  summary: DailyPostmortemSummary;
  trades: DailyTradePostmortem[];
  calculation_version: string;
  warnings: string[];
}

// `date` is the MARKET-LOCAL calendar day (IST for IN, ET for US) — the
// backend resolves each trade to its own market's local day, never UTC; the
// caller just supplies the plain YYYY-MM-DD the user picked.
export const fetchDailyPostmortem = (date: string, market: Market | "ALL" = "ALL") =>
  api.get<DailyPostmortemReport>("/api/paper-trading/postmortem/daily", {
    params: { date, market },
  }).then((r) => r.data);

export const acceptTerms = (
  userId: string, email: string,
  profile: { first_name: string; last_name: string; mobile: string; country: string }
) =>
  api.post("/api/auth/accept-terms", { user_id: userId, email, terms_version: "v1.0", ...profile }).then((r) => r.data);

export const getTermsStatus = (userId: string) =>
  api.get<{ accepted: boolean; terms_version?: string; accepted_at?: string; first_name?: string; last_name?: string; mobile?: string; country?: string }>(`/api/auth/terms-status/${userId}`).then((r) => r.data);

export type MultibaggerScreen = "quality_compounder" | "multibagger_discovery" | "tenbagger_early";

export interface MultibaggerStock {
  symbol: string;
  market: "IN" | "US";
  company_name: string | null;
  sector_name: string | null;
  market_cap_cr: number | null;
  market_cap_usd_m: number | null;
  pe_ratio: number | null;
  roe_pct: number | null;
  roe_5y_pct: number | null;
  roce_pct: number | null;
  debt_to_equity_pct: number | null;
  promoter_holding_pct: number | null;
  promoter_pledge_pct: number | null;
  insider_holding_pct: number | null;
  sales_growth_3y_pct: number | null;
  sales_growth_5y_pct: number | null;
  profit_growth_3y_pct: number | null;
  profit_growth_5y_pct: number | null;
  opm_pct: number | null;
  interest_coverage_ratio: number | null;
  ev_ebitda: number | null;
  price_to_sales: number | null;
  operating_cf_latest_cr: number | null;
  updated_at: string;
  scorecard: {
    score: number;
    max_score: number;
    verdict: "elite_strong_buy" | "strong_buy" | "watchlist" | "watch" | "avoid";
    checks: { label: string; passed: boolean }[];
    red_flags: string[];
    elite_strong_buy: boolean;
  };
  shortlisted: boolean;
}

export const fetchMultibaggerScreen = (screen: MultibaggerScreen, market: "IN" | "US" = "IN") =>
  api.get<{
    screen: string; market: string; status?: "ok" | "unavailable"; count: number;
    results: MultibaggerStock[]; last_refreshed: string | null; error?: string;
  }>(
    "/api/multibagger/screen", { params: { screen, market } }
  ).then((r) => r.data);

export interface MultibaggerRefreshSummary {
  total: number;
  refreshed: number;
  skipped: number;
  failed: number;
  elapsed_minutes: number;
}

export const importPortfolioHoldings = (
  userId: string,
  market: Market,
  holdings: { symbol: string; qty: number; avgPrice: number; originalSymbol?: string }[]
) =>
  api.post<{ added: number; updated: number; cleaned_up: number; total: number }>(
    `/api/portfolio/${userId}/import`,
    { holdings: holdings.map(h => ({ symbol: h.symbol, market, qty: h.qty, avg_price: h.avgPrice, original_symbol: h.originalSymbol ?? null })) }
  ).then(r => r.data);

export const fetchMultibaggerStatus = (market: "IN" | "US" = "IN") =>
  api.get<{
    market: string;
    running: boolean;
    last_summary: MultibaggerRefreshSummary | null;
    last_refreshed: string | null;
    // Product Integrity #009 — weekly refresh, durable status contract.
    schedule_frequency: "weekly";
    next_scheduled_refresh_hint: string;
    stale_after_days: number;
    durable_state_available: boolean;
    last_successful_refresh_at?: string | null;
    is_stale?: boolean;
    job_status?: "queued" | "running" | "completed" | "failed" | "interrupted" | "expired";
    trigger_source?: "scheduled" | "manual";
  }>(
    "/api/multibagger/status", { params: { market } }
  ).then((r) => r.data);
