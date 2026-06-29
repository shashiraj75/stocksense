# Sprint #012 — Recommendation Consolidation Live Stock Analysis Frontend Implementation (Epic 005)

**Status:** Complete. A narrow frontend implementation sprint — the Evidence Summary component now exists and is wired into the live Stock Analysis page, but remains invisible in production because `RCI_LIVE_STOCK_ANALYSIS_ENABLED` is still disabled in Railway, unchanged this sprint.

## Evidence Checkpoint

Reviewed directly: Sprint #001–#011 reports, SSDS-009, the Evidence Contract, Traceability/Versioning doc, the UI Consumer Design doc, the Copy and Frontend Contract Spec, the current RCI composer (`recommendation_consolidation_api_composer.py`), the current `/predict` response (confirmed via the composer's own code, not re-implemented), the current `frontend/src/utils/api.ts`, `frontend/src/app/stock/[symbol]/page.tsx`, and existing component conventions (`SignalBadge.tsx`, `BullBearCase.tsx`).

Confirmed directly from code before any change was made:
- The `Prediction` TypeScript interface lives at `frontend/src/utils/api.ts:54-68` (pre-this-sprint).
- The AI Signal / key-metrics card runs from roughly line 349 to line 690 of `frontend/src/app/stock/[symbol]/page.tsx`; the Market Regime notice (the page's only existing inline notice pattern) sits at lines 624-675; the horizon tab bar begins immediately after at line 677 (`{/* Tabs row */}`) — confirming the exact insertion point Sprint #010 specified (below the AI Signal/key-metrics area, above the horizon tabs) maps to **the gap between the Market Regime block and the Tabs row comment**, not a new top-level section.
- **No reusable card/notice/badge/icon primitive component exists as a generic, importable building block** — `SignalBadge.tsx` and `BullBearCase.tsx` are each purpose-built, single-use components; the Market Regime notice is inlined directly in `page.tsx`, not extracted. Evidence Summary therefore follows the same convention (a dedicated component, not a generic "Notice" library) rather than inventing a premature abstraction.
- **No accessible collapsible/disclosure primitive existed anywhere in `frontend/src`** — reconfirmed (Sprint #010/#011 finding, verified again directly this sprint via repo-wide search returning zero matches for `Collapsible`/`Accordion`/`<details`/`aria-expanded` before this sprint's changes). A new `DisclosurePanel` component was built, scoped narrowly to this need.
- **The frontend has no test framework, no test runner, and no test script of any kind** — confirmed directly from `frontend/package.json`: its `scripts` block has only `dev`/`build`/`start`/`lint`; `devDependencies` lists only TypeScript/Tailwind/PostCSS tooling, no Jest/Vitest/React Testing Library/Playwright; `node_modules` contains no test-runner binary. This is a **real, material discrepancy from this sprint's own brief**, which assumed "the repository's established frontend test conventions" exist — none do. See "Test Plan" section below for how this was resolved without violating the brief's own "do not invent a test framework" instruction.

Non-negotiable invariants reconfirmed unchanged throughout implementation:

| Invariant | Status |
|---|---|
| Prediction Engine remains sole source of truth for signal/confidence/score/gates | **Confirmed** — `EvidenceSummary` never reads or renders `prediction.signal`/`prediction.confidence`; it only reads `prediction.recommendation_consolidation` |
| RCI remains read-only, additive, deterministic, non-authoritative | **Confirmed** — the component performs zero computation of investment meaning; every value rendered is either a verbatim backend string or a small, fixed mapping table (`headlineFor`) over backend-provided enum/boolean fields |
| Frontend does not derive RCI states from raw engine values | **Confirmed** — `EvidenceSummary` has no reference to `business_quality`/`financial_strength`/`growth_intelligence`/`valuation_intelligence` anywhere in its source |
| Frontend does not compute a second score/confidence/recommendation/gate | **Confirmed** — no numeric percentage is rendered anywhere in the component; `explanation_confidence_category` is rendered as a category word only |
| RCI disabled in Railway throughout | **Confirmed unchanged** — no `.env.example`, Railway config, or backend env-read code touched |
| No backend RCI/Prediction Engine/router/cache/persistence code changed | **Confirmed** — `git diff` scope is limited to `frontend/` plus documentation |
| Daily Picks untouched | **Confirmed** — no `daily_picks.py` reference anywhere in this sprint's changes |
| Valuation Intelligence flag state unchanged | **Confirmed** |

### A genuine backend-contract gap found during implementation, not invented around

While implementing rendering for semantic state 8 ("feature-disabled engine state," e.g. Valuation Intelligence while its kill switch is off), direct inspection of `recommendation_consolidation_engine.py` confirmed: **`EvidenceStatus.FEATURE_DISABLED` is used only internally**, to gate whether `CP-02`/`CP-03` conflict detection fires (`recommendation_consolidation_engine.py:96`). **No field in the serialized `RecommendationConsolidationResponse` ever exposes a feature-disabled notice to an API consumer** — it is not in `coverage_notices`, not in any other field. Sprint #011's design assumed this state would render via some backend-sourced text; that assumption is now confirmed incorrect by direct code execution.

**Resolution, per this sprint's own explicit rule ("never invent frontend-only replacements for backend semantics")**: `EvidenceSummary` does not render anything for this state. It is the same safe default as full RCI absence (state 9) — silence, not invented copy. This is named as a known limitation below, with the precise minimal backend addition needed to close it.

## Frontend Files Changed

| File | Change |
|---|---|
| `frontend/src/utils/api.ts` | Added the `RecommendationConsolidation`/`RciConflict`/`RciThesisState`/`RciExplanationConfidenceCategory` types, `SUPPORTED_RCI_CONTRACT_VERSION`, the optional `recommendation_consolidation` field on `Prediction`, and `getValidRecommendationConsolidation()` — the single function that decides absence/validity/version-gating |
| `frontend/src/components/DisclosurePanel.tsx` | **New.** A small, accessible expand/collapse primitive (button + `aria-expanded`/`aria-controls`, keyboard-operable via native `<button>` semantics) — confirmed not to exist before this sprint |
| `frontend/src/components/EvidenceSummary.tsx` | **New.** The Evidence Summary component itself |
| `frontend/src/app/stock/[symbol]/page.tsx` | One import added; one render call (`<EvidenceSummary prediction={prediction} />`) inserted between the Market Regime block and the Tabs row, guarded by `!predLoading` |

No other file was touched. `frontend/tsconfig.tsbuildinfo`'s pre-existing, unrelated modification (a build-cache artifact, present before this sprint per every prior sprint's own audit) is not staged.

## TypeScript Contract Implemented

Implemented exactly per Sprint #011 §3's field-by-field table — every field name and shape matches the backend's `RecommendationConsolidationResponse` dataclass directly (cross-checked against `recommendation_consolidation_contract.py` and the directly-executed sample payload from Sprint #011's own evidence checkpoint). `contract_version`, `snapshot_id`, and `engine_versions_used` are typed but never rendered (internal-only, per spec). `evidence_completeness_pct: number | null` is typed nullable, matching the confirmed-nullable real behavior.

`getValidRecommendationConsolidation()` implements all six top-level cases from Sprint #011 §3A in one place:

| Case | Verified behavior (via direct script execution, see Test Plan) |
|---|---|
| Absent | Returns `null` |
| `null` | Returns `null` (defensive — not reachable via today's backend, per Sprint #011's own finding) |
| Malformed (e.g. `active_gates` not an array) | Returns `null` |
| Incomplete (missing `narrative`) | Returns `null` |
| Unsupported version (`contract_version !== 1`) | Returns `null` — suppressed entirely, not a minimal fallback, per Sprint #011 §3C's recommended default |
| Valid | Returns the object, unmodified |

`EvidenceSummary` calls this once and returns `null` immediately if it returns `null` — no other code path in the component can render partial or malformed content.

## Components and Primitives Added

- **`DisclosurePanel`** — generic, reusable, two props (`label`, `defaultOpen`), uses `useId()` for `aria-controls`, a native `<button>` for keyboard/screen-reader correctness, and a `ChevronDown` icon that rotates on open (no new icon asset, reuses `lucide-react`, already a project dependency).
- **`EvidenceSummary`** — the section itself, composed of two small internal helpers (`EvidenceList`, capping visible items at 3 with a "Show N more" `DisclosurePanel`; `EvidenceSummaryNotice`, a tone-aware icon+text row reused for gates/warnings/flags/coverage).

## Placement Confirmation

Rendered between the Market Regime block and the horizon Tabs row — confirmed by direct line-number inspection (above) to be the exact gap Sprint #010 specified as "below the AI Signal/key-metrics area, above the time-horizon tabs." Guarded by `!predLoading`, matching the existing Market Regime block's own loading-guard convention, so it never flashes during a pending fetch.

## Semantic-State Behavior

| State | Implementation |
|---|---|
| 1. Active enforced gate | Rendered via `EvidenceSummaryNotice` with `tone="critical"` and a `Lock` icon, mapped 1:1 from `active_gates`; **always rendered outside any `DisclosurePanel`**, never collapsible; overrides the headline via `headlineFor()`'s first precedence check |
| 2. Unresolved risk flag | `Flag` icon, `tone="caution"` — a different icon from state 1's `Lock`, same tone tier as state 3 but visually distinguished by icon and `aria-label` text (`"Unresolved risk flag, not currently enforced: ..."`) |
| 3. Material warning | `AlertTriangle` icon, `tone="caution"` — always rendered outside any collapse, per Sprint #011 §8 |
| 4. Mixed evidence/conflict | Reflected via `headlineFor()` → "Evidence is mixed"; `conflicts` rendered inside the `DisclosurePanel` under "Notable patterns," each item keyed by the confirmed-stable `conflict_id` |
| 5. Broadly aligned evidence | `headlineFor()` → "Evidence broadly aligned" (the unstyled, lowest-weight default — no special icon/notice row, just the headline + narrative) |
| 6. Company-specific evidence gap | Surfaces only via `conflicts` (e.g. a `CP-07`-style entry) and a lower `evidence_completeness_pct` feeding into the "Limited evidence available" headline — never a dedicated notice row, since no separate field exists for it (correctly mirrors the actual contract shape) |
| 7. Structural market coverage notice | Rendered inside a nested `DisclosurePanel` labeled "Coverage," `Info` icon, `tone="neutral"`; de-duplicated via `Array.from(new Set(...))` on the exact array from this one response only — **no cross-response or cross-session de-duplication implemented**, per the explicit instruction not to use text matching across fetches |
| 8. Feature-disabled engine state | **Not rendered — confirmed no backend field exists for it** (see Evidence Checkpoint finding above); documented as a known limitation, not worked around |
| 9. RCI absent/omitted | `EvidenceSummary` returns `null`; nothing renders; the rest of the page is unaffected (confirmed by the production build succeeding and by direct reasoning about the component's own early-return structure — there is no code path between `getValidRecommendationConsolidation` returning `null` and any other render call) |

## Accessibility Behavior

- `DisclosurePanel`'s toggle is a native `<button type="button">` with `aria-expanded` and `aria-controls` pointing at a `useId()`-generated panel id — keyboard-operable by default (native button semantics handle Enter/Space without extra key handlers).
- Every `EvidenceSummaryNotice` row carries a descriptive `aria-label` (e.g. `"Active gate: Financial Strength: true_veto (enforced)"`) plus a non-decorative icon — meaning, per state, is never color-only: `Lock` (gate) / `AlertTriangle` (warning) / `Flag` (unresolved risk) / `Info` (coverage) are four visually distinct icon shapes, never relying on the critical/caution/neutral color tier alone.
- Color tiers reuse only existing design tokens (`bear`/`yellow-500`/neutral white-alpha), confirmed by direct reuse of the exact class patterns already present in the Market Regime block — no new colors introduced.
- The headline renders as an `<h3>`, reachable via heading-level screen-reader navigation, not a styled `<div>`.
- No state uses red/green alone: the "broadly aligned" state (5) uses no color treatment at all (plain text), avoiding a celebratory-green default explicitly named as a risk in Sprint #010/#011.

## Copy Catalogue Adherence

Headlines (`"Existing gate blocks the thesis"`, `"Important caution present"`, `"Evidence is mixed"`, `"Limited evidence available"`, `"Evidence broadly aligned"`) are taken verbatim from Sprint #011 §4's Layer A table — no wording was improvised. Section label is exactly `"Evidence Summary"`. All other user-visible text (gate/flag/warning/coverage rows, `engine_agreement`, `explanation_confidence_category`, `narrative`) is rendered **verbatim from the backend response**, never reworded — per Sprint #011 §5/§6's "no LLM-generated wording, no improvised investment-language alternatives" rule. No forbidden word (`Buy`, `Sell`, `Hold`, `Strong Buy`, `Avoid`, `recommendation`, a confidence percentage, `risk score`, `guaranteed`, `opportunity`, `target`, `upside`, `stop loss`, `allocation`, `entry zone`, etc.) appears anywhere in `EvidenceSummary.tsx`'s static strings — confirmed by direct re-reading of the component's source, the entirety of which is reproduced in this sprint's diff.

## Test Matrix and Results

**Material discrepancy from the brief, documented honestly per its own instruction**: this repository has no frontend test framework, test runner, or test script (Evidence Checkpoint, above). The brief's own instruction — "Use the repository's established frontend test conventions. Do not invent a test framework." — cannot both be satisfied when no convention exists; inventing one (e.g. adding Jest/Vitest from scratch) would itself violate the second half of that same instruction. **Resolution adopted**: no test framework was added. Validation was performed via three other concrete, evidence-based methods instead:

1. **TypeScript compilation** (`npx tsc --noEmit`) — passed cleanly, zero errors, confirming the new types and their usage are internally consistent with the existing codebase's types.
2. **Production build** (`npm run build`) — passed cleanly (`✓ Compiled successfully`, all 18 routes generated, including the dynamic `/stock/[symbol]` route), confirming the new component tree renders without a build-time error across Next.js's full static/dynamic page generation.
3. **Direct, deterministic script execution of the two pure logic functions** (`headlineFor` and `getValidRecommendationConsolidation`), run via `npx tsx -e "..."` against the exact same mocked payload shapes Sprint #011's brief enumerates — not committed to the repository (no test file added, consistent with "do not invent a test framework"), but executed and its output inspected directly as part of this sprint's own validation, reproduced below:

| Scenario | Mocked input | Verified output |
|---|---|---|
| Broadly aligned | `thesis_state: "supported"`, no gates/flags/warnings, `evidence_completeness_pct: 90` | `"Evidence broadly aligned"` |
| Mixed evidence | `thesis_state: "mixed"` | `"Evidence is mixed"` |
| Limited evidence (insufficient) | `thesis_state: "insufficient_evidence"` | `"Limited evidence available"` |
| Limited evidence (null completeness) | `evidence_completeness_pct: null` | `"Limited evidence available"` |
| Unresolved flag / warning present | `unresolved_risk_flags: ["x"]` | `"Important caution present"` |
| Active gate overrides caution | `active_gates: ["g"]`, `unresolved_risk_flags: ["x"]` | `"Existing gate blocks the thesis"` (confirms gate precedence over states 2/3) |
| RCI absent | `{}` (no `recommendation_consolidation` key) | `null` (no render) |
| RCI explicitly `null` | `{recommendation_consolidation: null}` | `null` (no render) |
| Valid object | A fully-populated, contract-shaped object | Truthy (renders) |
| Unsupported version | `contract_version: 2` | `null` (no render) |
| Malformed (missing `narrative`) | `narrative: undefined` | `null` (no render) |
| Malformed (wrong type) | `active_gates: "not-a-list"` | `null` (no render) |

This covers scenarios 1, 2, 3, 4, 5 (via the unresolved-flag/active-gate cases), 12, 13 (via the unsupported-version case, treated identically to an unknown enum per Sprint #011 §3C's fallback rule), and 14 from the brief's required list directly. Scenarios 6-11 (specific real-world engine combinations like Business Quality's fraud flag, India coverage notices, Bank/NBFC non-applicability, multiple simultaneous items) were validated by **manual code-path tracing** rather than executed scripts: each is a direct pass-through of a backend-provided string into an already-verified rendering path (`EvidenceSummaryNotice`/`EvidenceList`), with no additional branching logic specific to those scenarios — confirmed by reading `EvidenceSummary.tsx`'s source directly, not assumed. Scenario 15 (mobile) was reviewed via Tailwind class inspection (the component uses only flex/text-size utilities already proven responsive elsewhere on this page, no fixed pixel widths) but not visually screenshotted on a real narrow viewport this sprint (see Known Limitations). Scenario 16 (`DisclosurePanel` keyboard behavior) was verified by code inspection (native `<button>` + `aria-expanded`/`aria-controls`) rather than an automated accessibility-tree assertion, since no test runner exists to host one.

**Regression checks required by §9** (no second signal, no second confidence score, no derivation from raw engine scores, no render when absent, no coverage-notice-as-weakness framing, no gate/flag visual conflation, no alteration of existing content when RCI is absent) — all confirmed by direct source inspection of `EvidenceSummary.tsx`: it imports nothing from `business_quality`/`financial_strength`/etc., contains no numeric percentage render, returns `null` immediately on invalid/absent input with no other code path executed first, uses distinct icons/tones for gates vs. flags, and is inserted as a sibling addition to existing JSX (verified via the minimal, additive diff to `page.tsx` — no existing line was modified, only a new import and a new conditional block inserted).

## Manual / Visual Validation

Per this sprint's explicit instruction, the Railway RCI flag was **not** enabled to validate the UI, and no production-user validation is claimed. Validation performed:

1. **Production build** confirmed the page compiles and all routes generate successfully with the new component present (method, above).
2. **Static code-path review** confirmed correct behavior across all 16 scenarios in the design scenario matrix (Sprint #011 §9), as detailed in the Test Matrix section.
3. **No live or local-fixture-driven render was performed in an actual running browser** — `npm run dev` was not started against a live or mocked backend this sprint. This is named honestly as a real, incomplete part of "manual validation" rather than glossed over: the component has been verified to compile and to contain logically correct, type-safe rendering branches, but has not been visually inspected rendering real pixels in a browser, on either desktop or mobile width, in this sprint.

**Honest copy/UX concern found and documented, not silently fixed**: the headline "Important caution present" is shared by both unresolved-risk-flags and material-warnings cases (Sprint #011 §4's own headline set has only 5 options, and neither state has a dedicated headline of its own) — meaning a user cannot tell from the headline alone which of the two is present; they must read the body to know. This is a direct, faithful implementation of Sprint #011's own 5-headline design (not a frontend deviation), but is flagged here as worth revisiting if user feedback later finds it ambiguous.

## RCI and Backend Confirmation

- **`RCI_LIVE_STOCK_ANALYSIS_ENABLED` remains absent/disabled** in every local and documented environment — `.env.example` unchanged, no Railway variable touched, confirmed by `git diff` showing zero changes outside `frontend/` and `Documentation/`.
- **No backend code was changed** — `git diff --stat` for this sprint touches no file under `backend/`.
- **Daily Picks is untouched** — no `daily_picks.py` reference anywhere in this sprint's diff.
- **Prediction Engine is untouched** — no `prediction_engine.py` reference anywhere in this sprint's diff.
- **Valuation Intelligence's kill-switch state is unchanged** — not referenced by any change this sprint.

## Known Limitations

1. **No frontend test framework exists in this repository** — this sprint's validation relied on TypeScript compilation, a full production build, and direct, ad hoc script execution of the two pure logic functions, not a committed, repeatable automated test suite. Introducing a test framework (Jest/Vitest/React Testing Library) is a real, separately-scoped prerequisite for any future sprint that wants committed, CI-enforced frontend regression tests.
2. **State 8 (feature-disabled engine, e.g. Valuation Intelligence) cannot be rendered today** — the backend never serializes this status into any public RCI field (confirmed by direct code inspection of `recommendation_consolidation_engine.py`). The smallest fix: add a `feature_disabled_notices: tuple[str, ...]` (or fold it into a generalized, identified notice structure, see limitation 3) field to `RecommendationConsolidationResponse`, populated analogously to `_coverage_notices()`.
3. **`coverage_notices`/`unresolved_risk_flags`/`material_warnings` still have no stable per-item identifier** — reconfirmed unchanged from Sprint #011. De-duplication in this implementation is correctly scoped to within a single response only (`Array.from(new Set(...))` on the exact array), never across stocks or sessions, per the explicit instruction. The previously-recommended minimal backend addition (a `reason_code` per item) remains open.
4. **No real-browser visual validation was performed this sprint** (named above) — a future sprint or manual QA pass should run `npm run dev` against either a local mock or a temporarily-flagged-on local backend instance (never Railway) and visually confirm desktop/mobile rendering before any production activation.
5. **`next lint` cannot run in this environment** — confirmed pre-existing, not introduced by this sprint: no ESLint package or config exists anywhere in `frontend/` (`node_modules/.bin/eslint` is absent, no `.eslintrc*`/`eslint.config.*` file exists). `npm run lint` / `npx next lint` both fail with an unrelated CLI argument-parsing error rather than running any actual lint check. This is a repository-wide gap predating this sprint, not a regression caused by it.

## Recommendation for Epic 005 Sprint #013

Given the limitations above, the recommended next step is **not** enabling the Railway flag yet. Instead: **a short, narrowly-scoped frontend-tooling and visual-QA sprint** — (a) run the app locally against a deterministic mocked `/predict` response (e.g. a local intercept or a temporary, non-Railway local env flag) and visually confirm the Evidence Summary section across desktop and mobile widths for at least the broadly-aligned, mixed, active-gate, and coverage-notice states; (b) optionally introduce a minimal test framework (e.g. Vitest, which integrates cleanly with Next.js/TypeScript with little new configuration) specifically so the scenario matrix validated manually this sprint can become a committed, repeatable regression suite; (c) decide whether to pursue the two named backend-contract additions (feature-disabled notice exposure; stable per-item identifiers on coverage/flag/warning lists) as a small, separate backend sprint before or after the visual QA pass. Only after visual QA passes should a controlled, no-broader-rollout Railway flag test (Sprint #009's own Path C, still unexercised) be considered.

---

*No backend code, Prediction Engine, API router, cache, persistence, Daily Picks, Portfolio, Watchlist, Alert, or Paper Trading code was modified. No Railway variable was changed. `RCI_LIVE_STOCK_ANALYSIS_ENABLED` and Valuation Intelligence's kill switches remain disabled. The Evidence Summary component is implemented and integrated but renders nothing in production today, since the backend never includes `recommendation_consolidation` while the flag is off.*
