# CHANGELOG

## Unreleased

### Added

- **Sprint #013 — Recommendation Consolidation Frontend Test Foundation.** Added a frontend test framework (Vitest + React Testing Library + jsdom), which did not exist at all before this sprint, plus 32 tests covering the RCI/Research-Report contract-validation boundary (`getValidRecommendationConsolidation`/`getValidResearchReport`) and the `DisclosurePanel`/`EvidenceSummary` components. New `frontend_tests.yml` GitHub Actions workflow runs the suite plus `tsc --noEmit` on every `frontend/**` push/PR, mirroring `backend_tests.yml`. Closes the exact gap Sprint #012 disclosed (validated only via manual script execution, no committed suite) and acts on `MASTER-ROADMAP.md`'s own recommendation. No production code, contract, or UI changed; `RCI_LIVE_STOCK_ANALYSIS_ENABLED` remains disabled. See [Sprint #013](Releases/Sprint-013-Recommendation-Consolidation-Frontend-Test-Foundation.md).

### Fixed

- **Multibagger (all India screens returned zero results):** `promoter_pledge_pct` is `NULL` (not `0`) for any stock screener.in shows no pledge disclosure for, since `screener_data.py` only sets it when a "Pledge" row exists on the page. Every India screen's `promoter_pledge_pct < N` condition failed on that `NULL`, excluding almost every clean company. `backend/services/fundamentals_cache.py`'s `_SCREENS` now use `COALESCE(promoter_pledge_pct, 0) < N`. Verified live: `quality_compounder` 0→52, `multibagger_discovery` 0→167, `tenbagger_early` 0→73.
- **Portfolio:** added a Day's P&L column (amount + %), a By Sector/By Stock allocation toggle (`PortfolioAllocationChart.tsx`), fixed India vs. US allocation-view inconsistency (sector-data threshold and a stale-default `useState` bug), fixed real mobile table overflow, fixed wrapped P&L number spacing and an empty Signal column.
- **Stock Detail:** AI Signal panel and Daily Picks confidence bars now tier BUY color by confidence (mirroring `SignalBadge`) instead of showing flat green regardless of strength; opening a stock from a specific Daily Picks horizon now lands on that same horizon tab instead of always Short Term; fixed the History tab's Factor Breakdown chart rendering completely empty (Recharts Fragment-vs-array children scanning + a stuck-animation `requestAnimationFrame` dependency, both in `ScoreHistoryChart.tsx`).

### Documentation

- Added the Current Release Status register as the authoritative source for live release state, validation gates, feature flags, scheduler state, and operational blockers.
- Reconciled roadmap and architecture references so Recommendation Consolidation Intelligence (Epic 005) is distinct from future Prediction and Recommendation Decision Architecture Evolution (Epic 006).
- Marked the legacy Selection Engine roadmap as historical rather than current implementation guidance.
- Recorded Session 10 (2026-07-10) fixes in `Documentation/STOCKSENSE_DOCUMENTATION.md` §27 Changelog. Flagged that the Multibagger Screen still has no dedicated numbered section in that document (only glossary mentions in §18a) — owed as follow-up.
