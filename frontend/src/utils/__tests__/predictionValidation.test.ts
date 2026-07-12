import { describe, it, expect, vi, afterEach } from "vitest";
import {
  api, fetchPrediction, fetchSignalSummary, isValidPrediction, isValidSignalSummaryBody, PredictionError,
} from "../api";

// Production truthfulness hotfix — NDSL incident. Root cause: a cached
// backend {error: ...} dict was served as a plain HTTP 200, and the old
// pollPredictionEndpoint blindly `as T`-cast any 200 body, so a symbol
// with no `signal`/`confidence` fields (an unsupported/failed prediction)
// fell through Stock Detail's ternary to a fabricated "HOLD" with a blank
// confidence bar and an unrestricted Paper Trade button. These tests lock
// in the corrected contract: a 200 body must pass runtime validation
// before it's ever treated as a real Prediction/SignalSummary, and a
// non-200 (404/503) is surfaced as a typed, distinguishable PredictionError.
function fullPrediction(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    symbol: "RELIANCE", market: "IN", horizon: "medium",
    signal: "BUY", confidence: 72.5, current_price: 210.0, target_price: 240.0,
    reasoning: [], technical: { overall: "BUY", rsi: 55, macd_diff: 0.4 },
    fundamental_score: { score: 71, reasons: [] },
    sentiment_score: { score: 0.3, label: "positive", bullish: 4, bearish: 1 },
    ...overrides,
  };
}

describe("isValidPrediction", () => {
  it("rejects a 200 body containing an error key", () => {
    expect(isValidPrediction({ error: "No price data returned — Yahoo Finance may be rate-limiting." })).toBe(false);
  });

  it("rejects a body missing signal", () => {
    const { signal, ...rest } = fullPrediction();
    expect(isValidPrediction(rest)).toBe(false);
  });

  it("rejects a body missing confidence", () => {
    const { confidence, ...rest } = fullPrediction();
    expect(isValidPrediction(rest)).toBe(false);
  });

  it("rejects a body with a non-finite confidence", () => {
    expect(isValidPrediction(fullPrediction({ confidence: NaN }))).toBe(false);
    expect(isValidPrediction(fullPrediction({ confidence: "72" }))).toBe(false);
  });

  it("rejects a body with an invalid signal value", () => {
    expect(isValidPrediction(fullPrediction({ signal: "MAYBE" }))).toBe(false);
  });

  it("rejects a body missing current_price", () => {
    const { current_price, ...rest } = fullPrediction();
    expect(isValidPrediction(rest)).toBe(false);
  });

  it("accepts a fully-populated, valid RELIANCE-shaped prediction", () => {
    expect(isValidPrediction(fullPrediction())).toBe(true);
  });

  it("accepts HOLD and SELL as valid signals too", () => {
    expect(isValidPrediction(fullPrediction({ signal: "HOLD" }))).toBe(true);
    expect(isValidPrediction(fullPrediction({ signal: "SELL" }))).toBe(true);
  });

  it("rejects null/non-object bodies", () => {
    expect(isValidPrediction(null)).toBe(false);
    expect(isValidPrediction(undefined)).toBe(false);
    expect(isValidPrediction("BUY")).toBe(false);
  });
});

describe("isValidSignalSummaryBody", () => {
  it("accepts null signal/confidence — Portfolio's legitimate 'nothing cached yet' state", () => {
    expect(isValidSignalSummaryBody({ symbol: "TCS", market: "IN", horizon: "medium", signal: null, confidence: null })).toBe(true);
  });

  it("rejects a 200 body containing an error key even with symbol/market/horizon present", () => {
    expect(isValidSignalSummaryBody({ symbol: "NDSL", market: "IN", horizon: "medium", error: "No price data returned." })).toBe(false);
  });

  it("rejects a body missing an identifying field", () => {
    expect(isValidSignalSummaryBody({ market: "IN", horizon: "medium", signal: null, confidence: null })).toBe(false);
  });

  it("accepts a real resolved signal", () => {
    expect(isValidSignalSummaryBody({ symbol: "TCS", market: "IN", horizon: "medium", signal: "BUY", confidence: 65 })).toBe(true);
  });
});

