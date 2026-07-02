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
  high?: number;
  low?: number;
  company_name?: string;
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
  // Optional per Sprint #011 §3A — absent today in every production response
  // since RCI_LIVE_STOCK_ANALYSIS_ENABLED is disabled in Railway.
  recommendation_consolidation?: RecommendationConsolidation;
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

export const fetchQuote = (symbol: string, market: Market) =>
  api.get<StockQuote>(`/api/stocks/quote/${symbol}`, { params: { market } }).then((r) => r.data);

export const fetchOHLCV = (symbol: string, market: Market, period = "1y", interval = "1d") =>
  api
    .get<{ data: OHLCVBar[] }>(`/api/stocks/ohlcv/${symbol}`, { params: { market, period, interval } })
    .then((r) => r.data);

export const fetchPrediction = async (
  symbol: string,
  market: Market,
  horizon: Horizon,
  onComputing?: () => void,
): Promise<Prediction> => {
  // Poll up to 180 s (36 × 5 s) for background computation to complete
  for (let attempt = 0; attempt < 36; attempt++) {
    const res = await api.get<Prediction | { status: string; retry_after?: number }>(
      `/api/predictions/${symbol}`,
      {
        params: { market, horizon },
        validateStatus: (s) => s === 200 || s === 202,
      },
    );
    if (res.status === 200) return res.data as Prediction;
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

export const fetchNews = (symbol: string, market: Market) =>
  api
    .get<{
      articles: NewsArticle[];
      total_article_count?: number;
      eligible_article_count?: number;
      historical_article_count?: number;
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

export const fetchScoreHistory = (symbol: string, horizon: Horizon, days = 90) =>
  api
    .get<{ symbol: string; horizon: string; window_days: number; points: ScoreHistoryPoint[] }>(
      `/api/stocks/${symbol}/score-history`,
      { params: { horizon, days } }
    )
    .then((r) => r.data);

// ─── Paper Trading ────────────────────────────────────────────────────────────

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
}

export interface PaperPortfolio {
  user_id: string;
  cash: number;
  cash_usd: number;
  starting_cash: number;
  starting_cash_usd: number;
  open_trades: PaperTrade[];
  closed_trades: PaperTrade[];
  total_realized_pnl: number;
  total_realized_pnl_usd: number;
}

export const fetchPaperPortfolio = (userId: string, email?: string | null) =>
  api.get<PaperPortfolio>("/api/paper-trading/portfolio", { params: { user_id: userId, email: email ?? undefined } }).then((r) => r.data);

export const placePaperBuy = (data: {
  user_id: string; symbol: string; market: Market;
  quantity: number; price: number; signal?: string; horizon?: string;
  stop_loss?: number | null; target_price?: number | null; email?: string | null;
}) => api.post("/api/paper-trading/buy", data).then((r) => r.data);

export const closePaperTrade = (tradeId: number, userId: string, price: number) =>
  api.post(`/api/paper-trading/sell/${tradeId}`, { user_id: userId, price }).then((r) => r.data);

export const resetPaperPortfolio = (userId: string, market: Market | "ALL" = "ALL") =>
  api.post("/api/paper-trading/reset", null, { params: { user_id: userId, market } }).then((r) => r.data);

export const editPaperTrade = (tradeId: number, userId: string, stopLoss: number | null, targetPrice: number | null, entryPrice?: number | null) =>
  api.patch(`/api/paper-trading/trade/${tradeId}`, { user_id: userId, stop_loss: stopLoss, target_price: targetPrice, entry_price: entryPrice ?? null }).then((r) => r.data);

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
  api.get<{ market: string; running: boolean; last_summary: MultibaggerRefreshSummary | null; last_refreshed: string | null }>(
    "/api/multibagger/status", { params: { market } }
  ).then((r) => r.data);
