# Product Integrity Workstream #015 — Stock Detail Page Forensic Audit, MEDIUM/LOW-Severity Fixes

**Status:** Implemented, tested, and locally committed. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

**Follows directly on [Product Integrity #014](Product-Integrity-014-Stock-Detail-Page-Forensic-Audit-HIGH-Severity-Fixes.md)**, which fixed the 7 HIGH-severity findings from the same audit. This release fixes the remaining 15 of 16 open findings (10 MEDIUM, 5 of 6 LOW); finding #15 is excluded — it requires a backend database schema change (adding a `market` column to `score_snapshots`) and is a materially different risk category than the rest, so it's tracked separately pending explicit confirmation before any DB work.

## Fixes in this release

### MEDIUM

- **#8/#9 — Market Regime and Evidence/Research Summary panels gated to horizon tabs.** Previously had no tab restriction at all (unlike the already-correctly-gated Trade Levels card) and would render the medium-horizon fallback's regime/signal, unlabeled, on Fundamentals/History/**and Backtest**. Now gated identically to Trade Levels — Short/Medium/Long tabs only.
- **#10 (residual) — hero AI Signal card shows an "updating…" indicator.** The Paper Trade button already disabled itself during a background horizon refetch (PI-014); the card itself had no visible reason why. Added a small "· updating…" label next to "AI Signal" during the same `predFetching` window, matching the "AI Prediction" card's existing pattern.
- **#11 — sentiment/news drift disclosed.** The Sentiment score (from the `prediction` query, staleTime 14min) and the News & Sentiment article list (from a separate `news` query, staleTime 10min) have no shared cache key or timestamp — nothing guarantees they reflect the same window. Added a disclosure note rather than attempting to unify the two queries (a larger backend/caching change out of scope here).
- **#12 — Take Profit color now reflects win/loss, not price-direction.** Take Profit is definitionally the "win" outcome for both BUY and SELL; a SELL's target sits below current price by design, so the old price-direction-based coloring rendered it red — visually identical to the Stop Loss box next to it. Now always the bull/success color, same way Stop Loss is always bear/failure color.
- **#13 — Mkt Cap uses ₹ Cr for India.** The hero stat-pill row previously always used T/B/M suffixes even for India, contradicting the Fundamentals tab's own ₹ Cr convention on the same page. Now branches on `market === "IN"`, using the same `/1e7` raw-INR→crore conversion `getCapCategory` already uses.
- **#14 — hero header company name includes the US-fundamentals fallback.** The sticky ticker bar's fallback chain already included `usFund?.company_name`; the hero header's chain didn't, so a US stock could show two different company names simultaneously once the Fundamentals tab's query resolved. Chains now match.
- **#16 — History chart dates pinned to UTC.** `fmtDate` parsed a date-only string (`"2026-07-15"`) as UTC midnight via `new Date(d)`, then rendered it in the browser's local timezone with no explicit `timeZone` — for viewers west of UTC, that could show the date one day earlier than the actual snapshot. Pinned `timeZone: "UTC"` so the label always matches what was parsed, regardless of viewer location.
- **#17 — History tab distinguishes loading/error/no-data-yet.** `ScoreHistoryChart` only had one empty-points branch, so a genuine backend failure was indistinguishable from "not enough history yet." Added explicit `isLoading`/`isError` props and states, wired through from the `scoreHistory` query.

### LOW

- **#19 — frontend defensive check on trade-level ordering.** Previously trusted the backend's `stop_loss < price < take_profit` (BUY) / reversed (SELL) invariant blindly. Added a same-page sanity check that fails safe (renders nothing, same as the existing null-guard) rather than risk showing nonsensical numbers if a future backend regression ever violates that invariant.
- **#20 — Debt/Equity convention now matches the Multibagger page.** Both the US Key Ratios card and India's Debt-to-Equity note previously rendered the raw 0–300+ scale value suffixed with "%"; the Multibagger page renders the identical field (confirmed same scale via `screener_data.py`'s own comment) divided by 100 with a "×" suffix. Both Stock Detail page locations now match that convention.
- **#21 — US Revenue/Earnings Growth color-coded.** Previously plain white regardless of sign, unlike the adjacent 3-Year CAGR card and India's Compounded Growth Rates table — easy to misread a negative figure. Now green/red matching those.
- **#22 — previously-missing US fundamentals fields surfaced.** ROCE, Operating Margin, EV/EBITDA, Interest Coverage, and P/S Ratio were already computed and returned by the backend (`us_fundamentals.py`) but never rendered on the US Fundamentals tab, even though India's equivalent card shows comparable fields. Added to the Key Ratios grid.
- **#23 — Backtest zero-trades edge case handled.** A backtest with no qualifying signals in the window previously rendered an empty 0/0% summary and an empty results table with no explanation. Now shows an explicit "no signals to backtest" message instead.

**#18** (per-stock accuracy inheriting the unlabeled medium-horizon fallback) required no separate work — it sits inside the same AI Signal card whose "· Medium Term" label (PI-014) already covers it.

## Not in this release

**#15 — History tab's score-history has no market column.** `score_snapshots` (Postgres) has no `market` column; the frontend query key also omits `market`. A symbol string colliding across US and IN markets could show the wrong market's score history — a pre-existing, code-acknowledged gap (`postgres_store.py`'s own comment already notes this: "hasn't been observed in practice"). Fixing this properly requires a schema migration (add `market` column, backfill or accept NULL for historical rows, update the write path in the score-snapshot job, update the read query, update the frontend query key) — a materially bigger and riskier unit of work than the 15 fixes above, and involves the production database. Deferred pending explicit confirmation.

## Tests

- `mediumLowSeverityAuditFixes.test.ts` — 21 new tests, source-assertion style (same convention as PI-014, this page mounts through live data-fetching/auth/router context impractical to isolate).
- Full frontend suite: **312/312 passed** (291 baseline + 21 new).
- Typecheck: clean.
- No backend changes this release.

## Rollback

All 15 fixes are independent — any can be reverted individually. None touch schema, API contracts, or shared state beyond the single page/component files listed above.
