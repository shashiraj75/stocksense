# Recommendation Consolidation Intelligence — Live Stock Analysis UI / Consumer Design (Epic 005, Sprint #010)

**Status:** Design study only. No frontend code, backend code, API behavior, or Railway configuration was modified. No TypeScript interfaces or React components were added. No mockups or visual assets were created — all layout/placement decisions below are described in prose/tables, not rendered.

> **Update (Sprint #011):** §6's assumption that coverage notices might carry a reason code was not verified against the real, executed serialized contract. Sprint #011's direct execution of the actual RCI pipeline confirmed `coverage_notices` (and `unresolved_risk_flags`/`material_warnings`) are bare `tuple[str, ...]` with no identifier of any kind — only `conflicts` items carry a stable `conflict_id`. See [Live Stock Analysis Copy and Frontend Contract Spec, §6](Recommendation-Consolidation-Live-Stock-Analysis-Copy-and-Frontend-Contract-Spec.md) for the confirmed shape, the resulting de-duplication scope limitation, and the recommended backend-contract addition. This original section is left unchanged below, per this engagement's non-destructive "Update" pointer convention.

## Evidence Checkpoint

Reviewed directly: Sprint #001-#009 reports, SSDS-009, the Evidence Contract (`recommendation_consolidation_contract.py`), Traceability/Versioning doc, the Integration Readiness doc, Sprint #008's implementation report, Sprint #009's Operational Activation decision, the current `recommendation_consolidation_contract.py`/`recommendation_consolidation_api_composer.py`, and the current frontend: `frontend/src/utils/api.ts`'s `Prediction` interface, `frontend/src/app/stock/[symbol]/page.tsx` (1,838 lines), and `frontend/src/components/BullBearCase.tsx`.

Non-negotiable facts re-confirmed directly from code, not assumed:

| Fact | Status |
|---|---|
| Prediction Engine is sole source of truth for signal/confidence/score/gates | **Confirmed** — unchanged since Sprint #008's own confirmation; no commit since `089af91` touches `prediction_engine.py` |
| RCI is an additive explanation layer only | **Confirmed** — `RecommendationConsolidationResponse` (contract.py:155-186) has no score/signal/confidence-replacement field, by construction |
| RCI must never appear as a second Buy/Hold/Sell/Strong Buy decision or confidence percentage | **Design constraint, carried forward** — no contract field could even produce one; `thesis_state` is an enum-like string (`"supported"`/`"mixed"`/`"conflicted"`/`"insufficient_evidence"`), not a signal label |
| RCI must never expose a master score or hidden weights | **Confirmed by contract inspection** — no numeric scoring field exists anywhere in the response dataclass |
| Frontend must only render the structured backend response, never recompute RCI from raw engine values | **Design constraint** — backend already does 100% of the consolidation; frontend has no access to a recomputation path even if it wanted one, since raw engine evidence objects (`business_quality`, etc.) are separate, pre-existing response fields never intended for RCI-style synthesis |
| RCI must remain disabled in Railway throughout this sprint | **Confirmed unchanged** — no commit since Sprint #009 touches `.env.example` or Railway config |
| Daily Picks outside scope | **Confirmed** — no `daily_picks.py` reference in this document beyond the explicit live-vs-snapshot compatibility note in §5.I |
| Valuation Intelligence separately governed | **Confirmed** — its kill switches are referenced only as a feature-disabled *state* RCI must explain (§5.G), never as something this sprint activates |
| Structural coverage limitations are not company weaknesses | **Confirmed at the contract level** — `coverage_notices` is a dedicated, separate tuple field, deliberately not part of `conflicts`/`opposing_evidence` (contract.py's own in-line comment, Sprint #005's Structural Coverage Narrative Refinement) |
| Active gates visually distinct from unresolved risk flags | **Confirmed at the contract level** — `active_gates` (only `currently_enforced=True`) and `unresolved_risk_flags` (computed but not enforced) are separate tuple fields; this distinction is load-bearing data, not a UI nicety |

**No contradiction found** between this sprint's direct code inspection and any prior sprint's conclusion.

### Current frontend inventory (new findings this sprint)

- **API contract gap, reconfirmed**: `frontend/src/utils/api.ts`'s `Prediction` interface (`api.ts:54-68`) still has no `recommendation_consolidation` field; `fetchPrediction` still returns an untyped `as Prediction` cast over raw axios JSON (`api.ts:104`). Unknown fields are ignored safely **today only because nothing references them**, not because of a tested ignore-unknown-fields policy.
- **Page structure**: `frontend/src/app/stock/[symbol]/page.tsx` is a single 1,838-line client component. Visual order, top to bottom: a sticky symbol/price header bar; an optional `MarketDisclaimer` banner; a primary card containing the AI Signal badge (`SignalBadge`), `ConfidenceMeter`, current/target price, and an inline, non-collapsible **Market Regime notice** (a colored, icon-prefixed row, conditionally rendered only when the regime is non-`SIDEWAYS`); then a horizontal tab bar (`Short Term` / `Medium Term` / `Long Term` / `Fundamentals` / `Backtest` / `History`); each horizon tab renders `FactorAttributionWaterfall` and `BullBearCase` (a two-column bull/bear reason-chip list, each chip auto-categorized into Technical/Institutional/Fundamental/Sentiment/Market/Risk via keyword matching) plus RSI/technical detail further down.
- **Reusable visual primitives that already exist**: colored bordered notice rows with an icon + bold label + note text (the Market Regime pattern, `page.tsx:648-661`); a bull/bear two-column chip layout with auto-categorization and per-category color badges (`BullBearCase.tsx`).
- **A reusable primitive that does NOT exist today**: there is no collapsible/accordion/`<details>` component anywhere in `frontend/src` (confirmed by repo-wide search returning zero matches). The Market Regime notice is permanently visible, not collapsible — meaning a future "Show more" / expandable evidence panel (§4, Layer C) would require **building a new primitive**, not reusing an existing one. This is a genuine implementation prerequisite, named honestly, not assumed away.
- **Mobile**: the existing page uses Tailwind responsive classes throughout (e.g., `flex-col sm:flex-row`, `overflow-x-auto scrollbar-hide` on the tab bar) — confirming a mobile-first layout convention already exists that a new RCI section should follow, not invent its own pattern for.

## Primary Objective — Restated

The future RCI section must let a user answer "what supports this view, what challenges it, and what's unavailable" — and must not let them conclude they are looking at a second AI recommendation.

## Design Question — Label Selection

| Option | Retail clarity | Mistaken-for-2nd-recommendation risk | Explains mixed evidence | Live-suitability | Future snapshot-suitability | Mobile usability | Density |
|---|---|---|---|---|---|---|---|
| Investment Thesis | Medium | **High** — "thesis" reads as a position/call | Medium | Good | Good | Good | Medium |
| Why This Signal? | Medium | **High** — "signal" directly echoes the existing AI Signal label, inviting conflation | Medium | Good | Poor (implies live-only) | Good | Low |
| What Supports and Challenges This View | High | Low | High — names both sides directly in the title | Good | Good | Fair (long for a tab/header) | Medium |
| **Evidence Summary** | High | **Low** — "summary" of pre-existing facts, not a new verdict | High | Good | Good | Good | Low-medium |

**Recommended label: "Evidence Summary."** It is shortest among the low-risk options (mobile-friendly as a header/tab label), reads unambiguously as a recap of already-existing evidence rather than a new conclusion, and generalizes cleanly to a future Daily Picks snapshot view ("Evidence Summary — as of [date]" vs. "Evidence Summary — live"). "What Supports and Challenges This View" is the recommended fallback if user testing later finds "Evidence Summary" too vague on its own — it could appear as a one-line subtitle under the "Evidence Summary" header rather than as the header itself, getting the clarity of the longer phrase without its mobile-width cost.

## Required UI Information Architecture

### Layer A — Concise headline

| Headline | Backend source |
|---|---|
| "Evidence broadly aligned" | `thesis_state == "supported"` AND no `active_gates` |
| "Evidence is mixed" | `thesis_state == "mixed"` or `"conflicted"` AND no `active_gates` |
| "Limited evidence available" | `thesis_state == "insufficient_evidence"`, or `evidence_completeness_pct` below a backend-defined low threshold |
| "Existing gate blocks the thesis" | `active_gates` is non-empty (this overrides every other headline — an enforced gate is always the most important fact) |
| "Important caution present" | `unresolved_risk_flags` or `material_warnings` non-empty AND no `active_gates` AND `thesis_state` is otherwise `"supported"`/`"mixed"` |

The headline is a **direct, backend-driven mapping** — the frontend selects among a small, fixed set of pre-approved headline strings based on which contract fields are populated; it never invents new wording or infers a state the backend didn't already classify. Precedence order (highest first): `active_gates` non-empty → `unresolved_risk_flags`/`material_warnings` non-empty → low `evidence_completeness_pct` → `thesis_state`. No numeric score or Buy/Sell/Hold/Avoid word appears in any headline option.

### Layer B — Main explanation

Render `narrative` directly, verbatim, as the explanation body — the backend's own RCI core (Sprints #003-#005) already produces deterministic, human-readable text per Sprint #008's own confirmation. The frontend must not truncate, reword, or re-summarize `narrative` in a way that changes meaning; light, purely cosmetic transforms (e.g., paragraph spacing) are acceptable, semantic rewriting is not.

### Layer C — Expandable evidence details

A new collapsible panel (does not exist today, per the inventory above) revealing, in this order: `active_gates` (if any — always shown expanded by default, never inside the collapsed-by-default region, since these are the highest-severity facts); `unresolved_risk_flags`; `supporting_evidence`; `opposing_evidence`; `conflicts` (each rendered as a labeled headline + narrative + the named supporting/opposing engines); `material_warnings`; `coverage_notices` (shown last, least visually weighted — these are platform facts, not company findings); `explanation_confidence_category`; a small "Live · computed [timestamp]" footer line using `computed_at`. `contract_version` and `snapshot_id` are retained for potential future debugging/audit affordances but are **not user-visible by default** — they are implementation/audit metadata, not investor-facing content, consistent with the "no internal code identifiers by default" requirement.

## Required Semantic States

| State | Visible content | Must NOT show | Notes |
|---|---|---|---|
| **A. Broadly aligned** | Headline "Evidence broadly aligned"; narrative; supporting evidence list | Any certainty language beyond what `explanation_confidence_category` supports (e.g. never imply "guaranteed") | Lowest visual weight among the cautionary states — calm, not celebratory, styling |
| **B. Mixed evidence** | Headline "Evidence is mixed"; supporting AND opposing evidence given equal size/order, side-by-side or clearly alternating, never supporting-first-and-larger | — | Mirrors `BullBearCase.tsx`'s existing two-column equal-weight pattern — a reusable precedent already in this codebase |
| **C. Active enforced gate** | Headline "Existing gate blocks the thesis"; the specific gate text from `active_gates`; shown **above** the normal narrative, with the most severe available visual treatment (e.g. a bordered alert row, not just colored text) | Any wording suggesting RCI itself created or decided the gate — copy must read as "Financial Strength's existing veto is active," never "RCI is blocking this stock" | This is the one state allowed to override normal collapse/expand behavior — always visible, never hidden inside Layer C's collapsed region |
| **D. Unresolved risk flag** | A visually distinct (different color/icon than an active gate — e.g. amber vs. red) flag row reading "not currently enforced as an exclusion, but merits attention" | Any phrasing implying current exclusion or that the stock is already filtered out | Must never share the same icon/color as state C — color-independent distinction (icon + label text) per §8 |
| **E. Structural market coverage notice** | A single, deduplicated line (e.g. "Financial Strength data is not currently available for the Indian market") placed in the lowest-priority region of Layer C, under a "Coverage" sub-heading shown collapsed by default | Any per-company framing ("this company lacks...") — must read as a market-wide fact | The frontend should recognize and collapse a repeated, identical coverage-notice string across the same market into one line — preventing the "clutter across every India stock" problem named in the brief; this requires no new backend field, only frontend de-duplication logic on an already-deterministic string |
| **F. Company-specific evidence gap** | A normal `opposing_evidence`/`material_warnings` entry, styled identically to other company-specific findings | Conflation with state E's market-wide framing | Distinguished from E purely by which contract field/category it arrives in, not by new frontend logic — coverage notices and company gaps are already separate fields |
| **G. Feature-disabled engine state (e.g. Valuation Intelligence off)** | A neutral, low-weight note inside Layer C (not Layer A/B) reading e.g. "Valuation evidence is not currently included in this analysis" | Must never appear as a negative finding, a warning color, or anywhere near `opposing_evidence`/`active_gates` | Per the contract's own `NEVER_NEGATIVE_STATUSES` set (`FEATURE_DISABLED` included), this is enforced at the backend data layer already — the frontend's only job is to not visually contradict that by, e.g., coloring it red |
| **H. RCI unavailable / API failure** | Nothing — the Evidence Summary section simply does not render at all | Any error banner, broken-UI indicator, or "RCI failed" message | Mirrors the backend's own Option A choice (Sprint #008): omit entirely, never a structured failure marker. The rest of the Stock Analysis page (signal, confidence, fundamentals) must be visually unaffected, confirming the existing page already degrades this way since it has no `recommendation_consolidation`-dependent rendering today |
| **I. Live vs. snapshot** | A small, persistent "Live · computed [timestamp]" label, always visible (not just in Layer C) at the top of the Evidence Summary section | — | Future Daily Picks snapshot UI (out of scope) would need an equivalent "Snapshot · as of [date]" label using the same visual slot — documented here as a compatibility requirement, not designed |

## Visual Hierarchy and Placement Study

| Option | Comprehension speed | AI Signal clash | Page-scan impact | Mobile | Accessibility | Crowding risk |
|---|---|---|---|---|---|---|
| 1. Below the top AI Signal / key-metrics card | High | Low — sits after the signal/confidence is already established | Low | Good | Good | Low |
| 2. Below market-regime banner, before horizon tabs | Medium | Medium — sits between two already-dense rows (price header + tab bar), increasing pre-tab scroll length | Medium | Fair | Good | Medium |
| 3. Inside the Fundamentals tab | Low | Low | Low (hidden by default) | Good | Good | Low, but **buries the feature** — many users may never open the Fundamentals tab |
| 4. A new dedicated "Evidence" tab alongside the existing horizon tabs | Medium | Low | Low | Good — matches existing tab-bar mobile pattern (`overflow-x-auto`) | Good | Low |

**Recommended default placement: Option 1** (below the top AI Signal / key-metrics card, above the horizon tab bar) — it is seen by every visitor without an extra click (unlike Option 3 or 4), and it does not compete visually with the Market Regime notice that already occupies the "Option 2" position. A future dedicated "Evidence" tab (Option 4) is a reasonable **secondary** entry point for users who want to return to the detail later, but should not be the *only* entry point, since it would hide the feature from users who never explore tabs.

Visibility/collapse behavior:
- The **headline (Layer A)** and a 1-2 line excerpt of the **narrative (Layer B)** are visible by default, uncollapsed.
- **Layer C** (full evidence detail) is collapsed by default, behind a "Show evidence detail" affordance — except for an **active gate (state C)**, which is never collapsed, per the table above.
- Maximum visible items before "Show more": **3** per list (supporting/opposing/warnings) — matching the density level of the existing `BullBearCase` chip layout, which the design should visually echo rather than diverge from.
- An active gate or any `material_warnings` entry always overrides collapse state — these render expanded regardless of the user's last toggle interaction.

## Frontend API Contract Readiness

| Backend field | Future frontend type | Required/optional | Default handling | User-visible? | Display location | Notes |
|---|---|---|---|---|---|---|
| `contract_version` | `number` | optional | ignore if absent/unknown version | No | — | Audit-only |
| `snapshot_id` | `string` | optional | ignore if absent | No | — | Audit-only |
| `computed_at` | `string` (ISO) | optional | hide timestamp if absent | Yes | Live/snapshot label (state I) | Format client-side, don't trust a pre-formatted string |
| `is_snapshot` | `boolean` | optional, default `false` if absent | treat missing as live | Yes | Drives the "Live"/"Snapshot" label | |
| `thesis_state` | `"supported" \| "mixed" \| "conflicted" \| "insufficient_evidence"` (string union) | optional | if absent/unrecognized, fall back to "Limited evidence available" headline | Yes (via headline mapping only) | Layer A | Never displayed as raw text |
| `engine_agreement` | `string` | optional | omit row if absent | Yes | Layer C, near top | Free text, already human-readable |
| `conflicts` | array of `{conflict_id, headline, narrative, supporting_engines[], opposing_engines[]}` | optional | empty list renders nothing | Yes | Layer C | `conflict_id` not user-visible, used only as a React key |
| `coverage_notices` | `string[]` | optional | empty list renders nothing | Yes | Layer C, "Coverage" sub-section, de-duplicated per market client-side | |
| `supporting_evidence` | `string[]` | optional | empty list renders nothing | Yes | Layer C | |
| `opposing_evidence` | `string[]` | optional | empty list renders nothing | Yes | Layer C | |
| `active_gates` | `string[]` | optional | empty list → state A/B/D applies instead | Yes | Layer A override + always-expanded Layer C row | Highest display priority |
| `unresolved_risk_flags` | `string[]` | optional | empty list renders nothing | Yes | Layer C, visually distinct from `active_gates` | |
| `material_warnings` | `string[]` | optional | empty list renders nothing | Yes | Layer C, always expanded | |
| `evidence_completeness_pct` | `number \| null` | optional | hide if null/absent | Display-logic only (drives "Limited evidence" headline threshold), not shown as a raw percentage to avoid reading as a confidence score | — | Used for headline selection, not rendered as a number — showing a percentage here risks being mistaken for the AI Signal's own confidence percentage |
| `explanation_confidence_category` | `"high" \| "moderate" \| "low"` | optional | omit if absent | Yes | Layer C footer | A category label, never a number, per the same "no second confidence percentage" rule |
| `narrative` | `string` | required for the section to render meaningfully | if absent, treat as state H (don't render the section) | Yes | Layer B | Rendered verbatim |
| `engine_versions_used` | `dict[str, str\|None]` | optional | ignore | No | — | Audit-only |

**Display-logic-only fields** (drive behavior, never rendered as raw values): `thesis_state`, `evidence_completeness_pct`. **Always-hidden fields**: `contract_version`, `snapshot_id`, `engine_versions_used`. **Backward-compatibility rule**: the future frontend type must mark every field optional and the parsing logic must ignore any unrecognized future field by default (an additive contract, mirroring the backend's own additive-only design discipline) — a new backend field appearing later must never break rendering, only be invisible until a future frontend update adds support for it. The frontend must never recreate `thesis_state`/`engine_agreement`/conflict detection from `business_quality`/`financial_strength`/etc. raw fields already present elsewhere in the `Prediction` object — those exist for the existing AI Signal/Fundamentals tabs, and reusing them for an independent RCI-style synthesis would defeat the entire "single source of truth in the backend" principle this contract exists to enforce.

## Accessibility and Trust Requirements

- Severity must never rely on color alone: every state (gate/flag/warning/coverage/disabled) pairs a distinct icon with a text label (e.g. a lock icon for gates, a flag icon for unresolved risk, an info icon for coverage/feature-disabled).
- Contrast must meet existing app conventions (the codebase already uses `bull`/`bear`/`yellow-500` tokens with sufficient contrast in the Market Regime banner — new components should reuse these tokens, not introduce new ad hoc colors).
- The collapsible Layer C panel (a new primitive, per the inventory finding) must be keyboard-operable (Enter/Space to toggle, visible focus ring) and use `aria-expanded`/`aria-controls` — since no existing collapsible component exists in this codebase to inherit these from, this is a concrete new-build requirement, not an assumption that "the existing pattern already handles it."
- Screen readers: the headline must be announced as a normal heading-level element (e.g. `<h3>`), not a styled `<div>`, so it is reachable via heading navigation.
- Language must stay plain-English: avoid raw `HardGateType`/`EvidenceStatus` enum tokens or `EngineEvidence` field names anywhere in user-visible text — only the dataclass's own pre-written `positive_evidence`/`negative_evidence`/`warnings` strings (already human-readable per Sprint #002's contract design) should appear.
- No personalized-financial-advice phrasing: copy should describe what evidence exists ("Financial Strength data shows an enforced liquidity-distress gate"), never instruct the user ("you should sell").
- Avoid heavy red/green dependence: state C (active gate) should use a distinct visual treatment from a simple "bad/negative" red (e.g., a bordered alert box rather than red text), since it represents a structural fact, not a sentiment judgment.

## Consumer Design Test Plan (scenarios, not implemented tests)

| # | Scenario | Must show | Must NOT show | Misunderstanding risk | UI safeguard | Future automated test |
|---|---|---|---|---|---|---|
| 1 | Broadly aligned evidence | Headline A, narrative, supporting list | Certainty language | User over-trusts | Calm, non-celebratory styling | Snapshot test: state-A headline mapping |
| 2 | Mixed Growth-vs-Valuation, Valuation enabled | Headline B, equal-weight support/oppose | One-sided emphasis | User picks a side prematurely | Equal-size columns | Snapshot test: equal DOM-order rendering |
| 3 | Active Financial Strength gate | Headline C, gate text, always-expanded | "RCI decided" phrasing | User blames RCI for the gate | Copy reviewed for attribution language | Copy-content unit test (string contains engine name, not "RCI") |
| 4 | Business Quality unresolved fraud flag | Distinct amber flag row | Same styling as an active gate | User assumes the stock is already excluded | Separate icon/color token from gates | Visual-regression test distinguishing gate vs. flag styling |
| 5 | India Financial Strength coverage notice | One de-duplicated "Coverage" line | Per-company weakness framing | User thinks this company specifically lacks something | Market-wide copy template | De-duplication unit test across repeated calls in the same market |
| 6 | Bank/NBFC valuation non-applicability | A `not_applicable`-sourced note, neutral tone | Negative framing | User reads non-applicability as failure | Neutral tone, no warning color | Status-mapping unit test (`not_applicable` never maps to a warning style) |
| 7 | Company-specific missing evidence | Normal opposing/warning entry | Confusion with state E | User can't tell market-wide vs. company-specific | Field-source-based styling, not text-based guessing | Field-source unit test |
| 8 | Valuation feature-disabled | Neutral Layer-C note | Warning color, negative framing | User reads "disabled" as bad news | `NEVER_NEGATIVE_STATUSES`-aligned neutral styling | Status-mapping unit test |
| 9 | RCI unavailable / API failure | Nothing (section absent) | Error banner | User thinks the page is broken | Render-nothing-on-absence logic | Test: page renders unaffected when `recommendation_consolidation` key is absent |
| 10 | Mobile screen | Collapsed Layer C, full Layer A/B | Horizontal overflow | Content cut off | Reuse existing responsive Tailwind conventions | Mobile-viewport snapshot test |
| 11 | Long evidence lists | First 3 items + "Show more" | An unbounded, scroll-heavy list by default | Overwhelm | Item cap matching `BullBearCase`'s density | Item-cap unit test |
| 12 | Live vs. future Daily Picks snapshot (conceptual only) | "Live · computed [time]" label | A label implying it's a stored historical record | User can't tell live from cached/stale | Persistent live/snapshot label slot | Documented compatibility requirement only — no test this sprint |

## Implementation Prerequisites

1. Widen the `Prediction` TypeScript interface (`frontend/src/utils/api.ts:54-68`) to add an optional `recommendation_consolidation` field with the full shape in the contract-map table above.
2. Build a new collapsible/expandable panel primitive — confirmed not to exist anywhere in `frontend/src` today.
3. Define and review exact copy strings for every state in §5, with explicit attention to the "never blame RCI for an existing gate" requirement (scenario 3).
4. Implement client-side de-duplication logic for repeated `coverage_notices` strings within the same market (scenario 5) — a small, frontend-only logic addition, not a backend change.
5. Confirm visual-token reuse plan with existing `bull`/`bear`/severity color tokens before any component is built, to avoid introducing a parallel, inconsistent color system.

## Non-Goals (this sprint and the section it designs)

- No second Buy/Sell/Hold/Strong Buy/Avoid label, ever.
- No second confidence percentage or numeric score of any kind.
- No hidden weighting system.
- No recomputation of RCI logic from raw engine fields in the frontend.
- No Daily Picks UI work.
- No LLM-generated explanatory text — all copy is either backend-sourced verbatim (`narrative`) or a small, pre-approved set of static frontend strings (headlines, labels).
- No new data provider or backend computation.

## Recommendation for Epic 005 Sprint #011

**B — resolve one named frontend/API-contract prerequisite before implementation**, specifically: finalize and review the exact Layer A headline copy and Layer C state-by-state wording (item 3 above) as a short, focused copy-and-contract-typing sprint, before any React component is written. This is narrower than a full implementation sprint (Final Recommendation option A) because the highest-risk part of this entire feature — wording that could make RCI look like a second recommendation or misattribute an existing gate's severity — is a content/wording risk, not a rendering risk, and is cheapest to get right before component code exists to constrain it. Once that copy is finalized and the TypeScript interface is widened (prerequisite #1), a narrowly-scoped frontend implementation sprint (option A) can follow directly, still keeping `RCI_LIVE_STOCK_ANALYSIS_ENABLED` disabled in Railway until the implemented UI has been reviewed end-to-end against this design.

- **RCI must remain disabled in Railway** through Sprint #011 and through frontend implementation — it should only be enabled after the UI is built, reviewed, and (per Sprint #009) ideally validated via a controlled, no-frontend API check first (Sprint #009's Path C, still not exercised).
- **Valuation Intelligence activation remains a separate, parallel workstream** — per Sprint #009's own conclusion, unchanged here. This sprint's design explicitly accommodates Valuation's current feature-disabled state (§5.G) so frontend implementation does not need to wait for that activation decision.
- **Mandatory user-facing safeguards before RCI becomes visible**: the never-collapsed treatment for active gates (state C); the distinct, non-red treatment for feature-disabled/not-applicable states; the de-duplicated coverage-notice rendering (scenario 5); and a verified render-nothing fallback on absence (state H) confirmed by a real test, not just code inspection.

---

*No production code, frontend code, API behavior, Railway configuration, signal, confidence, score, gate, or Daily Picks behavior was modified by this sprint. No mockup, screenshot, or visual asset was created — all design decisions above are documented in prose and tables only.*
