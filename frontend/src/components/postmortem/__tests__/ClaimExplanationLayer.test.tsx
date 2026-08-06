import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { ClaimExplanationLayer, bucketClaimsForWhatYouCanLearn } from "@/components/postmortem/ClaimExplanationLayer";
import type { PostmortemClaim } from "@/utils/api";
import type { PriceFieldAssessment, PriceFieldFactor } from "@/utils/postmortemPriceFieldAssessment";

function makeAssessment(
  factor: PriceFieldFactor, learningClassification: PriceFieldAssessment["learningClassification"]
): PriceFieldAssessment {
  return {
    factor, availability: "UNAVAILABLE", reasonCategory: "TEST", explanation: "test explanation",
    learningClassification, isOperationalCloseVsUnverifiedPriceGap: false,
  };
}

function makeAllNotEstablishedAssessments(): Record<PriceFieldFactor, PriceFieldAssessment> {
  return {
    MFE: makeAssessment("MFE", "NOT_ESTABLISHED"),
    MAE: makeAssessment("MAE", "NOT_ESTABLISHED"),
    TARGET_TOUCH: makeAssessment("TARGET_TOUCH", "NOT_ESTABLISHED"),
    STOP_TOUCH: makeAssessment("STOP_TOUCH", "NOT_ESTABLISHED"),
  };
}

function makeClaim(overrides: Partial<PostmortemClaim>): PostmortemClaim {
  return {
    claim_id: overrides.claim_id ?? Math.random().toString(36),
    report_section: "contributor_assessments",
    factor: "STOCK_SELECTION",
    claim_text: "Insufficient evidence to determine this factor reliably.",
    evidence_class: "INSUFFICIENT_EVIDENCE",
    confidence_band: "NOT_ASSESSABLE",
    supporting_evidence_ids: [],
    opposing_evidence_ids: [],
    missing_evidence: [],
    contradiction_flags: [],
    rule_id: "CONTRIBUTOR_UNACQUIRED_EVIDENCE_001",
    rule_version: "1.0.0",
    limitations: [],
    ...overrides,
  };
}

