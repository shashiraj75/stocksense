/**
 * Entry-zone actionability evaluation (Release 12A).
 *
 * The old check compared a live quote against the persisted entry zone with
 * no proof that the quote was newer than the pick or even on the same price
 * basis — the generation reference is a Yahoo daily-history close (adjusted
 * basis, bar date can lag), while displayed quotes come from NSE official /
 * Finnhub / Yahoo fast_info (unadjusted last-traded). That produced "price
 * has moved since this was generated" warnings while the market was closed.
 *
 * This helper returns an explicit state instead of a boolean, and only the
 * `verified_*` states may ever claim the price is outside the entry zone.
 * Every unprovable situation gets its own conservative state. Pure and
 * deterministic: all clocks/market state arrive as explicit inputs. Market-
 * agnostic: IN and US differ only in the marketOpen input the caller derives
 * from the existing marketHours utilities.
 */

export type ActionabilityState =
  | "verified_within_entry_zone"
  | "verified_above_entry_zone"
  | "verified_below_entry_zone"
  | "market_closed_unverified"
  | "quote_unavailable"
  | "quote_timestamp_unknown"
  | "quote_incomparable"
  | "legacy_reference_unknown";

export interface ActionabilityInput {
  /** Persisted generation reference (Release 12A provenance fields). */
  generationBasis: string | null | undefined;   // e.g. "adjusted_close"
  generationPrice: number | null | undefined;   // reference price the zone was built from
  generatedAt: string | null | undefined;       // ISO timestamp of the pick
  entryLow: number | null | undefined;
  entryHigh: number | null | undefined;
  /** Displayed quote and its backend-supplied provenance. */
  quotePrice: number | null | undefined;
  quoteBasis: string | null | undefined;        // e.g. "last_traded_unadjusted"
  quoteTimestamp: string | null | undefined;    // ISO, provider-supplied only
  /** Session state from the existing marketHours utilities (null = unknown). */
  marketOpen: boolean | null;
  /** Injectable clock for deterministic tests. */
  now?: Date;
}

/** Max age for a quote to count as current while the market is open. */
export const QUOTE_MAX_AGE_MS = 15 * 60_000;

/**
 * Release 12A1: a "verified comparable" claim may only ever cross-check a
 * quote against a reference on the SAME known price basis. An adjusted
 * daily close and an unadjusted last-traded price are NOT inherently
 * comparable — a split, bonus issue, special dividend, or vendor/bar-date
 * divergence between generation and viewing makes them economically
 * different numbers even when the quote is newer, fresh, and the market is
 * open. No corporate-action detection exists here, so the cross-basis pair
 * is conservatively incomparable until a provably corporate-action-safe
 * method exists.
 */
const KNOWN_BASES = new Set(["adjusted_close", "last_traded_unadjusted"]);

/**
 * Same-basis material-divergence truthfulness guard. NOT a trading
 * threshold and NOT a market-circuit claim: a quote differing from the
 * generation reference by more than this is not automatically wrong, but it
 * is consistent with a corporate action, a stale/mismatched bar date, or a
 * gross vendor fault — so it is not safe enough to present as a VERIFIED
 * comparable quote. Deterministic and currency-agnostic (relative ratio).
 */
export const MAX_VERIFIED_REFERENCE_DIVERGENCE_PCT = 20;

function parseIso(raw: string | null | undefined): Date | null {
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function evaluateEntryZoneActionability(input: ActionabilityInput): ActionabilityState {
  const { entryLow, entryHigh, quotePrice } = input;

  if (quotePrice == null || !Number.isFinite(quotePrice)) return "quote_unavailable";
  if (entryLow == null || entryHigh == null) return "legacy_reference_unknown";

  // Legacy picks lack the Release 12A provenance fields — never claim
  // movement for them.
  if (!input.generationBasis || !input.generatedAt ||
      input.generationPrice == null || !Number.isFinite(input.generationPrice) ||
      input.generationPrice <= 0) {
    return "legacy_reference_unknown";
  }

  const generated = parseIso(input.generatedAt);
  if (!generated) return "legacy_reference_unknown";

  // Rule 1 (Release 12A1): only an IDENTICAL known price basis is
  // comparable. Cross-basis pairs (adjusted vs unadjusted, either
  // direction) and unknown bases are conservatively incomparable.
  if (!input.quoteBasis ||
      input.quoteBasis !== input.generationBasis ||
      !KNOWN_BASES.has(input.quoteBasis)) {
    return "quote_incomparable";
  }

  const quoteTime = parseIso(input.quoteTimestamp);
  if (!quoteTime) {
    // A number exists but nothing proves when it traded.
    return input.marketOpen === false ? "market_closed_unverified" : "quote_timestamp_unknown";
  }

  // The quote must be provably NEWER than the pick.
  if (quoteTime.getTime() <= generated.getTime()) {
    return input.marketOpen === false ? "market_closed_unverified" : "quote_timestamp_unknown";
  }

  // Rule 3: verification requires a known, OPEN session; while open, the
  // quote must also be fresh.
  if (input.marketOpen === null) return "quote_timestamp_unknown";
  if (input.marketOpen === false) return "market_closed_unverified";
  const now = input.now ?? new Date();
  if (now.getTime() - quoteTime.getTime() > QUOTE_MAX_AGE_MS) return "quote_timestamp_unknown";

  // Rule 2: same-basis material-divergence guard — a gap this large is
  // consistent with a corporate action or vendor/bar-date fault, so it is
  // never presented as a verified comparison. Truthfulness-only: it changes
  // no model output.
  const divergencePct =
    Math.abs(quotePrice - input.generationPrice) / input.generationPrice * 100;
  if (divergencePct > MAX_VERIFIED_REFERENCE_DIVERGENCE_PCT) return "quote_incomparable";

  if (quotePrice > entryHigh) return "verified_above_entry_zone";
  if (quotePrice < entryLow) return "verified_below_entry_zone";
  return "verified_within_entry_zone";
}

/** True only for states that PROVE the quote is outside the entry zone. */
export function isVerifiedOutsideEntryZone(state: ActionabilityState): boolean {
  return state === "verified_above_entry_zone" || state === "verified_below_entry_zone";
}

/**
 * True when the displayed quote is proven comparable to the generation
 * reference — the only case where upside may be labelled "from current
 * price".
 */
export function isQuoteVerifiedComparable(state: ActionabilityState): boolean {
  return state === "verified_within_entry_zone" || isVerifiedOutsideEntryZone(state);
}
