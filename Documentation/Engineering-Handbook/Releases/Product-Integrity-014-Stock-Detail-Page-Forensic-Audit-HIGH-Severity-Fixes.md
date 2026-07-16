# Product Integrity Workstream #014 — Stock Detail Page Forensic Audit, HIGH-Severity Fixes

**Status:** Deployed to production (2026-07-16, commit `645a7c8`).

## 1. Trigger

User reported a specific ambiguity on `stocksense360.com/stock/{symbol}`: clicking the "Fundamentals" tab left the "AI Signal" card showing Medium Term data with no indication of that fallback. Given the size and complexity of this page (~2,100 lines, multiple independent data sources — live quote, per-horizon AI predictions, fundamentals, backtest, score history, news/sentiment), a full forensic audit was requested rather than a single spot-fix.

## 2. Audit scope and method

Five parallel read-only investigations covered: tab/horizon state consistency, AI Signal/confidence/trade-level internal logic, Fundamentals tab data accuracy, Backtest/History tab correctness, and News/Sentiment + cross-page price consistency. Each investigation was instructed to report both confirmed defects **and** explicitly-verified-clean areas, with file:line citations, to avoid speculative findings.

**Result: 23 distinct findings** — 7 HIGH, 10 MEDIUM, 6 LOW — plus a documented list of areas checked and confirmed correct (signal-tone muting logic itself, backend trade-level math ordering, confidence-number consistency across display locations, fundamentals/sentiment/RSI pill independence from confidence, news query symbol+market scoping, cap-tier threshold logic, and all fundamentals label/field bindings).

Given the volume, the user chose to fix the 7 HIGH-severity findings as a first scoped release, deferring MEDIUM/LOW to later passes (tracked below).

## 3. HIGH-severity fixes in this release

### 3a. AI Signal card discloses the Medium Term fallback (findings #1, #2)

Fundamentals and History tabs have no horizon of their own — `horizon` silently resolves to `"medium"` for both (`page.tsx:150`, pre-existing, unchanged). The AI Signal card rendered that fallback with zero on-screen indication, and the Short/Medium/Long tab row shows nothing highlighted while on a non-horizon tab either — so there was no cue anywhere. Added `" · Medium Term"` directly to the "AI Signal" label when `tab` is `"fundamentals"` or `"history"`, resolving both findings without touching tab-bar highlighting (which would risk implying the user is *on* the Medium Term tab when they aren't).

### 3b. 52W High/Low pills guarded against missing data (finding #4)

`quote.fifty_two_week_high`/`low` were interpolated unconditionally; a stock without a full year of trading history (e.g. a recent IPO) could render the literal string `"₹undefined"` as a stat pill. Now conditionally spread into the pill array, matching the existing guard pattern already used for D-Open/D-High/D-Low.

### 3c. AI Prediction card's signal strip uses `getSignalTone` (finding #5)

The strip previously colored its border and confidence-text purely from `signal` (BUY→green, SELL→red, HOLD→gray), ignoring confidence — while the `SignalBadge` chip inside the same strip already correctly muted low-confidence BUYs to gray via `getSignalTone`. Result: a 30%-confidence BUY could show a muted gray badge sitting directly next to a bright green border and confidence number for the identical signal+confidence pair. The strip now computes and uses the same `getSignalTone` result as the hero card and `SignalBadge`, so muting is consistent everywhere on the page.

### 3d. Trade Levels discloses price drift from the live quote (finding #3)

Entry/target/stop-loss and the "Target Price %" figure are computed off `prediction.current_price` — frozen at whenever the AI prediction was last generated (`staleTime` 14 minutes, can persist longer via `placeholderData` across horizon switches) — while the hero shows the *live* `quote.price` (refetched every 60s). `PaperTradeModal` already discloses this drift at trade-execution time; the main Trade Levels card had no equivalent note. Added a disclosure line, using the same $0.01 drift threshold `PaperTradeModal` already uses, so float-rounding noise doesn't trigger a false disclosure.

### 3e. Paper Trade button disabled during a background horizon refetch (finding #6)

`placeholderData: (prev) => prev` on the prediction query avoids a loading flash when switching tabs by keeping the *previous* horizon's prediction on screen while the new one fetches — but the hero panel (unlike the "AI Prediction" card below it) had no fetching indicator, so a user could click "Paper Buy"/"Paper Sell" during that window and have the trade modal open with the wrong horizon's signal/stop-loss/target. The button now disables and shows "Updating…" while `predFetching` is true.

### 3f. Backtest results cleared on symbol/market navigation (finding #7)

`btData`/`btError`/`btRunning` are plain `useState`, with no query-key scoping by symbol/market. Because this page component is reused across client-side navigation (e.g. clicking a different stock from Watchlist/Screener/Dashboard), a backtest run for one stock could keep rendering — win rate, results table, everything — under a *different* stock's header until the user manually re-ran it. Added a `useEffect` keyed on `[symbol, market]` that clears all three on navigation, so stale results are never shown even briefly.

## 4. What this release does not do

Explicitly deferred to later passes, per the user's chosen sequencing (HIGH first):

- **MEDIUM (10 findings)**: Market Regime / Evidence / Research Summary panels have no tab gating at all (leak onto Fundamentals/History/Backtest, further than the AI Signal card did); sentiment score and news list can drift independently (no shared cache key); Take Profit color logic uses price-direction not win/loss semantics (a SELL's target-hit and stop-hit both render red); Mkt Cap unit convention (T/B/M vs ₹ Cr) is inconsistent across the same page for India; hero header's company-name fallback chain omits `usFund.company_name` while the sticky ticker includes it; History tab's score-history has no market column (pre-existing, code-acknowledged gap); History chart dates can show the wrong calendar day for non-IST browsers; History tab collapses loading/error/no-data-yet into one message.
- **LOW (6 findings)**: per-stock accuracy inherits the same fallback as 3a (now covered by 3a's label); no frontend defensive validation of trade-level ordering (currently safe only because the backend invariant holds); Debt/Equity shown as % here vs. × ratio on the Multibagger page; US Revenue/Earnings Growth not color-coded unlike adjacent cards; several backend-computed US fundamentals fields (ROCE, EV/EBITDA, OPM%, interest coverage) never surfaced on the US Fundamentals tab; Backtest's zero-trades edge case has no explanatory empty state.

None of these are touched in this release.

## 5. Tests

- `highSeverityAuditFixes.test.ts` — 12 new tests, source-assertion style (this page mounts through live data-fetching/auth/router context impractical to isolate, matching this file's existing test convention) covering all 7 fixes.
- Full frontend suite: **291/291 passed** (279 baseline + 12 new).
- Typecheck: clean.
- No backend changes this release.

## 6. Rollback

All six code changes (3a–3f) are independent and additive/corrective — no schema, no API contract change. Any can be reverted individually without affecting the others.
