/**
 * Daily Picks price-basis rule (UI/UX Truthfulness Correction Program, Wave 0B).
 *
 * A Daily Picks card must never show a current/refreshed price next to an
 * upside percentage computed from the older generation-time price — the
 * visible price and the visible upside must always share one price basis.
 *
 * The rule, in order:
 *   1. valid current/refreshed price        → basis "current"
 *   2. else valid generation/reference price → basis "generation"
 *                                              (caller must label it as such)
 *   3. else                                  → no basis; upside is unavailable
 *
 * Pure and market-agnostic: numbers in, numbers out. Currency formatting,
 * labels, and locale stay in the component — India and US flow through the
 * identical math. No import may be added here; keeping this dependency-free
 * is what makes it directly executable by the standalone regression script
 * (frontend/scripts/test-price-basis.mjs) in a repo with no frontend test
 * framework.
 */

export type PriceBasis = "current" | "generation";

export interface PriceBasisResult {
  /** Which price the card is showing (and must compute upside from). */
  basis: PriceBasis | null;
  /** The price for that basis — null when no valid basis exists. */
  price: number | null;
}

/** A price usable as a display/calculation basis: a finite number > 0.
 *  Zero, negatives, NaN, and ±Infinity are all invalid — a 0 or negative
 *  quote is provider noise, not a real traded price, and dividing by it
 *  fabricates Infinity/NaN or a nonsense percentage. */
export function isValidPrice(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value) && value > 0;
}

/** Select the single price basis the card displays and calculates from. */
export function selectPriceBasis(
  currentPrice: number | null | undefined,
  generationPrice: number | null | undefined,
): PriceBasisResult {
  if (isValidPrice(currentPrice)) return { basis: "current", price: currentPrice };
  if (isValidPrice(generationPrice)) return { basis: "generation", price: generationPrice };
  return { basis: null, price: null };
}

/**
 * Estimated upside percent of `target` over `basisPrice`, or null when either
 * side is invalid. Never returns NaN, ±Infinity, or a fabricated 0 — a null
 * here means the UI must show an explicit unavailable state, not a number.
 */
export function computeEstimatedUpsidePct(
  target: number | null | undefined,
  basisPrice: number | null | undefined,
): number | null {
  if (!isValidPrice(target) || !isValidPrice(basisPrice)) return null;
  const pct = ((target - basisPrice) / basisPrice) * 100;
  return Number.isFinite(pct) ? pct : null;
}

/**
 * Whether the backend-generated pick summary's target/upside narrative is
 * trustworthy enough to display. The generation pipeline composes its
 * summary sentence ("Target ₹X implies Y% upside within …") unconditionally,
 * substituting 0 when the generation-time price or target was missing — so
 * a payload with an invalid generation price or target carries a fabricated
 * "Target ₹0.00 implies 0% upside" sentence frozen inside it. The frontend
 * receives the same two underlying values (pick.price, pick.target) and must
 * suppress that narrative rather than show a fabricated figure.
 */
export function hasValidGenerationBasis(
  generationPrice: number | null | undefined,
  target: number | null | undefined,
): boolean {
  return isValidPrice(generationPrice) && isValidPrice(target);
}
