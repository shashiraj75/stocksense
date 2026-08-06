// Sprint 3b (targeted correction) — deterministic, curated plain-English
// reasons for INSUFFICIENT_EVIDENCE claims in Layer 2 (Investor
// Explanation). A prior version of ClaimExplanationLayer surfaced the raw
// governed `missing_evidence`/`limitations` strings directly as the
// "plain language reason", leaking internal identifiers and phrasing
// (snake_case field names, "this codebase", "a later, separately-scoped
// sprint", raw evidence-class constants) into the investor-facing layer —
// a real defect found during preview QA.
//
// The exact original governed text is NEVER discarded — it remains fully
// accessible, unmutated, in Layer 3 (see ClaimExplanationLayer.tsx's
// expandable detail panel, which still renders claim.claim_text,
// claim.missing_evidence, claim.limitations verbatim). This module only
// supplies what Layer 2 displays instead.
//
// PRECEDENCE:
//   1. A curated (report_section, factor) reason — keyed the same way as
//      postmortemFactorLabels.ts's SECTION_FACTOR_LABELS, so a claim with a
//      curated factor title also gets a curated, factor-specific reason
//      (several factors currently share identical raw backend text but
//      need DIFFERENT investor-facing sentences — e.g. MARKET_CONDITIONS
//      vs NEWS_OR_EVENT both cite the same shared "not acquired by this
//      codebase" string today, but must not render the same generic
//      sentence for both).
//   2. A safe, content-free generic fallback that never contains a raw
//      identifier, evidence-class constant, or development-process
//      reference. This is the universal safety net: even a factor this
//      module hasn't been curated for yet cannot leak internal language.
export interface ClaimReasonInput {
  reportSection: string;
  factor: string;
}

const CURATED_INSUFFICIENT_REASONS: Record<string, string> = {
  // signal_scorecard — entry-snapshot-derived fields not captured at Buy time.
  "signal_scorecard::technical_signal": "The technical signal available when this trade was opened was not captured.",
  "signal_scorecard::recommendation_signal": "The recommendation signal available when this trade was opened was not captured.",
  "signal_scorecard::fundamental_score": "The fundamental score available when this trade was opened was not captured.",
  "signal_scorecard::sentiment_score": "The sentiment score available when this trade was opened was not captured.",

  "thesis_verdict::thesis_verdict": "A captured entry thesis exists, but no point-in-time price evidence is available yet to check whether it played out.",

  // contributor_assessments — the "_UNACQUIRED_CATEGORIES" set shares one
  // raw backend message today, but each needs a distinct, factor-specific
  // investor-facing sentence.
  "contributor_assessments::STOCK_SELECTION": "No governed evidence was available to assess whether stock selection contributed to this trade's outcome.",
  "contributor_assessments::ENTRY_TIMING": "No governed evidence was available to assess whether entry timing contributed to this trade's outcome.",
  "contributor_assessments::MARKET_CONDITIONS": "No governed broad-market evidence was available for this trade.",
  "contributor_assessments::SECTOR_CONDITIONS": "No governed sector evidence was available for this trade.",
  "contributor_assessments::VOLATILITY": "No governed volatility evidence was available for this trade.",
  "contributor_assessments::LIQUIDITY": "No governed liquidity evidence was available for this trade.",
  "contributor_assessments::NEWS_OR_EVENT": "No governed news or event evidence was available for this trade.",
  "contributor_assessments::PRICE_NOISE": "No governed evidence was available to separate ordinary price noise from a real signal for this trade.",
  "contributor_assessments::ADMINISTRATIVE_ACTION": "No governed evidence was available to assess whether a corporate action affected this trade.",
  "contributor_assessments::POSITION_MANAGEMENT": "This trade's stop-loss or target price was not set, so position management could not be assessed.",

  "primary_contributor::primary_contributor": "No single contributor to this trade's outcome was strongly supported by the available evidence.",

  "contradictions::entry_signal_agreement": "Fewer than two entry-time signals had an assessable direction, so signal agreement could not be checked.",
  "contradictions::momentum_vs_entry_range": "The technical signal or execution-range position needed for this check was not available.",

  // exit_evidence — built inline, distinct wording per factor.
  "exit_evidence::exit_evidence": "No exit record exists for this trade.",
  "exit_evidence::exit_mechanism": "How this trade closed was not recorded.",
  "exit_evidence::exit_execution": "The exact execution details for this trade's close were not recorded.",
  "exit_evidence::exit_management_levels": "The risk levels in effect at close were not recorded.",
  "exit_evidence::exit_trigger_timing": "Whether the close trigger was independently verified could not be determined.",

  // price_path / governed_price_path — most of these are also covered by
  // the dedicated resolveAvailabilityReason (for the summary-card N/A
  // values); this covers the same factors when they appear as a Layer-2
  // claim row instead.
  "price_path::mfe": "The maximum favorable price movement during this trade's holding period could not be determined from the available evidence.",
  "price_path::mae": "The maximum adverse price movement during this trade's holding period could not be determined from the available evidence.",
  "governed_price_path::target_touch": "Whether the target level was independently crossed could not be verified from the available price evidence.",
  "governed_price_path::stop_touch": "Whether the stop level was independently crossed could not be verified from the available price evidence.",
};

const GENERIC_FALLBACK = "This factor could not be assessed from the available evidence.";

/**
 * Layer-2 display reason for an INSUFFICIENT_EVIDENCE claim. Never returns
 * raw backend text — either a curated, factor-specific sentence or the
 * safe generic fallback. The exact original claim.missing_evidence /
 * claim.limitations remain available verbatim in Layer 3.
 */
export function resolveClaimReason(input: ClaimReasonInput): string {
  const key = `${input.reportSection}::${input.factor}`;
  return CURATED_INSUFFICIENT_REASONS[key] ?? GENERIC_FALLBACK;
}
