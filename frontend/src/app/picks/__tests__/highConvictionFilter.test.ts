import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { computeVisiblePicks, type Pick } from "../page";

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
// crossMarketPayloadGuard.test.ts. The filter/sort computation itself is
// exercised behaviorally below against the REAL exported `computeVisiblePicks`
// (finding 1, corrective follow-up to commit 0f2bbed8 — this file used to
// duplicate the production one-liner as a local copy, which would not catch
// a regression in the real function).

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

  it("visiblePicks is computed via the shared computeVisiblePicks() helper, gated on publicationPolicy activity", () => {
    expect(pageSource).toContain(
      "const visiblePicks = computeVisiblePicks(picks, highConvictionOnly, publicationPolicy !== null);"
    );
  });

  it("publicationPolicy is derived before visiblePicks/picks are computed (finding 1 ordering requirement)", () => {
    const policyIdx = pageSource.indexOf("const publicationPolicy = derivePublicationPolicy(");
    const visibleIdx = pageSource.indexOf("const visiblePicks = computeVisiblePicks(");
    expect(policyIdx).toBeGreaterThan(-1);
    expect(visibleIdx).toBeGreaterThan(-1);
    expect(policyIdx).toBeLessThan(visibleIdx);
  });

  it("legacyHighConvictionFilterActive is derived from publicationPolicy===null && highConvictionOnly, and used for the empty state", () => {
    expect(pageSource).toContain(
      "const legacyHighConvictionFilterActive = publicationPolicy === null && highConvictionOnly;"
    );
    expect(pageSource).toContain("legacyHighConvictionFilterActive && picks.length > 0 ?");
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
    expect(pageSource).toContain("No picks ≥{HIGH_CONVICTION_THRESHOLD}/100 Model Conviction right now");
    expect(pageSource).toContain("Show all {picks.length} picks");
  });

  // finding 3 (follow-up to commit 5a006498): once the backend
  // conviction-gated publication policy is active for a horizon, every
  // published pick already clears the identical >=85/100 Model Conviction
  // bar server-side — the client-side toggle above must not remain an
  // active, unconditional second gate at the same threshold.
  it("the toggle is gated off (not unconditionally rendered) once the backend publication policy is active", () => {
    expect(pageSource).toMatch(/\{!publicationPolicy\s*&&\s*\(\s*\n\s*<button onClick=\{\(\) => setHighConvictionOnly/);
  });
});

describe("computeVisiblePicks — behavior (real exported production function)", () => {
  function pick(symbol: string, confidence: number): Pick {
    return { symbol, name: symbol, price: 100, target: 110, confidence, reasoning: [], horizon: "short" } as Pick;
  }

  const mixedPicks: Pick[] = [
    pick("A", 62), pick("B", 91), pick("C", 85), pick("D", 84), pick("E", 100),
  ];

  describe("legacy mode (publicationPolicyActive = false)", () => {
    it("off: returns picks unchanged, original order preserved (no accidental sort/mutation)", () => {
      const result = computeVisiblePicks(mixedPicks, false, false);
      expect(result).toBe(mixedPicks);
      expect(result.map(p => p.symbol)).toEqual(["A", "B", "C", "D", "E"]);
    });

    it("on: keeps only confidence >= 85 (inclusive boundary) and drops everything below", () => {
      const result = computeVisiblePicks(mixedPicks, true, false);
      expect(result.map(p => p.symbol).sort()).toEqual(["B", "C", "E"].sort());
      expect(result.some(p => p.symbol === "D")).toBe(false); // 84 must not leak in at the boundary
    });

    it("on: exactly 85 is retained (inclusive boundary)", () => {
      const result = computeVisiblePicks(mixedPicks, true, false);
      expect(result.some(p => p.symbol === "C" && p.confidence === 85)).toBe(true);
    });

    it("on: sorts strictly by confidence descending, highest first", () => {
      const result = computeVisiblePicks(mixedPicks, true, false);
      expect(result.map(p => p.symbol)).toEqual(["E", "B", "C"]);
      expect(result.map(p => p.confidence)).toEqual([100, 91, 85]);
    });

    it("on: does not mutate the original picks array (uses a copy before sorting)", () => {
      const original = [...mixedPicks];
      computeVisiblePicks(mixedPicks, true, false);
      expect(mixedPicks).toEqual(original);
    });

    it("on: empty result when no pick in the horizon clears the bar", () => {
      const lowConfidencePicks: Pick[] = [pick("X", 40), pick("Y", 70)];
      expect(computeVisiblePicks(lowConfidencePicks, true, false)).toEqual([]);
    });

    it("on: empty input stays empty (no crash on an empty horizon)", () => {
      expect(computeVisiblePicks([], true, false)).toEqual([]);
    });
  });

  // Finding 1 (corrective follow-up to 0f2bbed8): once the backend
  // conviction-gated publication policy is active, the list is already
  // authoritative (<=3 picks, all >=85/100, in the backend's own rank
  // order) — computeVisiblePicks must ALWAYS return it unfiltered/unsorted,
  // regardless of the (possibly stale) highConvictionOnly toggle state.
  describe("policy-active mode (publicationPolicyActive = true) — backend order is authoritative", () => {
    const publishedOrder: Pick[] = [pick("Z", 90), pick("Y", 99), pick("X", 85)]; // deliberately NOT confidence-sorted

    it("toggle off: returns backend-published order unchanged", () => {
      const result = computeVisiblePicks(publishedOrder, false, true);
      expect(result).toBe(publishedOrder);
      expect(result.map(p => p.symbol)).toEqual(["Z", "Y", "X"]);
    });

    it("stale toggle on: backend order is still unchanged — filtering/sorting is suppressed entirely", () => {
      const result = computeVisiblePicks(publishedOrder, true, true);
      expect(result).toBe(publishedOrder);
      expect(result.map(p => p.symbol)).toEqual(["Z", "Y", "X"]);
    });

    it("a legacy->active transition on the same toggle state cannot reorder or remove picks", () => {
      // Same `highConvictionOnly=true` state, only `publicationPolicyActive`
      // flips — proves the transition itself is what neutralizes filtering,
      // not some incidental reset of the toggle.
      const legacyResult = computeVisiblePicks(mixedPicks, true, false);
      const activeResult = computeVisiblePicks(mixedPicks, true, true);
      expect(legacyResult.map(p => p.symbol)).not.toEqual(mixedPicks.map(p => p.symbol)); // legacy: filtered/reordered
      expect(activeResult).toBe(mixedPicks); // active: untouched, full backend order/count preserved
      expect(activeResult.map(p => p.symbol)).toEqual(["A", "B", "C", "D", "E"]);
    });

    it("empty published list stays empty regardless of toggle state", () => {
      expect(computeVisiblePicks([], true, true)).toEqual([]);
      expect(computeVisiblePicks([], false, true)).toEqual([]);
    });
  });
});
