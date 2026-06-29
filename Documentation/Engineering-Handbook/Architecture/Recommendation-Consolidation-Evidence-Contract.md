# Recommendation Consolidation Intelligence — Evidence Contract (Epic 005, Sprint #002)

**Status:** Contract-design sprint only. No production code modified — per this sprint's explicit "do not implement, do not modify Prediction Engine/Daily Picks/Portfolio/UI behavior, do not add providers, do not activate kill switches, do not alter thresholds/weights/grades" rule, this document and its companion Traceability & Versioning specification are the entirety of this sprint's output.

> **Update (Epic 005, Sprint #003 — Evidence Contract Implementation):** This contract has been implemented as designed in `services/recommendation_consolidation_contract.py` and `services/recommendation_evidence_adapter.py` — see [Sprint #003's report](../Releases/Sprint-003-Recommendation-Consolidation-Evidence-Contract-Implementation.md) for the field-by-field implemented-vs-deferred breakdown. The §7 conflict taxonomy's V1 implementation covers 5 of the 8 patterns specified below (`CP-01`, `CP-02`, `CP-03`, `CP-07`, `CP-08`); `CP-04`/`CP-05`/`CP-06` remain deferred, named explicitly in the Sprint #003 report, not silently dropped. This document's own specification is otherwise unchanged and remains the authoritative design reference.

> **Update (Epic 005, Sprint #004 — Real-World Narrative Validation & Contract Integrity Review):** Real-world validation against 274 live companies found two genuine defects in Sprint #003's implementation of this contract, both corrected, not a flaw in this document's own design: (1) `active_gates` was conflating genuinely-enforced gates (Financial Strength's `liquidity_distress`) with merely-computed, never-enforced flags (Business Quality's fraud-risk) — corrected by adding `currently_enforced` and splitting the output into `active_gates` vs. a new `unresolved_risk_flags` field; (2) Business Quality's adapter-supplied `engine_version` was not traceable as such — corrected by adding `engine_version_provenance`. Both confirmed live: `AAL` (American Airlines) is a real company whose response correctly shows an enforced Financial Strength veto in `active_gates` and a separate, unenforced Business Quality flag in `unresolved_risk_flags` simultaneously. See [Sprint #004's report](../Releases/Sprint-004-Recommendation-Consolidation-Real-World-Narrative-Validation.md) for the full validation.

> **Update (Epic 005, Sprint #005 — Structural Coverage Narrative Refinement):** Sprint #004's real-world validation found `CP-07` (the "missing engine" conflict pattern) fired for 100% of India companies for one always-true structural reason (Financial Strength has no India coverage at all) — confirmed this was a usefulness defect in the *consolidation engine's* pattern logic, not a flaw in this contract's own status taxonomy (§5), which already distinguished `NOT_APPLICABLE` from `UNAVAILABLE` correctly. Corrected by narrowing `CP-07` to genuine company-specific causes (`UNAVAILABLE`/`EXECUTION_ERROR` only) and adding a new, purely additive output field, `coverage_notices`, for market-structural unavailability — using the adapter's own pre-existing `reason_code="not_applicable_for_market"` as the discriminator. No new `EvidenceStatus` value and no contract-version bump were required. See [Sprint #005's report](../Releases/Sprint-005-Recommendation-Consolidation-Structural-Coverage-Refinement.md).

## Evidence Checkpoint (Mandatory — performed before any contract design below)

Re-reviewed SSDS-009, the Recommendation Consolidation Research Report, all four Epic closure reports, and — critically — **read the current code directly** (`prediction_engine.py`, `business_quality_engine.py`, `financial_strength_engine.py`, `growth_intelligence_engine.py`, `valuation_intelligence_engine.py`, `daily_picks.py`, `postgres_store.py`) rather than relying on SSDS-009's own conceptual descriptions.

**Sprint #001's V1 design remains valid.** Recommendation Consolidation Intelligence (RCI) is confirmed to remain read-only, additive, non-scoring, non-voting, non-authoritative over the live signal, and independent of any new external data provider — nothing in this sprint's code review contradicts that.

**Two real, concrete discrepancies were found between SSDS-009's documented assumptions and actual current code. Both are disclosed openly below, not silently assumed away.**

### Discrepancy 1 — classified as a **contract gap**

SSDS-009 §5.B specified `engine_version`, `applicability`, `hard_gate_status`, and `timestamp` as contract fields every engine "should" expose. Direct inspection found:

| Field | Business Quality | Financial Strength | Growth Intelligence | Valuation Intelligence |
|---|---|---|---|---|
| `engine_version` | **Absent** (confirmed: its `metadata` dict has no such key) | Present (`"v1"`) | Present (`"v1"`) | Present (`"v1"`) |
| `market` (in metadata) | **Absent** | Present | Present | Present |
| `applicability` (explicit, top-level) | Absent | Absent — signaled instead by the closure returning `None` entirely | Absent | Absent — only metric-level `inapplicable_fields`/`skipped_fields` exist, no whole-engine flag |
| `hard_gate_status` (first-class) | Absent — `rejection_reason` exists but is a free-form string, not a structured veto flag | Absent — same pattern (`rejection_reason: "liquidity_distress"` is a string match, not a structured field) | Absent | Absent |
| `timestamp` (per-engine) | Absent | Absent | Absent | Absent — only implicit via `predict()`'s own surrounding `generated_at` |
| `positive_evidence`/`negative_evidence`/`warnings` (as named) | Absent — `strengths`/`weaknesses`/`risks` exist instead | Same | Same | Same |

**Impact:** none of this blocks RCI's design, but it means **the Engine-Output Contract is not yet implemented on any engine** — it is a real, named gap requiring a future, separately-scoped sprint (Sprint #003, per this document's recommendation) before RCI can consume these fields directly. Not implemented this sprint, per this sprint's own rule.

### Discrepancy 2 — classified as an **implementation prerequisite**, more material than Discrepancy 1

**`daily_picks.py` does not reference `business_quality`, `financial_strength`, `growth_intelligence`, or `valuation_intelligence` anywhere** — confirmed by direct search returning zero matches. The existing per-stock row dict `_predict_stock()` builds for Daily Picks carries forward only `confidence` (already inclusive of every engine's numeric adjustment, blended and no longer separable) and `reasoning` (free-text entries, which do include each engine's own message, confirmed in Sprint #008's own work) — **never the structured score/grade/strengths/weaknesses/risks/metadata objects** the Stock Analysis page's live `predict()` call returns.

**Separately, the existing Postgres snapshot mechanism (`log_score_snapshot`/`score_snapshots` table) stores fields named `growth_score` and `valuation_score` — but these are sourced from `quality_factors.py`'s own legacy `breakdown.earnings_revision`/`breakdown.valuation` sub-scores, a completely different, pre-Epic-003/004 computation that merely shares a similar name with the new Growth Intelligence/Valuation Intelligence engines.** Confirmed directly in `daily_picks.py`'s `_write_score_snapshots`: `growth_score=breakdown.get("earnings_revision")`, `valuation_score=breakdown.get("valuation")` — neither line references the new engines at all.

**Impact:** this is a genuine implementation prerequisite, not a documentation nuance. **If RCI were asked today to operate on a historical Daily Pick rather than a live Stock Analysis page call, it would have no structured per-engine evidence to consume at all** — only an already-blended confidence number and free-text reasoning strings, which is exactly the kind of "final post-adjustment result" this sprint's own §3 instructs RCI must not depend on solely. Daily Picks' row-construction and snapshot-persistence code would need to be extended to carry the four engines' structured outputs forward *before* RCI can meaningfully consolidate a historical pick — named explicitly here as a prerequisite for a *future* Daily-Picks-integration sprint, not something to work around now, and not something this sprint implements.

**A third, smaller observation, named for completeness:** Business Quality's own `REJECTED` grade includes a genuine hard-negative case (`rejection_reason: "fraud_risk"` or `"distress_and_aggressive_accruals"`) — but `daily_picks.py`'s existing `_passes_quality_gate` function never checks Business Quality's grade at all (confirmed: it only checks for the `"Risk/Reward"`/`"Governance Risk"` indicator names and the Financial-Strength-specific `"liquidity distress"` phrase). **This is a pre-existing characteristic of the current system, not something RCI introduces or must fix** — named here because RCI's Hard-Gate Contract (§6 below) must describe this honestly rather than assume Business Quality's rejection is already enforced as a veto somewhere downstream, when it presently is not.

**No other contradiction was found. With the two discrepancies above disclosed explicitly, the Sprint #001 architecture otherwise remains valid.**

---

## 3. Pre-Consolidation Evidence Snapshot — the Critical Architecture Rule

**`RecommendationEvidenceSnapshot`** — the immutable, structured object representing all evidence available during a single Prediction Engine run, **captured before any confidence adjustment, gate check, or signal decision has discarded the per-engine detail**.

### A. Inputs that are evidence (captured into the snapshot)

Per engine (Business Quality, Financial Strength, Growth Intelligence, Valuation Intelligence): `score`, `grade`, `confidence`, `strengths` (mapped as positive evidence), `weaknesses` (mapped as negative evidence), `risks` (mapped as warnings), `metadata.engine_version` *(absent for Business Quality today — Discrepancy 1)*, `metadata.sector_bucket`, `metadata.data_completeness_pct`, `metadata.rejection_reason` (where present), and — once Sprint #003 closes Discrepancy 1 — the new `applicability`/`hard_gate_status` fields directly.

### B. Inputs that are existing final-decision context (referenced, never recomputed)

`signal` (BUY/HOLD/SELL), `composite_score`, final `confidence` (the already-adjusted number), `technical` score, `market_regime`, the risk/reward and pledge adjustment outcomes (read from `reasoning`'s existing entries), and `_passes_quality_gate`'s own pass/fail outcome. **RCI may cite these for explanation context. It must never recompute, alter, or silently duplicate any of them** — confirmed as a hard architectural rule, not a preference.

### C. Inputs that must never be inferred — explicit appearance in the contract

| Case | How it must appear |
|---|---|
| Unavailable data (engine returned `None`/exception) | `status: unavailable`, never silently treated as `avoid` or omitted entirely |
| Missing provider fields (an engine ran but a specific metric was missing) | Surfaced via that engine's own existing `skipped_fields`/missing-data accounting, read not re-derived |
| Non-applicable sector metrics (e.g., Valuation Intelligence's Bank/NBFC EV/EBITDA gating) | `status: not_applicable`, explicitly distinct from `unavailable` — confirmed Valuation Intelligence already tracks this distinction internally (`inapplicable_fields` vs `skipped_fields`); RCI inherits, never re-derives, that distinction |
| Disabled feature flags (Valuation Intelligence's kill switches, both markets, today) | `status: feature_disabled` — the engine's score/explanation may still be cited for context, but RCI must state explicitly that it is not currently influencing live confidence |
| Engine execution failure (an exception inside the engine itself) | `status: execution_error` — never conflated with `unavailable` (a data gap) or `not_applicable` (a structural gate) |
| Unknown engine version (Business Quality today — Discrepancy 1) | `engine_version: unknown` — an honest placeholder, never a fabricated value |
| Stale snapshot fields (a historical Daily Pick whose stored fields predate a contract version) | `status: stale_snapshot` — see the companion Traceability document |

---

## 4. Minimum Input Contract — Field-by-Field Table

| Field | Required for V1? | Current availability | Source / component | Contract transformation needed? | Risk if missing |
|---|---:|---|---|---|---|
| Engine identifier | **Must-have** | Present (`metadata.engine`, all four engines) | Engine's own `EngineResponse.metadata` | None | Cannot attribute evidence to a source |
| Engine version | **Must-have** | Present (FS/GI/VI); **Absent (Business Quality)** | `metadata.engine_version` | **Yes** — add to Business Quality (Sprint #003) | Cannot audit which logic produced a historical conclusion |
| Execution timestamp | Helpful but deferrable | **Absent on all four engines** — only implicit via `predict()`'s own `generated_at` | None today | **Yes** — needs a new field, or formally adopt `predict()`'s timestamp as authoritative | Cannot distinguish a stale cached call from a fresh one at the per-engine level |
| Score | **Must-have** | Present, all four | `EngineResponse.score` | None | No evidence to consolidate |
| Grade | **Must-have** | Present, all four | `EngineResponse.grade` | None | Cannot apply the status taxonomy (§5) |
| Confidence | **Must-have** | Present, all four (note: Business Quality's `confidence` is its own `data_completeness_pct` — never consumed for adjustment, a real semantic difference worth noting in the contract, not hidden) | `EngineResponse.confidence` | None | Cannot represent evidence-quality per engine |
| Applicability | **Must-have** | **Absent on all four** (Discrepancy 1) | None today | **Yes** — new field, Sprint #003 | Without it, an inapplicable engine (e.g. Financial Strength for India) cannot be distinguished from a genuinely missing one |
| Data completeness | **Must-have** | Present, inconsistently named (`data_completeness_pct` on all four, confirmed) | `metadata.data_completeness_pct` | Minor — standardize the key name | Low risk; already close to usable |
| Positive evidence list | **Must-have** | Present as `strengths` | `EngineResponse.strengths` | Naming/mapping only | Low risk |
| Negative evidence list | **Must-have** | Present as `weaknesses` | `EngineResponse.weaknesses` | Naming/mapping only | Low risk |
| Warning list | **Must-have** | Present as `risks` | `EngineResponse.risks` | Naming/mapping only | Low risk |
| Hard-gate / veto status | **Must-have** | **Absent as a first-class field** (Discrepancy 1) — only inferable via string-matching `rejection_reason`/`reasoning` text, the same fragile pattern `_passes_quality_gate` already uses | `metadata.rejection_reason` (string) | **Yes** — needs a structured field, Sprint #003 | Without it, RCI must re-implement string-matching, duplicating (and risking inconsistency with) `_passes_quality_gate`'s own logic — explicitly forbidden by this sprint's "no silent double counting" principle |
| Reason codes | Helpful but deferrable | Partially present (`rejection_reason` strings exist, but are not a stable, enumerated code set) | `metadata.rejection_reason` | Yes, eventually — stable enum | Low near-term risk; free-text strings work for V1 narrative templates |
| Provider/source context | Future-engine field | Absent as an explicit field (implicit in which adapter ran) | None | Not needed for V1 | None for V1 |
| Market | **Must-have** | Present (FS/GI/VI); **Absent (Business Quality)** | `metadata.market` | Add to Business Quality | Cannot apply market-specific narrative caveats (§6 of SSDS-009) without it |
| Sector | **Must-have** | Present (`sector_bucket`, all four) | `metadata.sector_bucket` | None | Cannot apply sector-gating narrative context |
| Currency | Not appropriate for consolidation | Not tracked at the metadata level | N/A | N/A | RCI operates on grades/scores, never raw currency-denominated figures |
| Unavailable/non-applicable reason | **Must-have** | Partially present (`rejection_reason` for unavailable; `inapplicable_fields` for Valuation Intelligence's metric-level gating; **no whole-engine equivalent on any engine**) | Mixed | **Yes** — standardize as part of the `applicability`/`hard_gate_status` additions | Without it, RCI cannot explain *why* an engine's evidence is absent, only that it is |

**Must-have V1 fields:** engine identifier, score, grade, confidence, applicability *(once added)*, data completeness, positive/negative evidence, warnings, hard-gate status *(once added)*, market, sector.
**Helpful but deferrable:** execution timestamp, reason codes (as a stable enum rather than free text).
**Future-engine fields:** provider/source context (becomes more relevant once Risk Intelligence or additional providers exist).
**Not appropriate for consolidation:** currency.

---

## 5. Status and Severity Taxonomy

| Status | Plain-language meaning | Positive evidence? | Negative evidence? | Reduces explanation confidence? | Acts as veto? | Must be shown to users? | Valid for |
|---|---|---|---|---|---|---|---|
| `supported` | The engine's evidence favors the thesis | Yes | No | No | No | Yes | Live + snapshot |
| `mixed` | The engine's evidence is genuinely ambiguous (e.g., a HOLD-grade result) | Partial | Partial | Slightly | No | Yes | Live + snapshot |
| `warning` | Real, negative evidence — not severe enough to veto | No | Yes | Yes | No | Yes | Live + snapshot |
| `avoid` | The engine's own grade is AVOID — a strong, named opposing signal | No | Yes | Yes | **No, by default** — only a `true_veto`-tagged subtype (e.g., Financial Strength's liquidity distress) is a veto; an ordinary `avoid` grade (e.g., Growth Intelligence's) is a strong warning, not a veto | Yes | Live + snapshot |
| `rejected` | The engine could not produce a real signal for this company (insufficient data) — **not a negative judgment** | No | No | Yes (lowers confidence in the *thesis*, not in the company) | No | Yes, with the reason named | Live + snapshot |
| `unavailable` | The engine simply did not run or returned no result | No | No | Yes | No | Yes, explicitly named | Live + snapshot |
| `not_applicable` | The metric/engine structurally does not apply (sector gating, market coverage) | No | No | No — an informational fact, not missing evidence | No | Yes, explicitly named | Live + snapshot |
| `execution_error` | The engine raised an exception | No | No | Yes | No | Internally logged; user-facing message generic ("temporarily unavailable") | Live + snapshot |
| `feature_disabled` | The engine computed a real result, but a kill switch currently prevents it from influencing confidence (Valuation Intelligence, both markets, today) | Cited for context only | Cited for context only | No — the *engine's* confidence is unaffected; only its *influence* is gated | No | Yes, with an explicit "not currently influencing confidence" caveat | Live + snapshot |
| `stale_snapshot` | A historical Daily Pick's stored evidence predates the current contract version | N/A — inherited from whatever the original capture recorded | N/A | Yes — must visibly lower confidence in any *live* re-interpretation of old data | No | Yes, explicitly labeled as historical | Snapshot only |

**Confirmed, per this sprint's explicit instruction: a low Valuation grade (`warning` or `avoid`), Financial Strength's liquidity distress (`avoid` + `true_veto` subtype), missing data (`unavailable`), and Bank/NBFC non-applicability (`not_applicable`) are four genuinely distinct statuses, never collapsed into one category.**

---

## 6. Hard-Gate, Warning, and Veto Contract

| Example | Current owner | Current behavior | Future RCI interpretation | May RCI describe it? | May RCI treat it as a veto? | Code change eventually required? |
|---|---|---|---|---|---|---|
| Financial Strength liquidity distress | `_apply_financial_strength_adjustment` (confidence capped at 30) | Already a true veto in effect (caps confidence severely) | `status: avoid`, `hard_gate: true_veto` | Yes | **Yes — describing an existing veto, not creating a new one** | No |
| Existing `_passes_quality_gate` rejection (Risk/Reward, Governance Risk, liquidity-distress phrase, Overbought) | `daily_picks.py` | Excludes the stock from Top-6 entirely | RCI reads the gate's pass/fail outcome (§3.B) | Yes | Yes — same reasoning | No |
| Growth Intelligence `avoid` | `growth_intelligence_engine.py` | A strong negative score; no hard-gate behavior anywhere downstream today | `status: avoid`, `hard_gate: none` (a strong warning, not a veto) | Yes | **No** | No |
| Valuation overvaluation warning (-4, ungated) | `_apply_valuation_intelligence_adjustment` | A bounded confidence demotion; never a veto | `status: warning` | Yes | No | No |
| Valuation undervaluation boost eligibility gate (the cross-engine AND-gate) | `_apply_valuation_intelligence_adjustment` | A *conditional block* on a positive adjustment — not a veto on the company, a gate on the *boost* | `status: feature_disabled`-adjacent — more precisely, a `conditional_block` on the positive-evidence path specifically | Yes | No — this gates an *adjustment*, not the company itself | No |
| Bank/NBFC valuation non-applicability | `valuation_intelligence_engine.py` (`inapplicable_fields`) | Correctly excluded from scoring already | `status: not_applicable` | Yes | No | No |
| Low-confidence or unavailable engine output | Each engine's own `REJECTED` path | Confidence/score unaffected (never penalized for missing data) | `status: unavailable` or `rejected` | Yes | No | No |
| Feature-disabled Valuation Intelligence (both markets, today) | `_valuation_intelligence_confidence_enabled` kill switch | Computed, explainability-only, zero numeric influence | `status: feature_disabled` | Yes | No | No |
| **Business Quality `REJECTED` (fraud_risk/distress_and_aggressive_accruals)** *(found this sprint, not previously named in SSDS-009)* | `business_quality_engine.py` | **Computed, but not currently enforced as a gate anywhere in `_passes_quality_gate`** | `status: avoid`, `hard_gate: true_veto`-eligible | Yes — RCI may describe this honestly | **RCI may treat it as a veto in its own narrative even though the existing Daily Picks gate does not** — this is RCI *adding genuine new value through honest description*, not creating a new gate in the underlying system | **Possibly, eventually** — whether `_passes_quality_gate` itself should also check this is a real, separate question for a future, differently-scoped sprint, not this one |

**RCI describes existing gates honestly and may surface Business Quality's already-computed-but-currently-unenforced fraud/distress rejection as a veto in its own narrative — a genuine, real improvement in transparency — without that requiring any change to `_passes_quality_gate`'s own code this sprint or ever, unless a future sprint separately decides to.**

---

## 7. Conflict Pattern Contract — V1 Taxonomy (8 patterns, not a combinatorial table)

| ID | Conditions | Supporting evidence | Opposing evidence | Severity | Headline | Can emit if an engine is unavailable? | Snapshot-safe? | Needs validation before production? |
|---|---|---|---|---|---|---|---|---|
| `CP-01-quality-vs-strength` | BQ grade ∈ {BUY, STRONG_BUY} AND FS grade ∈ {AVOID, WATCH} | Business Quality | Financial Strength | Moderate | "Good business, fragile finances" | No — requires both | Yes | Yes — narrative wording needs review against real cases |
| `CP-02-cheap-but-avoid-growth` | VI grade ∈ {BUY, STRONG_BUY} AND GI grade ∈ {AVOID, REJECTED} | Valuation Intelligence | Growth Intelligence | **High** — this is the `RELINFRA`-shaped value-trap pattern Epic 004 specifically validated | "Statistically cheap, but growth evidence raises a value-trap concern" | No — requires both | Yes | **No — already evidence-validated by Epic 004 Sprint #005's own outcome data** |
| `CP-03-growth-priced-in` | GI grade ∈ {BUY, STRONG_BUY} AND VI grade ∈ {AVOID, WATCH} | Growth Intelligence | Valuation Intelligence | Moderate | "Quality growth, priced for it" | No — requires both | Yes | Yes — wording review |
| `CP-04-resilient-not-exciting` | FS grade ∈ {BUY, STRONG_BUY} AND BQ grade ∈ {AVOID, WATCH} | Financial Strength | Business Quality | Low | "Financially resilient, but the underlying business is mediocre" | No — requires both | Yes | Yes |
| `CP-05-technicals-vs-fundamentals` | Existing technical signal is BUY-leaning AND ≥2 of {BQ, FS, GI, VI} are AVOID/WATCH | Technical (existing, context only) | The fundamental engines | Moderate | "Favorable price action against cautious fundamental evidence" | Yes — degrades gracefully to fewer cited engines | Yes | Yes |
| `CP-06-regime-context` | Favorable engine evidence AND an unfavorable `market_regime` (existing, read not recomputed) | The favorable engines | Regime (context only, never scored) | Low | "Fundamentals favorable; current market regime has historically dampened this kind of signal" | Yes | Yes | Yes |
| `CP-07-missing-engine` | Any core engine returns `unavailable`/`not_applicable` | Whatever engines ARE available | None — explicitly not treated as opposing | Informational | "X engine's evidence wasn't available/applicable for this company" | **Yes — this pattern exists specifically because an engine is missing** | Yes | No — purely descriptive, no judgment to validate |
| `CP-08-low-completeness-favorable` | Available evidence is favorable AND aggregate data completeness is low | The favorable engines | None | Moderate | "This conclusion rests on incomplete evidence" | Yes | Yes | Yes — threshold for "low completeness" needs review |

**Deliberately a small, explicit, evidence-backed V1 set — not a combinatorial expansion across every possible grade pairing**, per this sprint's own instruction. `CP-02` is the only pattern with direct, already-existing outcome validation behind it (Epic 004 Sprint #005); the rest are narrative templates whose *wording* needs review (a future Sprint #004-equivalent narrative-quality pass, per SSDS-009's own proposed sequence), not new statistical validation, since none of them propose a new numeric rule.

---

## 8. Output Contract Design — `RecommendationConsolidationResponse` (specification only, no data class implemented)

| Output field | Purpose | User-visible? | Snapshot-safe? | Live-safe? | Source/derivation | Must be auditable? |
|---|---|---|---|---|---|---|
| `thesis_state` | A categorical summary (Supported / Mixed / Conflicted / Insufficient Evidence) | Yes | Yes | Yes | Derived from engine-agreement counting (§5/§7) | Yes |
| `engine_agreement` | "3 of 4 applicable engines support this thesis" | Yes | Yes | Yes | Derived, never a blended score | Yes |
| `conflict_ids` | List of `CP-xx` identifiers that matched | Internal + user-facing headline only | Yes | Yes | Derived from §7's taxonomy | Yes |
| `supporting_evidence` | Which engines/statuses support the thesis | Yes | Yes | Yes | Read from each engine's status (§5) | Yes |
| `opposing_evidence` | Which engines/statuses oppose it | Yes | Yes | Yes | Same | Yes |
| `active_gates` | Any true veto or conditional block currently applied | Yes | Yes | Yes | Read from §6's taxonomy | Yes |
| `material_warnings` | Non-veto but significant negative evidence | Yes | Yes | Yes | Read from `status: warning`/`avoid` (non-veto) entries | Yes |
| `evidence_completeness` | Aggregate data-completeness summary | Yes | Yes | Yes | Aggregated from each engine's `data_completeness_pct` | Yes |
| `explanation_confidence_category` | A category (High/Moderate/Low), **never a number** | Yes | Yes | Yes | Derived from evidence completeness + engine agreement | Yes |
| `narrative` | The user-facing plain-language explanation | Yes | Yes | Yes | Selected template (§7) + field substitution | Yes |
| `is_snapshot` | Live vs. historical flag | Yes (as a label, e.g. "As of [date]") | Yes (always `true` for a stored pick) | Yes (always `false` for a fresh call) | Set at generation time | Yes |
| `computed_at` | Timestamp of this specific RCI computation | Yes (small print) | Yes — frozen at original generation | Yes — current time | New | Yes |
| `contract_version` | Version of the Evidence Contract used | Internal/audit | Yes | Yes | New | Yes |
| `engine_versions_used` | Map of engine → version string consumed | Internal/audit | Yes | Yes | Read from each engine's `metadata.engine_version` (once Discrepancy 1 is closed) | Yes |

**Explicitly excluded from this output contract, per this sprint's own rule:** a replacement final signal, a replacement final confidence, a blended master score, hidden scoring weights, a new independent buy/sell recommendation. None of the fields above produce or imply any of these — confirmed by construction, every field is either a categorical label, a list, or a direct read of already-existing data.

**No data class is implemented this sprint.** Documentation alone is sufficient to validate this contract — every field above is already expressible as plain Python types (`str`, `list[str]`, `bool`, `datetime`) against data that either already exists or is specified (not built) in §4's table; a typed artifact would add no validation value beyond what this table already provides, and would risk pre-committing to a shape before Sprint #003's contract-design work (closing Discrepancy 1) is actually done.

---

## 9. Explicit Confirmation Against This Sprint's Own Validation Checklist

- **Does not alter existing Prediction Engine outputs** — confirmed: every field in §8 is newly-introduced or a direct read; nothing modifies `signal`/`composite_score`/`confidence`.
- **Does not introduce a hidden master score** — confirmed: `engine_agreement` and `thesis_state` are categorical labels derived from existing grades, never a new number.
- **Does not create a second source of truth** — confirmed: §3.B explicitly forbids RCI from recomputing or altering any existing final-decision field.
- **Does not treat unavailable or non-applicable evidence as negative** — confirmed: §5's taxonomy gives both `unavailable` and `not_applicable` `No` for "negative evidence."
- **Does not silently contradict any previous epic conclusion** — confirmed: `CP-02`'s value-trap pattern directly *cites* Epic 004's own validated finding rather than re-deriving or contradicting it.

---

*Companion: [Recommendation Consolidation — Traceability and Versioning](Recommendation-Consolidation-Traceability-and-Versioning.md). No production code, data class, or API was implemented this sprint.*
