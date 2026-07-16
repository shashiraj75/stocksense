# Product Integrity Workstream #021 — High Conviction Picks Filter

**Status:** Deployed to production (2026-07-16, commit `37cf159`) — verified live on stocksense360.com: toggle renders, activates with correct styling, and correctly filters/sorts against real India Long Term data (LUPIN 91%, NATIONALUM 88%).

## 1. Trigger

User question during PI-020's US Daily Picks monitoring: "how to get highest AI Confidence Daily Picks, with more than 85% confidence/Conviction score?" Investigation found there was no way to do this on the Picks page — no sort or filter by confidence existed; a user would have to manually scan every card across all three horizon tabs. Confirmed via direct production data query that 85%+ confidence picks do occur regularly (2 in the latest India batch, 3 in the last US batch) but almost exclusively in the long-horizon list, making them easy to miss. User asked for the feature to be built.

## 2. Feature

Added a "High Conviction Only (≥85%)" toggle to the Picks page, alongside the existing Short/Medium/Long horizon tabs (`frontend/src/app/picks/page.tsx`):

- **Off (default):** no behavior change — picks render exactly as before, in the existing rank order.
- **On:** the currently selected horizon's picks are filtered to `confidence >= 85` and sorted highest-confidence-first. Composes with the horizon tabs rather than replacing them — switching horizons while the toggle is on re-filters/re-sorts that horizon's picks.
- A dedicated empty state distinguishes "this horizon has picks, but none reach 85%" from the pre-existing "no BUY signals at all today" state, with a one-click "Show all N picks" button to turn the filter back off.
- The India session-freshness containment (stale/unknown price-reference notice) now evaluates against the filtered/sorted list actually on screen, not the full unfiltered horizon list, so its counts stay accurate under the new filter.

`85` was chosen to match the threshold the user asked for directly, and because it's confirmed (via production data) to be the range where the top slice of real picks actually lands — not an arbitrary round number picked without checking against live data.

## 3. What this does not do

- Does not change confidence computation, ranking, or any backend logic — purely a client-side view filter over data the API already returns for the current horizon.
- Does not filter/sort across horizons simultaneously (e.g. a single "all high-conviction picks regardless of horizon" view) — scoped to the existing per-horizon tab structure, matching how the rest of the page already works.
- Does not persist the toggle's on/off state across page reloads or add a URL query param for it — resets to off on navigation, same as the horizon tab's default.
- Does not add a general confidence range slider or arbitrary threshold input — the ask was specifically for a ≥85% high-conviction view, so that's what was built.

## 4. Tests

- New `highConvictionFilter.test.ts` — 13 tests: threshold constant and toggle-state wiring, the filter/sort composing correctly with the horizon tabs (not replacing them), freshness evaluation running against the filtered list, the picks grid and both empty-state branches keying off the filtered list, and — separately — a set of real behavioral assertions (not source-text checks) against an exact copy of the filter/sort computation: off returns picks unchanged in original order with no mutation; on keeps only `confidence >= 85` with the boundary tested explicitly (84% must not leak in, 85% must be included); on sorts strictly descending by confidence; on does not mutate the source array; on returns an empty result (not a crash) when no pick clears the bar or the horizon itself is empty.
- One pre-existing test in `validationIntegrityHold.test.ts` updated (not weakened) to match the intentional `picks` → `visiblePicks` rename at the grid's render call site — the assertion still locks in that `PickCard`/`TopReasons` render with the pick's reasoning, unchanged.
- Full frontend suite: **334/334 passed** (321 baseline + 13 new).
- Clean `tsc --noEmit` and clean `next build` (production build completed, all 18 routes generated).
- Manual browser verification: toggle renders correctly positioned alongside the horizon tabs, activates with correct active-state styling (emerald) on click, and the API request/error path was confirmed to fire correctly — a live confidence-value round-trip against production data wasn't captured in this pass because the local preview's CORS is scoped to allowed origins and didn't include the temp preview's local port; behavior is otherwise fully covered by the automated tests above, including the exact filter/sort logic and every rendering branch.

## 5. Rollback

Single-file frontend change (`frontend/src/app/picks/page.tsx`) plus test files — reverting restores the exact pre-feature horizon-tabs-only interface. No backend, schema, or API contract change.
