# Recommendation Consolidation Intelligence — Integration Path Decision (Epic 005, Sprint #006)

**Status:** Architecture and integration-path decision sprint only. No production integration, API change, UI change, persistence change, or Prediction Engine/Daily Picks behavior change was made — this document is the entirety of this sprint's output.

## Evidence Checkpoint (Mandatory)

Reviewed Sprints #001–#005's reports, SSDS-009, the Evidence Contract, the Traceability and Versioning document, and — critically — **read the current API, Prediction Engine, and Daily Picks code directly**, not just prior documentation.

**All required invariants reconfirmed true**: RCI remains read-only, additive, creates no master score/replacement signal/replacement confidence, does not alter gates, does not consume legacy `growth_score`/`valuation_score`, is not exposed anywhere (API, UI, Daily Picks, persistence, Portfolio, Watchlists, Alerts, Paper Trading), kill switches and fraud-risk enforcement unchanged.

**A material, positive finding, confirmed by direct code inspection, not assumed from prior sprints' framing**: `api/routers/predictions.py`'s main `/predict` endpoint (line 109: `return JSONResponse(content=_to_python(result))`) **already returns the entire `predict()` result dict to the API consumer today** — including `business_quality`, `financial_strength`, `growth_intelligence`, and `valuation_intelligence` keys, unmodified, in every live response. This means **the live Stock Analysis flow already has full, structured, in-memory access to all four engines' raw outputs in the exact shape RCI's adapter layer already consumes** — confirmed directly, not inferred. Building a live RCI snapshot here requires **zero new engine calls, zero duplicated computation, and zero new provider access** — it is a pure, additive transformation of data the response already contains.

**A second finding, a minor factual correction to this sprint's own brief**: the brief's framing references a "9 AM IST" Daily Picks workflow. Direct inspection of `.github/workflows/daily_picks.yml` shows the actual cron schedule is `30 20 * * 0-4` UTC (India) = **02:00 IST**, not 9 AM. Corrected here explicitly, per this sprint's own "if documentation and current code differ, document the difference openly" rule — a small factual correction, not a finding that changes any architectural conclusion below.

**No contradiction was found between Sprint #005's conclusions and current code.** Sprint #005's recommendation (defer until Daily Picks persistence is addressed) is **independently re-evaluated below, not assumed correct** — per this sprint's own explicit instruction not to assume Daily Picks persistence blocks every possible integration.

## Primary Question — Answered

> **What is the safest next user-facing path for RCI, given that live Stock Analysis and historical Daily Picks have different traceability requirements?**

**Live Stock Analysis read-only integration is safe to pursue now; Daily Picks integration is not, for a reason specific to Daily Picks, not a general RCI-readiness gap.** The two consumers' requirements are genuinely different — confirmed below, not assumed.

## Path A — Live Stock Analysis Read-Only Integration First

