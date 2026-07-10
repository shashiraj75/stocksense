# Sprint #013 — Recommendation Consolidation Frontend Test Foundation

**Epic:** 005 — Recommendation Consolidation Intelligence
**Status:** Complete.
**Follows:** Sprint #012 ([Live Stock Analysis Frontend Implementation](Sprint-012-Recommendation-Consolidation-Live-Stock-Analysis-Frontend-Implementation.md)), which shipped the Evidence Summary component but validated it only via `tsc --noEmit`, a full production build, and direct script execution against mocked scenarios — no committed test suite. That gap, and `MASTER-ROADMAP.md`'s own explicit recommendation ("a visual-QA-and-tooling sprint next, not a Railway flag enable"), is this sprint's entire scope.

---

## Summary

The frontend had **zero test framework** of any kind before this sprint (confirmed directly: no `jest`, `vitest`, or `@testing-library/*` in `frontend/package.json`). This sprint adds one and uses it to close the exact gap Sprint #012 named: committed, CI-gated tests for the RCI contract-validation function and the two components that render it.

This is infrastructure and test coverage only — no production behavior, contract, or UI copy changed. `RCI_LIVE_STOCK_ANALYSIS_ENABLED` remains disabled; nothing here is a step toward enabling it.

## Files Changed

- `frontend/package.json` / `package-lock.json` — added `vitest`, `@vitejs/plugin-react`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, `vite-tsconfig-paths` as dev dependencies; added `test`/`test:watch` scripts.
- `frontend/vitest.config.ts` (new) — Vitest config: jsdom environment, the existing `@/*` tsconfig path alias resolved via `vite-tsconfig-paths`, and dummy `NEXT_PUBLIC_SUPABASE_URL`/`_ANON_KEY` values injected via `define` (see Migration Notes).
- `frontend/vitest.setup.ts` (new) — loads `@testing-library/jest-dom/vitest` matchers globally.
- `frontend/src/utils/__tests__/getValidRecommendationConsolidation.test.ts` (new) — 19 tests covering `getValidRecommendationConsolidation` and `getValidResearchReport`: the fully-valid path, absent/null/non-object prediction or field, unsupported contract version, missing/empty load-bearing field (`narrative` / `sections`), and every string-array field rejecting a non-string-array value.
- `frontend/src/components/__tests__/DisclosurePanel.test.tsx` (new) — 4 tests: default-collapsed, expand-on-click, `defaultOpen`, re-collapse on second click, and `aria-expanded` state.
- `frontend/src/components/__tests__/EvidenceSummary.test.tsx` (new) — 9 tests: renders nothing for absent/null/malformed RCI (the "feature-disabled" case Sprint #012 flagged as unrenderable by design), each headline-selection branch (`headlineFor`'s gate/warning/mixed/low-completeness/default cases), always-visible active gates and material warnings, single-response coverage-notice de-duplication, and that supporting/opposing evidence plus conflicts stay behind the "Show evidence detail" disclosure until expanded.
- `.github/workflows/frontend_tests.yml` (new) — runs `npm test` (Vitest) and `npx tsc --noEmit` on every push/PR touching `frontend/**`, mirroring `backend_tests.yml`'s existing pattern exactly (path-scoped trigger, same job shape).

## Architecture Changes

None. No production component, contract, or API shape changed — verified by diffing `frontend/src/utils/api.ts` back to its pre-sprint state byte-for-byte after the sanity-check step below.

## Testing Status

- **32/32 new frontend tests passing** (`npx vitest run`).
- **Sanity-checked per SES-003 §4's discipline** (that standard formally applies to `backend/tests/` only, but the same method was applied here since no frontend equivalent exists yet): deliberately removed the `narrative` validity check from `getValidRecommendationConsolidation`, confirmed the corresponding test failed with a clear assertion message, then restored the file and confirmed the suite passed again with `api.ts` byte-identical to before.
- `npx tsc --noEmit` clean.
- **What is covered:** the RCI/Research-Report contract-validation boundary function (the single most safety-critical piece — it decides whether anything renders at all) and the two components that consume it end-to-end.
- **What is not covered, named explicitly:** no other frontend component in the codebase has any test coverage yet (Portfolio, Daily Picks, Stock Detail, Multibagger, Paper Trading, Alerts — all zero). This sprint is a foundation, not frontend-wide coverage, exactly as Sprint #002 was for the backend. `ScoreHistoryChart.tsx`'s recent Factor Breakdown bug (session prior to this one) is a concrete example of a bug this new framework could have caught with a rendered-line-count assertion — not retroactively added here, named as a good first candidate for the next test-writing pass.

## Risks

- **Narrow scope, named honestly.** This closes the specific gap Sprint #012 flagged, not general frontend test debt — a reviewer should not read "frontend now has tests" as "frontend is now well-tested."
- **`vite-tsconfig-paths` deprecation notice.** Vitest 4's own CLI output notes native `resolve.tsconfigPaths` support now exists and the plugin is no longer strictly required; kept the plugin for this sprint since it's proven working, not because it's the only option — a future cleanup could drop the extra dependency.
- **Dummy Supabase credentials in `vitest.config.ts`.** `utils/api.ts` imports the Supabase browser client at module scope, which throws at import time without *some* URL/key present — tests never make a real Supabase call, so placeholder values are safe here, but this is a real coupling worth someone eventually addressing (e.g. lazy-initializing the client) rather than every test file needing to route around it.
- **CI cost.** A new required check adds ~30-60s to every `frontend/**` push/PR — negligible against the risk it closes, but named since Section 5 of `SES-001` treats CI changes as worth flagging, not silent.

## Migration Notes

- Any future test importing `@/utils/api` (directly or transitively) needs the `NEXT_PUBLIC_SUPABASE_URL`/`_ANON_KEY` `define` entries already in `vitest.config.ts` — no per-test setup required, this is global.
- New component tests belong in a `__tests__/` folder alongside the component being tested (mirrors this sprint's layout), not a single top-level test directory — keeps a test physically next to what it covers.
- Run `npm test` (single run, CI-shaped) or `npm run test:watch` (interactive) from `frontend/`.

## Recommendations for the Next Sprint

- Extend coverage to `ScoreHistoryChart.tsx` (Composite Score / Factor Breakdown line-rendering, given its recent real bug), `PortfolioAllocationChart.tsx` (sector/stock toggle logic, recently fixed in this session), and the confidence-color-tiering helpers on the Stock Detail and Daily Picks pages (`confidenceTextColor`/`confidenceGradientClass`) — all are pure-enough logic or isolated-enough components to test cheaply, and all had real, user-reported bugs recently.
- Once component coverage broadens, revisit whether the operational decision on activating `RCI_LIVE_STOCK_ANALYSIS_ENABLED` and/or `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US` (both still named as open items in `MASTER-ROADMAP.md` Section 4) should be scheduled — this sprint deliberately does not touch that decision.
