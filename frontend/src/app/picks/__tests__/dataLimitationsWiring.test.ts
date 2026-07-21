import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// DP-026 user-facing disclosure (product-owner authorized). page.tsx mounts
// through live data-fetching that isn't practically mountable in isolation
// here — same rationale as historicalTrackRecord.test.ts and
// validationIntegrityHold.test.ts — so this locks in the required wiring
// as source-text assertions against the real file, matching this
// repository's existing convention for this exact file.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("DP-026 disclosure wiring — HistoricalTrackRecordSummary (always-visible card)", () => {
  it("imports DataLimitationsNotice", () => {
    expect(pageSource).toContain('import { DataLimitationsNotice } from "@/components/DataLimitationsNotice";');
  });

  it("renders the notice inside HistoricalTrackRecordSummary, before the per-universe grid", () => {
    const fnStart = pageSource.indexOf("function HistoricalTrackRecordSummary");
    const fnEnd = pageSource.indexOf("\nfunction ", fnStart + 1);
    const body = pageSource.slice(fnStart, fnEnd);
    const noticeIndex = body.indexOf("<DataLimitationsNotice");
    const gridIndex = body.indexOf('className="grid grid-cols-1 sm:grid-cols-2 gap-3"');
    expect(noticeIndex).toBeGreaterThan(-1);
    expect(gridIndex).toBeGreaterThan(-1);
    expect(noticeIndex).toBeLessThan(gridIndex);
  });

  it("is not gated by any per-universe or per-entry conditional inside HistoricalTrackRecordSummary — same fixed JSX runs for every entry in `entries`, which already carries whichever markets/universes the caller passed for this horizon", () => {
    const fnStart = pageSource.indexOf("function HistoricalTrackRecordSummary");
    const fnEnd = pageSource.indexOf("\nfunction ", fnStart + 1);
    const body = pageSource.slice(fnStart, fnEnd);
    const noticeLine = body.split("\n").find((l) => l.includes("<DataLimitationsNotice"))!;
    expect(noticeLine).not.toMatch(/&&|\?/); // not conditionally rendered
  });

  it("does not depend on a data_limitations field being present anywhere in this file", () => {
    // The component itself takes no data/props tied to the API response —
    // confirms legacy runs (pre-dating data_limitations) still show it.
    expect(pageSource).not.toMatch(/DataLimitationsNotice[^>]*data_limitations/);
  });

  it("is called with horizon already, so short/medium/long all render the same disclosure by construction (one call site, parametrized by horizon, not three separate branches)", () => {
    expect(pageSource).toContain("<HistoricalTrackRecordSummary");
    expect(pageSource).toContain("horizon={horizon}");
  });
});

describe("DP-026 disclosure wiring — BacktestPanel (INTEGRITY_HOLD_ACTIVE-gated component)", () => {
  it("renders the notice inside BacktestPanel, before the key-metrics grid", () => {
    const fnStart = pageSource.indexOf("function BacktestPanel");
    const fnEnd = pageSource.indexOf("\nfunction ", fnStart + 1);
    const body = pageSource.slice(fnStart, fnEnd);
    const noticeIndex = body.indexOf("<DataLimitationsNotice");
    const metricsIndex = body.indexOf("Key metrics");
    expect(noticeIndex).toBeGreaterThan(-1);
    expect(metricsIndex).toBeGreaterThan(-1);
    expect(noticeIndex).toBeLessThan(metricsIndex);
  });

  it("does NOT bypass the integrity hold — INTEGRITY_HOLD_ACTIVE still gates BacktestPanel out of the render tree entirely", () => {
    // This is the critical non-regression check: adding the notice inside
    // BacktestPanel must not change the surrounding {INTEGRITY_HOLD_ACTIVE
    // ? <ValidationIntegrityHold /> : (...)} branch that keeps the
    // component from ever mounting while the hold is active.
    expect(pageSource).toContain("{INTEGRITY_HOLD_ACTIVE ? (");
    expect(pageSource).toContain("<ValidationIntegrityHold />");
    const holdBranchStart = pageSource.indexOf("{INTEGRITY_HOLD_ACTIVE ? (");
    const holdBranchElseIndex = pageSource.indexOf(") : (", holdBranchStart);
    const backtestCallIndex = pageSource.indexOf("<BacktestPanel horizon=");
    expect(backtestCallIndex).toBeGreaterThan(holdBranchElseIndex);
  });

  it("BacktestPanel's own INTEGRITY_HOLD_ACTIVE import is untouched (still imported, still exported true from ValidationIntegrityHold.tsx)", () => {
    expect(pageSource).toContain(
      'import { INTEGRITY_HOLD_ACTIVE, ValidationIntegrityHold } from "@/components/ValidationIntegrityHold";'
    );
    const holdFileSource = readFileSync(
      path.resolve(process.cwd(), "src/components/ValidationIntegrityHold.tsx"),
      "utf-8",
    );
    expect(holdFileSource).toContain("export const INTEGRITY_HOLD_ACTIVE = true;");
  });
});

describe("DP-026 disclosure wiring — BacktestPanel loading/empty states do not show a stray notice", () => {
  it("loading skeleton and no-results messages are returned before the notice line, not alongside it", () => {
    const fnStart = pageSource.indexOf("function BacktestPanel");
    const fnEnd = pageSource.indexOf("\nfunction ", fnStart + 1);
    const body = pageSource.slice(fnStart, fnEnd);
    const loadingReturnIdx = body.indexOf("if (isLoading) return (");
    const noDataReturnIdx = body.indexOf('if (!data?.available || data.buy_hit_rate_pct == null) return (');
    const noticeIdx = body.indexOf("<DataLimitationsNotice");
    expect(loadingReturnIdx).toBeGreaterThan(-1);
    expect(noDataReturnIdx).toBeGreaterThan(loadingReturnIdx);
    expect(noticeIdx).toBeGreaterThan(noDataReturnIdx);
  });
});

describe("DP-026 disclosure wiring — unrelated surfaces must NOT show the notice", () => {
  it("PaperTradeModal does not import or reference the notice", () => {
    const src = readFileSync(
      path.resolve(process.cwd(), "src/components/PaperTradeModal.tsx"),
      "utf-8",
    );
    expect(src).not.toContain("DataLimitationsNotice");
    expect(src).not.toContain("DataLimitationsMark");
  });

  it("appears exactly twice in picks/page.tsx — HistoricalTrackRecordSummary and BacktestPanel only, never on the live pick-card grid or paper-trade flow", () => {
    const occurrences = pageSource.split("<DataLimitationsNotice").length - 1;
    expect(occurrences).toBe(2);
  });
});
