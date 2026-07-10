import { describe, it, expect } from "vitest";
import {
  getValidRecommendationConsolidation,
  getValidResearchReport,
  SUPPORTED_RCI_CONTRACT_VERSION,
  SUPPORTED_RESEARCH_REPORT_CONTRACT_VERSION,
  Prediction,
  RecommendationConsolidation,
  ResearchReport,
} from "../api";

// Sprint #012's release report named this as validated only via tsc/build/
// direct script execution against mocked scenarios — no committed test
// suite. This closes that gap for the single decision point both Evidence
// Summary and the Research Report consumer rely on (per Sprint #011 §3A/§3C
// and the Phase 3 UI/Copy spec): absent, null, malformed, incomplete, or an
// unsupported contract version must all degrade to "do not render."

function validRci(overrides: Partial<RecommendationConsolidation> = {}): RecommendationConsolidation {
  return {
    contract_version: SUPPORTED_RCI_CONTRACT_VERSION,
    snapshot_id: "snap-1",
    computed_at: "2026-07-10T00:00:00Z",
    is_snapshot: false,
    thesis_state: "supported",
    engine_agreement: "3/4 engines agree",
    conflicts: [],
    coverage_notices: [],
    supporting_evidence: ["Strong ROCE"],
    opposing_evidence: [],
    active_gates: [],
    unresolved_risk_flags: [],
    material_warnings: [],
    evidence_completeness_pct: 80,
    explanation_confidence_category: "high",
    narrative: "Evidence broadly supports this thesis.",
    engine_versions_used: { business_quality: "1.0" },
    ...overrides,
  };
}

function predictionWith(rci: unknown): Prediction {
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
    recommendation_consolidation: rci as RecommendationConsolidation,
  } as Prediction;
}

describe("getValidRecommendationConsolidation", () => {
  it("returns the object unchanged when it is fully valid", () => {
    const rci = validRci();
    expect(getValidRecommendationConsolidation(predictionWith(rci))).toEqual(rci);
  });

  it("returns null when prediction is null/undefined", () => {
    expect(getValidRecommendationConsolidation(null)).toBeNull();
    expect(getValidRecommendationConsolidation(undefined)).toBeNull();
  });

  it("returns null when recommendation_consolidation is absent", () => {
    const p = predictionWith(undefined);
    delete (p as Partial<Prediction>).recommendation_consolidation;
    expect(getValidRecommendationConsolidation(p)).toBeNull();
  });

  it("returns null when recommendation_consolidation is explicitly null", () => {
    expect(getValidRecommendationConsolidation(predictionWith(null))).toBeNull();
  });

  it("returns null when recommendation_consolidation is not an object", () => {
    expect(getValidRecommendationConsolidation(predictionWith("not an object"))).toBeNull();
    expect(getValidRecommendationConsolidation(predictionWith(42))).toBeNull();
  });

  it("returns null on an unsupported (future) contract version", () => {
    const rci = validRci({ contract_version: SUPPORTED_RCI_CONTRACT_VERSION + 1 });
    expect(getValidRecommendationConsolidation(predictionWith(rci))).toBeNull();
  });

  it("returns null when narrative is missing or empty (the one load-bearing field)", () => {
    expect(
      getValidRecommendationConsolidation(predictionWith(validRci({ narrative: "" }))),
    ).toBeNull();
    const missing = validRci();
    delete (missing as Partial<RecommendationConsolidation>).narrative;
    expect(getValidRecommendationConsolidation(predictionWith(missing))).toBeNull();
  });

  it.each([
    "coverage_notices",
    "supporting_evidence",
    "opposing_evidence",
    "active_gates",
    "unresolved_risk_flags",
    "material_warnings",
  ] as const)("returns null when %s is not a string array", (field) => {
    const rci = validRci({ [field]: [1, 2, 3] } as Partial<RecommendationConsolidation>);
    expect(getValidRecommendationConsolidation(predictionWith(rci))).toBeNull();
  });

  it("returns null when conflicts is not an array", () => {
    const rci = validRci({ conflicts: "not-an-array" as unknown as RecommendationConsolidation["conflicts"] });
    expect(getValidRecommendationConsolidation(predictionWith(rci))).toBeNull();
  });
});

function validReport(overrides: Partial<ResearchReport> = {}): ResearchReport {
  return {
    report_contract_version: SUPPORTED_RESEARCH_REPORT_CONTRACT_VERSION,
    snapshot_id: "snap-1",
    snapshot_hash: "hash-1",
    scope: { symbol: "TEST", market: "IN", exchange: "NSE", currency: "INR", horizon: "short" },
    generated_at: "2026-07-10T00:00:00Z",
    data_as_of: "2026-07-10T00:00:00Z",
    overall_status: "COMPLETE",
    sections: [
      { section_id: "executive_snapshot", title: "Snapshot", entries: [], evidence_ids: [], text: "text" },
    ],
    disclosures: [],
    ...overrides,
  };
}

function predictionWithReport(report: unknown): Prediction {
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
    research_report: report as ResearchReport,
  } as Prediction;
}

describe("getValidResearchReport", () => {
  it("returns the object unchanged when it is fully valid", () => {
    const report = validReport();
    expect(getValidResearchReport(predictionWithReport(report))).toEqual(report);
  });

  it("returns null when research_report is absent, null, or malformed", () => {
    expect(getValidResearchReport(predictionWithReport(undefined))).toBeNull();
    expect(getValidResearchReport(predictionWithReport(null))).toBeNull();
    expect(getValidResearchReport(predictionWithReport("nope"))).toBeNull();
  });

  it("returns null on an unsupported report_contract_version", () => {
    const report = validReport({ report_contract_version: "0.9" });
    expect(getValidResearchReport(predictionWithReport(report))).toBeNull();
  });

  it("returns null when sections is missing, non-array, or empty", () => {
    expect(
      getValidResearchReport(predictionWithReport(validReport({ sections: [] }))),
    ).toBeNull();
    expect(
      getValidResearchReport(
        predictionWithReport(validReport({ sections: "not-an-array" as unknown as ResearchReport["sections"] })),
      ),
    ).toBeNull();
  });

  it("returns null when a section is missing entries/evidence_ids arrays", () => {
    const report = validReport({
      sections: [
        { section_id: "executive_snapshot", title: "Snapshot", entries: undefined as never, evidence_ids: [], text: null },
      ],
    });
    expect(getValidResearchReport(predictionWithReport(report))).toBeNull();
  });
});
