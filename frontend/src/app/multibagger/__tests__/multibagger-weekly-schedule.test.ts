import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Product Integrity #009 (2026-07-16) — Multibagger full-universe
// fundamentals refresh converted from nightly (India) / daily (US) to
// weekly for both markets. page.tsx isn't practically mountable in
// isolation here (live data-fetching through useQuery, market-hours
// checks — same established convention as picks/__tests__), so these are
// source-text assertions on the exact wording that must/must-not appear.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/multibagger/page.tsx"),
  "utf-8",
);

describe("Multibagger page describes the weekly refresh schedule truthfully", () => {
  it("no longer claims a nightly refresh cadence", () => {
    expect(pageSource).not.toMatch(/Refreshed nightly/i);
    expect(pageSource).not.toMatch(/normal schedule/i);
  });

  it("states the refresh is weekly", () => {
    expect(pageSource).toMatch(/Refreshed weekly/i);
    expect(pageSource).toMatch(/Full fundamentals refreshed weekly/i);
  });

  it("shows India's Saturday 3:00 AM IST schedule, not stale Dubai/nightly values", () => {
    expect(pageSource).toContain('"Saturday 3:00 AM IST"');
    expect(pageSource).not.toContain("10:30 PM IST");
    expect(pageSource).not.toContain("7:30 AM IST");
  });

  it("US schedule truthfully discloses both EST and EDT local times for the single fixed 08:00 UTC cron", () => {
    // Product Integrity #010: replaced the #009 dual-DST-candidate design
    // with one fixed Sunday 08:00 UTC cron. Since that single UTC instant
    // maps to two different local times across the year (3 AM EST / 4 AM
    // EDT), the frontend must disclose both — a single "3:00 AM ET"
    // year-round claim would be wrong for half the year.
    expect(pageSource).toContain("Sunday 08:00 UTC (3:00 AM EST / 4:00 AM EDT)");
    expect(pageSource).not.toMatch(/"Sunday 3:00 AM ET"/);
  });
});

describe("Multibagger page shows the actual last-refresh timestamp, never a fabricated one", () => {
  it("still derives lastRefreshed from the real data?.last_refreshed field", () => {
    expect(pageSource).toContain("data?.last_refreshed ? new Date(data.last_refreshed) : null");
  });

  it("does not claim the refresh completed exactly at the scheduled target", () => {
    expect(pageSource).not.toMatch(/completed at.*3:00 AM/i);
  });
});

describe("Multibagger page surfaces a truthful stale-data warning", () => {
  it("derives isStale from the durable status field", () => {
    expect(pageSource).toContain("status?.is_stale");
  });

  it("shows a distinct visual state (yellow) for stale data, and a distinct one for a failed refresh", () => {
    expect(pageSource).toMatch(/isStale[\s\S]{0,200}yellow/);
    expect(pageSource).toContain('status?.job_status === "failed"');
  });

  it("renders an explanatory stale-data banner referencing the configurable threshold, not a hardcoded 9", () => {
    expect(pageSource).toContain("status?.stale_after_days ?? 9");
  });
});

describe("Multibagger page exposes no public force-refresh control", () => {
  it("has no client-triggered POST to /api/multibagger/refresh", () => {
    expect(pageSource).not.toMatch(/multibagger\/refresh/);
  });
});
