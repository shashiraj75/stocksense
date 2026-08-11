// Paper Trading Repeat-Buy Awareness — Phase 1 (Daily Picks Open-Trade Count).
//
// Pure, testable derivation of "how many OPEN Paper Trades does the
// authenticated user already have for this (market, symbol), summed across
// all horizons (Short + Medium + Long all count toward the same total)."
//
// Deliberately narrow: this file only counts OPEN trades. It does not
// compute exposure, P&L, concentration, or anything else — see the Phase 1
// product spec. Do not extend this helper for those without a new,
// separately-ratified phase.
import type { PaperTrade } from "@/utils/api";

// `(market, symbol)` is the compound identity — the same textual ticker in
// two different markets (e.g. IN:TCS vs US:TCS) is intentionally two
// separate counts. No ticker normalization is applied; the canonical
// `market`/`symbol` values already returned by the portfolio API are used
// verbatim as the map key.
export function openTradeCountKey(market: string, symbol: string): string {
  return `${market}:${symbol}`;
}

// Builds a lookup of open-trade counts keyed by `${market}:${symbol}`,
// summed across every horizon. Does not mutate `openTrades`.
export function buildOpenTradeCountMap(openTrades: PaperTrade[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const trade of openTrades) {
    if (trade.status !== "OPEN") continue;
    const key = openTradeCountKey(trade.market, trade.symbol);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

// Convenience accessor for a single (market, symbol) pair.
export function getOpenTradeCount(openTrades: PaperTrade[], market: string, symbol: string): number {
  return buildOpenTradeCountMap(openTrades).get(openTradeCountKey(market, symbol)) ?? 0;
}
