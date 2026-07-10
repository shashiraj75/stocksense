import { describe, it, expect } from "vitest";
import { confidenceTextColor, confidenceGradientClass, buildPickStockHref } from "../page";

// Daily Picks confidence display — every Daily Pick is already a BUY, so
// these mirror SignalBadge's/getSignalTone's own muted-BUY tiers. Test
// rendering-decision logic only; the confidence NUMBER itself (scoring) is
// untouched, only the color it maps to.
describe("confidenceTextColor", () => {
  it("weak confidence (< 45) renders muted gray", () => {
    expect(confidenceTextColor(10)).toBe("text-gray-400");
    expect(confidenceTextColor(44)).toBe("text-gray-400");
  });

  it("moderate confidence (45-59) renders yellow", () => {
    expect(confidenceTextColor(45)).toBe("text-yellow-400");
    expect(confidenceTextColor(59)).toBe("text-yellow-400");
  });

  it("strong confidence (>= 60) renders green", () => {
    expect(confidenceTextColor(60)).toBe("text-green-400");
    expect(confidenceTextColor(95)).toBe("text-green-400");
  });
});

describe("confidenceGradientClass", () => {
  it("weak confidence renders a gray gradient", () => {
    expect(confidenceGradientClass(10)).toBe("bg-gradient-to-r from-gray-500 to-gray-400");
  });

  it("moderate confidence renders a yellow/amber gradient", () => {
    expect(confidenceGradientClass(50)).toBe("bg-gradient-to-r from-yellow-500 to-amber-400");
  });

  it("strong confidence renders a green/emerald gradient", () => {
    expect(confidenceGradientClass(85)).toBe("bg-gradient-to-r from-green-500 to-emerald-400");
  });
});

// Daily Picks horizon navigation — the picks-card side of the contract.
describe("buildPickStockHref", () => {
  it("includes horizon=medium for a medium-horizon pick", () => {
    expect(buildPickStockHref("RELIANCE", "IN", "medium")).toBe(
      "/stock/RELIANCE?market=IN&horizon=medium",
    );
  });

  it("includes horizon=long for a long-horizon pick", () => {
    expect(buildPickStockHref("AAPL", "US", "long")).toBe(
      "/stock/AAPL?market=US&horizon=long",
    );
  });

  it("includes horizon=short for a short-horizon pick", () => {
    expect(buildPickStockHref("TCS", "IN", "short")).toBe(
      "/stock/TCS?market=IN&horizon=short",
    );
  });

  it("URL-encodes the symbol", () => {
    expect(buildPickStockHref("BRK.B", "US", "long")).toBe(
      "/stock/BRK.B?market=US&horizon=long",
    );
  });
});
