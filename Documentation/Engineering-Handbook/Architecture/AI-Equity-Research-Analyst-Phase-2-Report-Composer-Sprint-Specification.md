# AI Equity Research Analyst — Phase 2 Sprint Specification: ReportAssembler and Flag-Gated /predict Composer

**Document ID:** Epic 008 — Implementation Phase 2 Sprint Specification
**Status:** Specification — drafted per SSDS-002; implementation NOT started and NOT authorized by this document alone
**Governed by:** `CLAUDE.md` → `INDEX.md` → SES-001–005 → EPIC-008A → [Epic 008B Engineering Contract](AI-Equity-Research-Analyst-Engineering-Contract.md) (frozen) → [Phase 1 Sprint Specification](AI-Equity-Research-Analyst-Phase-1-Intelligence-Snapshot-Sprint-Specification.md) → this specification
**Builds on:** Phase 1 (commit `55ad6a5`) — `LiveIntelligenceSnapshot`, `EvidenceItem`, `snapshot_builder`, `contract_validator`; 1440/1440 suite; zero production call sites
**Direct precedent:** the RCI Live Stock Analysis composer (Epic 005 Sprints #007–#008): a dedicated copy-on-read composer, one call site in `/predict`'s return path behind a disabled-by-default flag, fail-open by returning the original result reference — the exact pattern at `api/routers/predictions.py:251-256` today

---

## 1. Objective

Build the deterministic explanation layer on top of Phase 1's validated snapshot: a `ResearchReportAssembler` producing a small, fixed set of deterministic report sections from a validated `LiveIntelligenceSnapshot`, and a flag-gated, copy-on-read composer that attaches the assembled report to the `/predict` response under one additive key. **No LLM, no UI, no new calculation, no persistence.** Category: AI/future capability. This executes Phase 2 of the Architecture roadmap ("deterministic explainability, no LLM") and closes Phase 1's named risk 1 with a shared-shape regression test binding the builder's mapping table to `predict()`'s real output keys.

Per Contract §1.1 this document is the required phase design and test plan; coding begins only on explicit approval.

## 2. Non-Goals

No LLM, prompt, AnswerGuard, or evaluation suite (008C+). No chatbot or conversational API. No frontend/UI work of any kind (the additive key is invisible to today's UI — see §11). No Daily Picks change, persistence, or research snapshot. No Portfolio-aware Analyst. No Prediction Engine scoring/target/stop-loss/confidence/gate change. No new provider call, no new data. No new persistence of any kind — the report lives only inside the API response that carried it. No Railway or production flag change in either this design task or the Phase 2 implementation itself (the flag ships default-off and is not enabled in any committed configuration, exactly the RCI Sprint #008 rule).

## 3. Proposed New Modules

| File | Purpose |
|---|---|
| `backend/services/research_analyst/report_assembler.py` | `assemble_research_report(snapshot) -> ResearchReport` — deterministic sections from a validated snapshot. |
| `backend/services/research_analyst/research_composer.py` | `compose_prediction_response_with_research(prediction_result, *, symbol, market, horizon) -> dict` plus `research_analyst_v2_enabled()` — the API-boundary glue, mirroring `recommendation_consolidation_api_composer.py` line-for-line in discipline. |
| `backend/tests/unit/test_report_assembler.py` | Assembler tests (§9). |
| `backend/tests/unit/test_research_composer.py` | Composer unit tests (§9). |
| `backend/tests/regression/test_research_composer_integration.py` | Cache-safety, fail-open, flag-off no-op, shared-shape regression (§8–§9). |

**One existing production file is modified, named here explicitly (not smuggled):** `backend/api/routers/predictions.py` gains one import and one flag-gated call in the same return path where the RCI composer already sits — the single approved call site, mirroring `predictions.py:251-256`. **One existing test file is amended, also explicitly:** Phase 1's `test_research_analyst_non_interference.py` currently proves *no* production module imports `research_analyst`; Phase 2 narrows that assertion to "no production module **except `api/routers/predictions.py`** imports it," with the exemption list length asserted to be exactly one. This is a deliberate, documented contract narrowing — the test must fail if a second call site ever appears. No other existing file may change (stop condition, §10).

## 4. ReportAssembler Contract

1. **Input:** exactly one `LiveIntelligenceSnapshot`. The assembler calls `validate_snapshot()` itself as its first act and **refuses to assemble from an invalid snapshot** (returns a typed rejection carrying the validator's rule ids) — fail-closed for ungrounded research (Contract §13.1); it never repairs or partially renders.
2. **Deterministic:** same snapshot → byte-identical report. No clock, randomness, environment read (other than nothing — the assembler reads no env at all), network, or I/O.
3. **Presentation derivations only** (Contract §5.3): select, group, sort by owner-provided fields, format owner-supplied values, count statuses as plain counts. **No calculation:** no new metric, score, average, percentage, probability, ranking, or threshold; no bull/base/bear scenario probabilities (barred as false precision by the Architecture spec); no synthesis of a conclusion two owners did not themselves support.
4. **Citation discipline:** every factual sentence/field in every section carries the `evidence_id`(s) it derives from; section payloads expose `evidence_ids` arrays and items carry `provenance_ref`. A section with zero citable evidence renders its honest absence text (§5), never uncited prose (Contract §4.2 row 15: a report without a valid evidence ledger is invalid).
5. **Honest absence:** `MISSING`/`UNAVAILABLE`/`NOT_APPLICABLE`/`FEATURE_DISABLED`/`EXECUTION_ERROR` are surfaced with the owner's reason, never collapsed into "no data," never inferred into a direction (Contract invariant 9).
6. **No advice language:** the assembler's templates are fixed strings; none may contain directive/advice phrasing. A fixed forbidden-token list (at minimum: "you should", "buy now", "sell now", "we recommend", "must buy", "must sell", "guaranteed", "cannot lose", "allocate") is a test artifact (§9) run against every renderable string.
7. **Output envelope** (per Contract §4.1, subset applicable to Phase 2): `report_contract_version` (independent of the snapshot's `contract_version`, starts `"1.0"`), `snapshot_id`, `snapshot_hash`, `scope` (symbol/market/exchange/currency/horizon), `generated_at`/`data_as_of`, `overall_status`, the six sections in fixed order, and `disclosures`. No field recomputes anything; the report never carries a value the snapshot does not.

## 5. Initial Deterministic Sections (Phase 2 set — the full 15-section report is deferred)

Fixed order, satisfying Contract §4.3 (no positive conclusion may be ordered ahead of its corresponding risk/limitation disclosure — sections 4 and 5 always render, never collapse):

| # | Section id | Content (all cited) | Owner mapping | Absence behavior |
|---|---|---|---|---|
| 1 | `executive_snapshot` | Scope line (symbol, market, exchange, currency, horizon), `generated_at`/`data_as_of`, `overall_status`, plain per-status evidence counts (counts only — never converted to a score, Contract §8.1). | SnapshotBuilder + scope/availability fields | Always renderable (scope is validator-guaranteed). |
| 2 | `current_signal_context` | The exact owned decision values: signal, confidence, composite score, score band, target price, trade levels — verbatim, cited to the `PredictionEngine:decision:*` items. | Prediction Engine (Contract §4.2 row 2: display exactly; never relabel/soften/harden) | "Current decision output unavailable"; no substitute recommendation. |
| 3 | `key_evidence` | SUPPORTED items grouped by owner (BusinessQuality, FinancialStrength, GrowthIntelligence, ValuationIntelligence, QualityFactorsLegacy — labeled as the legacy parallel factor, never as Business Quality — MarketRegime, GlobalContext), each entry = owner, label, display value, evidence_id. Bull-case items (`owner_conclusion`) render here. | Named engine owners via snapshot | Owners with no SUPPORTED evidence are simply absent here and appear in section 5 instead. |
| 4 | `risks_and_invalidation` | All `counter_evidence`/`risk` items (bear case), each cited. Invalidation subsection: Phase 1 emits no `invalidation` claim-type items (no owner supplies structured invalidation conditions today), so this subsection renders the fixed honest text "No approved invalidation condition is currently available for this scope." — stated as a standing Phase 2 reality, not generated case-by-case (Contract §4.2 row 10: never invent thresholds, dates, or triggers). | Risk items via snapshot; invalidation owner does not exist yet | Risk subsection: "No counter-evidence items are present in this snapshot." (a fact about the snapshot, not a safety claim). |
| 5 | `data_availability` | Every non-SUPPORTED owner with its exact state and reason, preserving NOT_APPLICABLE (market-structural, e.g. Financial Strength in IN) vs UNAVAILABLE (company-specific) vs FEATURE_DISABLED ("deliberately inactive; not a negative result") distinctions verbatim from the snapshot. | All owners via `owner_statuses` + status items | If every owner is SUPPORTED: "All registered evidence owners supplied evidence for this scope." |
| 6 | `disclaimer` | The snapshot's `disclosures` verbatim (the user-decision boundary), plus the fixed research-not-advice sentence. | SnapshotBuilder disclosures | Always renders; a report without it is invalid. |

## 6. Composer Contract (`research_composer.py`)

Mirrors `compose_prediction_response_with_rci` exactly in discipline:

1. **Copy-on-read:** accepts the already-computed `predict()`-shaped dict — which may be the same object reference stored in the shared `_pred_cache` — and never mutates it. The Phase 1 builder's own up-front `deepcopy` provides the isolation; the composer's only construction is `{**prediction_result, "research_report": payload}` — a new top-level dict, original untouched.
2. **Pipeline:** build snapshot (Phase 1 builder) → validate (Phase 1 validator) → assemble (§4) → serialize to plain JSON-safe dict → attach under the single additive key **`research_report`**.
3. **Fail-open, Option A (omit entirely):** any failure at any stage — including a validator rejection — returns the **original `prediction_result` reference unchanged, with no `research_report` key at all**; one bounded log line, no stack trace or internal detail to the caller. A failure must never prevent a valid prediction from being returned (Contract §13.1). `BaseException` is caught at the boundary, matching the RCI composer's proven choice.
4. **Never computes or fetches:** no `PredictionEngine` call, no provider/network/DB/filesystem access, no fresh prediction, no second snapshot build per request. Same AST-allowlist discipline as Phase 1, plus imports of the package's own modules; `os.getenv` is permitted **only** for the composer's own flag (the same single documented exception the RCI composer carries).
5. **Call site:** exactly one, in `api/routers/predictions.py`'s return path, adjacent to and after the existing RCI block, same shape: `if research_analyst_v2_enabled(): result = compose_prediction_response_with_research(result, symbol=sym, market=market, horizon=horizon)`. No other route, no Daily Picks, no background path.
6. **Ordering note (fixed by this spec):** the research composer runs **after** the RCI composer and receives RCI's output dict when RCI is enabled; the snapshot builder reads only its fixed mapping-table keys, so the `recommendation_consolidation` key passes through untouched and is never read as evidence (RCI is a presentation input per Contract §5.1 only in later phases; Phase 2 does not consume it — deferred explicitly).

## 7. Feature Flag

`RESEARCH_ANALYST_V2_ENABLED` — env-var-backed, **default off**, parsed with the identical fail-safe accepted-values set ("1"/"true"/"yes"/"on", anything else = off) used by `rci_live_stock_analysis_enabled` and both engine kill switches. Single global flag (no market split — the composer applies no market-specific numeric influence to gate). Phase 2 implementation ships with the flag absent from every committed configuration; enabling it in Railway is a separate future operational decision with its own approval, per the register's standing rules. This design task changes no flag anywhere.

## 8. Shared-Shape Regression Test (closes Phase 1 risk 1)

**Problem:** the builder is shape-coupled to `predict()`'s result keys; a rename would degrade snapshots to MISSING silently, and no existing test would notice. **Design — static, deterministic, zero production calls:**

1. AST-parse `services/prediction_engine.py`; locate the `result = { ... }` dict literal assembled at the end of `predict()` (anchor: the assignment whose literal contains both `"signal"` and `"generated_at"` string keys — resilient to line movement, precise enough to reject lookalikes).
2. Extract its top-level string keys.
3. Assert every key the builder's fixed mapping tables read (`_DECISION_CONTEXT_KEYS`, the five `_ENGINE_OWNERS` keys, `_MARKET_CONTEXT_KEYS`, `generated_at`, `data_timestamp`, plus the scope keys `symbol`/`market`/`horizon`) is present in that extracted set.
4. Cross-check the shared test fixture (`research_analyst_fixtures.make_predict_result`) declares no key absent from the extracted set (fixture drift detection, both directions).

If Prediction Engine renames or removes a mapped key, this test fails in *this* package's suite with a message naming the key — turning silent degradation into a loud, attributed failure while still touching zero Prediction Engine code and executing zero engine logic.

## 9. Test Plan

Unit — assembler: byte-identical output for identical snapshots (determinism); every section's `evidence_ids` non-empty or its absence text exact; every cited id exists in the snapshot (no dangling citation); degraded snapshots render section 5 with verbatim owner reasons and preserve NOT_APPLICABLE/UNAVAILABLE/FEATURE_DISABLED distinctions; invalid snapshot → typed rejection with validator rule ids, zero sections rendered; forbidden-token scan over every renderable string in reports assembled from full, degraded, and error-shaped fixtures; no numeric field in the report that is absent from the snapshot (anti-synthesis check); section order fixed with risks/limitations never after-stripped.

Unit — composer: flag parser fail-safe matrix (unset/0/garbage → off; 1/true/yes/on → on); output dict is a new object, input reference returned unchanged on failure; `research_report` present iff pipeline succeeded; JSON-serializability of the attached payload.

Regression: **copy-on-read against the real `_pred_cache`** (populate, compose, byte-compare the cache entry — the exact Phase 1/RCI pattern); **fail-open** (assembler monkeypatched to raise → original reference returned, no key added, response identical); **flag-off no-op** (composer never invoked shape-identical response — asserted at the router level via the FastAPI test client if the existing suite's route-test pattern permits, else at the call-boundary function level); **flag-on additive-key** (only `research_report` differs; every pre-existing key byte-identical); AST allowlist over the two new modules (stdlib + package + the composer's single `os` exception); non-interference test amended per §3 (exactly one approved importer); **shared-shape regression** per §8; full pre-existing suite (1440 at Phase 1 close; current baseline taken from latest CI evidence) passes unchanged.

## 10. Stop Conditions

Implementation halts and the gap is documented (Contract §1.1) if any of the following arises: (1) any need to change a signal, score, target, stop-loss, confidence, gate, or threshold; (2) any need to call a provider or fetch data; (3) any need to modify Daily Picks, any engine, any adapter, `prediction_engine.py`, or any router other than the one named call site in `predictions.py`; (4) any need for UI work; (5) any need for an LLM; (6) any inability to keep strict fail-open behavior (the original prediction returned unchanged on every failure path); (7) the §8 AST anchor cannot uniquely locate the result literal without modifying `prediction_engine.py`; (8) scope pressure to add persistence, more sections, or RCI consumption "while we're here."

## 11. Deferred Phases (each requires its own spec and approval)

Full 15-section Research Report (Contract §4.2) including thesis, historical change narrative, and consumer-specific sections; any UI display of `research_report` (today's frontend `Prediction` typing ignores unknown keys — the additive field is invisible until a future UI phase types and renders it, with its own copy spec per the Sprint #010/#011 precedent); LLM narrative rendering, AnswerGuard, and the §14.2 evaluation gate (008C+); the Portfolio-aware Analyst (blocked on Epic 007 + separate authorization); persistence and historical snapshot comparison; Daily Picks research snapshots (persist-on-event, own design study); RCI consumption as a presentation input.

## 12. Verification, Reporting, Definition of Done

Per SES-001/003/004 and the SSDS-002 baseline: full suite green locally and in CI; app imports; sprint report under `Releases/` with Files Changed / Architecture Changes / Risks / Migration Notes / Testing Status / Recommendations, plus (a) confirmation the `predictions.py` diff is exactly one import + one flag-gated call, shown in the report; (b) the amended non-interference assertion quoted; (c) explicit statement that the flag is absent from every committed configuration. Done = all deliverables, all §9 tests passing, no file outside §3's named set touched, flag off everywhere, and no claim that a working AI Research Analyst exists (Contract §16/§17).
