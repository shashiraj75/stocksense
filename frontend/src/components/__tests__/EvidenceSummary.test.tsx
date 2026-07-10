import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EvidenceSummary } from "../EvidenceSummary";
import { Prediction, RecommendationConsolidation, SUPPORTED_RCI_CONTRACT_VERSION } from "@/utils/api";

function baseRci(overrides: Partial<RecommendationConsolidation> = {}): RecommendationConsolidation {
  return {
    contract_version: SUPPORTED_RCI_CONTRACT_VERSION,
    snapshot_id: "snap-1",
    computed_at: new Date().toISOString(),
    is_snapshot: false,
    thesis_state: "supported",
    engine_agreement: "4/4 engines agree",
    conflicts: [],
    coverage_notices: [],
    supporting_evidence: [],
    opposing_evidence: [],
    active_gates: [],
    unresolved_risk_flags: [],
    material_warnings: [],
    evidence_completeness_pct: 90,
    explanation_confidence_category: "high",
    narrative: "Evidence broadly supports this thesis.",
    engine_versions_used: {},
    ...overrides,
  };
}

function predictionWith(rci: RecommendationConsolidation | undefined): Prediction {
  return {
    symbol: "TEST",
    market: "IN",
    horizon: "short",
    signal: "BUY",
    confidence: 70,
    current_price: 100,
    target_price: 110,
    reasoning: [],
    technical: { overall: "BUY", rsi: 50, macd_diff: 0 },
    fundamental_score: { score: 60, reasons: [] },
    sentiment_score: { score: 60, label: "neutral", bullish: 0, bearish: 0 },
    recommendation_consolidation: rci,
  } as Prediction;
}

describe("EvidenceSummary", () => {
  it("renders nothing when RCI is absent (feature disabled / no data)", () => {
    const { container } = render(<EvidenceSummary prediction={predictionWith(undefined)} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a null/undefined prediction", () => {
    const { container: c1 } = render(<EvidenceSummary prediction={null} />);
    const { container: c2 } = render(<EvidenceSummary prediction={undefined} />);
    expect(c1).toBeEmptyDOMElement();
    expect(c2).toBeEmptyDOMElement();
  });

  it("renders nothing when RCI is malformed (unsupported contract version) — same as fully absent, never an error state", () => {
    const rci = baseRci({ contract_version: SUPPORTED_RCI_CONTRACT_VERSION + 1 });
    const { container } = render(<EvidenceSummary prediction={predictionWith(rci)} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows "Existing gate blocks the thesis" and renders active gates always-visible (not collapsed)', () => {
    const rci = baseRci({ active_gates: ["Business Quality hard-gate rejection"] });
    render(<EvidenceSummary prediction={predictionWith(rci)} />);
    expect(screen.getByText("Existing gate blocks the thesis")).toBeInTheDocument();
    expect(screen.getByText("Business Quality hard-gate rejection")).toBeInTheDocument();
  });

  it('shows "Important caution present" when material warnings exist but no active gate', () => {
    const rci = baseRci({ material_warnings: ["Elevated leverage vs. sector peers"] });
    render(<EvidenceSummary prediction={predictionWith(rci)} />);
    expect(screen.getByText("Important caution present")).toBeInTheDocument();
    expect(screen.getByText("Elevated leverage vs. sector peers")).toBeInTheDocument();
  });

  it('shows "Evidence is mixed" for a conflicted thesis with no gates/warnings/flags', () => {
    const rci = baseRci({ thesis_state: "conflicted" });
    render(<EvidenceSummary prediction={predictionWith(rci)} />);
    expect(screen.getByText("Evidence is mixed")).toBeInTheDocument();
  });

  it('shows "Limited evidence available" when completeness is below 50%', () => {
    const rci = baseRci({ evidence_completeness_pct: 30 });
    render(<EvidenceSummary prediction={predictionWith(rci)} />);
    expect(screen.getByText("Limited evidence available")).toBeInTheDocument();
  });

  it('falls back to "Evidence broadly aligned" when nothing else applies', () => {
    const rci = baseRci();
    render(<EvidenceSummary prediction={predictionWith(rci)} />);
    expect(screen.getByText("Evidence broadly aligned")).toBeInTheDocument();
  });

  it("de-duplicates coverage_notices within a single response", () => {
    const rci = baseRci({ coverage_notices: ["Growth Intelligence unavailable", "Growth Intelligence unavailable"] });
    render(<EvidenceSummary prediction={predictionWith(rci)} />);
    fireEvent.click(screen.getByRole("button", { name: "Show evidence detail" }));
    fireEvent.click(screen.getByRole("button", { name: "Coverage" }));
    expect(screen.getAllByText("Growth Intelligence unavailable")).toHaveLength(1);
  });

  it("keeps supporting/opposing evidence and conflicts collapsed behind 'Show evidence detail'", () => {
    const rci = baseRci({
      supporting_evidence: ["Strong ROCE"],
      opposing_evidence: ["Weak sentiment"],
      conflicts: [
        {
          conflict_id: "c1",
          headline: "Value trap pattern",
          narrative: "Cheap valuation offset by deteriorating fundamentals.",
          supporting_engines: ["valuation"],
          opposing_engines: ["business_quality"],
          severity: "high",
        },
      ],
    });
    render(<EvidenceSummary prediction={predictionWith(rci)} />);

    expect(screen.queryByText("Strong ROCE")).not.toBeInTheDocument();
    expect(screen.queryByText("Weak sentiment")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show evidence detail" }));

    expect(screen.getByText("Strong ROCE")).toBeInTheDocument();
    expect(screen.getByText("Weak sentiment")).toBeInTheDocument();
    expect(screen.getByText("Value trap pattern.")).toBeInTheDocument();
  });
});
