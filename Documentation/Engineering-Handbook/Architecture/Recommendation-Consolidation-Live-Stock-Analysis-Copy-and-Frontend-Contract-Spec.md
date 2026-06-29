# Recommendation Consolidation Intelligence — Copy, Semantic-State & Frontend Contract Specification (Epic 005, Sprint #011)

**Status:** Copy/contract specification only. No backend code, frontend code, TypeScript interfaces, React components, API payloads, Railway configuration, or visual assets were created or modified.

## Evidence Checkpoint

Reviewed directly: Sprint #001–#010 reports, SSDS-009, the Evidence Contract, Traceability/Versioning doc, the Integration Readiness doc, the Operational Activation decision, the UI Consumer Design doc, `recommendation_consolidation_contract.py`, `recommendation_consolidation_engine.py`, `recommendation_consolidation_api_composer.py`, and `frontend/src/utils/api.ts`.

Non-negotiable facts re-confirmed, unchanged since Sprint #010:

| Fact | Status |
|---|---|
| Prediction Engine remains sole source of truth for signal/confidence/score/gates | **Confirmed** — no commit since `3a2f674` touches `prediction_engine.py` |
| RCI remains additive, read-only, deterministic, non-authoritative | **Confirmed** |
| RCI never produces a replacement signal/confidence/master score/recommendation | **Confirmed by contract inspection** — no such field exists in `RecommendationConsolidationResponse` |
| Frontend must render backend-provided meaning only, never derive `thesis_state` from raw engine scores | **Design constraint, carried forward** |
| RCI disabled in Railway | **Confirmed unchanged** |
| Daily Picks untouched | **Confirmed** — no `daily_picks.py` reference in scope |
| Valuation Intelligence separately governed | **Confirmed** |
| Active gates / unresolved flags / warnings / coverage notices / feature-disabled / evidence gaps remain semantically distinct | **Confirmed at the contract level** — each is its own dataclass field |

**No contradiction found.**

### Actual serialized RCI response shape — inspected directly, not from documentation alone

To avoid relying only on the contract's docstrings, the real pipeline (`build_recommendation_evidence_snapshot` → `compute_recommendation_consolidation` → `dataclasses.asdict`) was executed directly against synthetic engine inputs and the actual JSON-shaped output inspected:

```json
{
  "contract_version": 1,
  "snapshot_id": "f7ce8c8e-...",
  "computed_at": "2026-06-29T14:12:54.584806+00:00",
  "is_snapshot": false,
  "thesis_state": "insufficient_evidence",
  "engine_agreement": "No applicable engine evidence available",
  "conflicts": [
    {
      "conflict_id": "CP-07-missing-engine",
      "headline": "...",
      "narrative": "...",
      "supporting_engines": [],
      "opposing_engines": [],
      "severity": "informational"
    }
  ],
  "coverage_notices": ["Financial Strength analysis is not currently available for this market ..."],
  "supporting_evidence": [],
  "opposing_evidence": [],
  "active_gates": [],
  "unresolved_risk_flags": [],
  "material_warnings": [],
  "evidence_completeness_pct": null,
  "explanation_confidence_category": "low",
  "narrative": "...",
  "engine_versions_used": {"business_quality": "v1", "financial_strength": null, "growth_intelligence": null, "valuation_intelligence": null}
}
```

**Direct findings from this real execution, not assumed from documentation:**

