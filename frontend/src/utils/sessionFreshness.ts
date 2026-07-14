/**
 * India Daily Picks session-freshness containment (Phase 0).
 *
 * Confirmed production finding: 12 of 17 current India Daily Picks used a
 * `generation_reference_as_of` older than the latest completed NSE session
 * at generation time (see Product-Integrity-004). This module answers one
 * question only — "does this pick's persisted reference session match the
 * session it should have used?" — from fields the API already returns. It
 * does not recompute price, target, entry zone, stop-loss, or any score;
 * those stay exactly as generated. This is presentation-layer containment,
 * not a fix to the underlying generation pipeline (that remains a separate,
 * backend follow-up phase).
 *
 * Pure and deterministic: the clock is an explicit input for testability,
 * and the NSE calendar comes from the single source of truth in
 * marketHours.ts (same weekend/holiday rules `getMarketStatus("IN", ...)`
 * already uses) — this module never maintains its own holiday list.
 */

import { getExpectedLatestCompletedSession, isNseTradingSessionDate, toIstDateKey } from "@/utils/marketHours";

export type FreshnessStatus = "fresh" | "stale" | "unknown";

export interface FreshnessResult {
  /** The latest NSE session that should have a completed, published bar as of `now`. */
  expectedLatestCompletedSession: string | null;
  /** The session date (YYYY-MM-DD, NSE/IST) the pick's price reference actually came from. */
  referenceSessionDate: string | null;
  freshnessStatus: FreshnessStatus;
  /** Number of NSE trading sessions the reference is behind the expected session, when determinable. */
  freshnessLagSessions: number | null;
}

const UNKNOWN: Omit<FreshnessResult, "expectedLatestCompletedSession" | "referenceSessionDate"> = {
  freshnessStatus: "unknown",
  freshnessLagSessions: null,
};

function parseReferenceSessionDate(generationReferenceAsOf: string | null | undefined): string | null {
  if (!generationReferenceAsOf) return null;
  const parsed = new Date(generationReferenceAsOf);
  if (Number.isNaN(parsed.getTime())) return null;
  return toIstDateKey(parsed);
}

/** Count NSE trading sessions strictly after `fromDateKey` up to and including `toDateKey`. */
function countTradingSessionsBetween(fromDateKey: string, toDateKey: string): number {
  let count = 0;
  // Both keys are "YYYY-MM-DD" — safe to construct as IST-midnight UTC anchors and
  // step forward one calendar day at a time; India has no DST to shift the offset.
  let cursor = new Date(`${fromDateKey}T00:00:00+05:30`);
  const end = new Date(`${toDateKey}T00:00:00+05:30`);
  const MS_DAY = 24 * 60 * 60 * 1000;
  for (let i = 0; i < 30 && cursor.getTime() < end.getTime(); i++) {
    cursor = new Date(cursor.getTime() + MS_DAY);
    if (isNseTradingSessionDate(cursor)) count++;
  }
  return count;
}

/**
 * Evaluate whether an India pick's persisted price reference matches the
 * latest NSE session that should have been used, using only fields the API
 * already returns (`generation_reference_as_of`) plus the existing NSE
 * calendar. Never assumes fresh: any unparseable or missing input, or a
 * reference date that is somehow ahead of the expected session (which
 * should never legitimately happen), resolves to "unknown," not "fresh."
 */
export function evaluateSessionFreshness(
  generationReferenceAsOf: string | null | undefined,
  now: Date = new Date(),
): FreshnessResult {
  const expectedLatestCompletedSession = getExpectedLatestCompletedSession(now);
  const referenceSessionDate = parseReferenceSessionDate(generationReferenceAsOf);

  if (!expectedLatestCompletedSession || !referenceSessionDate) {
    return { expectedLatestCompletedSession, referenceSessionDate, ...UNKNOWN };
  }
  if (referenceSessionDate === expectedLatestCompletedSession) {
    return { expectedLatestCompletedSession, referenceSessionDate, freshnessStatus: "fresh", freshnessLagSessions: 0 };
  }
  if (referenceSessionDate > expectedLatestCompletedSession) {
    // A reference "from the future" relative to the expected session is not
    // provable as fresh — conservatively unknown, never fresh.
    return { expectedLatestCompletedSession, referenceSessionDate, ...UNKNOWN };
  }
  const freshnessLagSessions = countTradingSessionsBetween(referenceSessionDate, expectedLatestCompletedSession);
  return { expectedLatestCompletedSession, referenceSessionDate, freshnessStatus: "stale", freshnessLagSessions };
}

/** Human-readable session date for card copy, e.g. "Fri, 10 Jul 2026". Null-safe. */
export function formatSessionDate(dateKey: string | null, locale: string): string {
  if (!dateKey) return "an unknown date";
  const d = new Date(`${dateKey}T00:00:00+05:30`);
  if (Number.isNaN(d.getTime())) return "an unknown date";
  return d.toLocaleDateString(locale, { weekday: "short", day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}
