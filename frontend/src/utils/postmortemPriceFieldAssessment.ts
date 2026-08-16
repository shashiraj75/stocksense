// Sprint 3b (final targeted correction) — ONE factor-specific,
// deterministic assessment per price-path field (MFE, MAE, TARGET_TOUCH,
// STOP_TOUCH). Every UI element that describes one of these four fields
// (the price-path summary card, "What You Can Learn", the corresponding
// governed-factor section row, and the target-hit explanatory note) MUST
// consume the SAME resolved assessment object — never recompute or
// reclassify the same factor independently, which is exactly how the
// prior version produced contradictions (e.g. "Stop touched: N/A" in the
// card while the claim simultaneously appeared under CONFIRMED).
//
// Root-cause defect this replaces: the prior resolver evaluated one
// undifferentiated collection of price_path_limitations + evidence_gaps +
// warnings and returned the FIRST match — so a report-wide entry-snapshot
// limitation ("entry evidence completeness is LIMITED", an
// evidence_gaps-only signal) incorrectly won for MFE/MAE/target-touch/
// stop-touch, which are holding-period/boundary-verification factors,
// never entry-time factors. This module deliberately never accepts
// evidence_gaps as an input — only price_path_limitations and
// provider-coverage warnings, which are the only signals that can
// legitimately describe these four factors.
export type PriceFieldFactor = "MFE" | "MAE" | "TARGET_TOUCH" | "STOP_TOUCH";
export type PriceFieldAvailabilityState = "AVAILABLE" | "NOT_APPLICABLE" | "UNAVAILABLE";
export type LearningClassification =
  | "CONFIRMED" | "SUPPORTED_BUT_NOT_PROVEN" | "NOT_ESTABLISHED" | "DATA_NEEDED";

export interface PriceFieldAssessment {
  factor: PriceFieldFactor;
  availability: PriceFieldAvailabilityState;
  reasonCategory: string;
  explanation: string;
  learningClassification: LearningClassification;
  dataNeeded?: string;
  /** True only for the TARGET_HIT/STOP_LOSS + null-touch case — used to render the "not a contradiction" note. */
  isOperationalCloseVsUnverifiedPriceGap: boolean;
}

export interface PriceFieldAssessmentInput {
  factor: PriceFieldFactor;
  /** The field's own top-level value — null/undefined means unavailable. */
  value: unknown;
  /** report.availability (lifecycle) — distinct from report.status. */
  reportAvailability: string | null;
  /** postmortem.exit_mechanism — used only for the TARGET_HIT/STOP_LOSS operational-evidence case. */
  exitMechanism: string | null;
  /** price_path.price_path_limitations — NEVER report.evidence_gaps. */
  pricePathLimitations: string[];
  /** report.warnings, filtered to provider-coverage-shaped entries by the caller (or passed as-is; this function itself only pattern-matches on price/coverage vocabulary, never entry-snapshot vocabulary). */
  warnings: string[];
}

const FALLBACK_EXPLANATION = "Reason unavailable was not supplied by this report.";

function hasHoldingPeriodCoverageLimitation(pricePathLimitations: string[], warnings: string[]): boolean {
  const haystack = [...pricePathLimitations, ...warnings];
  return haystack.some((text) =>
    /provider|compatible|coverage|window|bars|crossing|boundary/i.test(text)
  );
}

