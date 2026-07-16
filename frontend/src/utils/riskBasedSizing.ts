// Risk-based position sizing for the Paper Trade modal (user-requested,
// 2026-07-16): a flat share count (e.g. "10 shares") carries wildly
// different dollar risk depending on the stock's price. Real paper-trading
// data showed avg losses 5.7x avg wins in the US ledger, traced to a
// handful of high-priced, flat-quantity positions (e.g. 10 shares of a
// $1,189 stock = $11,894 exposure) dwarfing many small wins on cheap
// stocks. Sizing by risk-per-trade instead keeps every position's worst
// case (a stop-loss hit) roughly equal in dollar terms, regardless of
// share price.

// Risk ~1% of virtual capital per trade — not a new/untested convention,
// matches common real-world position-sizing guidance (risk 1-2% per trade).
export const RISK_PCT_OF_CAPITAL = 0.01;

export interface SuggestedQuantityInput {
  currentPrice: number;
  stopLoss: number | null;
  availableCash: number | null | undefined;
  riskPct?: number;
}

/**
 * Suggested share count so that a stop-loss hit costs ~riskPct of available
 * capital. Capped at what available cash can actually buy — a very tight
 * stop-loss (small per-share risk) must not suggest a position larger than
 * the account can afford; paper trading shouldn't model leverage that
 * isn't real. Returns null when there isn't enough information to size
 * (no stop loss, no cash data, or a non-positive price).
 */
export function computeSuggestedQuantity({
  currentPrice,
  stopLoss,
  availableCash,
  riskPct = RISK_PCT_OF_CAPITAL,
}: SuggestedQuantityInput): number | null {
  if (!Number.isFinite(currentPrice) || currentPrice <= 0) return null;
  if (stopLoss == null || !Number.isFinite(stopLoss) || stopLoss <= 0) return null;
  if (availableCash == null || !Number.isFinite(availableCash) || availableCash <= 0) return null;

  const perShareRisk = Math.abs(currentPrice - stopLoss);
  if (perShareRisk <= 0) return null; // stop loss equals entry — no defined risk to size against

  const riskBasedQty = Math.floor((availableCash * riskPct) / perShareRisk);
  const affordableQty = Math.floor(availableCash / currentPrice);

  return Math.max(1, Math.min(riskBasedQty, affordableQty));
}
