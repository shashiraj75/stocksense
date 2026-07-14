/**
 * Cross-market retained-payload containment bypass fix.
 *
 * Daily Picks' `useQuery` uses `queryKey: ["daily-picks", market]` with
 * `placeholderData: keepPreviousData` — switching IN -> US is a query-key
 * change, and `keepPreviousData` deliberately keeps the PREVIOUS key's
 * (India's) payload visible as `data` while the new (US) query is in
 * flight. That's desirable for smoothness (it avoids a loading flash on
 * every market toggle), but every downstream read of `data` in the page
 * trusted it unconditionally — including the India-only freshness
 * containment, which gated on the *selected* market rather than on which
 * market the *payload itself* actually belongs to. The result: once
 * `market` state had already flipped to "US", the retained India payload's
 * cards rendered fully unguarded (entry/target/stop/upside/risk-reward/
 * summary/Paper Trade all exposed) for the one render before the US query
 * resolves.
 *
 * This module is the single place that decides whether a fetched payload
 * is safe to render for a given selected market. Fail closed: a payload
 * whose `market` field is missing, malformed, or does not exactly match
 * the selected market (after normalization) is never treated as safe —
 * callers must fall back to a loading/skeleton state, never the payload.
 */

export type MarketKey = "IN" | "US";

/** Normalizes a possibly-loose market string to a canonical MarketKey, or null if unrecognized. */
export function normalizeMarketKey(value: string | null | undefined): MarketKey | null {
  if (!value) return null;
  const v = value.trim().toUpperCase();
  return v === "IN" || v === "US" ? v : null;
}

/**
 * Returns `payload` only when its own `market` field can be proven to equal
 * `selectedMarket` exactly (after normalization). Returns `undefined`
 * otherwise — including when `payload` is null/undefined, or either market
 * value is missing/malformed. Never assumes a match: an unprovable payload
 * is treated identically to "not loaded yet," never as safe to render.
 */
export function selectPayloadForMarket<T extends { market?: string | null }>(
  payload: T | null | undefined,
  selectedMarket: string | null | undefined,
): T | undefined {
  if (!payload) return undefined;
  const payloadMarket = normalizeMarketKey(payload.market);
  const selected = normalizeMarketKey(selectedMarket);
  if (payloadMarket === null || selected === null) return undefined;
  return payloadMarket === selected ? payload : undefined;
}
