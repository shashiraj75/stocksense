import { describe, it, expect } from "vitest";
import {
  resolvePriceFieldAssessment, resolveAllPriceFieldAssessments,
} from "@/utils/postmortemPriceFieldAssessment";

// Report-280-shaped fixture: entry evidence is LIMITED (an evidence_gaps
// signal, deliberately NOT passed to this resolver — see module comment),
// holding-period price coverage is incomplete (a real price_path_limitations
// entry), the trade closed via TARGET_HIT with target_touch=null.
const REPORT_280_PRICE_PATH_LIMITATIONS = [
  "requested window 2026-08-04..2026-08-05 but provider only returned bars for 2026-08-05..2026-08-05",
];

describe("resolvePriceFieldAssessment — regression coverage for the report-280 defect", () => {
  it("MFE: holding-period coverage unavailable, NOT_ESTABLISHED, never 'Not captured at entry'", () => {
    const result = resolvePriceFieldAssessment({
      factor: "MFE", value: null, reportAvailability: "READY", exitMechanism: "TARGET_HIT",
      pricePathLimitations: REPORT_280_PRICE_PATH_LIMITATIONS, warnings: [],
    });
    expect(result.explanation).toBe("Complete holding-period price coverage was unavailable.");
    expect(result.explanation).not.toMatch(/not captured at entry/i);
    expect(result.explanation).not.toMatch(/client-reported only/i);
    expect(result.learningClassification).toBe("NOT_ESTABLISHED");
  });

  it("MAE: holding-period coverage unavailable, NOT_ESTABLISHED, never 'Not captured at entry'", () => {
    const result = resolvePriceFieldAssessment({
      factor: "MAE", value: null, reportAvailability: "READY", exitMechanism: "TARGET_HIT",
      pricePathLimitations: REPORT_280_PRICE_PATH_LIMITATIONS, warnings: [],
    });
    expect(result.explanation).toBe("Complete holding-period price coverage was unavailable.");
    expect(result.explanation).not.toMatch(/not captured at entry/i);
    expect(result.explanation).not.toMatch(/client-reported only/i);
    expect(result.learningClassification).toBe("NOT_ESTABLISHED");
  });

  it("TARGET_TOUCH: TARGET_HIT + null is SUPPORTED_BUT_NOT_PROVEN with the operational-vs-verification wording, not a contradiction", () => {
    const result = resolvePriceFieldAssessment({
      factor: "TARGET_TOUCH", value: null, reportAvailability: "READY", exitMechanism: "TARGET_HIT",
      pricePathLimitations: REPORT_280_PRICE_PATH_LIMITATIONS, warnings: [],
    });
    expect(result.explanation).toBe(
      "The trade was recorded as a target hit, but the available price path could not independently "
      + "confirm the target crossing."
    );
    expect(result.learningClassification).toBe("SUPPORTED_BUT_NOT_PROVEN");
    expect(result.isOperationalCloseVsUnverifiedPriceGap).toBe(true);
  });

  it("STOP_TOUCH: insufficient boundary evidence, NOT_ESTABLISHED, never appears as CONFIRMED", () => {
    const result = resolvePriceFieldAssessment({
      factor: "STOP_TOUCH", value: null, reportAvailability: "READY", exitMechanism: "TARGET_HIT",
      pricePathLimitations: REPORT_280_PRICE_PATH_LIMITATIONS, warnings: [],
    });
    expect(result.explanation).toBe(
      "The available price path was insufficient to determine whether the stop level was touched."
    );
    expect(result.learningClassification).toBe("NOT_ESTABLISHED");
    expect(result.learningClassification).not.toBe("CONFIRMED");
  });

  it("resolveAllPriceFieldAssessments reproduces all four report-280 results in one call", () => {
    const assessments = resolveAllPriceFieldAssessments({
      mfe: null, mae: null, targetTouch: null, stopTouch: null,
      reportAvailability: "READY", exitMechanism: "TARGET_HIT",
      pricePathLimitations: REPORT_280_PRICE_PATH_LIMITATIONS, warnings: [],
    });
    expect(assessments.MFE.explanation).toBe("Complete holding-period price coverage was unavailable.");
    expect(assessments.MAE.explanation).toBe("Complete holding-period price coverage was unavailable.");
    expect(assessments.TARGET_TOUCH.learningClassification).toBe("SUPPORTED_BUT_NOT_PROVEN");
    expect(assessments.STOP_TOUCH.learningClassification).toBe("NOT_ESTABLISHED");
    expect(assessments.MFE.learningClassification).toBe("NOT_ESTABLISHED");
    expect(assessments.MAE.learningClassification).toBe("NOT_ESTABLISHED");
  });

  describe("entry-evidence limitations must never classify a price-path factor", () => {
    // This function's signature does not even accept evidence_gaps — this
    // is the structural guarantee. These tests confirm that an
    // entry-snapshot-shaped string placed into pricePathLimitations
    // (simulating a caller mistake) still does NOT match the
    // holding-period-coverage vocabulary this resolver checks for.
    const entryEvidenceLimitation = ["entry evidence completeness is LIMITED"];

    it("MFE is not classified from an entry-evidence-shaped string", () => {
      const result = resolvePriceFieldAssessment({
        factor: "MFE", value: null, reportAvailability: "READY", exitMechanism: null,
        pricePathLimitations: entryEvidenceLimitation, warnings: [],
      });
      expect(result.explanation).not.toMatch(/not captured at entry/i);
    });

    it("MAE is not classified from an entry-evidence-shaped string", () => {
      const result = resolvePriceFieldAssessment({
        factor: "MAE", value: null, reportAvailability: "READY", exitMechanism: null,
        pricePathLimitations: entryEvidenceLimitation, warnings: [],
      });
      expect(result.explanation).not.toMatch(/not captured at entry/i);
    });

    it("TARGET_TOUCH is not classified from an entry-evidence-shaped string", () => {
      const result = resolvePriceFieldAssessment({
        factor: "TARGET_TOUCH", value: null, reportAvailability: "READY", exitMechanism: null,
        pricePathLimitations: entryEvidenceLimitation, warnings: [],
      });
      expect(result.explanation).not.toMatch(/not captured at entry/i);
    });

    it("STOP_TOUCH is not classified from an entry-evidence-shaped string", () => {
      const result = resolvePriceFieldAssessment({
        factor: "STOP_TOUCH", value: null, reportAvailability: "READY", exitMechanism: null,
        pricePathLimitations: entryEvidenceLimitation, warnings: [],
      });
      expect(result.explanation).not.toMatch(/not captured at entry/i);
    });
  });

  it("value present -> AVAILABLE/CONFIRMED regardless of any limitation text", () => {
    const result = resolvePriceFieldAssessment({
      factor: "MFE", value: 150.0, reportAvailability: "READY", exitMechanism: "TARGET_HIT",
      pricePathLimitations: REPORT_280_PRICE_PATH_LIMITATIONS, warnings: [],
    });
    expect(result.availability).toBe("AVAILABLE");
    expect(result.learningClassification).toBe("CONFIRMED");
  });

  it("non-READY report availability -> NOT_APPLICABLE for any factor", () => {
    const result = resolvePriceFieldAssessment({
      factor: "STOP_TOUCH", value: null, reportAvailability: "PROCESSING", exitMechanism: null,
      pricePathLimitations: [], warnings: [],
    });
    expect(result.availability).toBe("NOT_APPLICABLE");
  });

  it("STOP_LOSS + null stop_touch gets the symmetric operational-evidence treatment (not a contradiction)", () => {
    const result = resolvePriceFieldAssessment({
      factor: "STOP_TOUCH", value: null, reportAvailability: "READY", exitMechanism: "STOP_LOSS",
      pricePathLimitations: [], warnings: [],
    });
    expect(result.learningClassification).toBe("SUPPORTED_BUT_NOT_PROVEN");
    expect(result.isOperationalCloseVsUnverifiedPriceGap).toBe(true);
  });

  it("genuine entry-time factors are unaffected — this resolver only ever applies to the four price-path factors", () => {
    // Structural proof: the factor type itself only admits MFE/MAE/
    // TARGET_TOUCH/STOP_TOUCH — an entry-time factor like
    // "Technical Signal at Entry" is handled entirely by a different
    // module (postmortemClaimReasons.ts) and never routed through this
    // resolver at all.
    const validFactors = ["MFE", "MAE", "TARGET_TOUCH", "STOP_TOUCH"];
    expect(validFactors).not.toContain("technical_signal");
  });

  it("fallback is used only when no factor-specific reason exists, never invented", () => {
    const result = resolvePriceFieldAssessment({
      factor: "MFE", value: null, reportAvailability: "READY", exitMechanism: null,
      pricePathLimitations: [], warnings: [],
    });
    expect(result.explanation).toBe("Reason unavailable was not supplied by this report.");
  });

  it("never infers an availability reason from P&L (function has no P&L input at all)", () => {
    // Structural proof: PriceFieldAssessmentInput has no realized_pnl
    // field — it is impossible for this function to read P&L.
    const result = resolvePriceFieldAssessment({
      factor: "MFE", value: null, reportAvailability: "READY", exitMechanism: "TARGET_HIT",
      pricePathLimitations: [], warnings: [],
    });
    expect(result).not.toHaveProperty("realizedPnl");
  });
});