export function resolvePriceFieldAssessment(input: PriceFieldAssessmentInput): PriceFieldAssessment {
  const { factor, value, reportAvailability, exitMechanism, pricePathLimitations, warnings } = input;

  // Tier 1 — the value itself.
  if (value !== null && value !== undefined) {
    return {
      factor, availability: "AVAILABLE", reasonCategory: "AVAILABLE", explanation: "",
      learningClassification: "CONFIRMED", isOperationalCloseVsUnverifiedPriceGap: false,
    };
  }

  // Tier 2 — report-level availability.
  if (reportAvailability && reportAvailability !== "READY") {
    return {
      factor, availability: "NOT_APPLICABLE", reasonCategory: "NOT_APPLICABLE",
      explanation: "Not applicable — this report has not reached a state where this factor could be computed.",
      learningClassification: "DATA_NEEDED", isOperationalCloseVsUnverifiedPriceGap: false,
    };
  }

  // Tier 3 — target-hit / stop-hit operational evidence. Scoped ONLY to
  // TARGET_TOUCH+TARGET_HIT and STOP_TOUCH+STOP_LOSS — never applied to
  // MFE/MAE, and never presented as a contradiction.
  if (factor === "TARGET_TOUCH" && exitMechanism === "TARGET_HIT") {
    return {
      factor, availability: "UNAVAILABLE", reasonCategory: "TARGET_HIT_UNVERIFIED",
      explanation:
        "The trade was recorded as a target hit, but the available price path could not independently "
        + "confirm the target crossing.",
      learningClassification: "SUPPORTED_BUT_NOT_PROVEN",
      dataNeeded: "Independently verifiable target boundary crossing evidence.",
      isOperationalCloseVsUnverifiedPriceGap: true,
    };
  }
  if (factor === "STOP_TOUCH" && exitMechanism === "STOP_LOSS") {
    return {
      factor, availability: "UNAVAILABLE", reasonCategory: "STOP_LOSS_UNVERIFIED",
      explanation:
        "The trade was recorded as a stop hit, but the available price path could not independently "
        + "confirm the stop crossing.",
      learningClassification: "SUPPORTED_BUT_NOT_PROVEN",
      dataNeeded: "Independently verifiable stop boundary crossing evidence.",
      isOperationalCloseVsUnverifiedPriceGap: true,
    };
  }

  // Tier 4 — holding-period price-path acquisition coverage. This is the
  // ONLY tier that reads pricePathLimitations/warnings — never
  // evidence_gaps, which the caller must not pass into this function at
  // all (see module comment).
  const hasCoverageGap = hasHoldingPeriodCoverageLimitation(pricePathLimitations, warnings);
  if (hasCoverageGap && (factor === "MFE" || factor === "MAE")) {
    return {
      factor, availability: "UNAVAILABLE", reasonCategory: "HOLDING_PERIOD_COVERAGE_UNAVAILABLE",
      explanation: "Complete holding-period price coverage was unavailable.",
      learningClassification: "NOT_ESTABLISHED",
      dataNeeded: "Complete holding-period price bars.",
      isOperationalCloseVsUnverifiedPriceGap: false,
    };
  }
  if (hasCoverageGap && (factor === "TARGET_TOUCH" || factor === "STOP_TOUCH")) {
    const levelWord = factor === "TARGET_TOUCH" ? "target" : "stop";
    return {
      factor, availability: "UNAVAILABLE", reasonCategory: "BOUNDARY_EVIDENCE_INSUFFICIENT",
      explanation: `The available price path was insufficient to determine whether the ${levelWord} level was touched.`,
      learningClassification: "NOT_ESTABLISHED",
      dataNeeded: "Independently verifiable target and stop boundary crossings.",
      isOperationalCloseVsUnverifiedPriceGap: false,
    };
  }

  // Tier 5 — final safe fallback. Never invented, never derived from
  // entry-evidence limitations or P&L.
  return {
    factor, availability: "UNAVAILABLE", reasonCategory: "FALLBACK", explanation: FALLBACK_EXPLANATION,
    learningClassification: "DATA_NEEDED", isOperationalCloseVsUnverifiedPriceGap: false,
  };
}

export function resolveAllPriceFieldAssessments(input: {
  mfe: unknown; mae: unknown; targetTouch: unknown; stopTouch: unknown;
  reportAvailability: string | null; exitMechanism: string | null;
  pricePathLimitations: string[]; warnings: string[];
}): Record<PriceFieldFactor, PriceFieldAssessment> {
  const shared = {
    reportAvailability: input.reportAvailability, exitMechanism: input.exitMechanism,
    pricePathLimitations: input.pricePathLimitations, warnings: input.warnings,
  };
  return {
    MFE: resolvePriceFieldAssessment({ factor: "MFE", value: input.mfe, ...shared }),
    MAE: resolvePriceFieldAssessment({ factor: "MAE", value: input.mae, ...shared }),
    TARGET_TOUCH: resolvePriceFieldAssessment({ factor: "TARGET_TOUCH", value: input.targetTouch, ...shared }),
    STOP_TOUCH: resolvePriceFieldAssessment({ factor: "STOP_TOUCH", value: input.stopTouch, ...shared }),
  };
}
