# Recommendation Consolidation Intelligence — Live Stock Analysis Integration Readiness Decision (Epic 005, Sprint #007)

**Status:** Integration-readiness decision sprint only. No production code, API response, UI, persistence, confidence, signal, score, gate, or engine behavior was changed — this document is the entirety of this sprint's output.

## Evidence Checkpoint (Mandatory)

Reviewed Sprints #001–#006's reports and code directly. **All non-negotiable invariants reconfirmed true.** Per this sprint's own explicit instruction to verify from code, not assumption, the following were inspected directly:

1. **`/predict`'s cache and `predict()`'s own internal cache are the same object.** Confirmed: `api/routers/predictions.py` imports `_pred_cache` directly from `services.prediction_engine` (`from services.prediction_engine import PredictionEngine, _pred_cache, _PRED_TTL`) — not a separate cache, the identical module-level dict.

2. **A critical, previously-unexamined risk, confirmed by direct code inspection: `predict()` returns the cached dict *by reference*, not a copy.** `services/prediction_engine.py`: `cached = _pred_cache.get(cache_key); if cached and (time.time() - cached[0]) < _PRED_TTL: return cached[1]` — `cached[1]` is returned directly. **Any code that later mutates this returned dict in place (`result["new_key"] = value`) would silently corrupt the shared cache entry for every other concurrent or future reader of that same cache key**, for the remainder of the 15-minute TTL (`_PRED_TTL = 15 * 60`, confirmed).

3. **Daily Picks shares this exact same cache and code path.** Confirmed: `services/daily_picks.py`'s `_predict_stock()` calls `engine.predict(symbol, market, horizon)` directly — the identical method, the identical `_pred_cache`. **This means an in-place mutation introduced anywhere in `predict()`'s own return path would not stay confined to live Stock Analysis traffic — it could silently reach Daily Picks' own per-stock prediction calls too**, a direct, real risk to this epic's own standing "do not modify Daily Picks" rule, not a hypothetical one.

