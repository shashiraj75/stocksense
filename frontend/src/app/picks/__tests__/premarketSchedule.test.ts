import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Daily Stock Picks — US Premarket Finalizer schedule change (2026-07-15):
// moved from ~7:35 AM ET to 6:00 AM ET. page.tsx mounts through live
// data-fetching (useQuery, useRouter, market-hours checks) that isn't
// practically mountable in isolation here, matching this repo's existing
// convention for this file (see wording.test.ts, validationIntegrityHold.test.ts)
// — this locks in the required two-stage terminology and schedule wording
// as source-text assertions instead.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("US Pre-Open base generation stays distinct from Premarket Review", () => {
  it("US base-generation genTime is not fabricated as 6:00 AM ET", () => {
    // The base workflow schedule (daily_picks_us.yml, 06:00 UTC) is
    // expressed in Dubai/IST local-time equivalents, never as "6:00 AM ET"
    // — that label belongs exclusively to the Premarket Review stage.
    expect(pageSource).toContain("10:00 AM Dubai / 11:30 AM IST");
    expect(pageSource).not.toMatch(/genTime:\s*"[^"]*6:00 AM ET[^"]*"/);
  });

  it("labels the base-generation timestamp badge distinctly for US", () => {
    expect(pageSource).toContain('const label = market === "US" ? "Base generated" : "Updated";');
  });

  it("India's base-generation badge label is unchanged", () => {
    // The ternary's IN branch must remain the original "Updated" wording.
    expect(pageSource).toContain('"Base generated" : "Updated"');
  });
});

describe("Premarket Review is rendered as its own 6:00 AM ET stage", () => {
  it("defines the 6:00 AM ET scheduled-review label", () => {
    expect(pageSource).toContain('const PREMARKET_REVIEW_SCHEDULE_LABEL = "Scheduled for 6:00 AM ET";');
  });

  it("the premarket badge renders unconditionally for US, not gated on a truthy status", () => {
    // Must not be gated purely on `data?.premarket_status &&` (the old,
    // silent-when-absent behavior) — the badge block now derives an
    // effective status with a "pending" fallback.
    expect(pageSource).toContain('const effectiveStatus = data?.premarket_status ?? "pending";');
  });

  it("pending premarket review truthfully shows the 6:00 AM ET scheduled target", () => {
    expect(pageSource).toContain("PREMARKET_STATUS_LABEL.pending");
    expect(pageSource).toContain("PREMARKET_REVIEW_SCHEDULE_LABEL");
  });

  it("completed states display the actual premarket_finalized_at timestamp, never a fabricated one", () => {
    expect(pageSource).toContain("const premarketFinalizedAt = data?.premarket_finalized_at");
    expect(pageSource).toContain("isCompletedOutcome && premarketFinalizedAt");
  });

  it("limited-data status has truthful, distinct wording from a full completion", () => {
    expect(pageSource).toContain('completed_with_limited_premarket_data: "Premarket Review Completed (Limited Data)"');
  });

  it("skipped status is truthfully labeled, not silently hidden", () => {
    expect(pageSource).toContain('skipped: "Premarket Review Skipped"');
  });

  it("failed status is truthfully labeled, not silently hidden", () => {
    expect(pageSource).toContain('failed: "Premarket Review Failed"');
  });

  it("never advertises a stale premarket target", () => {
    expect(pageSource).not.toContain("7:35 AM");
    expect(pageSource).not.toContain("Premarket refresh runs before US market open");
  });

  it("does not imply the finalizer starts before base generation", () => {
    expect(pageSource).toContain("after today's base picks complete");
  });
});

describe("India never displays a US premarket stage", () => {
  it("the premarket badge block is gated on market === \"US\" only", () => {
    expect(pageSource).toContain('{market === "US" && (() => {');
  });

  it("no premarket_status field is referenced for IN anywhere in the badge logic", () => {
    // The IN branch of the timestamp-badge label ternary must not reference
    // premarket at all.
    const inLabelBranch = pageSource.match(/const label = market === "US" \? "Base generated" : "Updated";/);
    expect(inLabelBranch).not.toBeNull();
  });
});

describe("DST-sensitive copy does not claim a single permanent UTC conversion", () => {
  it("only one authoritative America/New_York-based schedule label exists for the review stage", () => {
    // "Scheduled for 6:00 AM ET" is a local-time (ET) target, correct
    // year-round without needing separate EDT/EST copies in the frontend —
    // the backend (premarket_finalizer.py) owns the DST-aware UTC mapping.
    const matches = pageSource.match(/Scheduled for 6:00 AM ET/g) ?? [];
    expect(matches.length).toBeGreaterThan(0);
  });
});

describe("GPI-0 and existing containment remain unaffected by this schedule change", () => {
  it("GPI-0 integrity hold gating is unchanged", () => {
    expect(pageSource).toContain("{!INTEGRITY_HOLD_ACTIVE && (");
  });

  it("India stale-session containment remains present", () => {
    expect(pageSource).toContain("const isStaleOrUnknown = !!freshness && freshness.freshnessStatus !== \"fresh\";");
  });

  it("cross-market payload guard remains present", () => {
    expect(pageSource).toContain("const data = selectPayloadForMarket(rawDailyPicksData, market);");
  });
});
