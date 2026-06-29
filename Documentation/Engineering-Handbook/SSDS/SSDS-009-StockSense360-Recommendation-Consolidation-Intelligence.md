# SSDS-009 — StockSense360 Recommendation Consolidation Intelligence

**Status:** Design Study only. No production code modified — per this sprint's explicit "do not implement, do not modify the Prediction Engine, confidence, signals, Daily Picks, Portfolio, Watchlists, alerts, or UI" rule, this document and its companion Research Report are the entirety of this sprint's output.
**Governed by:** SES-001 through SES-005, mirroring SSDS-008's own structure (the most recently proven template).

> **Update (Epic 005, Sprint #002 — Contract Design & Traceability Specification):** Sprint #002's own code-level Evidence Checkpoint (going beyond this document's own review) found two real discrepancies between this document's §5.B Engine-Output Contract assumptions and actual current code, both disclosed openly in the [Evidence Contract](../Architecture/Recommendation-Consolidation-Evidence-Contract.md): (1) Business Quality is missing `engine_version`/`market` fields the other three engines already have; (2) **more materially**, `daily_picks.py` does not currently carry any of the four engines' structured outputs forward into a Daily Pick's row or snapshot at all — only the already-blended `confidence` number and free-text `reasoning`. Neither discrepancy invalidates this document's Hybrid-model architecture or its V1 scope — both are named, scoped prerequisites for a *future* implementation/integration sprint, not contradictions requiring a paused design. The finalized Engine-Output Contract field shapes, status taxonomy, and conflict-pattern identifiers are now specified in the [Evidence Contract](../Architecture/Recommendation-Consolidation-Evidence-Contract.md) and [Traceability and Versioning](../Architecture/Recommendation-Consolidation-Traceability-and-Versioning.md) documents — §5.B and §7 below remain as this document's own original conceptual proposal, correct in spirit, now superseded in *detail* by those two documents.

> **Update (Epic 005, Sprint #003 — Evidence Contract Implementation):** The Pure Core proposed in §11 is implemented — `services/recommendation_consolidation_contract.py`, `services/recommendation_evidence_adapter.py`, `services/recommendation_consolidation_engine.py` — exactly per this document's own "read-only, additive, no blended score" design, confirmed by 51 new tests (821/821 full suite passing) including a static non-interference proof that neither `prediction_engine.py` nor `daily_picks.py` imports any RCI module. See [Sprint #003's report](../Releases/Sprint-003-Recommendation-Consolidation-Evidence-Contract-Implementation.md). Daily Picks integration (§5.G's snapshot-vs-live requirement) remains unimplemented, exactly as this document's own V1 scope (§10) specified as deferred — not a gap introduced by Sprint #003.

> **Update (Epic 005, Sprint #004 — Real-World Narrative Validation & Contract Integrity Review):** Validated against 274 real, live companies (173 India, 101 US), zero crashes. Found and corrected two real defects in Sprint #003's implementation (active-gate/unresolved-flag conflation; untraceable engine-version provenance) — see [Sprint #004's report](../Releases/Sprint-004-Recommendation-Consolidation-Real-World-Narrative-Validation.md). The five implemented conflict patterns (§7) produce accurate, deterministic explanations; `CP-08` never fired against real data (high data completeness throughout, not a defect); `CP-07` was found to fire for 100% of India companies for a single, always-true structural reason — a real, named usefulness limitation for a future narrative-template refinement, not a correctness defect. `CP-04`/`CP-05`/`CP-06` remain deferred — `CP-04` newly rejected outright (real validation found no case `CP-01` doesn't already cover), `CP-05`/`CP-06` still deferred pending a new input surface.

---

## Evidence Checkpoint (Mandatory — performed before any architecture work below)

Re-reviewed the Strategic Intelligence Gap Analysis (commit `4ec282f`) and all four Epic closure reports directly, plus `prediction_engine.py`'s current confidence pipeline and `daily_picks.py`'s ranking/gate logic, before any design work.

1. **Recommendation Consolidation remains the highest-value next core-intelligence capability.** Confirmed unchanged: it is still the only candidate whose prerequisite is already satisfied (four closed, validated engines) and the only candidate requiring zero new provider data.
2. **The prerequisite is genuinely satisfied** — confirmed directly: Business Quality (EPIC-001, closed), Financial Strength (EPIC-002, closed), Growth Intelligence (EPIC-003, closed), Valuation Intelligence (EPIC-004, closed) all exist, are wired into `prediction_engine.py`, and pass their full test suites (770/770 at last check).
3. **This work requires no new external data providers** — confirmed: every design decision below reads fields already present in `predict()`'s existing result dict; no new adapter, provider, or fetch is proposed.
4. **Risk Intelligence remains deferred, not rejected** — confirmed unchanged from the Strategic Decision; this Design Study does not invent a new risk dimension and explicitly excludes doing so (see §3, Out of Scope).
5. **Valuation Intelligence's kill-switch activation remains a parallel operational item, not a blocker** — confirmed unchanged; this sprint does not touch it, and Recommendation Consolidation's design treats Valuation Intelligence's currently-disabled state as a fact to surface transparently (§5.C), not a gap to work around.

**No contradiction was found between the Strategic Decision and current code or documentation. The Epic 005 decision remains valid, and this Design Study proceeds on that basis.**

---

## 1. Purpose

Answer: *"Given all available independent evidence, what is the most transparent and defensible overall investment conclusion?"* — explicitly **not** "which engine scored highest," "how do we average the scores," or "how do we create a new opaque meta-score." Recommendation Consolidation Intelligence (RCI) is a **synthesis and explanation layer**, not a sixth scoring engine.

## 2. Primary Question

*"Given Business Quality, Financial Strength, Growth Intelligence, Valuation Intelligence, and the Prediction Engine's existing technical/sentiment/risk-reward/pledge/quality-gate signals — possibly in tension with one another — what should the user understand about this stock's overall thesis, including where the evidence agrees, where it conflicts, and what could invalidate it?"*

## 3. Scope Boundary

**In scope (design only, this sprint):** the decision-ownership model, the engine-output contract, the hard-gate taxonomy, the conflict-resolution narrative patterns, the double-counting safeguards, the confidence-decomposition model, the snapshot-vs-live data model, the explainability field set, and India/US applicability — all as **design**, not implementation.

**Out of scope, explicitly, per this sprint's own rule:** a new Risk Intelligence engine; new market-data providers; NLP/free-text extraction; portfolio-level decisioning; broker integration; trade execution; Paper Trading; UI implementation; notifications; Valuation Intelligence kill-switch activation; any score, threshold, or weight tuning. Product Enhancement Backlog items (broker integrations, UI polish, mobile, general performance work) remain separate from this Core Intelligence design, per the Strategic Decision's own Product-Roadmap Separation Check.

## 4. Architecture Investigation — Three Models Compared

### A. Weighted Composite Model

A structured blend of engine outputs into a single new number.

| Dimension | Assessment |
|---|---|
| Benefits | Simple to compute; familiar pattern from other investing tools |
| Double-counting risk | **High** — `fund_score` (legacy ratio score), `quality_score` (`quality_factors.py`'s own Piotroski/Altman/Buffett composite), and Business Quality all derive from materially overlapping underlying ratio data (confirmed: Business Quality was deliberately kept as a *parallel*, non-replacing field at Epic 001's own integration, exactly because of this overlap — re-weighting all three into one blend would silently triple-count the same evidence) |
| Explainability | **Poor** — a single blended number does not naturally produce a narrative; a user cannot see *why* 67 differs from 71 without reverse-engineering the weights |
| Missing-data sensitivity | **Poor** — a weight assigned to a missing/inapplicable engine (e.g., Financial Strength for India) requires renormalization, a classic, easy-to-get-silently-wrong source of behavior change |
| False precision | **High** — implies a level of rigor four independently-validated, modest-correlation engines plus two legacy scores do not collectively support |
| India/US suitability | Would need a *different* weight set per market (since Financial Strength doesn't exist for India and Growth Intelligence is numeric-only for India) — multiplying the false-precision and renormalization risks rather than resolving them |

**Verdict: rejected as the primary model** — the double-counting and false-precision risks are not edge cases, they are the *expected* behavior of this specific approach given this codebase's already-confirmed overlapping legacy scores.

### B. Rule-Based Decision Matrix

Explicit, enumerated rules for hard rejection, strong alignment, conflict, insufficient evidence, and uncertainty.

| Dimension | Assessment |
|---|---|
| Maintainability | Reasonable for a small number of engines; degrades as engine count grows — already 4 core engines + 2 legacy scores + macro regime + risk-reward + pledge, an enumerated rule set covering every meaningful combination is already large |
| Explainability | **Strong** — a rule that fired is, by construction, easy to state in plain language |
| Brittleness | **Real risk** — a genuinely novel combination not anticipated by a rule author (e.g., a company with a never-before-seen mix of applicability gaps) either falls through to an undefined default or requires constant rule-set expansion |
| Scalability | Combinatorial growth is a real, named concern, not hypothetical, given this codebase's own trajectory (four engines added in roughly a year) |
| Calibration feasibility | Different in kind from prior epics' calibration (tuning a number) — "calibration" here means "did we write the right rules," a real but differently-shaped validation problem |

**Verdict: valuable for the hard-gate layer specifically (where explicit, auditable rules are exactly right), but insufficient alone** for the more nuanced, combinatorially-large space of partial agreement/disagreement.

### C. Hybrid Model — Hard Gates + Narrative Templates, No Blended Score

Combines explicit, rule-based hard-gate handling (model B's strength, scoped narrowly) with a **narrative-generation layer driven by engine agreement/disagreement counts and categories**, never a second numeric score.

| Dimension | Assessment |
|---|---|
| Explainability | **Strongest of the three** — every output traces directly to which engines agreed, which disagreed, and which gate (if any) fired |
| Independence | **Preserved by construction** — no engine's score is ever blended with another's; each retains its own, already-validated meaning |
| Auditability | **Strong** — a narrative template selection is a discrete, loggable decision, not an opaque weighted sum |
| Missing-data resilience | **Strong** — an inapplicable/missing engine is simply absent from the agreement count, with its absence stated explicitly, never silently zero-weighted |
| Double-counting risk | **Lowest of the three** — explicitly resolved by tagging known-overlapping pairs (§5.E) rather than blending them |
| Stability | **Strong** — small changes in one engine's score change which narrative bucket it falls into only at defined boundaries (mirroring the grade-band pattern already proven across all four existing engines), not a continuously-shifting blended number |
| Testability | **Strong** — narrative selection is a pure function of categorical inputs (grades, applicability, gate status), straightforward to test against fixtures, mirroring every prior engine's own test-architecture |

**Verdict: selected.** This is the only model that adds zero new opaque numeric layer, reuses every existing engine's own already-validated grade/score for exactly what it already proved, and is the direct continuation of the same "lowest cross-engine duplication risk, lowest false-precision risk" reasoning the Strategic Decision sprint used to select Recommendation Consolidation over Risk Intelligence in the first place.

## 5. Mandatory Design Answers

### A. Decision Ownership

**One clear source of truth is preserved, not introduced anew:**

| Owns | Component | Change from today |
|---|---|---|
| Final live recommendation (`signal`, `composite_score`) | **Prediction Engine** | **Unchanged** |
| Final confidence (the single number shown today) | **Prediction Engine** | **Unchanged** |
| Hard rejection / exclusion from Daily Picks | **Existing `_passes_quality_gate`** | **Unchanged** — RCI reads its outcome, never re-implements it |
| Individual engine scores/grades | **Each engine itself** | **Unchanged** |
| Consolidated narrative, engine-agreement label, thesis-conviction label, invalidation conditions | **Recommendation Consolidation Intelligence (new)** | **New, additive field(s) only** |
| Snapshot storage (Daily Picks) | **Existing `_write_score_snapshots`/picks-cache mechanism** | **Unchanged in V1** — RCI's output would be stored *alongside* it in a future integration sprint, not this one |
| Live recalculation (Stock Analysis page) | **Existing `predict()` call path** | **Unchanged** — RCI runs as an additional, pure post-processing step over `predict()`'s already-computed result |

**RCI never overrides, recomputes, or competes with `signal`/`composite_score`/`confidence`. It is read-only with respect to every existing number.**

### B. Engine-Output Contract (minimum viable standard, not implemented this sprint)

Every engine's `EngineResponse` already provides `score`, `grade`, `confidence`, `strengths`, `weaknesses`, `risks`, `explanation`, `metadata` (confirmed unchanged since SSDS-003). For RCI to consume engine outputs safely, the following **should** additionally be present in each engine's `metadata` — specified here as the target shape; **adding these fields to each existing engine's code is explicitly deferred to Sprint #002 (a contract-design sprint), not implemented now:**

- `applicability` (bool + reason) — is this engine even relevant for this company (e.g., Financial Strength for India: `False, "no India coverage"`; Price/Book inside Valuation Intelligence for a non-FINANCIAL company: already exists as `inapplicable_fields`, just not yet a top-level applicability flag).
- `hard_gate_status` (none / veto / warning, + reason) — a first-class field distinguishing a true veto (liquidity distress) from a soft, score-driven warning, rather than buried inside `metadata.rejection_reason` string-matching as today.
- `engine_version` — for audit trail (none of the four engines currently version-tags their output; a real, named gap this contract would close).
- `timestamp` — when this specific result was computed (today implicit via the surrounding `predict()` call's own `generated_at`, not per-engine).
- `data_completeness` — already present, inconsistently named, across engines (`data_completeness_pct` in Growth/Valuation Intelligence, absent in Business Quality/Financial Strength) — should be standardized.

### C. Hard Gates and Vetoes — Taxonomy

| Category | Definition | Real examples in this codebase today |
|---|---|---|
| **True veto** | An already-validated, severe, hard-negative state that should make RCI's narrative state "do not buy regardless of other evidence" | Financial Strength's `liquidity_distress` rejection (confidence capped at 30); the existing `"Risk/Reward"`/`"Governance Risk"` exclusion checks in `_passes_quality_gate` |
| **Strong warning** | Real, negative evidence, not a veto | Valuation Intelligence's overvaluation demotion (-4, ungated) — a confirmed-real signal (Sprint #004/#005), but never elevated to veto status |
| **Confidence reduction** | A small, bounded engine-level nudge | Growth Intelligence's ±3, Valuation Intelligence's +2 (gated) |
| **Informational limitation** | The engine's evidence is genuinely unavailable or inapplicable — never a negative signal | Financial Strength for India (no coverage); Price/Book outside FINANCIAL/REAL_ESTATE; a `REJECTED` grade for insufficient data |
| **Disabled-by-configuration** | The engine computed a real result, but its numeric influence is currently switched off | Valuation Intelligence in both markets today (kill switches default disabled) |
| **Sector/population gate** | A specific metric is structurally inapplicable to a sector, not a data gap | Valuation Intelligence's Bank/NBFC EV/EBITDA/FCF/PEG gating |

**A weak Valuation signal must never be treated the same as a Financial-Strength liquidity-distress veto** — confirmed directly by this taxonomy: the former is a "strong warning" (bounded, narrative-level caution), the latter is a "true veto" (the narrative must say so unconditionally, mirroring exactly how `_passes_quality_gate` already excludes it from Daily Picks today).

### D. Conflict Resolution — Narrative Patterns (no new numeric rules)

For each named pattern, the narrative RCI should produce — **deliberately not a numeric rule**, per this sprint's own "do not specify final numeric rules yet" instruction, since no outcome evidence yet exists for any specific consolidation weighting:

| Pattern | Narrative produced |
|---|---|
| High Business Quality + weak Financial Strength | "A good business with real financial fragility — quality does not offset solvency risk." |
| Attractive Valuation + Growth AVOID | "Statistically cheap, but Growth Intelligence flags a pattern consistent with the value-trap risk Epic 004 specifically validated (the `RELINFRA` precedent) — caution, not a contradiction to explain away." |
| Strong Growth + expensive Valuation | "Quality growth, priced for it — limited margin of safety, not a defect in either engine's reading (Epic 004 confirmed premium compounders are *correctly* classified as expensive)." |
| Strong Financial Strength + weak Business Quality | "Financially resilient but the underlying business is mediocre — durable, not exciting." |
| Positive technicals + poor multi-engine fundamentals | "Favorable short-term price action against fundamentally cautious engine evidence — a speculative, not a conviction, setup." |
| Positive fundamentals + negative macro regime | "Fundamentals are favorable; the current market regime has historically dampened this kind of signal — context, not a contradiction." |
| One engine unavailable or low confidence | Explicitly named: "Financial Strength has no India coverage for this stock" — never silently treated as either support or opposition. |
| Low data completeness, favorable available evidence | "This conclusion rests on incomplete evidence" is surfaced as a visible caveat on the *thesis*, not hidden behind an otherwise-positive score. |

### E. Avoiding Double Counting

**Full input map, this sprint's own required deliverable:**

| Input | Independent of the others? |
|---|---|
| Technical score (`tech_score`) | Yes — price/momentum-based |
| Sentiment score | Yes — news-based |
| Legacy fundamental ratio score (`fund_score`) | **No** — overlaps with Business Quality (confirmed at Epic 001's own integration: kept as a deliberate *parallel*, non-replacing field specifically because of this overlap) |
| Legacy quality score (`quality_factors.py`'s Piotroski/Altman/Buffett composite) | **No** — overlaps with Business Quality's Earnings Quality/Balance Sheet categories (same underlying ratio data) |
| Macro regime | Independent as a *weighting* input, not a separately-scored fact RCI consumes directly |
| Risk/Reward, Pledge (Governance Risk) | Already-applied modifiers, surfaced via `reasoning` — RCI reads the outcome, never re-derives |
| Business Quality, Financial Strength, Growth Intelligence, Valuation Intelligence | **Confirmed mutually independent** by each engine's own Evidence Checkpoint and Sprint #007's Double-Counting Assessment — inherited here, not re-derived |
| Existing quality-gate string checks (`"Risk/Reward"`, `"Governance Risk"`, `"liquidity distress"`, `"Overbought"`) | Procedural filters — RCI reads pass/fail, never reimplements the matching logic (avoiding a second, possibly-inconsistent copy) |

**Safeguard specified for the Engine-Output Contract (§5.B):** known-overlapping pairs (`fund_score`↔Business Quality, `quality_score`↔Business Quality) should carry an explicit `overlap_group` tag so RCI's narrative generator applies an automatic "not independent confirmation" caveat whenever both happen to agree — a systematic safeguard, not a convention relying on every future narrative-writer remembering it by hand.

### F. Confidence Architecture

**Decomposed into visible dimensions, not blended into a new single number** — directly avoiding the false-precision risk Model A (§4.A) demonstrated:

1. **Final displayed confidence** — today's existing Prediction Engine value. **Unchanged, remains the single source of truth.**
2. **Data confidence** — an aggregate readability of each contributing engine's own confidence/data-completeness (already exists per-engine; not yet aggregated anywhere).
3. **Engine Agreement** *(new)* — a qualitative count: "3 of 4 applicable engines support this thesis; 1 is a caution" — explicitly a **label**, never a blended score.
4. **Thesis Conviction** *(new)* — a qualitative category (Strong / Moderate / Weak / Conflicted) derived from combining final confidence + Engine Agreement + hard-gate status — the closest thing to a "new metric" this design introduces, deliberately presented as a **category, not a number**, to avoid false precision.
5. **Market/regime uncertainty** — a contextual caveat, never folded into any score.

### G. Snapshot vs. Live Architecture

Using the illustrative scenario named in this sprint's own brief (a stock whose Daily Picks card and live Stock Analysis page show different confidence/rationale because they were computed at different times) as the permanent architectural requirement, not a one-off bug to patch: **Daily Picks' existing snapshot mechanism already freezes confidence/rationale/target/stop-loss at generation time (`_write_score_snapshots`, confirmed unchanged).** RCI's design requirement: when (in a future integration sprint, not this one) RCI's consolidated fields are surfaced on a Daily Pick, they must be **computed once, at generation time, and stored alongside the existing frozen fields — never recomputed live when a user later views a stale pick.** Stock Analysis pages, by contrast, call `predict()` fresh every time, so RCI's output there is always live. **The data model must carry an explicit `is_snapshot` (or equivalent) flag and a `computed_at` timestamp on every RCI output**, so a future UI can label each view "Live" vs. "As of [date]" — no UI is implemented this sprint, only this data/responsibility requirement.

### H. Explainability

Every consolidated thesis must be able to answer, via structured fields (not free text generated ad hoc):

| Question | Field |
|---|---|
| Why BUY/HOLD/AVOID? | References the unchanged Prediction Engine `signal` — RCI explains it, never invents a competing one |
| Which engines support it? | `supporting_engines: [...]` |
| Which oppose it? | `opposing_engines: [...]` |
| Which evidence is missing? | `missing_evidence: [...]`, sourced from each engine's `applicability` field (§5.B) |
| What could invalidate the thesis? | `invalidation_conditions: [...]` *(new concept)* — e.g., "if Growth Intelligence's grade moves to AVOID, this thesis weakens" — forward-looking, not just a backward summary |
| Was a hard gate applied? | `hard_gate: {applied: bool, type, reason}` |
| Live or historical? | `is_snapshot`, `computed_at` (§5.G) |
| Which engine version/rules? | `engine_versions: {...}` (§5.B) |

## 6. India / US Applicability

**The core decision architecture (gate taxonomy, conflict patterns, confidence decomposition, explainability fields) is market-agnostic by design** — it operates on whichever subset of engines is actually applicable for a given market, never assuming parity:

- **India** lacks Financial Strength entirely — RCI must surface this as an **informational limitation** (§5.C), never silently substitute another engine or treat the absence as either support or opposition.
- **Growth Intelligence** is numeric for India, explainability-only for US — RCI's narrative must say so explicitly, distinguishing "this engine's score is currently informing confidence" from "this engine's score is shown for context only," not blur the two.
- **Valuation Intelligence** is currently numerically disabled (kill switch default-off) in **both** markets — RCI must say "computed, not yet influencing confidence" identically for both markets today; this is a temporal, operational fact, not a market-specific design difference.
- **Sector-specific gating** (Bank/NBFC, FINANCIAL/REAL_ESTATE Price/Book) is read directly from each engine's own existing applicability metadata, never re-derived independently by RCI.

**No parity is invented where it does not exist** — confirmed directly against each engine's own closure-documented scope.

## 7. Data-Provider Dependencies

**None, new.** Every field RCI consumes already exists in `predict()`'s current result dict or in the contract additions specified in §5.B (which themselves require no new provider, only additional metadata fields on existing engine outputs).

## 8. Graceful Degradation Rules

- An engine returning `None`/exception (already-existing `BaseException` guards in every closure) → RCI treats it identically to an `applicability: False` case, never as a negative signal.
- An engine's `REJECTED` grade for insufficient data → informational limitation, not a hard gate.
- A genuinely novel combination of applicability gaps not anticipated by a narrative template → falls back to the most conservative, most-explicit category ("Conflicted/Insufficient evidence to form a clear thesis"), never a confident-sounding default.
- RCI itself failing (a bug in the narrative generator) → must never block or alter `predict()`'s existing return value; mirrors every prior engine's own `BaseException`-guarded, additive-only integration pattern exactly.

## 9. Risks and Open Questions

- **Narrative-template combinatorics**: as more engines/states are added, the template space could itself grow unwieldy — a real risk named honestly, mirroring Model B's own identified weakness, mitigated but not eliminated by the hybrid design's reliance on agreement *counts* rather than exhaustive per-combination enumeration.
- **"Thesis Conviction" label calibration**: the boundaries between Strong/Moderate/Weak/Conflicted are not yet evidence-derived — this is explicitly named as requiring its own future calibration-equivalent sprint (narrative-quality review against real cases), not assumed correct from this Design Study alone.
- **Open question**: should `invalidation_conditions` be generated from a fixed rule set or be more dynamically derived per company? Not resolved here — a Sprint #002 contract-design question.
- **Open question**: should the Engine-Output Contract's new fields (§5.B) be added to all four engines in one cross-cutting sprint, or incrementally? Not resolved here — recommended for Sprint #002 to decide explicitly.

## 10. Proposed V1 Scope

**Include in V1:** a read-only, pure, side-effect-free `compute_recommendation_consolidation()`-style function reading only existing `predict()` output fields (plus the Sprint #002 contract additions) — producing the Consolidated Thesis object (§5.H's field set) — exposed as a **new, additive field** in the API response only. No Daily Picks wiring, no UI.

**Exclude from V1:** any numeric consolidation weight/rule (no outcome evidence yet); Daily Picks snapshot-storage wiring; UI implementation; any new hard-gate category beyond what already exists; any new risk dimension (remains Risk Intelligence's future responsibility).

**Defer to later RCI sprints:** snapshot-storage wiring and Stock-Analysis-page integration (mirroring every prior engine's own Sprint #007/#008 pattern); calibration of the Thesis Conviction label boundaries against real outcome data; the Engine-Output Contract's formal rollout across all four existing engines.

**Remains Risk Intelligence's future responsibility:** any new risk dimension (litigation, concentration, regulatory, etc.) — RCI only synthesizes *existing* engines' evidence, never invents a new one.

**Remains Portfolio Intelligence / AI Research Analyst roadmap:** cross-stock aggregation, "what to research next" — explicitly out of RCI's scope, per the Strategic Decision's own dependency analysis.

## 11. Proposed Sprint Sequence

1. **Sprint #001 (this sprint)** — Design Study.
2. **Sprint #002 — Contract-Design Sprint, not a feasibility study.** §6's own Evidence Checkpoint already confirms no new data-feasibility question exists; the genuinely open design question is finalizing the Engine-Output Contract's exact field names/shapes (§5.B) and building a pure, fixture-tested `compute_recommendation_consolidation()` function.
3. **Sprint #003** — Implementation against real data, live validation (mirroring every prior engine's own Sprint #003).
4. **Sprint #004** — Narrative-quality review (not threshold calibration, since V1 proposes no new numeric thresholds) against real, varied companies.
5. **Sprint #005** — Daily Picks / Stock Analysis integration validation, mirroring Sprint #007/#008's own empirical-invariance-proof pattern.
6. **Closure.**

---

*This document is a Design Study only — no production code, scoring, threshold, or consumer-integration change was made. Companion: [Recommendation Consolidation Research Report](../Research/Recommendation-Consolidation-Research-Report.md).*
