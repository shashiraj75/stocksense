import { describe, it, expect } from "vitest";
import { buildOpenTradeCountMap, getOpenTradeCount, openTradeCountKey } from "@/utils/openTradeCount";
import type { PaperTrade } from "@/utils/api";

// Paper Trading Repeat-Buy Awareness — Phase 1 (Daily Picks Open-Trade
// Count). Pure derivation tests for the count-identity rules in the
// ratified product spec: (market, symbol) compound key, all horizons
// summed, OPEN-only, no cross-market merging, no input mutation.

function trade(overrides: Partial<PaperTrade>): PaperTrade {
  return {
    id: 1, symbol: "TCS", market: "IN", quantity: 1, entry_price: 100,
    exit_price: null, stop_loss: null, target_price: null, status: "OPEN",
    signal: "BUY", horizon: "short", opened_at: "2026-08-01T00:00:00Z",
    closed_at: null, invested: 100, trade_management_mode: "manual",
    exit_reason: null,
    ...overrides,
  };
}

describe("getOpenTradeCount / buildOpenTradeCountMap", () => {
  it("returns 0 for an empty open_trades array", () => {
    expect(getOpenTradeCount([], "IN", "TCS")).toBe(0);
  });

  it("counts a single OPEN trade for the (market, symbol)", () => {
    const trades = [trade({ symbol: "TCS", market: "IN", horizon: "short" })];
    expect(getOpenTradeCount(trades, "IN", "TCS")).toBe(1);
  });

  it("sums multiple OPEN trades for the same (market, symbol)", () => {
    const trades = [
      trade({ id: 1, symbol: "TCS", market: "IN", horizon: "short" }),
      trade({ id: 2, symbol: "TCS", market: "IN", horizon: "short" }),
      trade({ id: 3, symbol: "TCS", market: "IN", horizon: "short" }),
    ];
    expect(getOpenTradeCount(trades, "IN", "TCS")).toBe(3);
  });

  it("sums OPEN trades across Short/Medium/Long horizons into one total", () => {
    const trades = [
      trade({ id: 1, symbol: "TCS", market: "IN", horizon: "short" }),
      trade({ id: 2, symbol: "TCS", market: "IN", horizon: "medium" }),
      trade({ id: 3, symbol: "TCS", market: "IN", horizon: "long" }),
    ];
    expect(getOpenTradeCount(trades, "IN", "TCS")).toBe(3);
  });

  it("excludes a different symbol", () => {
    const trades = [
      trade({ id: 1, symbol: "TCS", market: "IN" }),
      trade({ id: 2, symbol: "INFY", market: "IN" }),
    ];
    expect(getOpenTradeCount(trades, "IN", "TCS")).toBe(1);
    expect(getOpenTradeCount(trades, "IN", "INFY")).toBe(1);
  });

  it("does not combine the same textual symbol across different markets (IN:TCS vs US:TCS)", () => {
    const trades = [
      trade({ id: 1, symbol: "TCS", market: "IN" }),
      trade({ id: 2, symbol: "TCS", market: "IN" }),
      trade({ id: 3, symbol: "TCS", market: "US" }),
    ];
    expect(getOpenTradeCount(trades, "IN", "TCS")).toBe(2);
    expect(getOpenTradeCount(trades, "US", "TCS")).toBe(1);
  });

  it("excludes CLOSED trades from the count", () => {
    const trades = [
      trade({ id: 1, symbol: "TCS", market: "IN", status: "OPEN" }),
      trade({ id: 2, symbol: "TCS", market: "IN", status: "CLOSED", closed_at: "2026-08-02T00:00:00Z" }),
      trade({ id: 3, symbol: "TCS", market: "IN", status: "CLOSED", closed_at: "2026-08-03T00:00:00Z" }),
    ];
    expect(getOpenTradeCount(trades, "IN", "TCS")).toBe(1);
  });

  it("does not mutate the input open_trades array", () => {
    const trades = [
      trade({ id: 1, symbol: "TCS", market: "IN" }),
      trade({ id: 2, symbol: "TCS", market: "IN", status: "CLOSED" }),
    ];
    const snapshot = trades.map(t => ({ ...t }));
    buildOpenTradeCountMap(trades);
    expect(trades).toEqual(snapshot);
    // Same element identity, not just deep-equal content.
    trades.forEach((t, i) => expect(t).toBe(trades[i]));
  });

  it("openTradeCountKey composes market:symbol verbatim, no normalization", () => {
    expect(openTradeCountKey("IN", "TCS")).toBe("IN:TCS");
    expect(openTradeCountKey("US", "TCS")).toBe("US:TCS");
  });
});