4. **`_to_python()` (the router's JSON-serialization helper) is non-mutating.** Confirmed by reading its implementation: every branch returns a *newly constructed* dict/list (`{k: _to_python(v) for k, v in obj.items()}`), never mutating its input in place. This confirms the *serialization* step is already safe — the risk identified above is specifically about code that would run **before** `_to_python()` is called, if that code took the shortcut of mutating the dict directly rather than building a new one.

5. **All live Stock Analysis consumers use the same `/predict` endpoint** — confirmed by inspecting `api/routers/predictions.py` and `api/routers/stocks.py`; no alternate prediction path exists for the frontend's stock-analysis page.

6. **Raw engine outputs are already JSON-safe.** Each of the four engines' `EngineResponse.to_dict()` (confirmed unchanged since SSDS-003) returns only `str`/`float`/`list`/`dict` — no raw dataclass, enum, or numpy type reaches the response without already passing through `_to_python()`'s existing, unchanged conversion logic.

**No prior sprint's conclusion is contradicted by this evidence.** Sprint #006's finding (the API already has full engine-output access) remains true and is reconfirmed. This sprint's new finding — the cache-mutation risk — is an **addition** to that picture, not a correction of it, and it directly determines the safest integration boundary below.

## Primary Question — Answered

> **What is the safest technical boundary for exposing a read-only RCI explanation in live Stock Analysis without changing or contaminating the existing Prediction Engine decision?**

**A dedicated, read-only response composer at the router layer, operating on a defensive copy, that never mutates the dict obtained from `predict()` or from the shared cache — confirmed as necessary, not merely preferred, by the cache-mutation risk found above.**

## Integration Boundary Options

### Option A — Build RCI inside `PredictionEngine.predict()`

| Dimension | Assessment |
|---|---|
| Risk of appearing part of core decision logic | **Real** — RCI's output would sit inside the same dict `predict()` returns for *every* caller, including Daily Picks |
| Cache mutation/payload implications | **Confirmed unsafe** — `predict()`'s own cache-hit path returns `cached[1]` by reference; building RCI inside this function means its result becomes part of the cached object itself, reaching every consumer of that cache key for the full 15-minute TTL |
| Unnecessary RCI for all Prediction Engine consumers | **Confirmed real** — Daily Picks calls `predict()` directly (confirmed above); Option A would compute and attach RCI to every Daily Picks prediction too, directly contradicting this sprint's own "do not modify Daily Picks" rule |
| Testability | Lower — would require testing `predict()`'s entire surface for non-interference, not a narrow, isolated unit |
| Rollback safety | Lower — removing RCI would require touching the core engine method again |
| Coupling | **High** — couples RCI's lifecycle to the engine's own |

**Rejected.** The cache-sharing and Daily-Picks-sharing findings make this option's risk concrete, not theoretical.

### Option B — Build RCI in the `/predict` API Router

| Dimension | Assessment |
|---|---|
| Prediction Engine independence | Preserved — RCI code would live outside `prediction_engine.py` |
| Required raw evidence available | **Yes** — confirmed, Sprint #006's own finding |
| Duplicate work | None, if implemented correctly |
| Avoiding cache mutation | **Only if implemented carefully** — the router has two cache-hit-or-fresh-result return paths (lines ~109 and ~117 inspected); a careless implementation could still mutate `cached[1]` or the fresh `result` dict in place before calling `_to_python()`, reproducing the exact risk identified above |
| Limits RCI to live Stock Analysis | Yes — Daily Picks does not go through this router |
| Backward compatibility | Preserved, if additive |

**Viable, but relies on implementation discipline rather than a structural guarantee** — the router's own code already has two separate return points (cache-hit and freshly-computed), doubling the chance a future edit introduces the mutation risk in one path but not the other.

### Option C — Dedicated Read-Only Response Composer (selected)

A small, separate, internal function whose own contract is: *take a `predict()`-shaped dict (cached or fresh), return a **new** dict with an additive `recommendation_consolidation` field — never mutate the input.*

| Dimension | Assessment |
|---|---|
| Separation of concerns | **Strongest of the three** — RCI's response-assembly logic has exactly one entry point to test and reason about, not two (the router's two return paths) |
| Cache safety | **Structurally guaranteed, not just disciplined** — a composer whose own function signature/contract is "input unchanged, output new" makes the non-mutation property something a single, focused test can verify once, rather than something every future router edit must remember independently |
| API-versioning safety | Strong — the composer is the one place a future contract-version bump or field rename would need to change |
| Future reuse by other live consumers | Real — if a future consumer (e.g., a different live endpoint) ever needs the same explanation, it calls the same composer, not a copy-pasted router snippet |
| Testability | **Strongest** — a pure function (`predict_result -> dict -> new_dict`) is trivially unit-testable in isolation, mirroring every prior engine's own pure-core testing pattern (Sprints #003–#005) |
| Performance | Identical to Option B — no additional engine/provider calls either way |
| Implementation complexity | Marginally higher than B (one more function), justified directly by the cache-mutation finding |

**Selected: Option C.** Directly justified by the cache-mutation risk found in this sprint's own Evidence Checkpoint — a dedicated composer makes the "never mutate the cached dict" rule a property of one function's own contract, not a discipline every future router change must independently uphold.

### Option D

Not recommended — no evidence found during this sprint's code inspection favors a different boundary than C.

## Cache, Immutability, and Side-Effect Review (Mandatory)

| Finding | Detail |
|---|---|
| Cache key | `f"{symbol}:{market}:{horizon}"` (confirmed identical in both `predict()` and the router) |
| Cached value shape | `(timestamp: float, result: dict)` tuple |
| Cache ownership | `services.prediction_engine._pred_cache`, a single module-level dict, imported by reference into the router |
| Cached output reused by reference | **Yes, confirmed** — `predict()`'s own cache-hit path returns `cached[1]` directly |
| Downstream mutation risk | **Real** — confirmed above for both Option A and a careless Option B |
| Same prediction served to multiple consumers | **Yes, confirmed** — any concurrent request for the same `symbol:market:horizon` within the 15-minute TTL receives the identical object |
| RCI build timing | **After** the cache lookup (whether hit or fresh), never before — RCI must never participate in the decision of whether to compute or reuse a prediction, only in explaining whichever one is already chosen |
| Should RCI itself be cached in V1? | **No** — named explicitly: RCI's own computation is already confirmed near-zero-cost (Sprint #003/#004), so caching it separately would add complexity without a measured performance need; it can be recomputed on every request from the (cached-or-fresh) prediction result without concern |

