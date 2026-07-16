import { describe, it, expect } from "vitest";
import { daysHeld, isOverdueForHorizon, HORIZON_MAX_HOLD_DAYS } from "../horizonHolding";

describe("HORIZON_MAX_HOLD_DAYS", () => {
  it("matches the documented calendar-day approximations of each horizon's stated window", () => {
    expect(HORIZON_MAX_HOLD_DAYS.short).toBe(7);
    expect(HORIZON_MAX_HOLD_DAYS.medium).toBe(28);
    expect(HORIZON_MAX_HOLD_DAYS.long).toBe(183);
  });
});

describe("daysHeld", () => {
  // Midday UTC timestamps — safe from crossing a local-timezone midnight
  // boundary under any real-world UTC offset, so these results are
  // deterministic regardless of the test runner's local timezone (matches
  // daysSinceLabel's own local-wall-clock convention elsewhere in this file).
  it("returns 0 for a position opened today", () => {
    const now = new Date("2026-07-16T12:00:00Z");
    expect(daysHeld("2026-07-16T12:00:00Z", now)).toBe(0);
  });

  it("counts whole calendar days regardless of time-of-day", () => {
    const now = new Date("2026-07-16T12:00:00Z");
    // Opened 17 days earlier — matches the real NNY example from production (29/6 -> 16/7).
    expect(daysHeld("2026-06-29T12:00:00Z", now)).toBe(17);
  });

  it("never returns a negative number for a future-looking clock skew", () => {
    const now = new Date("2026-07-16T12:00:00Z");
    expect(daysHeld("2026-07-17T12:00:00Z", now)).toBe(0);
  });
});

describe("isOverdueForHorizon", () => {
  it("short: not overdue at exactly the threshold, overdue one day past it", () => {
    expect(isOverdueForHorizon("short", 7)).toBe(false);
    expect(isOverdueForHorizon("short", 8)).toBe(true);
  });

  it("medium: not overdue at exactly the threshold, overdue one day past it", () => {
    expect(isOverdueForHorizon("medium", 28)).toBe(false);
    expect(isOverdueForHorizon("medium", 29)).toBe(true);
  });

  it("long: not overdue at exactly the threshold, overdue one day past it", () => {
    expect(isOverdueForHorizon("long", 183)).toBe(false);
    expect(isOverdueForHorizon("long", 184)).toBe(true);
  });

  it("flags the real production NNY example (short-term, held 17 days)", () => {
    expect(isOverdueForHorizon("short", 17)).toBe(true);
  });

  it("never flags an unrecognized horizon as overdue, no matter how long it's been held", () => {
    expect(isOverdueForHorizon("unclassified", 100_000)).toBe(false);
    expect(isOverdueForHorizon("", 100_000)).toBe(false);
  });

  it("a fresh position in any known horizon is never overdue", () => {
    expect(isOverdueForHorizon("short", 0)).toBe(false);
    expect(isOverdueForHorizon("medium", 0)).toBe(false);
    expect(isOverdueForHorizon("long", 0)).toBe(false);
  });
});
