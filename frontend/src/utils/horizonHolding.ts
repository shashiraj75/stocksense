// Paper Trading horizon-holding helpers (user-requested, 2026-07-16): the
// `horizon` on a paper trade is a label frozen at the moment the position
// was opened (see PaperTradeModal) — nothing previously checked it against
// how long the position had actually been held. A position opened as
// "Short Term (1-5 trading days)" could sit open for weeks with no
// indication anywhere that it had outlived its own window.
//
// Thresholds are calendar-day approximations of each horizon's stated
// window, not a precise trading-day calendar (that would need a full
// market-holiday calendar per market, not built here) — the short-term
// threshold adds a small buffer over the stated 5 trading days to avoid
// false positives from an ordinary weekend inside the holding period.
export const HORIZON_MAX_HOLD_DAYS: Record<string, number> = {
  short: 7,     // 5 trading days + a weekend buffer
  medium: 28,   // upper bound of "2-4 weeks"
  long: 183,    // upper bound of "3-6 months" (~6 months)
};

/** Whole calendar days between `openedAt` and `now`, floored at 0. */
export function daysHeld(openedAt: string, now: Date = new Date()): number {
  const opened = new Date(openedAt);
  opened.setHours(0, 0, 0, 0);
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const days = Math.round((today.getTime() - opened.getTime()) / 86_400_000);
  return Math.max(0, days);
}

/**
 * True when a position has been held longer than its horizon's expected
 * window. Unknown horizons never flag as overdue — this is a positive
 * assertion about exceeding a known window, not a default for missing data.
 */
export function isOverdueForHorizon(horizon: string, heldDays: number): boolean {
  const max = HORIZON_MAX_HOLD_DAYS[horizon];
  return max != null && heldDays > max;
}
