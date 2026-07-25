import { describe, it, expect, afterEach } from "vitest";
import {
  getValidLeadershipContext,
  isMarketLeadershipUiEnabled,
  MarketLeadershipContext,
} from "@/utils/marketLeadership";

function ok(overrides: Partial<MarketLeadershipContext> = {}): MarketLeadershipContext {
  return {
    status: "ok",
    market: "IN",
    symbol: "TCS",
    as_of: "2026-07-20",
    rs: {
      symbol: "TCS", market: "IN", stock_rs_score: 80, stock_rs_percentile: 80,
      rs_trend: "IMPROVING", data_quality_status: "OK", methodology_version: "ml-rs-1.0.0",
    },
    trend: {
      state: "CONFIRMED_ADVANCE", extension_risk: "MODERATE", evidence_completeness: 0.9,
      methodology_version: "ml-tl-1.0.0",
    },
    why_now: {
      summary: "Stock RS is 80 and improving.", supporting_factors: ["Stock RS is 80 and improving."],
      caution_factors: [], data_freshness: "current", data_quality: "OK",
      methodology_version: "ml-ex-1.0.0",
    },
    ...overrides,
  };
}

describe("getValidLeadershipContext — single decision point", () => {
  it("returns null for undefined/null input", () => {
    expect(getValidLeadershipContext(undefined)).toBeNull();
    expect(getValidLeadershipContext(null)).toBeNull();
  });

  it("returns null when status is disabled", () => {
    expect(getValidLeadershipContext({ status: "disabled" })).toBeNull();
  });

  it("returns null when status is unavailable or error", () => {
    expect(getValidLeadershipContext({ status: "unavailable" })).toBeNull();
    expect(getValidLeadershipContext({ status: "error" })).toBeNull();
  });

  it("returns null when status is ok but rs/trend/why_now missing", () => {
    expect(getValidLeadershipContext({ status: "ok" })).toBeNull();
  });

  it("returns the data when fully formed", () => {
    const data = ok();
    expect(getValidLeadershipContext(data)).toEqual(data);
  });

  it("returns null when market or symbol missing even if status ok", () => {
    const data = ok();
    delete (data as any).market;
    expect(getValidLeadershipContext(data)).toBeNull();
  });
});

describe("isMarketLeadershipUiEnabled — fail-closed frontend presentation gate", () => {
  const ORIGINAL = process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED;

  afterEach(() => {
    if (ORIGINAL === undefined) {
      delete process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED;
    } else {
      process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = ORIGINAL;
    }
  });

  it("is false when the variable is absent", () => {
    delete process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED;
    expect(isMarketLeadershipUiEnabled()).toBe(false);
  });

  it("is false for an empty string", () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "";
    expect(isMarketLeadershipUiEnabled()).toBe(false);
  });

  it('is false for "0"', () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "0";
    expect(isMarketLeadershipUiEnabled()).toBe(false);
  });

  it('is false for "true" (only the literal "1" enables it)', () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "true";
    expect(isMarketLeadershipUiEnabled()).toBe(false);
  });

  it("is false for malformed/arbitrary text", () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "yes-please-enable";
    expect(isMarketLeadershipUiEnabled()).toBe(false);
  });

  it('is false for "1 " (trailing whitespace) — exact match only, no trimming', () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "1 ";
    expect(isMarketLeadershipUiEnabled()).toBe(false);
  });

  it('is true only for the exact string "1"', () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "1";
    expect(isMarketLeadershipUiEnabled()).toBe(true);
  });
});
