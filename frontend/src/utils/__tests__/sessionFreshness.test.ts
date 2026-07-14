import { describe, it, expect } from "vitest";
import { evaluateSessionFreshness } from "../sessionFreshness";
import { getExpectedLatestCompletedSession } from "../marketHours";

// India Daily Picks session-freshness containment (Phase 0). Confirmed
// production finding: 12/17 current India Daily Picks (including
// AUROPHARMA, EIHOTEL, MAHABANK) persisted generation_reference_as_of =
// Friday 2026-07-10 despite generating early Tuesday 2026-07-14, after
// Monday 2026-07-13's NSE session had fully closed. These fixtures use
// that exact confirmed incident plus the surrounding calendar edge cases.
// All dates are fixtures — no network or production calls.

describe("getExpectedLatestCompletedSession", () => {
  it("AUROPHARMA case: Tuesday morning, before today's close, expects Monday (the last completed session)", () => {
    const now = new Date("2026-07-14T10:00:00+05:30"); // Tuesday, market open but today not yet closed
    expect(getExpectedLatestCompletedSession(now)).toBe("2026-07-13");
  });

  it("before Monday's own close, still expects Friday", () => {
    const now = new Date("2026-07-13T10:00:00+05:30"); // Monday morning, Monday's own session not yet closed
    expect(getExpectedLatestCompletedSession(now)).toBe("2026-07-10");
  });

  it("after Monday's close, expects Monday itself", () => {
    const now = new Date("2026-07-13T16:00:00+05:30"); // Monday evening, after 15:30 IST close
    expect(getExpectedLatestCompletedSession(now)).toBe("2026-07-13");
  });

  it("weekend: Saturday expects Friday", () => {
    const now = new Date("2026-07-11T12:00:00+05:30"); // Saturday
    expect(getExpectedLatestCompletedSession(now)).toBe("2026-07-10");
  });

  it("weekend: Sunday also expects Friday", () => {
    const now = new Date("2026-07-12T12:00:00+05:30"); // Sunday
    expect(getExpectedLatestCompletedSession(now)).toBe("2026-07-10");
  });

  it("NSE holiday: skips a weekday holiday (Republic Day, Mon 2026-01-26) and the weekend before it", () => {
    const now = new Date("2026-01-27T10:00:00+05:30"); // Tuesday, before today's close
    expect(getExpectedLatestCompletedSession(now)).toBe("2026-01-23"); // previous Friday
  });
});

describe("evaluateSessionFreshness", () => {
  const tuesdayMorning = new Date("2026-07-14T10:00:00+05:30");

  it("AUROPHARMA/EIHOTEL/MAHABANK case: Friday reference viewed Tuesday after Monday completed is stale, lag 1 session", () => {
    const result = evaluateSessionFreshness("2026-07-10T00:00:00+05:30", tuesdayMorning);
    expect(result.freshnessStatus).toBe("stale");
    expect(result.referenceSessionDate).toBe("2026-07-10");
    expect(result.expectedLatestCompletedSession).toBe("2026-07-13");
    expect(result.freshnessLagSessions).toBe(1);
  });

  it("fresh: reference exactly matches the expected completed session", () => {
    const result = evaluateSessionFreshness("2026-07-13T00:00:00+05:30", tuesdayMorning);
    expect(result.freshnessStatus).toBe("fresh");
    expect(result.freshnessLagSessions).toBe(0);
  });

  it("unknown: missing generation_reference_as_of", () => {
    const result = evaluateSessionFreshness(null, tuesdayMorning);
    expect(result.freshnessStatus).toBe("unknown");
    expect(result.freshnessLagSessions).toBeNull();
    expect(result.referenceSessionDate).toBeNull();
  });

  it("unknown: unparseable generation_reference_as_of", () => {
    const result = evaluateSessionFreshness("not-a-date", tuesdayMorning);
    expect(result.freshnessStatus).toBe("unknown");
  });

  it("unknown: reference date somehow ahead of the expected session is never treated as fresh", () => {
    const result = evaluateSessionFreshness("2026-07-14T00:00:00+05:30", tuesdayMorning);
    expect(result.freshnessStatus).toBe("unknown");
  });

  it("stale reference lag spans a weekend correctly (2 sessions behind)", () => {
    // Reference from the previous Thursday, evaluated the following Tuesday.
    const result = evaluateSessionFreshness("2026-07-09T00:00:00+05:30", tuesdayMorning);
    expect(result.freshnessStatus).toBe("stale");
    expect(result.freshnessLagSessions).toBe(2); // Friday 07-10 and Monday 07-13
  });
});