| Dimension | Assessment |
|---|---|
| Architecture feasibility | **High** — confirmed directly: the API endpoint already has all four engines' outputs in memory; `build_recommendation_evidence_snapshot()` (Sprint #003) already accepts exactly this shape |
| Source-of-truth safety | **Safe** — RCI would read `result["business_quality"]` etc. *after* `predict()` has already finalized `signal`/`composite_score`/`confidence`; it would never re-enter or recompute any part of that pipeline |
| API compatibility | **Fully additive** — a new, optional `recommendation_consolidation` key added to the existing response dict; no existing field renamed, removed, or reinterpreted |
| Performance impact | **Near-zero** — confirmed by Sprint #003/#004's own measurement: the pure consolidation function does no I/O; the snapshot-builder reads already-fetched dicts. No new engine call, no new provider call |
| Explainability benefit | **High** — directly addresses the gap Epic 005 was chosen to fill (Strategic Decision, commit `4ec282f`) |
| Auditability | **Strong for the live case** — `computed_at`, `contract_version`, `engine_versions_used`, and `is_snapshot=False` are already part of the existing output contract (Sprints #002/#003) |
| India/US behavior | **Already correctly differentiated** — confirmed by Sprint #004/#005's real validation (Financial Strength's market-structural absence in India produces a `coverage_notice`, not a false conflict) |
| Missing-data behavior | **Already correct** — confirmed by 78 passing RCI tests across Sprints #003–#005 |
| Risk of confusing users | **Low, with one explicit mitigation** — RCI's narrative must visually/structurally remain subordinate to the existing `signal`/`confidence` fields, never positioned as if it were a competing verdict (a UI-layer responsibility, out of this sprint's scope, but named as a hard requirement for whichever future sprint implements the UI) |
| Testing requirements | **Low incremental cost** — the pure core (Sprint #003) and real-data validation (Sprint #004) are already done; integration-layer testing would primarily need to prove the new response field doesn't alter any existing field, the same non-interference pattern already proven 3 times (Sprints #003–#005) |
| Rollback simplicity | **Trivial** — a single optional response field can be removed or feature-flagged independently of anything else, since nothing else reads it |

**Path A is architecturally ready today.** Nothing found in this sprint's review identifies a genuine blocker.

## Path B — Daily Picks Structured Persistence First

| Dimension | Assessment |
|---|---|
| Architecture benefit | Real — would close Sprint #002's own Discrepancy 2 (Daily Picks carries forward none of the four engines' structured outputs today, confirmed unchanged since Sprint #002, re-confirmed again this sprint via the same direct search method) |
| Snapshot auditability | Would be a genuine improvement over today's state (only `confidence` + free-text `reasoning` persist) |
| Migration requirements | **Real and non-trivial** — every historical Daily Pick predates this structure; no backfill is possible (the original engine outputs were never persisted, so there is nothing to reconstruct from) |
| Backward compatibility | Requires explicit, versioned handling of old records (see Legacy-Data Safety Review below) |
| Database/schema impact | Real — `score_snapshots`' existing schema (`postgres_store.py`) would need new structured columns or a versioned JSON payload, a genuine schema-design decision not yet made |
| Historical-data handling | Old records must be labeled "legacy/insufficient for RCI reconstruction," never silently treated as equivalent to a new, structured record |
| Risk of confusing legacy fields | **Already a real, confirmed risk independent of this decision** — the existing `growth_score`/`valuation_score` snapshot fields are sourced from `quality_factors.py`'s legacy `breakdown.earnings_revision`/`breakdown.valuation`, not the modern engines (Sprint #002's Discrepancy 3, re-confirmed unchanged) |
| Implementation complexity | **Higher than Path A** — touches `daily_picks.py`'s row construction, the Postgres schema, and the batch-generation flow, none of which Path A needs to touch at all |
| Impact on production reliability | A real, non-zero risk specific to Daily Picks' own batch-generation flow (a sequential `ThreadPoolExecutor(max_workers=1)` loop, an existing, deliberate Yahoo-Finance-rate-limit mitigation, confirmed unchanged) — adding new computation/persistence per stock in that loop is a genuinely different risk profile than adding a field to a single, on-demand API response |
| Essential before any live RCI exposure? | **No — confirmed not essential for Path A specifically.** Essential only for Daily-Picks-specific historical auditability, a narrower and separate requirement |

**Path B is real, valuable work — but it is not a prerequisite for Path A.** This is the central finding this sprint was tasked to verify, and it is confirmed: Sprint #005's blanket "defer until Daily Picks persistence is addressed" recommendation was **broader than the evidence supports**. The evidence supports deferring *Daily Picks* integration specifically, not *all* integration.

## Path C — Defer All User-Facing Integration

**No genuine blocker was found that applies to both Path A and Path B simultaneously.** The brief's own instruction — "do not invent a blocker" — is satisfied by *not* selecting this path: Path A's readiness is confirmed by direct code inspection (the API already has the data), not assumed, and no prerequisite was found that would also block it.

## Explicit Separation of Readiness

