import { describe, it, expect } from "vitest";
import { normalizeMarketKey, selectPayloadForMarket } from "../payloadMarketGuard";

// Cross-market retained-payload containment bypass fix. Confirmed production
// issue: React Query's `placeholderData: keepPreviousData` retains the
// previous market's Daily Picks payload across a market switch (the query
// key includes `market`), and nothing previously checked the retained
// payload's own `market` field against the newly-selected market before
// rendering it. A stale India payload (e.g. LUPIN) briefly rendered fully
// unguarded — entry/target/stop/upside/risk-reward/summary/Paper Trade all
// exposed — once `market` state had already flipped to "US".

describe("normalizeMarketKey", () => {
  it("normalizes exact canonical values", () => {
    expect(normalizeMarketKey("IN")).toBe("IN");
    expect(normalizeMarketKey("US")).toBe("US");
  });

  it("normalizes loose casing/whitespace safely", () => {
    expect(normalizeMarketKey("in")).toBe("IN");
    expect(normalizeMarketKey(" us ")).toBe("US");
    expect(normalizeMarketKey("Us")).toBe("US");
  });

  it("rejects unrecognized values rather than guessing", () => {
    expect(normalizeMarketKey("CRYPTO")).toBeNull();
    expect(normalizeMarketKey("")).toBeNull();
    expect(normalizeMarketKey(null)).toBeNull();
    expect(normalizeMarketKey(undefined)).toBeNull();
  });
});

describe("selectPayloadForMarket", () => {
  const inPayload = { market: "IN", picks: { short: [{ symbol: "LUPIN" }] } };
  const usPayload = { market: "US", picks: { short: [{ symbol: "NEM" }] } };

  it("returns the payload when its market matches the selected market", () => {
    expect(selectPayloadForMarket(inPayload, "IN")).toBe(inPayload);
    expect(selectPayloadForMarket(usPayload, "US")).toBe(usPayload);
  });

  it("returns undefined for a retained payload from a different market — the confirmed bug case", () => {
    // Selected US, but the retained payload is still India's (LUPIN).
    expect(selectPayloadForMarket(inPayload, "US")).toBeUndefined();
    // Selected IN, but the retained payload is still US's (NEM).
    expect(selectPayloadForMarket(usPayload, "IN")).toBeUndefined();
  });

  it("fails closed when the payload has no market field", () => {
    expect(selectPayloadForMarket({ picks: {} } as any, "IN")).toBeUndefined();
  });

  it("fails closed when the payload market is malformed/unrecognized", () => {
    expect(selectPayloadForMarket({ market: "in-dia", picks: {} } as any, "IN")).toBeUndefined();
  });

  it("fails closed when the payload itself is missing", () => {
    expect(selectPayloadForMarket(null, "IN")).toBeUndefined();
    expect(selectPayloadForMarket(undefined, "IN")).toBeUndefined();
  });

  it("fails closed when the selected market is missing/malformed — never assumes a match", () => {
    expect(selectPayloadForMarket(inPayload, "")).toBeUndefined();
    expect(selectPayloadForMarket(inPayload, undefined)).toBeUndefined();
  });

  it("case/whitespace-insensitive on both sides without fabricating a match across genuinely different markets", () => {
    expect(selectPayloadForMarket({ market: "in", picks: {} } as any, " IN ")).toEqual({ market: "in", picks: {} });
    expect(selectPayloadForMarket(inPayload, "us")).toBeUndefined();
  });
});
