import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Historical track record (2026-07-17) — connects the confidence % shown
// on each pick to its horizon's real, walk-forward validated track record
// (services.validation_engine.get_track_record_summary, surfaced via
// GET /api/picks/daily's historical_track_record field). page.tsx mounts
// through live data-fetching that isn't practically mountable in isolation
// here — same rationale as the other picks-page containment tests — so
// this locks in the required wiring as source-text assertions against the
// real file.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("Historical track record summary", () => {
  it("is rendered unconditionally, independent of INTEGRITY_HOLD_ACTIVE", () => {
    // Must sit OUTSIDE the {INTEGRITY_HOLD_ACTIVE ? ... : ...} branch —
    // it reads an already-reconciled source (get_track_record_summary),
    // not the withheld BacktestPanel/LivePerformanceTracker data path,
    // so it must never be gated by the same flag.
    const holdBlockEnd = pageSource.indexOf(")}", pageSource.indexOf("{INTEGRITY_HOLD_ACTIVE ? ("));
    const summaryCallIndex = pageSource.indexOf("<HistoricalTrackRecordSummary");
    expect(summaryCallIndex).toBeGreaterThan(holdBlockEnd);
  });

  it("passes the current horizon and the market's historical_track_record entries", () => {
    expect(pageSource).toContain("<HistoricalTrackRecordSummary");
    expect(pageSource).toContain("horizon={horizon}");
    expect(pageSource).toContain("entries={data?.historical_track_record?.[horizon]}");
  });

  it("renders nothing when no entries exist, rather than an error/empty state", () => {
    expect(pageSource).toContain("if (!entries || entries.length === 0) return null;");
  });

  it("never fabricates a beat-benchmark or hit-rate number when the backend omits one", () => {
    expect(pageSource).toContain('beatBenchmark != null ? `${beatBenchmark.toFixed(1)}%` : "—"');
    expect(pageSource).toContain("e.buy_hit_rate_pct != null && (");
  });

  it("flags a below-50%-beat-benchmark horizon honestly instead of just showing the raw number", () => {
    expect(pageSource).toContain("const weak = beatBenchmark != null && beatBenchmark < 50;");
    expect(pageSource).toContain("has not shown a reliable edge over");
  });

  it("flags a small sample size so a single lucky/unlucky bucket isn't over-trusted", () => {
    expect(pageSource).toContain("const lowSample = (e.n_signals ?? 0) < 200;");
    expect(pageSource).toContain("Small sample — treat as directional, not conclusive.");
  });

  it("historical_track_record is typed on DailyPicksResponse, not accessed via `any`", () => {
    expect(pageSource).toContain("historical_track_record?: Record<");
  });
});
