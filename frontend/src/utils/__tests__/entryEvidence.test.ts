import { describe, it, expect } from "vitest";
import { buildEntryEvidenceFromPrediction, buildEntryEvidenceFromDailyPick } from "@/utils/entryEvidence";
import type { Prediction } from "@/utils/api";

function fullPrediction(): Prediction {
  return {
    symbol: "AAPL", market: "US", horizon: "medium", signal: "BUY", confidence: 82,
    current_price: 190.5, target_price: 210, generated_at: "2026-08-01T10:00:00Z",
    reasoning: [{ indicator: "RSI", signal: "BUY", reason: "oversold" }],
    technical: { overall: "BUY", rsi: 28.4, macd_diff: 0.6 },
    fundamental_score: { score: 71, reasons: [] },
    sentiment_score: { score: 0, label: "NEUTRAL", bullish: 0.5, bearish: 0.5 },
    market_regime: { trend: "RISK_ON", score_adj: -1.5, reason: "vol spike" },
    trade_levels: { stop_loss: 180, take_profit: 220, entry_low: 188, entry_high: 192 },
  };
}

describe("buildEntryEvidenceFromPrediction", () => {
  it("maps every field exactly from a full prediction", () => {
    const evidence = buildEntryEvidenceFromPrediction(fullPrediction());
    expect(evidence).toEqual({
      recommendation_signal: "BUY",
      recommendation_generated_at: "2026-08-01T10:00:00Z",
      recommendation_reference_price: 190.5,
      recommendation_entry_low: 188,
      recommendation_entry_high: 192,
      recommended_stop_loss: 180,
      recommended_target_price: 220,
      confidence_score: 82,
      technical_signal: "BUY",
      technical_rsi: 28.4,
      technical_macd_diff: 0.6,
      fundamental_score: 71,
      sentiment_score: 0,
      sentiment_label: "NEUTRAL",
      market_regime_trend: "RISK_ON",
      market_regime_score_adj: -1.5,
      market_regime_reason: "vol spike",
      recommendation_reasoning: [{ indicator: "RSI", signal: "BUY", reason: "oversold" }],
      daily_pick_run_id: null,
      daily_pick_rank: null,
      model_version: null,
    });
  });

  it("returns null when there is no prediction", () => {
    expect(buildEntryEvidenceFromPrediction(null)).toBeNull();
    expect(buildEntryEvidenceFromPrediction(undefined)).toBeNull();
  });

  it("leaves fields missing (null) on a partial prediction instead of fabricating defaults", () => {
    const partial = {
      symbol: "AAPL", market: "US", horizon: "medium", signal: "HOLD", confidence: 50,
      current_price: 100, target_price: 100,
      reasoning: [],
      technical: { overall: "HOLD", rsi: undefined as any, macd_diff: undefined as any },
      fundamental_score: { score: undefined as any, reasons: [] },
      sentiment_score: { score: undefined as any, label: undefined as any, bullish: 0, bearish: 0 },
    } as unknown as Prediction;
    const evidence = buildEntryEvidenceFromPrediction(partial);
    expect(evidence?.recommendation_generated_at).toBeNull();
    expect(evidence?.recommended_stop_loss).toBeNull();
    expect(evidence?.recommended_target_price).toBeNull();
    expect(evidence?.recommendation_entry_low).toBeNull();
    expect(evidence?.recommendation_entry_high).toBeNull();
    expect(evidence?.technical_rsi).toBeNull();
    expect(evidence?.fundamental_score).toBeNull();
    expect(evidence?.sentiment_score).toBeNull();
    expect(evidence?.market_regime_trend).toBeNull();
    expect(evidence?.recommendation_reasoning).toBeNull();
  });

  it("does not drop a genuine numeric 0 (sentiment_score) via truthiness", () => {
    const evidence = buildEntryEvidenceFromPrediction(fullPrediction());
    expect(evidence?.sentiment_score).toBe(0);
  });

  it("never submits NaN", () => {
    const p = fullPrediction();
    (p.technical as any).rsi = NaN;
    (p.fundamental_score as any).score = NaN;
    const evidence = buildEntryEvidenceFromPrediction(p);
    expect(evidence?.technical_rsi).toBeNull();
    expect(evidence?.fundamental_score).toBeNull();
  });

  it("never submits Infinity", () => {
    const p = fullPrediction();
    (p.market_regime as any).score_adj = Infinity;
    (p as any).confidence = -Infinity;
    const evidence = buildEntryEvidenceFromPrediction(p);
    expect(evidence?.market_regime_score_adj).toBeNull();
    expect(evidence?.confidence_score).toBeNull();
  });

  it("handles a negative value (market_regime_score_adj) per field contract, not generic truthiness", () => {
    const p = fullPrediction();
    (p.market_regime as any).score_adj = -3.2;
    const evidence = buildEntryEvidenceFromPrediction(p);
    expect(evidence?.market_regime_score_adj).toBe(-3.2);
  });

  it("does not mutate the source Prediction object", () => {
    const p = fullPrediction();
    const snapshotBefore = JSON.parse(JSON.stringify(p));
    buildEntryEvidenceFromPrediction(p);
    expect(p).toEqual(snapshotBefore);
  });

  it("never invents daily_pick_run_id, daily_pick_rank, or model_version", () => {
    const evidence = buildEntryEvidenceFromPrediction(fullPrediction());
    expect(evidence?.daily_pick_run_id).toBeNull();
    expect(evidence?.daily_pick_rank).toBeNull();
    expect(evidence?.model_version).toBeNull();
  });
});

describe("buildEntryEvidenceFromDailyPick", () => {
  it("maps available Daily Pick fields and never derives run_id/rank from list position", () => {
    const evidence = buildEntryEvidenceFromDailyPick({
      price: 100, entry_low: 98, entry_high: 102, stop_loss: 90, target: 120,
      confidence: 75, fund_score: 60, sentiment: "BULLISH",
      reasoning: [{ indicator: "MACD", signal: "BUY", reason: "cross" }],
      generated_at: "2026-08-01T00:00:00Z", horizon: "short",
    });
    expect(evidence?.recommendation_signal).toBe("BUY");
    expect(evidence?.recommendation_reference_price).toBe(100);
    expect(evidence?.recommended_stop_loss).toBe(90);
    expect(evidence?.recommended_target_price).toBe(120);
    expect(evidence?.daily_pick_run_id).toBeNull();
    expect(evidence?.daily_pick_rank).toBeNull();
    // Fields the Daily Pick payload doesn't genuinely carry stay null
    expect(evidence?.technical_rsi).toBeNull();
    expect(evidence?.sentiment_score).toBeNull();
  });

  it("returns null for a missing pick", () => {
    expect(buildEntryEvidenceFromDailyPick(null)).toBeNull();
  });
});
