import { describe, it, expect } from "vitest";
import { VERDICT, SCREENS } from "../page";

// The Multibagger page previously used trade-recommendation-style verdict
// labels ("Elite Strong Buy", "Strong Buy", "Watchlist") and "suitable for
// 5-10 year holding" screen copy, which read as a final investment call
// rather than a research screen. These tests lock in the corrected,
// research-screen-scoped wording — none of the underlying backend verdict
// keys (elite_strong_buy/strong_buy/watchlist/watch/avoid) changed, only
// the display label.
describe("Multibagger — verdict labels no longer read as a buy/sell call", () => {
  it("uses candidate/research wording instead of Strong Buy / Watchlist", () => {
    expect(VERDICT.elite_strong_buy.label).toBe("Elite Candidate");
    expect(VERDICT.strong_buy.label).toBe("Strong Candidate");
    expect(VERDICT.watchlist.label).toBe("Research Watchlist");
    expect(VERDICT.watch.label).toBe("Watch / Risk Flag");
    expect(VERDICT.avoid.label).toBe("Avoid");
  });

  it("no verdict label contains the words 'Strong Buy'", () => {
    for (const v of Object.values(VERDICT)) {
      expect(v.label).not.toMatch(/strong buy/i);
    }
  });
});

describe("Multibagger — screen descriptions avoid holding-period investment-advice framing", () => {
  it("Quality Compounders no longer claims a fixed 5-10 year holding suitability", () => {
    const qc = SCREENS.find(s => s.key === "quality_compounder")!;
    expect(qc.desc).not.toMatch(/suitable for 5-10 year holding/i);
    expect(qc.desc).toMatch(/potential long-term compounder candidates/i);
    expect(qc.desc).toMatch(/requires deeper thesis validation/i);
  });
});
