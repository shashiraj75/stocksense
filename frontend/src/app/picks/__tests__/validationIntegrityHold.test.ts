import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { INTEGRITY_HOLD_ACTIVE } from "@/components/ValidationIntegrityHold";

// Product Integrity Workstream — Phase GPI-0, true two-branch integrity
// hold. Same rationale as the other picks-page containment test files:
// page.tsx mounts through live data-fetching (useQuery, useRouter,
// marketHours checks) that isn't practically mountable in isolation here,
// so this locks in the required wiring as source-text assertions, backed
// by a direct import of the real INTEGRITY_HOLD_ACTIVE flag (not a
// re-declared copy) so this test can never silently drift from the flag
// page.tsx actually reads.
//
// The design-correction this file proves: setting INTEGRITY_HOLD_ACTIVE to
// false must restore the EXACT pre-containment opt-in interface (the "Show
// Real Accuracy" toggle + both panels gated behind showTruth), not leave
// the section empty. We prove this by isolating the true-branch and
// false-branch JSX text of the ternary independently and asserting each
// branch contains exactly what it should — and nothing from the other
// branch.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

const ternaryStart = pageSource.indexOf("{INTEGRITY_HOLD_ACTIVE ? (");
const trueBranchEnd = pageSource.indexOf(") : (", ternaryStart);
const falseBranchEnd = pageSource.indexOf("\n      )}", trueBranchEnd);
const trueBranch = pageSource.slice(ternaryStart, trueBranchEnd);
const falseBranch = pageSource.slice(trueBranchEnd, falseBranchEnd);

describe("Phase GPI-0 — true two-branch integrity hold", () => {
  it("the ternary was located correctly (sanity check for the extraction above)", () => {
    expect(ternaryStart).toBeGreaterThan(-1);
    expect(trueBranchEnd).toBeGreaterThan(ternaryStart);
    expect(falseBranchEnd).toBeGreaterThan(trueBranchEnd);
  });

  it("the hold is currently active", () => {
    expect(INTEGRITY_HOLD_ACTIVE).toBe(true);
  });

  describe("hold ENABLED (INTEGRITY_HOLD_ACTIVE === true) — the branch that actually executes right now", () => {
    it("renders ValidationIntegrityHold exactly once, in the true branch only", () => {
      const totalCallSites = (pageSource.match(/<ValidationIntegrityHold\s*\/>/g) ?? []).length;
      expect(totalCallSites).toBe(1);
      expect(trueBranch).toContain("<ValidationIntegrityHold />");
    });

    it("does not render the 'Show Real Accuracy' control in the true branch", () => {
      expect(trueBranch).not.toContain("Real Accuracy");
      expect(trueBranch).not.toContain("setShowTruth");
    });

    it("does not instantiate BacktestPanel or LivePerformanceTracker in the true branch", () => {
      expect(trueBranch).not.toContain("<BacktestPanel");
      expect(trueBranch).not.toContain("<LivePerformanceTracker");
    });

    it("neither validation nor performance query can execute — both components exist only in the false branch's JSX, which does not render while the flag is true", () => {
      const backtestCallSites = (pageSource.match(/<BacktestPanel\b/g) ?? []).length;
      const perfCallSites = (pageSource.match(/<LivePerformanceTracker\b/g) ?? []).length;
      expect(backtestCallSites).toBe(1);
      expect(perfCallSites).toBe(1);
      expect(falseBranch).toContain("<BacktestPanel");
      expect(falseBranch).toContain("<LivePerformanceTracker");
    });

    it("no unverified statistic or badge string appears in the true branch", () => {
      for (const label of ["Real data", "Real P&L", "BUY Hit Rate", "Sharpe Ratio", "Alpha vs", "Beat "]) {
        expect(trueBranch).not.toContain(label);
      }
    });
  });

  describe("hold DISABLED (INTEGRITY_HOLD_ACTIVE === false) — proving the restored branch is structurally correct for when the flag flips", () => {
    it("the integrity notice does not appear in the false branch", () => {
      expect(falseBranch).not.toContain("ValidationIntegrityHold");
    });

    it("'Show Real Accuracy' control is restored, gated on !INTEGRITY_HOLD_ACTIVE, with original wording/icon/behavior unchanged", () => {
      expect(pageSource).toContain("{!INTEGRITY_HOLD_ACTIVE && (");
      expect(pageSource).toContain("onClick={() => setShowTruth(v => !v)}");
      expect(pageSource).toContain("<CheckCircle size={12} /> {showTruth ? \"Hide\" : \"Show\"} Real Accuracy");
    });

    it("showTruth defaults to false", () => {
      expect(pageSource).toContain("const [showTruth, setShowTruth] = useState(false);");
    });

    it("both panels remain gated behind the user's own showTruth opt-in (not mounted unconditionally) inside the false branch", () => {
      expect(falseBranch).toContain('{showTruth && <BacktestPanel horizon={horizon} benchmarkLabel={market === "IN" ? "Nifty" : "S&P 500"} />}');
      expect(falseBranch).toContain('{showTruth && <LivePerformanceTracker horizon={horizon} currency={currency} locale={marketCfg.locale} benchmarkLabel={market === "IN" ? "Nifty" : "S&P 500"} />}');
    });

    it("original market/horizon/currency/locale props to both panels are preserved verbatim for later restoration", () => {
      expect(falseBranch).toContain("horizon={horizon}");
      expect(falseBranch).toContain('benchmarkLabel={market === "IN" ? "Nifty" : "S&P 500"}');
      expect(falseBranch).toContain("currency={currency}");
      expect(falseBranch).toContain("locale={marketCfg.locale}");
    });

    it("BacktestPanel/LivePerformanceTracker implementations are not deleted", () => {
      expect(pageSource).toContain("function BacktestPanel(");
      expect(pageSource).toContain('queryKey: ["validation", horizon]');
      expect(pageSource).toContain("function LivePerformanceTracker(");
      expect(pageSource).toContain('queryKey: ["picks-performance", horizon]');
    });
  });

  it("India stale-session containment remains present", () => {
    expect(pageSource).toContain("const isStaleOrUnknown = !!freshness && freshness.freshnessStatus !== \"fresh\";");
    expect(pageSource).toContain("Price reference is stale — this pick used market data from");
  });

  it("cross-market payload guard remains present", () => {
    expect(pageSource).toContain("const data = selectPayloadForMarket(rawDailyPicksData, market);");
    expect(pageSource).toContain("const isMarketTransitioning = !!rawDailyPicksData && !data;");
  });

  it("Daily Pick cards and non-price research evidence remain available", () => {
    expect(pageSource).toContain("function PickCard(");
    expect(pageSource).toContain("{visiblePicks.map((pick, i) => <PickCard");
    expect(pageSource).toContain("<TopReasons reasoning={pick.reasoning ?? []} />");
  });

  it("Paper Trade containment is unchanged", () => {
    expect(pageSource).toContain("disabled={isStaleOrUnknown}");
    expect(pageSource).toContain("showPaperTrade && pick.price && !isStaleOrUnknown");
  });
});