| Consumer | Readiness |
|---|---|
| **Live Stock Analysis** | **Ready today** — confirmed, the API already has full engine-output access in memory |
| **Daily Picks** | **Not ready** — confirmed, structured evidence is not persisted, and the legacy-field confusion risk (Sprint #002's Discrepancy 3) remains live |
| **Historical snapshot/audit** | **Not ready** — depends entirely on Daily Picks' own persistence gap; no separate blocker exists beyond that one |
| **API** | **Ready for an additive field** — confirmed, the existing response shape already supports adding one without breaking anything |
| **UI** | **Not assessed this sprint** (explicitly out of scope — "no UI changes") — a future sprint's responsibility, with the one named hard requirement above (RCI must never visually compete with the existing signal) |
| **Persistence** | **Not ready** — same gap as Daily Picks, confirmed the same root cause |
| **Portfolio/Watchlist** | **Not ready, and not evaluated as a near-term candidate** — both depend on a per-stock thesis existing first (consistent with the Strategic Decision's own dependency analysis, commit `4ec282f`), unrelated to today's specific question |

**A readiness gap in Daily Picks/Persistence does not automatically block Stock Analysis or API readiness** — confirmed by direct architectural tracing (below), not assumed.

## Single Source of Truth Review

For Path A specifically:
- **Live evidence originates**: in `predict()`'s own existing engine-closure calls (unchanged).
- **The final current recommendation originates**: in `predict()`'s existing `_composite_signal`/confidence-adjustment pipeline (unchanged, confirmed by Sprint #003's own non-interference proof, re-confirmed unaffected by anything since).
- **RCI reads its inputs**: from the *already-returned* `result` dict's four engine keys — **after** the pipeline has finished, never before and never re-entering it.
- **No engine runs twice** — confirmed: `build_recommendation_evidence_snapshot()` takes the engine dicts as parameters, it does not call any engine itself.
- **RCI could not accidentally consume post-adjustment-only output**: the four engine dicts (`business_quality`, `financial_strength`, `growth_intelligence`, `valuation_intelligence`) it reads are each engine's own, independent `EngineResponse.to_dict()` output — never the blended `confidence` number. (RCI reading the blended `confidence` field too, purely for *context* in a future narrative enhancement, would be a separate, explicitly-scoped decision — not assumed or implemented here.)
- **No circular logic exists**: confirmed, RCI has no path back into any engine or into `predict()`'s own confidence pipeline (re-confirmed via the same static-import non-interference test pattern Sprints #003–#005 already established).
- **No page could show a conflicting live recommendation without explanation**: Path A's RCI output would always carry `is_snapshot=False` and a live `computed_at` timestamp, distinguishable by construction from any future Daily Pick's frozen state — satisfying the Traceability document's own non-negotiable requirement.

**Confirmed: Path A keeps RCI a pure explanation layer, never a competing decision engine.**

## Performance and Reliability Assessment

| Path | Additional engine calls | Duplicate computation | Latency impact | Failure behavior | Impact on the Daily Picks batch flow |
|---|---|---|---|---|---|
| **A** | **Zero** — confirmed, reuses the already-fetched `result` dict | **None** | **Negligible** — Sprint #003's own measurement confirmed the pure function has no I/O; building the snapshot is a pure Python dict transformation | RCI failure would be wrapped the same `BaseException`-guarded, additive-only pattern every existing closure already uses — a failure here must never affect the existing response's other fields, a design requirement for the future implementation sprint, not assumed automatically true without that sprint enforcing it | **None — Path A does not touch Daily Picks at all** |
| **B** | None directly, but the persistence write itself is new per-stock work | None | Real, not yet measured — would require instrumenting the actual sequential batch loop, which this sprint does not do (a future Path-B-specific measurement, not fabricated here) | A new failure mode (a persistence write failing) that does not exist today | **Real, unmeasured risk** — the existing `ThreadPoolExecutor(max_workers=1)` loop is already a deliberate rate-limit mitigation; adding new persistence work per stock changes its risk profile in a way this sprint does not have the evidence to quantify precisely, named as a real limitation rather than estimated by intuition |

**This sprint does not fabricate a precise latency number for Path B's batch-flow impact — that would require a production-adjacent measurement out of this sprint's own scope (no production integration this sprint).** Named as a real, open measurement gap for whichever future sprint scopes Path B's actual implementation.

## Data and Contract Readiness Matrix

| Consumer | Can construct live RCI snapshot today? | Can preserve historical RCI snapshot today? | Needs schema work? | Needs API work? | Needs UI work? | Safe next action |
|---|---:|---:|---:|---:|---:|---|
| Stock Analysis | **Yes** | No (no persistence layer involved) | No | Minimal (one additive field) | Yes (deferred to its own sprint) | **Proceed to Integration Readiness Decision** |
| Daily Picks | No (no structured per-stock evidence carried forward) | No | **Yes** | Yes | Yes | Defer — needs its own Design Study |
| Portfolio | No | No | Yes | Yes | Yes | Defer — depends on a per-stock thesis existing first |
| Watchlist | No | No | Yes | Yes | Yes | Defer — same reasoning as Portfolio |
| Alerts | No | No | Yes | Yes | Yes | Defer — not a near-term candidate, unrelated to today's question |
| Paper Trading | No | No | Yes | Yes | Yes | Defer — explicitly out of Epic 005's own scope (Product Enhancement Backlog territory per the Strategic Decision) |

**This matrix is for dependency clarity only — it does not propose integrating every consumer**, per this sprint's own instruction.

## Daily Picks Legacy-Data Safety Review

Reconfirmed, unchanged from Sprints #002–#005: `growth_score`/`valuation_score` in the existing `score_snapshots` table are sourced from `quality_factors.py`'s legacy `breakdown.earnings_revision`/`breakdown.valuation` — **never** Growth Intelligence or Valuation Intelligence. No migration may silently reinterpret them; any future persistence design must use clearly new field names or a versioned structured payload, never overload the existing ones. Historical Daily Picks must remain immutable; old records without structured evidence must be labeled `legacy_unversioned`/`not_recorded` (per the Traceability document's own existing convention), never backfilled by guesswork.

**Recommended future schema approach (not implemented this sprint): C — a hybrid.** Normalized columns (Option A) for the small, stable, frequently-queried fields (`contract_version`, `thesis_state`, `engine_versions_used` as a compact summary) plus a versioned structured JSON payload (Option B) for the full evidence detail (`supporting_evidence`, `opposing_evidence`, `coverage_notices`, `unresolved_risk_flags`, etc.), which is exactly the shape needed to extend without a schema migration every time a new field is added — consistent with the Traceability document's own "additive, versioned" backward-compatibility rule. This is a recommendation for a future Design Study to validate, not a decision made here.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A future UI presents RCI's narrative as if it were a competing recommendation | Named explicitly as a hard requirement for the UI sprint, not assumed solved by this decision |
| Path A's "safe to integrate" finding is mistaken for "ready to implement without its own readiness decision" | This sprint explicitly recommends a *further*, narrowly-scoped Integration Readiness Decision sprint, not immediate implementation |
| Path B's real value is deprioritized indefinitely because Path A is unblocked | Path B is recommended as the very next sprint after Path A's own Integration Readiness Decision, not abandoned — named explicitly in the roadmap update below |

## Selected Path

**A — proceed to a narrowly scoped Live Stock Analysis RCI Integration Readiness Decision next.**

**Path B remains real, valuable, and necessary — but for Daily Picks specifically, not as a universal prerequisite.** It is deferred to its own future sprint (a Design Study, not an implementation sprint, given the genuine schema/migration complexity found above), not abandoned.

**Path C was not selected** — no genuine blocker was found that applies to Path A.

## Prerequisites for the Recommended Next Sprint

None beyond what already exists. The Integration Readiness Decision sprint can proceed directly from this document's own findings — no further evidence-gathering sprint is required first, since Sprints #003–#005 already validated the pure core's correctness and Sprint #006 (this document) confirms the architectural feasibility of reading its inputs from the live API response.

## Recommended Next Sprint

**Epic 005, Sprint #007 — Live Stock Analysis RCI Integration Readiness Decision.** Objective: decide the exact, narrow scope of exposing RCI's output as a new, additive field in the existing `/predict` API response — confidence-cap-equivalent safeguards (if any are needed for a non-scoring, non-authoritative explanation layer — likely none, since RCI assigns no confidence adjustment at all, but this must be explicitly confirmed, not assumed, in that sprint), the exact response field name and shape, and the UI-safety requirement named above (RCI must never visually compete with the existing signal). **This is a decision sprint, mirroring every prior engine's own Sprint #006-equivalent pattern — not an implementation sprint.**

## Deferred Paths and Reasons

- **Path B (Daily Picks Structured Persistence)** — deferred to its own future Design Study, not rejected; real schema/migration complexity found, genuinely different risk profile than Path A, and not a prerequisite for Path A specifically.
- **Path C (defer all integration)** — not selected; no genuine blocker was found applying to Path A.
- **Portfolio/Watchlist/Alerts/Paper Trading integration** — deferred, consistent with the original Strategic Decision's (commit `4ec282f`) own dependency analysis; unrelated to this sprint's specific question.

---

*No production code, API, UI, or persistence change was made. No Prediction Engine or Daily Picks behavior changed. This document is the entirety of this sprint's output.*
