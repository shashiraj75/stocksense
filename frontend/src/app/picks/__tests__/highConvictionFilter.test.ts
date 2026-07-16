import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// High Conviction filter (user-requested: "how to get highest AI Confidence
// Daily Picks, with more than 85% confidence"). Adds a toggle to the Picks
// page that filters the currently selected horizon's picks to
// confidence >= 85 and sorts them highest-first, composing with (not
// replacing) the existing horizon tabs.
//
// page.tsx isn't practically mountable in isolation (live useQuery,
// useRouter, marketHours checks — same constraint noted in the sibling
// test files in this directory), so wiring is locked via source-text
// assertions, same pattern as validationIntegrityHold.test.ts and
// crossMarketPayloadGuard.test.ts. The filter/sort computation itself is a
// pure one-liner, so it's additionally verified behaviorally below against
// an exact copy of the implementation — real numeric assertions, not
// source-text checks, for the part that can be isolated.

const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("High Conviction filter — wiring", () => {
  it("threshold constant is defined and set to 85", () => {
    expect(pageSource).toContain("const HIGH_CONVICTION_THRESHOLD = 85;");
  });

  it("toggle state exists and defaults off (existing horizon behavior unchanged by default)", () => {
    expect(pageSource).toContain('const [highConvictionOnly, setHighConvictionOnly] = useState(false);');
  });

  it("visiblePicks filters by the threshold and sorts descending when active, else falls through to the unfiltered horizon picks", () => {
    expect(pageSource).toContain(
      "const visiblePicks = highConvictionOnly\n" +
      "    ? [...picks].filter(p => p.confidence >= HIGH_CONVICTION_THRESHOLD).sort((a, b) => b.confidence - a.confidence)\n" +
      "    : picks;"
    );
  });

  it("the toggle button is rendered alongside the horizon tabs, not replacing them", () => {
    const tabsBlockStart = pageSource.indexOf("{/* Horizon tabs */}");
    const tabsBlockEnd = pageSource.indexOf("</div>\n\n      {/* Product Integrity", tabsBlockStart);
    const tabsBlock = pageSource.slice(tabsBlockStart, tabsBlockEnd);
    expect(tabsBlock).toContain("HORIZONS.map(({ key, label, sub })");
    expect(tabsBlock).toContain("setHighConvictionOnly(v => !v)");
    expect(tabsBlock).toContain("High Conviction Only");
  });

  it("freshness evaluation (India session-freshness containment) runs against visiblePicks, not the unfiltered horizon list — so the notice/badges reflect what's actually on screen", () => {
    expect(pageSource).toContain("for (const pick of visiblePicks) map[pick.symbol] = evaluateSessionFreshness(");
  });

  it("the picks grid renders visiblePicks, and the length-gated branches (grid vs empty states) key off visiblePicks", () => {
    expect(pageSource).toContain("visiblePicks.length > 0 ?");
    expect(pageSource).toContain("{visiblePicks.map((pick, i) => <PickCard");
  });

  it("a distinct empty state exists for 'picks exist but none clear the bar', separate from the 'no BUY signals at all' state", () => {
    expect(pageSource).toContain("highConvictionOnly && picks.length > 0 ?");
    expect(pageSource).toContain("No picks ≥{HIGH_CONVICTION_THRESHOLD}% confidence right now");
    expect(pageSource).toContain("Show all {picks.length} picks");
  });
});

describe("High Conviction filter — behavior (exact copy of the page.tsx computation)", () => {
  const HIGH_CONVICTION_THRESHOLD = 85;
  type MinimalPick = { symbol: string; confidence: number };

  function computeVisiblePicks(picks: MinimalPick[], highConvictionOnly: boolean): MinimalPick[] {
    return highConvictionOnly
      ? [...picks].filter(p => p.confidence >= HIGH_CONVICTION_THRESHOLD).sort((a, b) => b.confidence - a.confidence)
      : picks;
  }

  const mixedPicks: MinimalPick[] = [
    { symbol: "A", confidence: 62 },
    { symbol: "B", confidence: 91 },
    { symbol: "C", confidence: 85 },
    { symbol: "D", confidence: 84 },
    { symbol: "E", confidence: 100 },
  ];

  it("off: returns picks unchanged, original order preserved (no accidental sort/mutation)", () => {
    const result = computeVisiblePicks(mixedPicks, false);
    expect(result).toBe(mixedPicks);
    expect(result.map(p => p.symbol)).toEqual(["A", "B", "C", "D", "E"]);
  });

  it("on: keeps only confidence >= 85 (inclusive boundary) and drops everything below", () => {
    const result = computeVisiblePicks(mixedPicks, true);
    expect(result.map(p => p.symbol).sort()).toEqual(["B", "C", "E"].sort());
    expect(result.some(p => p.symbol === "D")).toBe(false); // 84% must not leak in at the boundary
  });

  it("on: sorts strictly by confidence descending, highest first", () => {
    const result = computeVisiblePicks(mixedPicks, true);
    expect(result.map(p => p.symbol)).toEqual(["E", "B", "C"]);
    expect(result.map(p => p.confidence)).toEqual([100, 91, 85]);
  });

  it("on: does not mutate the original picks array (uses a copy before sorting)", () => {
    const original = [...mixedPicks];
    computeVisiblePicks(mixedPicks, true);
    expect(mixedPicks).toEqual(original);
  });

  it("on: empty result when no pick in the horizon clears the bar", () => {
    const lowConfidencePicks: MinimalPick[] = [{ symbol: "X", confidence: 40 }, { symbol: "Y", confidence: 70 }];
    expect(computeVisiblePicks(lowConfidencePicks, true)).toEqual([]);
  });

  it("on: empty input stays empty (no crash on an empty horizon)", () => {
    expect(computeVisiblePicks([], true)).toEqual([]);
  });
});