describe("ClaimExplanationLayer", () => {
  it("renders every claim exactly once, each with a visible factor title", () => {
    const categories = [
      "STOCK_SELECTION", "ENTRY_TIMING", "MARKET_CONDITIONS", "SECTOR_CONDITIONS",
      "VOLATILITY", "LIQUIDITY", "NEWS_OR_EVENT", "PRICE_NOISE", "ADMINISTRATIVE_ACTION",
      "POSITION_MANAGEMENT", "EXIT_LOGIC",
    ];
    const claims = categories.map((c, i) => makeClaim({ claim_id: `c${i}`, factor: c }));

    render(<ClaimExplanationLayer claims={claims} evidenceById={new Map()} />);

    const titles = screen.getAllByTestId("factor-title");
    expect(titles.length).toBe(claims.length);
    for (const category of categories) {
      // every factor is individually named, never merged into a generic row
      expect(screen.getAllByText(new RegExp(category.replace(/_/g, ".").slice(0, 6), "i")).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText(/^insufficient evidence$/i)).not.toBeInTheDocument();
  });

  it("shows an unknown future factor humanized rather than dropping it", () => {
    const claims = [makeClaim({ report_section: "future_section", factor: "SOME_NEW_FUTURE_FACTOR" })];
    render(<ClaimExplanationLayer claims={claims} evidenceById={new Map()} />);
    const title = screen.getByTestId("factor-title");
    expect(title).toHaveTextContent(/Some new future factor/i);
  });

  it("groups by report_section without dropping any claim", () => {
    const claims = [
      makeClaim({ claim_id: "a", report_section: "signal_scorecard", factor: "technical_signal" }),
      makeClaim({ claim_id: "b", report_section: "contradictions", factor: "entry_signal_agreement" }),
      makeClaim({ claim_id: "c", report_section: "primary_contributor", factor: "primary_contributor" }),
    ];
    render(<ClaimExplanationLayer claims={claims} evidenceById={new Map()} />);
    expect(screen.getAllByTestId("factor-title").length).toBe(3);
  });

  // Regression coverage for defects found during preview QA (targeted
  // correction pass).
  describe("no current governed factor renders '(uncurated)'", () => {
    const __dirname = dirname(fileURLToPath(import.meta.url));
    const MATRIX_PATH = resolve(
      __dirname, "../../../../../backend/tests/unit/postmortem_evidence_coverage_matrix.json"
    );

    it("every (report_section, internal_factor) in the evidence-coverage matrix renders without the uncurated marker", () => {
      const raw = JSON.parse(readFileSync(MATRIX_PATH, "utf-8"));
      const entries: { report_section: string; internal_factor: string }[] = raw.entries;
      expect(entries.length).toBeGreaterThan(0);

      const claims = entries.map((e, i) =>
        makeClaim({ claim_id: `matrix-${i}`, report_section: e.report_section, factor: e.internal_factor })
      );
      render(<ClaimExplanationLayer claims={claims} evidenceById={new Map()} />);
      expect(screen.queryByText(/\(uncurated\)/i)).not.toBeInTheDocument();
    });
  });

  describe("Layer 2 contains no internal implementation language", () => {
    it("does not render raw snake_case missing_evidence/limitations text, 'this codebase', a sprint reference, or the raw evidence-class constant", () => {
      const claims = [
        makeClaim({
          claim_id: "a", report_section: "signal_scorecard", factor: "technical_signal",
          missing_evidence: ["technical_signal was not captured in this trade's entry snapshot"],
        }),
        makeClaim({
          claim_id: "b", report_section: "contributor_assessments", factor: "MARKET_CONDITIONS",
          limitations: [
            "holding-period price-path and/or benchmark evidence is not acquired by this codebase yet " +
            "(a later, separately-scoped sprint)",
          ],
        }),
        makeClaim({
          claim_id: "c", report_section: "governed_price_path", factor: "target_touch",
          limitations: ["no compatible crossing of the supplied value was observed in the acquired evidence window"],
        }),
      ];
      render(<ClaimExplanationLayer claims={claims} evidenceById={new Map()} />);

      const body = document.body.textContent ?? "";
      expect(body).not.toMatch(/technical_signal/);
      expect(body).not.toMatch(/this codebase/i);
      expect(body).not.toMatch(/sprint/i);
      expect(body).not.toMatch(/\buncurated\b/i);
      expect(body).not.toMatch(/INSUFFICIENT_EVIDENCE/);
      expect(body).not.toMatch(/governed_order_conclusion|ORDER_UNAVAILABLE/);
    });

    it("preserves the exact original governed text inside the expanded Layer-3 detail panel", () => {
      const rawText = "technical_signal was not captured in this trade's entry snapshot";
      const claims = [
        makeClaim({
          claim_id: "a", report_section: "signal_scorecard", factor: "technical_signal",
          missing_evidence: [rawText],
        }),
      ];
      render(<ClaimExplanationLayer claims={claims} evidenceById={new Map()} />);
      expect(screen.queryByText(new RegExp(rawText))).not.toBeInTheDocument();

      fireEvent.click(screen.getByText("Details"));
      expect(screen.getByText(new RegExp(rawText))).toBeInTheDocument();
    });
  });

  describe("bucketClaimsForWhatYouCanLearn — single-source-of-truth via priceFieldAssessments", () => {
    it("does not classify stop_touch as CONFIRMED when its assessment is NOT_ESTABLISHED", () => {
      const claims = [
        makeClaim({
          claim_id: "stop", report_section: "governed_price_path", factor: "stop_touch",
          evidence_class: "MECHANICALLY_VERIFIED", confidence_band: "HIGH",
          claim_text: "No compatible crossing of the stop level was observed.",
        }),
      ];
      // makeAllNotEstablishedAssessments() supplies assessments for all 4
      // price fields; only stop_touch has a matching claim here, so the
      // other 3 (MFE/MAE/TARGET_TOUCH) now correctly appear as standalone
      // items too (defect-A fix) — 4 total, none CONFIRMED.
      const buckets = bucketClaimsForWhatYouCanLearn(claims, makeAllNotEstablishedAssessments());
      expect(buckets.confirmed).toHaveLength(0);
      expect(buckets.notEstablished).toHaveLength(4);
    });

    it("classifies stop_touch as CONFIRMED when its assessment says so (value genuinely present)", () => {
      const claims = [
        makeClaim({
          claim_id: "stop", report_section: "governed_price_path", factor: "stop_touch",
          evidence_class: "MECHANICALLY_VERIFIED", confidence_band: "HIGH",
        }),
      ];
      const assessments = makeAllNotEstablishedAssessments();
      assessments.STOP_TOUCH = makeAssessment("STOP_TOUCH", "CONFIRMED");
      const buckets = bucketClaimsForWhatYouCanLearn(claims, assessments);
      expect(buckets.confirmed).toHaveLength(1);
    });

    it("applies the same single-source-of-truth classification to target_touch, mfe, mae AND the unclaimed stop_touch", () => {
      const claims = [
        makeClaim({ claim_id: "target", report_section: "governed_price_path", factor: "target_touch", evidence_class: "MECHANICALLY_VERIFIED" }),
        makeClaim({ claim_id: "mfe", report_section: "price_path", factor: "mfe", evidence_class: "MECHANICALLY_VERIFIED" }),
        makeClaim({ claim_id: "mae", report_section: "price_path", factor: "mae", evidence_class: "MECHANICALLY_VERIFIED" }),
      ];
      // stop_touch has no matching claim here, so it appears as a
      // standalone price-field item (defect-A fix) — 4 total.
      const buckets = bucketClaimsForWhatYouCanLearn(claims, makeAllNotEstablishedAssessments());
      expect(buckets.confirmed).toHaveLength(0);
      expect(buckets.notEstablished).toHaveLength(4);
    });

    it("falls back to evidence_class-only bucketing when priceFieldAssessments is not provided", () => {
      const claims = [
        makeClaim({ claim_id: "stop", report_section: "governed_price_path", factor: "stop_touch", evidence_class: "MECHANICALLY_VERIFIED" }),
      ];
      const buckets = bucketClaimsForWhatYouCanLearn(claims);
      expect(buckets.confirmed).toHaveLength(1);
    });

    it("classifies target_touch as SUPPORTED BUT NOT PROVEN for the TARGET_HIT + null case", () => {
      const claims = [
        makeClaim({ claim_id: "target", report_section: "governed_price_path", factor: "target_touch", evidence_class: "DIRECTLY_OBSERVED" }),
      ];
      const assessments = makeAllNotEstablishedAssessments();
      assessments.TARGET_TOUCH = makeAssessment("TARGET_TOUCH", "SUPPORTED_BUT_NOT_PROVEN");
      const buckets = bucketClaimsForWhatYouCanLearn(claims, assessments);
      expect(buckets.supportedNotProven).toHaveLength(1);
      expect(buckets.confirmed).toHaveLength(0);
    });
  });
});
