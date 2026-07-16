import { describe, it, expect } from "vitest";
import { computeSuggestedQuantity, RISK_PCT_OF_CAPITAL } from "../riskBasedSizing";

describe("RISK_PCT_OF_CAPITAL", () => {
  it("is 1%, matching the user-requested risk budget", () => {
    expect(RISK_PCT_OF_CAPITAL).toBe(0.01);
  });
});

describe("computeSuggestedQuantity", () => {
  it("sizes so a stop-loss hit costs ~1% of available capital", () => {
    // $100,000 capital, 1% = $1,000 risk budget. Entry $100, stop $90 -> $10/share risk.
    // 1000 / 10 = 100 shares. Affordable cap: 100000/100 = 1000 shares, not binding.
    const qty = computeSuggestedQuantity({ currentPrice: 100, stopLoss: 90, availableCash: 100_000 });
    expect(qty).toBe(100);
  });

  it("reproduces the exact MU incident from the user's real US ledger, scaled down", () => {
    // Real trade: 10 shares of a $1,189 stock (no stop loss set) lost $1,439 —
    // a $11,894 position, ~vastly more than 1% of a $100,000 account. With a
    // stop loss in place, this function must never suggest anywhere near 10
    // shares at that price against a $100k account.
    const qty = computeSuggestedQuantity({ currentPrice: 1189.43, stopLoss: 1045.57, availableCash: 100_000 });
    // per-share risk ~143.86; 1% of 100k = 1000; 1000/143.86 ≈ 6.95 -> floor 6
    expect(qty).toBe(6);
    expect(qty!).toBeLessThan(10); // strictly smaller than the real flat-quantity trade that caused the loss
  });

  it("caps at what available cash can actually buy when the stop loss is very tight", () => {
    // Entry $100, stop $99.50 -> $0.50/share risk. Risk-based qty would be
    // huge (100000*0.01/0.5 = 2000), but only 1000 shares are affordable at $100 each.
    const qty = computeSuggestedQuantity({ currentPrice: 100, stopLoss: 99.5, availableCash: 100_000 });
    expect(qty).toBe(1000);
  });

  it("floors at 1 share minimum, never suggests 0", () => {
    // Huge per-share risk relative to a tiny risk budget: 1% of $100 = $1,
    // stop is $50 away -> risk-based qty rounds to 0, but 1 is the floor.
    const qty = computeSuggestedQuantity({ currentPrice: 100, stopLoss: 50, availableCash: 100 });
    expect(qty).toBe(1);
  });

  it("returns null when no stop loss is set — no defined risk to size against", () => {
    expect(computeSuggestedQuantity({ currentPrice: 100, stopLoss: null, availableCash: 100_000 })).toBeNull();
  });

  it("returns null when stop loss is zero or negative", () => {
    expect(computeSuggestedQuantity({ currentPrice: 100, stopLoss: 0, availableCash: 100_000 })).toBeNull();
    expect(computeSuggestedQuantity({ currentPrice: 100, stopLoss: -5, availableCash: 100_000 })).toBeNull();
  });

  it("returns null when stop loss exactly equals entry price — zero defined risk", () => {
    expect(computeSuggestedQuantity({ currentPrice: 100, stopLoss: 100, availableCash: 100_000 })).toBeNull();
  });

  it("returns null when available cash is missing, zero, or negative", () => {
    expect(computeSuggestedQuantity({ currentPrice: 100, stopLoss: 90, availableCash: null })).toBeNull();
    expect(computeSuggestedQuantity({ currentPrice: 100, stopLoss: 90, availableCash: undefined })).toBeNull();
    expect(computeSuggestedQuantity({ currentPrice: 100, stopLoss: 90, availableCash: 0 })).toBeNull();
    expect(computeSuggestedQuantity({ currentPrice: 100, stopLoss: 90, availableCash: -500 })).toBeNull();
  });

  it("returns null for a non-positive or non-finite current price", () => {
    expect(computeSuggestedQuantity({ currentPrice: 0, stopLoss: 90, availableCash: 100_000 })).toBeNull();
    expect(computeSuggestedQuantity({ currentPrice: -10, stopLoss: 90, availableCash: 100_000 })).toBeNull();
    expect(computeSuggestedQuantity({ currentPrice: NaN, stopLoss: 90, availableCash: 100_000 })).toBeNull();
  });

  it("works identically for a SELL-side stop loss above entry (short-style risk direction)", () => {
    // Sizing is direction-agnostic — it only cares about the absolute per-share distance.
    const qty = computeSuggestedQuantity({ currentPrice: 100, stopLoss: 110, availableCash: 100_000 });
    expect(qty).toBe(100); // same $10/share risk as the BUY-side symmetric case above
  });

  it("honors a custom riskPct override", () => {
    // 2% of 100,000 = 2000; /10 per-share risk = 200 shares.
    const qty = computeSuggestedQuantity({ currentPrice: 100, stopLoss: 90, availableCash: 100_000, riskPct: 0.02 });
    expect(qty).toBe(200);
  });

  it("matches the India-scale example: ₹10,00,000 capital, a typical NSE stop distance", () => {
    // Entry ₹2,469.20, stop ₹2,115.84 (real DIXON long-horizon levels seen earlier this session)
    // per-share risk = 353.36; 1% of 1,000,000 = 10,000; 10000/353.36 ≈ 28.3 -> floor 28
    const qty = computeSuggestedQuantity({ currentPrice: 2469.2, stopLoss: 2115.84, availableCash: 1_000_000 });
    expect(qty).toBe(28);
  });
});
