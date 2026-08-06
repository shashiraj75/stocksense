import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ClaimExplanationLayer } from "@/components/postmortem/ClaimExplanationLayer";
import type { PostmortemClaim } from "@/utils/api";

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
});