describe("fetchPrediction / fetchSignalSummary — error contract", () => {
  afterEach(() => vi.restoreAllMocks());

  it("NDSL: a structured 404 SYMBOL_NOT_SUPPORTED throws a typed PredictionError, never resolves a fake prediction", async () => {
    vi.spyOn(api, "get").mockRejectedValue({
      response: {
        status: 404,
        data: { error: { code: "SYMBOL_NOT_SUPPORTED", message: "This symbol is not available in the supported NSE universe.", symbol: "NDSL", market: "IN" } },
      },
    });
    await expect(fetchPrediction("NDSL", "IN", "short")).rejects.toMatchObject({
      code: "SYMBOL_NOT_SUPPORTED", status: 404, symbol: "NDSL", market: "IN",
    });
    await expect(fetchPrediction("NDSL", "IN", "short")).rejects.toBeInstanceOf(PredictionError);
  });

  it("a structured 503 DATA_PROVIDER_UNAVAILABLE throws a distinguishable typed error for a valid symbol", async () => {
    vi.spyOn(api, "get").mockRejectedValue({
      response: {
        status: 503,
        data: { error: { code: "DATA_PROVIDER_UNAVAILABLE", message: "Prediction data is temporarily unavailable.", symbol: "RELIANCE", market: "IN" } },
      },
    });
    await expect(fetchPrediction("RELIANCE", "IN", "short")).rejects.toMatchObject({
      code: "DATA_PROVIDER_UNAVAILABLE", status: 503,
    });
  });

  it("a 200 body missing required fields (defensive — should be unreachable post-hotfix) is rejected, not returned as a prediction", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ status: 200, data: { error: "Insufficient price history" } });
    await expect(fetchPrediction("RELIANCE", "IN", "short")).rejects.toBeInstanceOf(PredictionError);
  });

  it("a valid RELIANCE prediction still renders exactly as before — 200 with a full valid body resolves normally", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ status: 200, data: fullPrediction() });
    const result = await fetchPrediction("RELIANCE", "IN", "medium");
    expect(result.signal).toBe("BUY");
    expect(result.confidence).toBe(72.5);
    expect(result.current_price).toBe(210.0);
  });

  it("a valid cold prediction still follows 202 -> polling -> 200", async () => {
    const spy = vi.spyOn(api, "get")
      .mockResolvedValueOnce({ status: 202, data: { status: "computing", retry_after: 0 } })
      .mockResolvedValueOnce({ status: 200, data: fullPrediction({ signal: "SELL", confidence: 40 }) });
    const onComputing = vi.fn();
    const result = await fetchPrediction("RELIANCE", "IN", "medium", onComputing);
    expect(onComputing).toHaveBeenCalledTimes(1);
    expect(result.signal).toBe("SELL");
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("fetchSignalSummary: NDSL's 404 also throws a typed, distinguishable PredictionError", async () => {
    vi.spyOn(api, "get").mockRejectedValue({
      response: { status: 404, data: { error: { code: "SYMBOL_NOT_SUPPORTED", message: "Not supported.", symbol: "NDSL", market: "IN" } } },
    });
    await expect(fetchSignalSummary("NDSL", "IN", "medium")).rejects.toMatchObject({ code: "SYMBOL_NOT_SUPPORTED", status: 404 });
  });

  it("fetchSignalSummary still tolerates a legitimate null-signal 200 body (Portfolio's non-blocking dash)", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      status: 200, data: { symbol: "OBSCURESTOCK", market: "IN", horizon: "medium", signal: null, confidence: null },
    });
    const result = await fetchSignalSummary("OBSCURESTOCK", "IN", "medium");
    expect(result.signal).toBeNull();
    expect(result.confidence).toBeNull();
  });
});