1. **`conflicts` items DO carry a stable identifier**: `conflict_id` is a fixed string drawn from a small, hardcoded set (`"CP-02-cheap-but-avoid-growth"`, `"CP-01-quality-vs-strength"`, `"CP-03-growth-priced-in"`, `"CP-07-missing-engine"`, `"CP-08-low-completeness-favorable"` — confirmed by direct grep of `recommendation_consolidation_engine.py`). This identifier is safe for frontend keying and de-duplication logic.
2. **`coverage_notices`, `unresolved_risk_flags`, `material_warnings`, `supporting_evidence`, and `opposing_evidence` are ALL plain `tuple[str, ...]` — no identifier, reason code, or structured object of any kind accompanies any entry in these five fields.** This is confirmed directly from the dataclass field types (`recommendation_consolidation_contract.py`'s `RecommendationConsolidationResponse`) and from the executed output above (`coverage_notices` is a bare list of one full sentence, nothing else).
3. **`evidence_completeness_pct` is genuinely nullable** (confirmed `null` in the executed output, not merely typed `float | None` on paper) — the frontend must handle an absent percentage, not just a low one.
4. **Every field listed in the contract was present in the executed output, including empty-tuple fields (`active_gates: []`, etc.)** — meaning when RCI succeeds, all 16 fields are always present (none are conditionally omitted by the engine itself), even if several are empty. The only top-level absence case is the composer's own failure path (Sprint #008), which omits the *entire* `recommendation_consolidation` key, never a partial object.
5. **`reason_code` exists internally** (on the pre-serialization `EngineEvidence` object, e.g. `"not_applicable_for_market"`, confirmed via direct grep of `_coverage_notices()`'s own discriminator logic) but is **NOT serialized into the public `RecommendationConsolidationResponse`** — it is consumed internally to *decide* whether something becomes a coverage notice vs. a conflict, then discarded. The public contract exposes only the resulting free-text string, not the reason code that produced it.

### Discrepancy classification

This is a real, material discrepancy between Sprint #010's design assumptions and the actual, executed contract:

> **Sprint #010's §6 (Coverage Notice and De-Duplication Specification) assumed a "reason code" might exist on coverage notices that the frontend could use for de-duplication.** Direct execution confirms this is **false** — no reason code, ID, or structured field of any kind survives serialization for `coverage_notices`, `unresolved_risk_flags`, or `material_warnings`. They are bare strings.

**Classification: a future backend-contract prerequisite, not a documentation issue and not something to standardize away in frontend design.** The reason code exists internally and is already computed — exposing it costs no new computation, only a contract addition (see §6 below for the specific, minimal recommendation). This is named openly here rather than silently designing a fragile text-matching workaround.

## Primary Objective — Restated

This document specifies exactly what users should see, what they must never infer, and how the frontend can render every RCI state safely, without recreating backend reasoning — resolving wording before any UI code exists.

## 3. Frontend Contract Typing Specification (documentation/pseudocode only — no TypeScript written)

### A. Top-Level `recommendation_consolidation` Field

```text
recommendation_consolidation?: RciResponse   // always optional at the top level
```

| Top-level state | Possible? | Frontend rule |
|---|---|---|
| 1. Absent (key missing entirely) | **Yes — the only real-world absence shape**, confirmed by the composer's own code: flag disabled → key never added; composer failure → key never added; older API version → key never existed | Render nothing. The Evidence Summary section does not appear. No error, no placeholder. |
| 2. `null` | **Not possible** per current backend code — the composer either omits the key entirely or includes a fully-populated object; it never assigns `null` | Treat defensively as equivalent to "absent" anyway (cheap, future-proof safety net) — but do not design around this as an expected case |
| 3. Malformed (wrong types on a field) | Theoretically possible only via a future backend regression, not today's code | If `narrative` is missing or not a string, treat as case H (render nothing) — `narrative` is the one field this spec treats as load-bearing for "is this object usable at all" |
| 4. Incomplete (some fields missing, others present) | **Not possible today** — direct execution (§ above) confirms all 16 fields are always present, even as empty tuples, whenever the engine succeeds | Defensive frontend code should still default every list field to `[]` and every optional scalar to `undefined` rather than assuming presence, since "not possible today" is not "structurally guaranteed forever" |
| 5. Version-unsupported (`contract_version` higher than the frontend understands) | Possible in the future once the contract version increments | See §C below |
| 6. Valid | The normal case | Render per this spec |

**The default for every non-(6) case is graceful non-rendering — never an error banner, never a broken-looking page.**

### B. Inner Object Contract

| Backend field | Future frontend type | Required when RCI exists? | Default if absent | User-visible? | UI purpose | Must never be inferred from another field? |
|---|---|---|---|---|---|---|
| `contract_version` | `number` | Always present (confirmed) | — | No | Version-gate check only | Must never be inferred — read directly |
| `snapshot_id` | `string` | Always present | — | No | Audit/debug only | — |
| `computed_at` | `string` (ISO 8601) | Always present | — | Yes | Freshness/"Live" label | — |
| `is_snapshot` | `boolean` | Always present | `false` | Yes | Live vs. snapshot label | Must never be inferred from `computed_at`'s recency — read the explicit boolean |
| `thesis_state` | `"supported" \| "mixed" \| "conflicted" \| "insufficient_evidence"` | Always present | n/a (always present) | Indirectly, via headline mapping only | Drives Layer A headline | Must never be derived from counting `supporting_evidence.length` vs `opposing_evidence.length` client-side — always read the backend's own classification |
| `engine_agreement` | `string` | Always present | — | Yes | Layer C subtitle | — |
| `supporting_evidence` | `string[]` | Always present (may be `[]`) | `[]` | Yes | Layer C list | — |
| `opposing_evidence` | `string[]` | Always present (may be `[]`) | `[]` | Yes | Layer C list | — |
| `active_gates` | `string[]` | Always present (may be `[]`) | `[]` | Yes | Layer A override + always-expanded Layer C row | Must never be inferred from `opposing_evidence` text content — only this field counts as an enforced gate |
| `unresolved_risk_flags` | `string[]` | Always present (may be `[]`) | `[]` | Yes | Layer C, visually distinct from `active_gates` | Must never be promoted to `active_gates` by frontend heuristic |
| `coverage_notices` | `string[]` | Always present (may be `[]`) | `[]` | Yes | Layer C "Coverage" sub-section | Must never be merged into `opposing_evidence` |
| `material_warnings` | `string[]` | Always present (may be `[]`) | `[]` | Yes | Layer C, always expanded | — |
| `conflicts` | `{conflict_id, headline, narrative, supporting_engines[], opposing_engines[], severity}[]` | Always present (may be `[]`) | `[]` | Yes | Layer C | `conflict_id` is the one safe stable key — see §6 |
| `evidence_completeness_pct` | `number \| null` | Always present as a key, but **value is genuinely nullable** (confirmed) | Treat `null` as "unknown," not zero | No (display-logic only) | Drives "Limited evidence" headline threshold | Must never be shown as a raw percentage — risks reading as a second confidence score |
| `explanation_confidence_category` | `"high" \| "moderate" \| "low"` | Always present | — | Yes | Layer C footer, as a category label only | Must never be rendered as, or near, a percentage |
| `narrative` | `string` | Always present when RCI exists; treated as the field whose absence triggers non-rendering | n/a | Yes | Layer B | Rendered verbatim, never reworded |
| `engine_versions_used` | `Record<string, string \| null>` | Always present | `{}` | No | Audit/debug only | — |

**Internal-only field confirmed NOT in the public contract**: `reason_code` (exists on `EngineEvidence`, never serialized into `RecommendationConsolidationResponse`) — listed here for completeness per the discrepancy finding above, not because the frontend will ever receive it.

### C. Version Compatibility

- **Supported initial contract version: `1`** (matches `CONTRACT_VERSION = 1` in the backend module today).
- **Unknown future fields**: ignored by default — the frontend's parsing must not fail or warn on an additional, unrecognized key. This requires the future TypeScript type to be deliberately non-exhaustive (e.g., not using a runtime schema validator in "reject unknown keys" mode).
- **Unknown future enum values** (e.g., a future fifth `thesis_state` value, or a new `severity` level on `conflicts`): the frontend must fall back to the most conservative, lowest-certainty visual treatment it already knows about — for `thesis_state`, fall back to the "Limited evidence available" headline (§4) rather than crashing or guessing a mapping.
- **Malformed fields** (wrong type on a known field): treat the entire RCI object as unusable and render nothing (case H) — a malformed object is a signal something is wrong upstream; partial rendering of a malformed object risks displaying nonsense, which is worse than omission.
- **Unsupported contract version** (`contract_version` greater than what this frontend build understands): **suppress the full panel**, not a minimal neutral fallback — per the "never look like a second recommendation, never look broken" rule, a version the frontend doesn't understand should be treated identically to absence (case 1), since a partial render of an unknown future shape risks misrepresenting it. (A minimal fallback — e.g., "Additional evidence summary available in a future update" — is a defensible alternative if product wants to signal "more exists," but is **not** the default recommended here, since it is not required and adds a new untested message; the simpler suppress-entirely rule is recommended as the safer default.)

## 4. Semantic-State Precedence Model

| State | Backend source | Relative priority | Default visibility | Future label | Required wording rule | Must not imply |
|---|---|---:|---|---|---|---|
| 1. Active enforced gate | `active_gates` non-empty | **1 (highest)** | Always visible, never collapsed | "Existing gate blocks the thesis" | Name the *engine* as the source ("Financial Strength's existing gate is active"), never "RCI blocks..." | That RCI created or decided the gate |
| 2. Unresolved risk flag | `unresolved_risk_flags` non-empty | 2 | Visible by default, collapsible | "Unresolved risk flag present" | "noted, not currently enforced as an exclusion" | That the stock is currently excluded |
| 3. Material warning | `material_warnings` non-empty | 3 | Visible by default, collapsible | "Important caution noted" | State the specific warning text verbatim | A buy/sell instruction |
| 4. Mixed evidence / conflict | `thesis_state in {"mixed","conflicted"}` or `conflicts` non-empty | 4 | Visible by default (headline + brief), detail collapsible | "Evidence is mixed" | Present both sides with equal weight | That one side should be trusted over the other |
| 5. Broadly aligned evidence | `thesis_state == "supported"`, no gates/flags/warnings | 5 (lowest cautionary, i.e. default calm state) | Visible by default | "Evidence broadly aligned" | Avoid certainty language | Guarantee or high conviction |
| 6. Company-specific evidence gap | An engine's evidence missing for this symbol specifically (the `CP-07`-style conflict, or sparse `supporting_evidence`/`opposing_evidence`) | Coexists with 1-5; lowers `evidence_completeness_pct` | Visible inside Layer C | "Evidence unavailable for this company" | Frame as a data gap, not as a finding | That missing data is itself a negative finding |
| 7. Structural market coverage notice | `coverage_notices` non-empty | Coexists with 1-6; lowest visual weight | Collapsed under "Coverage" sub-heading by default | "Market-wide coverage limitation" | Always phrase as platform-wide, never per-company | That this specific company has a weakness |
| 8. Feature-disabled engine state | An engine evidence status of `feature_disabled` (e.g. Valuation Intelligence while its kill switch is off) | Coexists with 1-7; neutral, lowest urgency | Visible inside Layer C, neutral styling | "Valuation evidence not currently included" | Strictly neutral tone | That disabled = negative finding |
| 9. RCI unavailable / omitted via error isolation | Top-level key absent | Overrides all of the above — if the key is absent, none of states 1-8 can render at all | The entire section is absent | (none — no label is shown) | n/a | That the Stock Analysis page or prediction failed |

**Coexistence rule**: states 2-8 can all coexist simultaneously for one stock (e.g., an India bank could have a `coverage_notice` for Financial Strength, a `feature_disabled` note for Valuation, and a `material_warning` all at once) — the frontend renders all applicable states, ordered by the priority column, never picking only one. State 1 (active gate), when present, is additionally promoted to override the Layer A headline regardless of how many other states are also present. State 9 is the only state that suppresses everything else, since nothing else can render without the top-level object.

**Mandatory rules, reconfirmed**: an active enforced gate always outranks normal summary content (rule satisfied by priority 1 always controlling the headline); an unresolved risk flag never looks like an active exclusion (distinct label/icon, per §7); a structural coverage notice never looks like a company weakness (always market-wide phrasing, §5); a company-specific gap may lower `evidence_completeness_pct` but is never listed inside `opposing_evidence`-styled content (confirmed structurally — it surfaces via `conflicts`'/`CP-07`'s own `informational` severity, never via the opposing-evidence list); a feature-disabled engine is always neutral-toned; RCI omission via error isolation never looks like a product error (state 9, render nothing); the frontend never translates any state into Buy/Sell/Hold/Strong Buy/Avoid/confidence language anywhere in this model.

## 5. Copy Catalogue

Each case below specifies: Section label · Headline · Short explanation · Expanded explanation · Optional coverage/context note · Accessibility label · Forbidden wording examples.

### 1. Broadly aligned available evidence
- **Section label:** Evidence Summary
- **Headline:** "Evidence broadly aligned"
- **Short explanation:** "The available engine evidence supports this view, with no active gates or unresolved flags."
- **Expanded explanation:** Backend `narrative` rendered verbatim.
- **Context note:** None required.
- **Accessibility label:** `aria-label="Evidence summary: evidence broadly aligned, no active concerns"`
- **Forbidden wording:** "AI thinks this is a strong opportunity," "high confidence," "safe investment."
- **Why this avoids overstating certainty:** "Broadly aligned" describes the *current* evidence set, not a prediction of outcome; it omits any probability or certainty word entirely.

### 2. Mixed evidence
- **Section label:** Evidence Summary
- **Headline:** "Evidence is mixed"
- **Short explanation:** "Some evidence supports this view; some evidence challenges it."
- **Expanded explanation:** Backend `narrative`, plus equally-sized supporting/opposing lists.
- **Context note:** None required.
- **Accessibility label:** `aria-label="Evidence summary: mixed evidence, both supporting and challenging factors present"`
- **Forbidden wording:** "Leaning bullish," "leaning bearish," "net positive."
- **Why:** Presents both sides without resolving them into a single directional lean, which would functionally recreate a signal.

### 3. Active existing gate
- **Section label:** Evidence Summary — Active Gate
- **Headline:** "Existing gate blocks the thesis"
- **Short explanation:** "[Engine name]'s existing gate is currently active for this stock."
- **Expanded explanation:** "[Engine name] enforces a gate when [plain-English condition, e.g. 'a company shows signs of severe liquidity distress']. This gate is active for this stock today, independent of this evidence summary."
- **Context note:** "This gate already exists in [Engine name]'s own analysis — Evidence Summary is reporting it, not creating it."
- **Accessibility label:** `aria-label="Active gate: [Engine name]'s existing gate is currently blocking this thesis"`
- **Forbidden wording:** "RCI blocks," "Evidence Summary recommends avoiding," "this stock is unsafe."
- **Why:** Attribution to the originating engine is explicit and repeated, preventing the gate from being misread as something Evidence Summary itself decided.

### 4. Unresolved risk flag
- **Section label:** Evidence Summary — Noted, Not Enforced
- **Headline:** "Unresolved risk flag present"
- **Short explanation:** "[Engine name] has identified a risk factor that is not currently enforced as an exclusion."
- **Expanded explanation:** "[Specific flag text]. This is noted for awareness; it does not currently exclude this stock from any signal."
- **Context note:** None additional.
- **Accessibility label:** `aria-label="Unresolved risk flag, not currently enforced: [flag text]"`
- **Forbidden wording:** "Excluded," "blocked," "rejected," "this stock should be avoided."
- **Why:** "Not currently enforced" is stated explicitly and is the load-bearing phrase distinguishing this from state 3.

### 5. Structural India market coverage limitation
- **Section label:** Evidence Summary — Coverage
- **Headline:** (no separate headline — appears only inside the "Coverage" sub-section)
- **Short explanation:** "[Engine name] data is not currently available for the Indian market."
- **Expanded explanation:** "This is a platform-wide coverage limitation, true for every Indian stock today — not a finding specific to this company."
- **Context note:** Shown once per market per page load, de-duplicated (see §6).
- **Accessibility label:** `aria-label="Platform coverage note: [Engine name] data unavailable market-wide, not specific to this company"`
- **Forbidden wording:** "This company lacks," "missing data is concerning here."
- **Why:** "Platform-wide... not a finding specific to this company" is stated explicitly to forestall the exact misreading named in the brief.

### 6. Company-specific evidence gap
- **Section label:** Evidence Summary — Limited Evidence
- **Headline:** (folds into "Limited evidence available" headline if it dominates; otherwise appears inside Layer C)
- **Short explanation:** "Evidence for [Engine name] was not available for this specific company."
- **Expanded explanation:** "Unlike a market-wide limitation, this is specific to this stock — it lowers how complete the available evidence is, but is not treated as a negative finding on its own."
- **Context note:** None additional.
- **Accessibility label:** `aria-label="Company-specific evidence gap for [Engine name]"`
- **Forbidden wording:** "This is a red flag," "this hurts the case."
- **Why:** Explicitly distinguishes itself from state 5's market-wide framing and explicitly disclaims being negative evidence.

### 7. Bank / NBFC valuation non-applicability
- **Section label:** Evidence Summary — Not Applicable
- **Headline:** (folds into normal headline; appears as a coverage-style note)
- **Short explanation:** "Valuation Intelligence's standard metrics are not applicable to banks and NBFCs."
- **Expanded explanation:** "Banks and NBFCs are evaluated differently from non-financial companies; standard valuation ratios are not meaningful for this stock type, so this evidence category is intentionally excluded here."
- **Context note:** None additional.
- **Accessibility label:** `aria-label="Valuation evidence not applicable: bank or NBFC sector exclusion"`
- **Forbidden wording:** "Valuation looks bad," "valuation is unknown" (it is not unknown — it is structurally not applicable, a distinct status from `unavailable`).
- **Why:** Uses "not applicable" precisely, mirroring the backend's own `NOT_APPLICABLE` status, never conflating it with a data gap.

### 8. Valuation Intelligence feature-disabled state
- **Section label:** Evidence Summary — Feature Not Active
- **Headline:** (neutral note inside Layer C, never affects the main headline)
- **Short explanation:** "Valuation evidence is not currently included in this analysis."
- **Expanded explanation:** "StockSense360 has not yet enabled Valuation Intelligence's confidence signal in production. This is a platform configuration state, not a finding about this stock."
- **Context note:** None additional.
- **Accessibility label:** `aria-label="Valuation evidence currently disabled at the platform level, not company-specific"`
- **Forbidden wording:** "Valuation is weak," "valuation is missing" (implies absence rather than intentional non-inclusion).
- **Why:** "Not yet enabled... platform configuration state" forecloses any negative reading.

### 9. RCI omitted because backend error isolation occurred
- **Section label:** (none — section does not render)
- **Headline:** (none)
- **Short explanation:** (none — no user-visible text at all)
- **Expanded explanation:** (none)
- **Context note:** (none)
- **Accessibility label:** (n/a — nothing renders, so nothing needs a label)
- **Forbidden wording:** Any error message, broken-icon, or "evidence summary failed" text.
- **Why:** Absence is the entire design — silence is the only safe behavior, matching the backend's own Option A failure-isolation choice (Sprint #008).

### 10. Multiple supporting and opposing evidence items
- **Section label:** Evidence Summary
- **Headline:** Driven by `thesis_state` per §4 (typically "mixed")
- **Short explanation:** "Multiple factors support this view; multiple factors challenge it." (only used when both lists have 2+ items)
- **Expanded explanation:** Full lists, capped at 3 visible by default each (§8), "Show more" beyond that.
- **Context note:** None additional.
- **Accessibility label:** `aria-label="Evidence summary: [N] supporting factors, [M] challenging factors"`
- **Forbidden wording:** "Outweighs," "net," "on balance" — any phrase implying the frontend or backend has resolved the two lists into one direction.
- **Why:** Numeric counts are descriptive (how many items), never evaluative (which side wins).

### 11. No meaningful conflict but incomplete coverage
- **Section label:** Evidence Summary — Limited Evidence
- **Headline:** "Limited evidence available"
- **Short explanation:** "Available evidence does not show a conflict, but full coverage was not possible for this stock."
- **Expanded explanation:** Lists which engines were/weren't available, framed per state 6/7 rules depending on cause.
- **Context note:** None additional.
- **Accessibility label:** `aria-label="Limited evidence available, no conflict detected in what is available"`
- **Forbidden wording:** "Likely positive," "probably fine."
- **Why:** Explicitly separates "no conflict found" from "fully validated" — the absence of a detected conflict in incomplete evidence is not itself reassuring, and the copy does not claim it is.

### 12. Live analysis timestamp / freshness wording
- **Section label:** (small persistent label, top of Evidence Summary)
- **Headline:** "Live · computed [relative time, e.g. 'just now' / '4 minutes ago']"
- **Short explanation:** n/a (this is a metadata label, not a state)
- **Expanded explanation:** n/a
- **Context note:** A future Daily Picks snapshot view would use "Snapshot · as of [date]" in the same visual slot (compatibility requirement only, not implemented).
- **Accessibility label:** `aria-label="Live evidence, computed [exact ISO timestamp]"`
- **Forbidden wording:** "Up to date," "current as of now" (vague compared to an explicit timestamp).
- **Why:** An explicit, derived-from-`computed_at` relative time is verifiable and falsifiable; vague freshness language is not.

## 6. Coverage Notice and De-Duplication Specification

Direct execution (Evidence Checkpoint, above) confirms `coverage_notices` is a bare `tuple[str, ...]` with **no stable identifier, reason code, or structured field**. This changes Sprint #010's assumption.

**Frontend rules, given this confirmed shape:**

- A market-wide coverage notice (state 7) and a company-specific evidence gap (state 6) are **never the same field** — they arrive via different contract fields (`coverage_notices` vs. `conflicts`/lowered `evidence_completeness_pct`), so no de-duplication logic is needed to distinguish them; the distinction is already structural.
- **De-duplication is needed only within `coverage_notices` itself**, across multiple stocks the user views in a session (e.g., navigating between several India bank stocks, each independently reporting "Financial Strength is not available for the Indian market").
- **Per this sprint's explicit instruction, narrative-text matching is not an acceptable de-duplication strategy** — even though today's `coverage_notices` strings are deterministic per market (confirmed by Sprint #010's own reading of `_coverage_notices()`'s output), relying on exact-string matching is fragile: a future wording change to the backend's notice text would silently break frontend de-duplication with no compile-time signal.
- A duplicate notice appearing in multiple lists is not currently possible (confirmed: a given engine's `NOT_APPLICABLE` status surfaces in exactly one place, `coverage_notices`, never simultaneously in `conflicts` or elsewhere, per the discriminator logic in `_coverage_notices()`).
- Multiple notices sharing the same underlying cause (e.g., two different engines both citing market-wide non-coverage) are NOT currently distinguishable from each other by any field other than their full text — meaning per-notice de-duplication keyed correctly would require knowing which notices came from the same engine, which the current contract does not expose either.
- **A future Stock Analysis refresh (re-fetching `/predict`) may return a `coverage_notices` list with different string ordering or phrasing across requests** (not confirmed false by today's code — `_coverage_notices()`'s iteration order depends on `by_name`'s dict ordering, which is not guaranteed stable across different code paths) — meaning even a same-session, same-stock re-fetch should not assume identical strings.

**Conclusion — hard frontend implementation prerequisite, not a workaround:**

> **No safe, stable, non-text-based way exists today to de-duplicate `coverage_notices` (or `unresolved_risk_flags`/`material_warnings`) across multiple stocks or multiple fetches.** The frontend must not improvise text-matching. This is the single largest backend-contract gap this sprint identifies.

**Smallest recommended backend contract addition** (not implemented this sprint, a recommendation only): add a `reason_code: str | None` field to each `coverage_notices` entry — i.e., change the field's shape from `tuple[str, ...]` to `tuple[{text: str, reason_code: str | None}, ...]` (or a small dataclass) — exposing the already-computed-but-discarded `reason_code` (confirmed to already exist internally on `EngineEvidence`, per the Evidence Checkpoint) at zero new computation cost, only a serialization change. The same minimal addition (a stable identifier per item) is recommended for `unresolved_risk_flags` and `material_warnings` for the same reason, since both share the identical "bare string, no key" shape today. Until this addition exists, the frontend should de-duplicate **only within a single rendered response** (i.e., `Array.from(new Set(coverage_notices))` on the exact current array, a safe and limited use of exact-match dedup that does not span fetches or stocks), and should not attempt any cross-stock or cross-session de-duplication.

## 7. Color, Icon, and Accessibility Semantics

| State | Color token | Icon + text | Color-independent meaning | Contrast | Screen-reader wording | Keyboard behavior | Default focus | Mobile |
|---|---|---|---|---|---|---|---|---|
| 1. Active gate | Existing "critical"/`bear`-tier token | Lock or shield icon + "Active gate" text | Icon+label conveys meaning without color | Must meet existing app contrast minimums (reuse `bear` token, already audited) | "Active gate: [text]" | n/a (never collapsed, always reachable by normal tab order) | Receives a heading-level landmark | Full-width, never truncated |
| 2. Unresolved risk flag | Existing "caution"/amber-tier token (distinct from state 1's token) | Flag icon + "Unresolved" text | Distinct icon from state 1 | Same minimum | "Unresolved risk flag, not enforced: [text]" | Expand/collapse via Enter/Space, `aria-expanded` | Not auto-focused | Collapsible row |
| 3. Material warning | Caution token, same tier as state 2 but a distinct icon (e.g. exclamation-triangle) | Triangle icon + "Caution" text | Distinct from states 1-2 by icon shape | Same minimum | "Caution: [text]" | Same as state 2 | Not auto-focused | Always visible (per §8), not collapsed |
| 4. Mixed/conflict | Existing neutral-informational token (not red or green) | Two-tone icon or side-by-side bull/bear icons, reusing `BullBearCase.tsx`'s existing convention | Equal visual weight for both sides | Same minimum | "Mixed evidence: [N] supporting, [M] challenging" | Collapsible per-item | Not auto-focused | Two-column collapses to stacked |
| 5. Broadly aligned | Neutral-informational token, not celebratory green | Checkmark-outline icon (not a filled "success" icon, to avoid overstating) | Calm, non-celebratory | Same minimum | "Evidence broadly aligned" | n/a (no expand needed for headline) | Not auto-focused | Standard |
| 6. Company-specific gap | Neutral-informational token | Question/info icon, "Limited data" text | Distinct from states 2/3 (informational, not cautionary) | Same minimum | "Evidence gap for [Engine name], specific to this company" | Collapsible | Not auto-focused | Collapsible row |
| 7. Coverage notice | Lowest-weight neutral token (e.g. gray, no border) | Info icon, "Coverage" text, sub-heading | Must read as platform fact, never company-specific | Same minimum | "Platform coverage note: [text]" | Collapsed by default under "Coverage" | Not auto-focused | Collapsed, single line when expanded |
| 8. Feature-disabled | Same lowest-weight neutral token as state 7 | Info icon, "Not currently included" text | Must never use the caution/critical token | Same minimum | "[Engine name] evidence not currently included (platform setting)" | Collapsed by default | Not auto-focused | Collapsed |
| 9. RCI unavailable | n/a — nothing renders | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

**No state relies on red/green alone** — every row above pairs a distinct icon shape with text; color is reinforcement, not the sole carrier of meaning. **Required distinctions, confirmed satisfied**: active gate (state 1) uses the most severe token + a lock/shield icon never reused elsewhere; unresolved flag (state 2) uses a different token tier and a flag icon, never the lock/shield icon; material warning (state 3) shares state 2's token tier but a different icon (triangle), since both are "caution-adjacent" but represent different backend fields; coverage (state 7) and feature-disabled (state 8) share the lowest-weight neutral token deliberately, since both represent "not a finding, a platform fact" — they are distinguished by text content and icon ("Coverage" vs. "Not currently included"), not by color, since neither should ever look alarming.

## 8. Content Density and Expandable-Panel Rules

- **Maximum supporting items visible by default: 3.** Maximum opposing items visible by default: 3. Matches `BullBearCase.tsx`'s existing density convention (Sprint #010 finding), not a new number invented for this feature.
- **Material warnings (state 3) and active gates (state 1) always appear above the fold and are never subject to the "Show more" collapse** — they are the highest-severity content and must never require an extra click to discover.
- **Coverage notices (state 7) appear once per panel, de-duplicated only within the current response** (§6) — never repeated even if the underlying engine evidence theoretically supports it appearing twice.
- **Active gates are always expanded** — confirmed, restated from Sprint #010, non-negotiable.
- **Unresolved flags may be collapsed** — visible by default per §4's priority table, but the detailed expanded text may be behind a tap/click, unlike active gates.
- **"Show more" wording**: "Show 2 more supporting factors" / "Show 1 more challenging factor" — always states an exact count, never a vague "Show more evidence," so users know how much is hidden.
- **Mobile**: identical content, vertically stacked rather than side-by-side for the mixed-evidence two-column layout (state 4); the "Coverage" and "Not currently included" sub-sections collapse to single-line summaries with a tap target large enough for touch (reusing existing app conventions, not inventing new touch-target sizing).
- **Narrative truncation**: the Layer B `narrative` field is shown in full by default up to 2-3 sentences (the backend's own narratives are already concise, per Sprint #003-#005's deterministic-template design — confirmed not to require truncation today); if a future narrative exceeds roughly 280 characters, truncate with a "Read more" expansion rather than a hard cutoff that could change meaning mid-sentence.
- **Preventing important stock-analysis content from being pushed below the fold**: per Sprint #010's placement decision (below the AI Signal card, above the horizon tabs), Evidence Summary's own collapsed-by-default state (headline + 1-2 line excerpt only, per §4) keeps its default footprint small specifically so it does not push the horizon tabs or chart below an acceptable scroll distance — this is the reason Layer C is collapsed by default at all, restated here as a deliberate density constraint, not just an aesthetic choice.
- **Useful for both beginner and advanced users**: beginners see only the headline + short explanation (Layers A/B) without needing to expand anything; advanced users can expand Layer C for the full evidence breakdown, contract metadata remaining hidden from both audiences (confirmed never user-visible, §3B).

## 9. Design Scenario Matrix

| Scenario | Future render behavior | Required wording | Suppress/hide | User misunderstanding to prevent | Future automated test |
|---|---|---|---|---|---|
| 1. RCI absent — Railway flag disabled | Section absent | n/a | Fully hidden | "Why isn't this here?" confusion if a user expects it | Test: page renders correctly when `recommendation_consolidation` key is absent |
| 2. RCI absent — error isolation | Section absent | n/a | Fully hidden | Same as #1, but must also confirm rest of page (signal/confidence) renders unaffected | Test: malformed/missing RCI never affects other page sections |
| 3. Valid, broadly aligned | Full Layer A/B, collapsed Layer C | Case 1 copy (§5) | Layer C collapsed | Overconfidence | Snapshot test: headline mapping for `thesis_state == "supported"` with no gates/flags |
| 4. Valid, mixed | Full Layer A/B, equal-weight Layer C preview | Case 2 copy | Layer C collapsed but balanced preview shown | Picking a side prematurely | Test: supporting/opposing render in equal-size DOM blocks |
| 5. Active Financial Strength gate | Layer A overridden to state-1 headline, gate row always expanded | Case 3 copy | Never suppressed | Blaming RCI for the gate | Copy-content test: gate text contains engine name, never "RCI"/"Evidence Summary" as the actor |
| 6. Business Quality unresolved fraud flag | State-2 row, distinct styling from gates | Case 4 copy | Visible by default, detail collapsible | Assuming exclusion | Visual-regression test distinguishing gate vs. flag icon/token |
| 7. India structural Financial Strength coverage notice | State-7 row under "Coverage," de-duplicated within response | Case 5 copy | Collapsed by default | Reading as company-specific weakness | Dedup unit test (within one response only, per §6) |
| 8. BANDHANBNK-style company-specific gap | State-6 row | Case 6 copy | Visible inside Layer C | Confusing with #7 | Field-source test: rendered from `conflicts`/completeness, never `coverage_notices` |
| 9. Bank/NBFC valuation non-applicability | Neutral note, state 7-adjacent styling | Case 7 copy | Visible inside Layer C, low weight | Reading "not applicable" as "unknown"/"bad" | Status-mapping test: `NOT_APPLICABLE` never maps to warning styling |
| 10. Valuation feature-disabled | State-8 neutral note | Case 8 copy | Visible inside Layer C, low weight | Reading "disabled" as negative valuation evidence | Status-mapping test: `FEATURE_DISABLED` never maps to warning styling |
| 11. Multiple simultaneous supporting/opposing items | Capped lists (3 each) + "Show N more" | Case 10 copy | Beyond-cap items hidden behind explicit count | Assuming the visible 3 are "the most important" without basis | Item-cap unit test |
| 12. Unknown future contract version | Section suppressed entirely | n/a | Fully hidden | Page looking broken | Version-gate unit test: `contract_version > SUPPORTED_VERSION` → no render |
| 13. Unknown future enum value (e.g. new `thesis_state`) | Falls back to "Limited evidence available" headline | Fallback copy from case 11 | Not hidden — degrades to the safest known headline | Frontend guessing a mapping incorrectly | Enum-fallback unit test |
| 14. Malformed partial RCI object (e.g. `narrative` missing) | Section not rendered (treated as case H) | n/a | Fully hidden | Partial/garbled rendering | Malformed-payload unit test |
| 15. Mobile layout | Stacked single-column, same content, collapsed Layer C | All copy unchanged | Same collapse rules | Content cut off or requiring horizontal scroll | Mobile-viewport snapshot test |
| 16. Future comparison with Daily Picks snapshot (conceptual only, no Daily Picks UI built) | "Live · computed [time]" label always visible; a hypothetical future "Snapshot · as of [date]" would use the identical label slot | Case 12 copy | n/a — documentation only | Confusing a live read with a stored historical record | Documented compatibility requirement only; no test this sprint |

## 10. Explicit Non-Goals

This sprint did not, and explicitly does not: add TypeScript interfaces; add React components; modify frontend API calls; alter API payloads; enable the Railway RCI flag; enable Valuation Intelligence; change backend RCI logic; modify Prediction Engine behavior; create a second recommendation; create a second confidence score; modify Daily Picks; create UI mockups or screenshots; add LLM-generated wording (every copy string above is hand-specified, sourced from this document or verbatim backend `narrative` text); add new data providers; or alter production behavior in any way.

## Implementation Prerequisites (carried forward and updated from Sprint #010)

1. Widen the `Prediction` TypeScript interface per §3B's table.
2. Build the collapsible panel primitive (confirmed absent from `frontend/src`, Sprint #010 finding, unchanged).
3. Adopt the exact copy strings in §5 verbatim — no further wording iteration needed before implementation, since this was this sprint's whole purpose.
4. Implement within-response-only de-duplication for `coverage_notices` per §6's confirmed-safe scope (no cross-stock/cross-session dedup until the backend contract addition below exists).
5. **New, concrete backend-contract recommendation** (not required to unblock frontend work, but recommended before any future cross-session de-duplication is attempted): add a stable per-item identifier (e.g. `reason_code`) to `coverage_notices`, `unresolved_risk_flags`, and `material_warnings`, mirroring `conflicts`' existing `conflict_id` pattern.
6. Implement the version-gate check (`contract_version` comparison) before any other RCI rendering logic, per §3C.

## Recommended Updates to Prior Documents

- **Recommendation Consolidation Live Stock Analysis UI Consumer Design** (Sprint #010): its §6 (Coverage Notice and De-Duplication) assumed a possible reason code on coverage notices without confirming it via direct execution. **Non-destructive update pointer recommended**: add a note to that document's §6 pointing here, stating the assumption is now confirmed false by direct execution and superseded by this document's §6 — not rewriting Sprint #010's original text, per this engagement's established "Update" pointer convention (e.g. SSDS-007/SSDS-008's own precedent).
- **Recommendation Consolidation Evidence Contract / Traceability and Versioning**: no change recommended — both already correctly describe the conflict/coverage taxonomy at the design level; this sprint's finding is about the *serialized* shape lacking IDs on non-conflict fields, which is a gap in what was *implemented* (Sprint #003), not a gap in what was *specified* (Sprint #002). No misstatement to correct in either document.
- **SSDS-009**: no change recommended — it specifies the consolidation model at a level above this serialization detail.

## 12. Final Recommendation

**C — conduct a focused copy/UX review before implementation**, specifically: a short internal review confirming the §5 copy catalogue against actual product/brand voice guidelines (this sprint specified evidence-led, non-advisory wording rigorously, but did not have access to a formal brand-voice document to check tone against) — narrower than option A (full implementation) because the copy itself, while internally consistent and rule-compliant, has not yet been read by anyone outside this specification process. This is a faster, lower-risk gate than re-opening contract or backend questions (option B), since the contract-typing work in §3 is now complete and sufficient for implementation as specified.

- **RCI Railway flag**: remains disabled — unchanged through this sprint and through the recommended copy review.
- **Valuation Intelligence activation**: remains a separate, parallel workstream — unaffected by this sprint, consistent with Sprint #009/#010.
- **Current API contract sufficiency for safe frontend implementation**: **sufficient for the originally-scoped UI** (headline, narrative, expandable detail, all states in §4) **but NOT sufficient for safe cross-stock/cross-session de-duplication of coverage notices** — that specific capability requires the backend-contract addition named in §6/Implementation Prerequisite #5. Implementation can proceed without it by limiting de-duplication to within-response scope only (§6's confirmed-safe fallback).
- **New stable backend identifier required**: **Yes, recommended, not blocking** — a `reason_code` (or equivalent) on `coverage_notices`/`unresolved_risk_flags`/`material_warnings`, mirroring `conflicts`' existing `conflict_id`. Not required to begin frontend implementation; required only for the specific future feature of de-duplicating across multiple stocks/sessions.
- **Exact frontend files/areas for the next implementation sprint**: `frontend/src/utils/api.ts` (widen the `Prediction` interface per §3B); a new file for the collapsible panel primitive (no existing file to extend, per Sprint #010's finding); `frontend/src/app/stock/[symbol]/page.tsx` (insert the new Evidence Summary section at the placement Sprint #010 selected — below the AI Signal/key-metrics card, above the horizon tab bar); no changes to `frontend/src/components/BullBearCase.tsx` are required, but its visual conventions (chip styling, category coloring) should be referenced/reused, not duplicated, when building the new section's mixed-evidence layout.

---

*No production code, frontend code, API behavior, Railway configuration, signal, confidence, score, gate, or Daily Picks behavior was modified by this sprint. No mockup, screenshot, or visual asset was created. RCI and Valuation Intelligence flags remain disabled.*