**Decided rule, all three of this sprint's own proposed safeguards adopted together, not just one**:
- **A.** RCI is built only from a defensive copy of the live prediction result (or, more precisely, the composer never mutates its input — building a new top-level dict via `{**predict_result, "recommendation_consolidation": rci_payload}` is sufficient, since RCI never needs to mutate any nested engine sub-dict, only read it).
- **B.** RCI itself is kept out of the core `_pred_cache` in V1 — confirmed unnecessary given its near-zero cost.
- **C.** The composer **returns a newly assembled response object**, never mutates the cached or fresh prediction dict it received.

## Live Evidence Boundary

Confirmed, the future implementation will:
- Consume normalized evidence through the existing `recommendation_evidence_adapter.py` (unchanged since Sprint #003).
- Read each engine's own raw output (`result["business_quality"]`, `result["financial_strength"]`, `result["growth_intelligence"]`, `result["valuation_intelligence"]`) — confirmed present in every live response (Sprint #006's own finding, reconfirmed).
- **Not** infer engines from the blended `confidence` field — confirmed by the existing adapter's own design (Sprint #003), unchanged.
- **Not** derive evidence from `growth_score`/`valuation_score` — confirmed structurally impossible, those keys do not exist in the live `predict()` response at all (they are Daily-Picks-snapshot-only fields, sourced from a completely different code path, `_write_score_snapshots`, which `predict()` never calls).
- **Not** rerun individual engines, make external provider calls, modify individual-engine outputs, recompute signal/confidence, or reinterpret unresolved risk flags as active gates — all confirmed unchanged, structurally guaranteed by the existing pure-core design (Sprints #003–#005).

**Field-by-field boundary, from the live `predict()` response:**

| Field | Status for RCI |
|---|---|
| `business_quality`, `financial_strength`, `growth_intelligence`, `valuation_intelligence` | **Required** — the four evidence inputs |
| `signal`, `composite_score`, `confidence` | **Display context only** — may be referenced in a future narrative for human readability, never re-derived or treated as RCI's own evidence |
| `reasoning`, `bull_case`, `bear_case` | **Unsafe to consume as structured evidence** — free text, already a different (string-based) shape than the structured `EngineResponse` dicts; RCI must continue reading the structured engine dicts directly, never re-parsing this free text |
| `market`, `symbol` | Required, for the snapshot's own identity fields |
| Everything else (`technical`, `sentiment_score`, `target_price`, `trade_levels`, etc.) | **Not applicable to RCI** — out of scope, never consumed |

## Proposed Future API Contract (not implemented)

A new, additive `recommendation_consolidation` field (or a better name, to be finalized in the implementation sprint) on the existing `/predict` response:

| Field | Source | User-visible eventually? | Backward compatible? | Cache-safe? | Snapshot-safe? | Notes |
|---|---|---:|---:|---:|---:|---|
| `contract_version` | `RecommendationConsolidationResponse.contract_version` | Internal/audit | Yes | Yes | Yes | Already exists in the contract (Sprint #003) |
| `computed_at` | `.computed_at` | Small print | Yes | Yes | Yes | Always reflects *this* request's compute time, never the underlying prediction's cache age — named explicitly as a future UI nuance (a cached prediction + a freshly-recomputed RCI narrative could show two different timestamps, both honestly labeled) |
| `is_snapshot` | `.is_snapshot` | Yes, as "Live" | Yes | Yes | Yes | Always `False` for this integration — no snapshot path exists in V1 |
| `thesis_state` | `.thesis_state` | Yes | Yes | Yes | Yes | Category, never a number |
| `engine_agreement` | `.engine_agreement` | Yes | Yes | Yes | Yes | Plain-language count |
| `supporting_evidence`, `opposing_evidence` | `.supporting_evidence`/`.opposing_evidence` | Yes | Yes | Yes | Yes | Lists of strings |
| `active_gates`, `unresolved_risk_flags` | `.active_gates`/`.unresolved_risk_flags` | Yes | Yes | Yes | Yes | Sprint #004's corrected distinction |
| `coverage_notices` | `.coverage_notices` | Yes | Yes | Yes | Yes | Sprint #005's structural-vs-company-specific correction |
| `material_warnings` | `.material_warnings` | Yes | Yes | Yes | Yes | |
| `conflicts` (conflict IDs + narratives) | `.conflicts` | Yes | Yes | Yes | Yes | |
| `explanation_confidence_category` | `.explanation_confidence_category` | Yes | Yes | Yes | Yes | Category, never a number — confirmed no overlap with the Prediction Engine's own `confidence` field name |
| `narrative` | `.narrative` | Yes | Yes | Yes | Yes | |

**Confirmed excluded, per this sprint's own rule**: no replacement recommendation, no new Buy/Sell/Hold label, no replacement confidence, no master score, no hidden weights, no raw provider payloads, no ambiguous status values — every field above is either already a category/string/list, or a direct pass-through of an existing, already-validated contract field.

**V1 should always return a structured field, never omit it silently** — when RCI cannot build safely (an engine output is malformed, or the composer itself raises), the field should be present with an explicit `available: false` (or equivalent) marker, never simply absent, so a frontend consumer can distinguish "RCI ran and found nothing notable" from "RCI could not run for this request" — named here as a requirement for the implementation sprint, not decided in finer detail than this.

## Serialization and Backward Compatibility

Confirmed: every `RecommendationConsolidationResponse` field is already a plain `str`, `float`, `bool`, or `tuple`/`list` of strings (confirmed by direct inspection of `recommendation_consolidation_contract.py`) — no dataclass, enum, or numpy type would reach `_to_python()` unconverted; the existing serialization helper's recursive dict/list handling already covers this shape without modification. **Adding this new field is additive only** — existing frontend consumers that don't read `recommendation_consolidation` are unaffected; the brief's own "unknown fields ignored gracefully" principle (Sprint #002's Traceability document) already governs this. **No versioned endpoint or feature flag is technically required for serialization safety** — confirmed, the field's absence-or-presence is the only state a client needs to handle, and JSON clients universally ignore unknown keys by default. A feature flag is still recommended below, but for **rollback control**, not serialization safety — a different, separate justification.

## Performance and Reliability Readiness

| Measurement | Result |
|---|---|
| Incremental compute cost (snapshot building + RCI synthesis) | **Confirmed near-zero** — Sprint #003/#004's own measurement: the pure consolidation function performs no I/O; building the snapshot from an already-fetched dict is a pure, in-memory transformation |
| Impact on cold prediction path | **Unmeasured directly this sprint** (would require a production-adjacent benchmark out of this sprint's own "no production code change" scope) — but bounded above by the already-confirmed near-zero per-call cost; honestly labeled as "not separately measured," not fabricated |
| Impact on warm cached prediction path | Same reasoning — the composer runs *after* the cache lookup either way, adding the same small, already-bounded cost regardless of whether the underlying prediction was cached or fresh |
| Network/disk/environment/database work inside RCI | **None, confirmed** — re-confirmed via the existing static regression test (Sprint #003) asserting the absence of `os`/`yfinance`/`postgres` imports in the pure core |
| Error handling if RCI construction fails | **Required design rule, not yet implemented**: the composer must catch any exception from snapshot-building or `compute_recommendation_consolidation()` and return the original prediction result unmodified, with `recommendation_consolidation: {"available": false, "reason": ...}` — mirroring the exact `BaseException`-guarded, additive-only pattern every existing engine closure in `prediction_engine.py` already uses |
| Timeout/rollback behavior | No new timeout needed — the computation is in-memory and bounded; rollback is the feature-flag mechanism below |

**The non-negotiable rule from this sprint's own brief is satisfied by design, not by assumption**: *"A failure to build or synthesize RCI must never prevent a valid StockSense360 prediction from being returned."* The composer's own contract (build from a copy, catch all exceptions, always return the original prediction's other fields untouched) makes this true by construction.

## Test Plan for the Implementation Sprint

**Unit tests**: snapshot construction from a live-prediction-shaped dict; defensive-copy/no-mutation behavior (assert the input dict's identity and contents are unchanged after composing); RCI error isolation (a deliberately malformed engine sub-dict must not raise out of the composer); structural coverage notices; active gates vs. unresolved flags (already covered by Sprint #004/#005's 78 tests, re-confirmed applicable); stable serialization (round-trip through `_to_python`); deterministic output; unavailable/non-applicable states.

**Regression tests**: no change to live `signal`, `confidence`, `composite_score`, any individual engine's output, any existing gate, cache *contents* (the original dict's keys/values before composition must be byte-for-byte unchanged after composition — directly testing the cache-mutation risk this sprint found), Daily Picks (a static test confirming `daily_picks.py` still does not import the composer, mirroring the existing non-interference pattern), persistence, or any existing API field's shape.

**Integration tests**: `/predict` returns its current behavior unchanged when RCI is disabled (feature flag off) or when the composer reports `available: false`; the additive field is correctly formed when enabled; malformed RCI input degrades gracefully; **cached responses are not contaminated by a prior request's composed output** — directly testing this sprint's own central finding by issuing two requests for the same cache key and confirming the second's composed RCI field doesn't leak into a third, uncomposed read of the raw cache entry; no extra engine/provider calls occur (confirmed via the existing mocking/counting pattern other engine integration tests already use).

## Rollback Plan

**A router-level, env-var-backed feature flag** (e.g., `RECOMMENDATION_CONSOLIDATION_ENABLED`, mirroring the exact, already-proven pattern of `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US` and `GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US`) — **not implemented this sprint**, since this sprint makes no production code change; named here as the recommended mechanism for the implementation sprint to build, consistent with this codebase's own established rollback convention for every prior additive engine integration.

Per this sprint's own instruction — direct code inspection confirms a docs-only decision **is** possible here (the flag's design is a one-line `os.getenv` check, identical in shape to four already-existing precedents) — no exception to "do not implement the feature flag in this sprint" was found necessary.

## Explicit Non-Goals (reconfirmed)

The future Live Stock Analysis RCI V1 will not: alter investment decisions, confidence, or scores; create new gates; expose RCI in Daily Picks; persist RCI results; backfill historical picks; change frontend recommendation labels; add AI-generated prose, LLM dependencies, or new provider dependencies; change the Valuation Intelligence kill-switch state; enforce Business Quality fraud-risk rejection; or integrate Portfolio, Watchlists, Alerts, Paper Trading, or broker features.

## Implementation Prerequisites

**None beyond what already exists.** The pure core (Sprint #003), its real-world validation (Sprint #004), its narrative refinement (Sprint #005), and this sprint's own boundary/cache-safety findings together provide everything an implementation sprint needs to begin directly — no further evidence-gathering or design sprint is required first.

## Recommendation

**A — Proceed to a narrowly scoped Live Stock Analysis RCI Implementation Sprint.**

Justified directly by code evidence, not roadmap momentum: the integration boundary (a dedicated composer, Option C) is fully specified; the cache-mutation risk that could have made this unsafe is identified and has a concrete, structural mitigation; the API contract is fully specified field-by-field with no excluded-category violations; serialization safety is confirmed requiring no new mechanism; performance is bounded near-zero by existing measurement; the error-isolation rule is specified precisely enough to implement directly; and the rollback mechanism reuses an already-four-times-proven pattern in this exact codebase.

---

## Final Validation

No production code changed this sprint — confirmed by `git status` containing only this document and the roadmap files it updates, checked before commit below.

---

*The Prediction Engine decides. Recommendation Consolidation explains. Neither changes the other — confirmed structurally, not assumed, by this sprint's own cache-safety and boundary findings.*
