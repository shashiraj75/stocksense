// Market Leadership and Trend Context Layer — frontend contract for
// GET /api/leadership/context. Shadow-only, additive, flag-gated (see
// backend/services/market_leadership/configuration.py). Every field is
// optional/nullable by construction — a consumer must handle "disabled",
// "unavailable", "error", and partial evidence explicitly, never assume
// presence.

export type LeadershipStatus = "ok" | "disabled" | "unavailable" | "error";

export type RsTrend = "IMPROVING" | "FLAT" | "WEAKENING" | "INSUFFICIENT_DATA";
export type TrendLifecycleState =
  | "BASE_FORMING" | "EARLY_ADVANCE" | "CONFIRMED_ADVANCE" | "EXTENDED_ADVANCE"
  | "DISTRIBUTION_RISK" | "DECLINING" | "INSUFFICIENT_DATA";
export type ExtensionRisk = "LOW" | "MODERATE" | "HIGH" | "UNKNOWN";

export type StockRelativeStrength = {
  symbol: string;
  market: "IN" | "US";
  stock_rs_score: number | null;
  stock_rs_percentile: number | null;
  rs_trend: RsTrend;
  data_quality_status: string;
  methodology_version: string;
};

export type TrendLifecycle = {
  state: TrendLifecycleState;
  extension_risk: ExtensionRisk;
  evidence_completeness: number;
  methodology_version: string;
};

export type WhyNowExplanation = {
  summary: string;
  supporting_factors: string[];
  caution_factors: string[];
  data_freshness: string;
  data_quality: string;
  methodology_version: string;
};

export type MarketLeadershipContext = {
  status: LeadershipStatus;
  market?: "IN" | "US";
  symbol?: string;
  as_of?: string;
  rs?: StockRelativeStrength;
  trend?: TrendLifecycle;
  why_now?: WhyNowExplanation;
  reason?: string;
  error?: string;
};

/**
 * The single decision point for whether the Market Leadership panel may
 * render at all. Mirrors getValidRecommendationConsolidation's pattern —
 * anything other than a fully-formed "ok" response renders nothing, never
 * a placeholder or error banner (this is explicitly experimental,
 * informational context, not a page the user depends on).
 */
export function getValidLeadershipContext(
  data: MarketLeadershipContext | null | undefined,
): MarketLeadershipContext | null {
  if (!data || data.status !== "ok") return null;
  if (!data.rs || !data.trend || !data.why_now) return null;
  if (!data.market || !data.symbol) return null;
  return data;
}

/**
 * Fail-closed frontend presentation gate — the browser-side mirror of the
 * backend's MARKET_LEADERSHIP_UI_ENABLED flag. This does NOT replace either
 * backend gate (MARKET_LEADERSHIP_ENGINE_ENABLED, MARKET_LEADERSHIP_UI_ENABLED);
 * it is a THIRD, independent gate so that with no environment configured,
 * the browser never even issues a request to /api/leadership/context —
 * previously the component always called useQuery(), so every eligible
 * Stock Detail page view made that request regardless of any backend flag
 * state (the backend safely answered {"status":"disabled"}, but the
 * request itself still happened). Only the exact string "1" enables it —
 * absent, empty, "0", "true", or any other value fails closed. Contains no
 * secret: NEXT_PUBLIC_* variables are inlined into the client bundle by
 * Next.js and are visible to anyone regardless of this function's logic.
 */
export function isMarketLeadershipUiEnabled(): boolean {
  return process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED === "1";
}
