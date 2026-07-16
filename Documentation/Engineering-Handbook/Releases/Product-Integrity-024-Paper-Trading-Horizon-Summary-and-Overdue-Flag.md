# Product Integrity Workstream #024 — Paper Trading Horizon Summary and Overdue Flag

**Status:** Implemented and tested. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

## 1. Trigger

User asked two things while looking at their real Open Positions on the Paper Trading page: (1) do positions actually conform to their horizon's stated holding window (e.g. "Short Term — 1-5 trading days"), and (2) could the page show more per-horizon summary info (Invested, Unr. P&L, % of Total Investment, Avg Days Invested) alongside the existing group headers.

## 2. Investigation findings

Checked the actual code rather than assuming: `horizon` on a paper trade is a plain string, frozen at the moment the position was opened (whichever tab the modal was launched from) and used only to bucket trades for display — nothing anywhere checks it against how long the position has actually been held. The user's own real NNY position confirmed this concretely: opened 29/6/2026, still sitting under "Short Term (1-5 trading days)" 17 days later, with zero indication anywhere in the app that it had outlived its own window by more than 3x.

## 3. Fixes

- **New `frontend/src/utils/horizonHolding.ts`**: `daysHeld(openedAt, now)` (whole calendar days since open) and `isOverdueForHorizon(horizon, heldDays)`, backed by `HORIZON_MAX_HOLD_DAYS` — calendar-day approximations of each horizon's stated window (short: 7, adding a weekend buffer over the stated 5 trading days; medium: 28, the upper bound of "2-4 weeks"; long: 183, the upper bound of "3-6 months"). Explicitly a calendar-day approximation, not a precise trading-day calendar (that would need a full market-holiday calendar per market) — documented as such in the module, not overclaimed.
- **Overdue flag**: each open-position row now shows "⏱ Overdue for {horizon}" under its Date/days-held text when `isOverdueForHorizon` is true — using the same yellow warning tone already used elsewhere on this page (e.g. "Near stop loss"), not a new color language.
- **Per-horizon summary strip**: a new row beneath each horizon group's header (Short/Medium/Long), showing Invested (sum), Unr. P&L (sum, using the same live-price data already fetched for the rows below — shows "Loading…" honestly until at least one price has resolved, never a fake $0), % of Total (this horizon's invested amount as a % of the market's total open-position investment), and Avg Days Held (mean `daysHeld` across the group's positions). Placed on its own row below the existing header line (label/sub-text/count/"Sorted by action priority") rather than crammed onto the same line, which was already fairly full.

## 4. What this does not do

- Does not change what horizon a trade is bucketed into, or retroactively reclassify any existing trade — purely additive display information layered on the existing bucketing.
- Does not block, auto-close, or otherwise act on an overdue position — it's a visual flag only, matching this app's existing "alerts only by default, auto-close is opt-in" design for trade management.
- Does not build a true trading-day-aware overdue calendar (accounting for market holidays) — a calendar-day approximation with an explicit buffer was judged sufficient for a visibility flag, not a hard rule; this is disclosed in the code, not silently assumed precise.
- Does not touch the backend, any API contract, or Trade History (closed trades) — scoped entirely to the Open Positions section of the Paper Trading page.

## 5. Tests

- New `horizonHolding.test.ts` — 10 tests, real behavioral assertions (not source-text checks): the documented threshold values; `daysHeld` returns 0 for same-day, counts whole days correctly (including a direct reproduction of the real NNY case: 29/6 → 16/7 = 17 days), and never returns negative on a future-dated open (clock skew safety); `isOverdueForHorizon` is false exactly at each horizon's threshold and true one day past it for all three horizons, reproduces the real NNY overdue case, never flags an unrecognized/empty horizon regardless of how long held, and never flags a freshly-opened position in any horizon.
- Full frontend suite: **357/357 passed** (347 baseline + 10 new).
- Clean `tsc --noEmit` and clean `next build` (all 18 routes generated).
- Interactive browser verification not performed this pass — Paper Trading requires an authenticated session, unavailable in this pass's local preview (same constraint already established and accepted for PI-021/#022's own local-preview limitations). Relying on the passing behavioral test suite (which directly reproduces the real production NNY example) plus the clean build as verification.

## 6. Rollback

Two-file, additive change (`horizonHolding.ts` new; `paper-trading/page.tsx` modified) plus a new test file — reverting restores the exact pre-feature Open Positions display with no overdue flag and no summary strip. No backend, schema, or API contract change.
