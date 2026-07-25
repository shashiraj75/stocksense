"use client";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { api } from "@/utils/api";
import type { Market } from "@/utils/api";
import {
  MarketLeadershipContext as LeadershipContextData,
  getValidLeadershipContext,
} from "@/utils/marketLeadership";

// Market Leadership and Trend Context Layer — experimental, shadow-only
// consumer. Renders nothing at all unless the backend returns a fully
// formed "ok" payload (flag-gated server-side by MARKET_LEADERSHIP_UI_ENABLED
// and MARKET_LEADERSHIP_ENGINE_ENABLED — both default OFF). Never implies a
// BUY/HOLD/SELL recommendation: RS/Trend/Breadth are informational context
// only, always shown with a permanent "Experimental" label per Section 14.

const TREND_LABEL: Record<string, string> = {
  BASE_FORMING: "Base Forming",
  EARLY_ADVANCE: "Early Advance",
  CONFIRMED_ADVANCE: "Confirmed Advance",
  EXTENDED_ADVANCE: "Extended Advance",
  DISTRIBUTION_RISK: "Distribution Risk",
  DECLINING: "Declining",
  INSUFFICIENT_DATA: "Insufficient Data",
};

const EXTENSION_COLOR: Record<string, string> = {
  LOW: "text-green-400",
  MODERATE: "text-yellow-400",
  HIGH: "text-red-400",
  UNKNOWN: "text-gray-500",
};

function RsTrendIcon({ trend }: { trend: string }) {
  if (trend === "IMPROVING") return <TrendingUp size={14} className="text-green-400" aria-hidden="true" />;
  if (trend === "WEAKENING") return <TrendingDown size={14} className="text-red-400" aria-hidden="true" />;
  return <Minus size={14} className="text-gray-500" aria-hidden="true" />;
}

export function MarketLeadershipContext({ symbol, market }: { symbol: string; market: Market }) {
  const { data, isLoading } = useQuery<LeadershipContextData>({
    queryKey: ["leadership-context", symbol, market],
    queryFn: () =>
      api
        .get(`/api/leadership/context?symbol=${encodeURIComponent(symbol)}&market=${market}`)
        .then((r) => r.data)
        .catch(() => ({ status: "error" }) as LeadershipContextData),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  if (isLoading) return null;
  const ctx = getValidLeadershipContext(data);
  if (!ctx || !ctx.rs || !ctx.trend || !ctx.why_now) return null;

  // Market isolation: never render context for a market other than the one
  // this page is actually showing — belt-and-suspenders against any future
  // caching bug, even though the query key already keys on market.
  if (ctx.market !== market) return null;

  const { rs, trend, why_now } = ctx;

  return (
    <div
      data-testid="market-leadership-context"
      className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-3"
      aria-label="Market Leadership and Trend Context (experimental)"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <FlaskConical size={16} className="text-purple-400" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-gray-200">Market Leadership &amp; Trend Context</h3>
        <span
          className="text-[10px] uppercase tracking-wide bg-purple-500/15 text-purple-400 border border-purple-500/30 px-1.5 py-0.5 rounded-full font-semibold"
          title="Experimental — informational context only, does not affect BUY/HOLD/SELL"
        >
          Experimental
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <div>
          <p className="text-xs text-gray-500">RS Percentile</p>
          <p className="font-mono font-semibold text-gray-100">
            {rs.stock_rs_percentile != null ? Math.round(rs.stock_rs_percentile) : "—"}
            {rs.stock_rs_percentile != null && <span className="text-gray-500">/100</span>}
          </p>
          <p className="text-xs text-gray-500 flex items-center gap-1">
            <RsTrendIcon trend={rs.rs_trend} /> {rs.rs_trend.toLowerCase().replace("_", " ")}
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-500">Trend Lifecycle</p>
          <p className="font-semibold text-gray-100">{TREND_LABEL[trend.state] ?? trend.state}</p>
        </div>
        <div>
          <p className="text-xs text-gray-500">Extension Risk</p>
          <p className={`font-semibold ${EXTENSION_COLOR[trend.extension_risk] ?? "text-gray-400"}`}>
            {trend.extension_risk}
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-500">Data Freshness</p>
          <p className="text-gray-300">{why_now.data_freshness}</p>
        </div>
      </div>

      <p className="text-xs text-gray-400 leading-relaxed">{why_now.summary}</p>

      {why_now.caution_factors.length > 0 && (
        <ul className="text-xs text-yellow-300/80 list-disc list-inside space-y-0.5">
          {why_now.caution_factors.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}

      <p className="text-[11px] text-gray-600">
        Informational context, not a recommendation — separate from the BUY/HOLD/SELL signal above.
        Methodology {rs.methodology_version} / {trend.methodology_version}.
      </p>
    </div>
  );
}
