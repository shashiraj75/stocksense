# AI Equity Research Analyst — Phase 1 Sprint Specification: Intelligence Snapshot Foundation

**Document ID:** Epic 008 — Implementation Phase 1 Sprint Specification
**Status:** Specification — drafted per SSDS-002; implementation NOT started and NOT authorized by this document alone
**Governed by:** `CLAUDE.md` → `INDEX.md` → SES-001–005 → [EPIC-008A Concept and Safety Specification](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md) → [Epic 008B Engineering Contract](AI-Equity-Research-Analyst-Engineering-Contract.md) (frozen) → this specification
**Preceded by:** a read-only implementation preflight (2026-07-08, no code changed) that mapped reusable data, missing pieces, and the safe first slice; this spec encodes that preflight's recommendation
**Template:** SSDS-002 — sections 1–7 follow the template; sections 8–15 supply the schema-level detail the Engineering Contract requires a phase design to fix before code exists

---

## 1. Objective

Build the deterministic data foundation for the AI Equity Research Analyst: the `LiveIntelligenceSnapshot` contract (Engineering Contract §6), a read-only snapshot builder that consumes an **already-computed** `predict()`-shaped dict, and a `ContractValidator` implementing §6.5's rejection rules — as pure, additive backend modules with **zero production call sites**. Category: AI/future capability, engineering foundation. This executes Phase 1 ("shared snapshot foundation") of the [AI Equity Research Analyst Architecture](AI-Equity-Research-Analyst-Architecture.md) 4-phase roadmap, under Epic 008B.

Per Engineering Contract §1.1, an implementation phase requires an implementation-specific design, test plan, rollout plan, and release approval. This document is that design and test plan. **The rollout plan is trivial by construction (no call site, no flag, no consumer), and coding may begin only when this specification is explicitly approved.**

## 2. Deliverables

Each is independently completable and verifiable.

1. **`backend/services/research_analyst/__init__.py`** — package marker; exports nothing implicitly. Done when: file exists, package imports cleanly.
2. **`backend/services/research_analyst/intelligence_snapshot.py`** — frozen dataclasses (or equivalently immutable structures) for `LiveIntelligenceSnapshot`, `SnapshotScope`, `SnapshotTimestamps`, `SnapshotAvailability`, and `EvidenceItem`, matching §8 and §9 below field-for-field, plus the seven-state and three-tier enums (§10). Done when: the module contains exactly these structures, no I/O, no `os`/network/db imports, and unit tests construct every state.
3. **`backend/services/research_analyst/snapshot_builder.py`** — `build_live_intelligence_snapshot(prediction_result: dict, *, symbol, market, horizon) -> LiveIntelligenceSnapshot`, implementing the builder rules in §11. Done when: builder passes the mapping, immutability, and degradation tests in §14 against fixture dicts shaped like real `predict()` output.
4. **`backend/services/research_analyst/contract_validator.py`** — `validate_snapshot(snapshot) -> ValidationResult` implementing every rejection rule in §12; an invalid snapshot yields a structured rejection (rule id + reason), never a repaired snapshot. Done when: each §12 rule has at least one dedicated failing-input test.
5. **Test suite** per §14, in `backend/tests/unit/` and `backend/tests/regression/`. Done when: all new tests pass and the full pre-existing backend suite passes unchanged.
6. **Sprint report** under `Releases/` per SES-004 §3, plus an INDEX entry. Done when: committed.

## 3. Explicit Constraints

- **Investment methodology / business logic changes: none.** The builder computes no investment metric, score, signal, threshold, ranking, or probability — it restructures already-owned values.
- **Threshold values: none touched.** No `thresholds.py` entry is added, read, moved, or changed.
- **Scope broadening: not allowed.** Only the four named new files plus tests and docs. If implementation reveals that anything outside this set must change, that is a stop condition (§13), not a permitted widening.
- **No existing production module may be modified** — including (non-exhaustively) `prediction_engine.py`, `daily_picks.py`, all four intelligence engines and their adapters, `global_context.py`, RCI's four modules, `postgres_store.py`, `thresholds.py`, `engine_contract.py`, every `api/routers/*` file, `services/intelligence_engine/*`, `.github/workflows/*`, `telegram_bot.py`, and all of `frontend/`.
- **No new dependency** may be added to `requirements.txt`/`package.json`.
- **No feature flag** is introduced (nothing to gate — there is no call site).
- **No production endpoint calls** during development or validation; all tests run against fixtures.

## 4. Out of Scope (explicitly deferred, with tracking)

| Deferred item | Why deferred | Tracked where |
|---|---|---|
| **API route / composer call site** (flag-gated composition at the `/predict` boundary, mirroring `compose_prediction_response_with_rci`) | Phase 1 has no consumer by design; a call site needs its own design + approval (RCI Sprint #007/#008 precedent) | Phase 2 sprint spec, future |
| **ResearchReportAssembler and report sections** | Depends on a validated snapshot contract existing first | Phase 2 |
| **Any frontend/UI work** | No frontend test framework exists (confirmed Epic 005 Sprint #012); copy-misattribution is the named highest UI risk — copy spec must precede components (Sprint #010/#011 precedent) | Future UI phase after Phase 2 |
| **Any LLM integration, AnswerGuard, prompts, evaluation suite** | Gated by EPIC-008A phases 008C+ and Engineering Contract §9/§14.2; explicitly non-authorized by §17 | 008C+, separate approval |
| **PersistedIntelligenceSnapshot / any persistence, tables, or Daily Picks snapshot capture** | Requires touching Daily Picks generation and Postgres schema — forbidden here; needs its own design study (Epic 005 Sprint #006's Path B remains open) | Future persistence design study |
| **PortfolioContextSnapshot / portfolio-aware anything** | Portfolio Intelligence does not exist; Contract §10.2 precondition unmet | Epic 007 (per MASTER-ROADMAP §11) + separate authorization |
| **ScreeningContextSnapshot / SimulationContextSnapshot** | Same persist-on-event prerequisite as Daily Picks; screening/simulation consumers are Phase 3+ | Future phases |
| **Change-comparison service (§4.2 report section 12)** | Requires persisted snapshots | Future persistence phase |
| **Migrating engines onto `engine_contract.EngineResponse`** | The "extend all engines" refactor already rejected by RCI Sprint #003 as out-of-scope-style risk | Not planned; revisit only via contract change control |

## 5. Verification Requirements

- Full backend suite green **locally** before commit; CI (GitHub Actions) confirmed green after push. Current baseline is taken from the latest CI evidence at implementation time, not from a historical total (per the release-status register's own rule).
- Application still imports: `python -c "import api.main"` (or the suite's existing app-import test) passes.
- "No behavior changed" is proven by tests, not read-through: the static non-interference test (§14, T-12) and the full unchanged pre-existing suite are the evidence.
- Documentation-only artifacts (this spec, INDEX, sprint report) require no test run beyond the above.

## 6. Sprint Report Requirements

SES-004 §3 baseline (Files Changed, Architecture Changes, Risks, Migration Notes, Testing Status, Recommendations). Additionally required for this sprint:

- a field-by-field statement of any place the implemented schema deviated from §8/§9 of this spec, with rationale (deviation without documentation is a defect);
- the exact fixture provenance (which real `predict()` output shapes the fixtures mirror, without calling production to obtain them);
- confirmation that no new module imports any provider, engine, or router (T-10/T-11/T-12 results named explicitly).

## 7. Definition of Done

All six deliverables complete; every §14 test passing plus the full pre-existing suite unchanged, locally and in CI; no file outside the named new files + tests + docs touched (verified by `git diff --stat` in the sprint report); no investment logic, threshold, flag, dependency, or consumer wiring introduced; sprint report and INDEX entry committed. Checkable against this spec, not negotiated afterward. The Engineering Contract §16 checklist is **not** claimed satisfied by Phase 1 — only the checklist rows Phase 1 actually touches (snapshot consumption shape, zero duplicate calculation, zero provider retrieval, no cache mutation, state taxonomy, market/horizon preservation) are asserted, each with a named test.

---

## 8. Snapshot Schema (`LiveIntelligenceSnapshot`)

Concrete Phase 1 shape, conforming to Engineering Contract §6.3. Phase 1 implements **only** `snapshot_type = "live"`; the other four types are enum values reserved but unconstructable in Phase 1.

```json
{
  "contract_version": "1.0",
  "snapshot_id": "live:<symbol>:<market>:<horizon>:<generated_at>",
  "snapshot_hash": "sha256 of the canonical JSON serialization of every field except snapshot_hash itself",
  "snapshot_type": "live",
  "scope": {
    "symbol": "canonical symbol as supplied by the caller",
    "instrument_id": "Phase 1: symbol+market composite; a richer stable id is a future contract change",
    "market": "IN | US",
    "exchange": "Phase 1: derived label (NSE for IN, US-composite for US) — scope metadata, not a data claim",
    "currency": "INR for IN, USD for US — fixed market→currency mapping table, scope metadata only",
    "horizon": "short | medium | long"
  },
  "timestamps": {
    "generated_at": "predict() result's own generated_at (owner-supplied, never re-stamped)",
    "data_as_of": "predict() result's data_timestamp (last price bar)",
    "expires_at": null
  },
  "availability": {
    "overall_status": "COMPLETE | PARTIAL | UNAVAILABLE | INVALID",
    "owner_statuses": { "<owner>": "<one of the seven evidence states>" }
  },
  "decision_context": {
    "signal": "...", "confidence": "...", "composite_score": "...", "score_band": "...",
    "target_price": "...", "trade_levels": "...", "price_reference": "...",
    "confidence_breakdown": "...", "factor_contributions": "..."
  },
  "engine_outputs": {
    "business_quality": "...", "financial_strength": "...",
    "growth_intelligence": "...", "valuation_intelligence": "...",
    "quality_factors": "... (legacy-parallel PredictionEngine factor, named distinctly — never presented as Business Quality Engine output)"
  },
  "market_context": { "market_regime": "...", "global_context": "..." },
  "evidence_items": [ "see §9" ],
  "provenance": { "source": "prediction_engine.predict", "source_generated_at": "...", "builder_version": "..." },
  "consumer_context": {},
  "disclosures": [ "research-not-instruction boundary string (Contract §13.2)" ]
}
```

Schema notes, fixed by this spec:

- `horizon` enumerates the platform's real three horizons (`short|medium|long`). The Contract's `investment` horizon has no owning engine output today; it is reserved in the enum but a builder input of `investment` is rejected (V-2), not silently mapped — a documented narrowing, to be widened only when an owner exists.
- `overall_status` in Phase 1 can be `COMPLETE` (every registered owner `SUPPORTED`), `PARTIAL` (at least decision context supported, ≥1 owner not supported), `UNAVAILABLE` (decision context itself absent/REJECTED-with-error shape), or `INVALID` (validator use only). `STALE` is reserved: Phase 1 has no freshness policy owner, and inventing one would be a new threshold — deferred, named honestly.
- All structures are immutable after construction (frozen dataclasses / tuples); "immutable" is enforced by construction and asserted by test T-4, acknowledging Python's limits (no `ctypes`-level guarantees claimed).
- `snapshot_hash` is deterministic: identical input dict + identical scope → identical hash (T-9).
- Values inside `decision_context` / `engine_outputs` / `market_context` are **deep copies** of the corresponding `predict()` sub-objects. Phase 1 deliberately pays the deep-copy cost the RCI composer avoided, because a snapshot's whole purpose is immutability independent of the shared cache — and Phase 1 has no latency budget to protect (no call site). If a future phase measures this as material, relaxing it is a contract change with its own evidence.

## 9. Evidence Item Schema (`EvidenceItem`)

Conforming to Engineering Contract §6.4:

```json
{
  "evidence_id": "stable within snapshot: <owner>:<domain>:<slug>[:<n>]",
  "owner": "PredictionEngine | BusinessQuality | FinancialStrength | GrowthIntelligence | ValuationIntelligence | MarketRegime | GlobalContext | QualityFactorsLegacy | SnapshotBuilder",
  "domain": "decision | quality | financial_strength | growth | valuation | risk | macro | scope",
  "status": "SUPPORTED | MISSING | UNAVAILABLE | NOT_APPLICABLE | FEATURE_DISABLED | STALE | EXECUTION_ERROR",
  "tier": "CONFIRMED | LIKELY | UNKNOWN",
  "claim_type": "fact | owner_conclusion | risk | counter_evidence | limitation",
  "label": "human-readable label",
  "value": "typed owner-provided value or null",
  "display_value": "owner-provided display string or null",
  "source_reference": "owner-declared source category or null",
  "as_of": "ISO-8601 or null",
  "captured_at": "snapshot generated_at",
  "market": "IN | US",
  "horizon": "short | medium | long | not_applicable",
  "provenance_ref": "predict() result key path this item was read from, e.g. 'business_quality.score'",
  "reason": "REQUIRED (non-null, non-empty) for every non-SUPPORTED status",
  "invalidation_ref": null
}
```

Fixed rules:

- Phase 1 emits `tier: CONFIRMED` for owner-supplied values and `tier: UNKNOWN` for non-supported states. `LIKELY` (bounded inference) is reserved for the future assembler phase — the builder performs no inference, so it can never emit it.
- `claim_type: invalidation` is reserved; no owner supplies structured invalidation conditions today (bear_case entries map to `counter_evidence`/`risk`, not invented invalidation thresholds).
- Evidence items are traceability units, never counted into any score (Contract §8.1). The builder emits **no** field that aggregates statuses into a number other than the availability map's plain per-owner status strings.

## 10. The Seven Evidence States

Exactly Engineering Contract §8.2, restated as builder-facing mapping rules:

| State | Phase 1 builder emits it when | Required `reason` |
|---|---|---|
| `SUPPORTED` | The owner's field is present in the input dict with a valid, owner-shaped value. | n/a |
| `MISSING` | An expected key is absent from the input dict (e.g. an older cached shape without `growth_intelligence`). | yes — "expected field absent from source result" |
| `UNAVAILABLE` | The owner's field is present but explicitly `None`/empty in a shape the owner uses for "could not compute now" (e.g. `financial_strength: null` for a US symbol). | yes — owner-declared or "owner returned no output" |
| `NOT_APPLICABLE` | The owner's own output declares market/sector non-applicability (e.g. Financial Strength for `market == "IN"`, which has no India implementation — a market-structural fact, per RCI Sprint #005's proven discriminator). | yes — the structural reason |
| `FEATURE_DISABLED` | The owner's output exists but its numeric influence is kill-switched (e.g. Valuation Intelligence with both market switches off): the *evidence* is still SUPPORTED if present — `FEATURE_DISABLED` applies only where the owner output itself is absent *because* a feature is off. The builder never infers switch state itself; Phase 1 reads only what the input dict carries. | yes — "feature disabled by configuration" |
| `STALE` | **Never emitted in Phase 1** (no freshness policy exists; inventing one = new threshold). Reserved; validator accepts it structurally (a future phase's builder may emit it) but requires its reason. | yes |
| `EXECUTION_ERROR` | The owner's field carries an owner-declared error shape (engines log-and-return-None on failure today, so Phase 1 will usually see `UNAVAILABLE`; `EXECUTION_ERROR` is emitted only when the input dict explicitly says so). | yes — bounded, no raw traceback text |

The states are never collapsed, never inferred into a direction, and `FEATURE_DISABLED`/`NOT_APPLICABLE` are never treated as adverse evidence (Contract invariant 9).

## 11. Snapshot Builder Rules

1. **Input contract:** accepts an already-computed `predict()`-shaped `dict` plus explicit `symbol`, `market`, `horizon` keyword arguments. It never calls `PredictionEngine`, never reads `_pred_cache`, never triggers any computation to obtain its input — the caller owns that.
2. **Read-only input, verified not promised:** the builder performs zero assignments into the input dict or any nested object; every retained value is deep-copied. Test T-3 deep-compares the input before/after build.
3. **No provider, engine, network, DB, or filesystem imports:** the module imports only stdlib (`dataclasses`, `hashlib`, `json`, `copy`, `enum`, `typing`) and `intelligence_snapshot`. Enforced structurally by T-10.
4. **Scope consistency:** if the input dict's own `symbol`/`market`/`horizon` fields are present and disagree with the caller-supplied scope, the builder raises a typed `ScopeMismatchError` — it never silently trusts either side (Contract §6.5 mixing rule, moved as early as possible).
5. **Owner mapping is a fixed table** (input key path → owner → domain), specified in §8/§9; the builder must not opportunistically read keys outside the table (Contract §5.1: no informal alternate owners).
6. **Legacy-field firewall:** a Daily-Picks-legacy-shaped input (bare `growth_score`/`valuation_score` numbers where engine dicts are expected) yields `MISSING`/`UNAVAILABLE` states — never fabricated modern evidence (Contract §5.2; RCI Sprint #003's proven regression pattern is reused).
7. **Graceful degradation, fail-closed:** any per-owner extraction failure yields that owner's `EXECUTION_ERROR`/`UNAVAILABLE` evidence with a bounded reason; an input that cannot yield a valid scope yields **no snapshot** (typed exception), never a partially-fabricated one.
8. **No timestamps invented:** `generated_at`/`data_as_of` come from the input dict; if absent, that is a validator rejection (V-1), not a `now()` substitution. The builder calls no clock for content fields (`captured_at` mirrors `generated_at`).
9. **Determinism:** same input + same scope → byte-identical canonical serialization and hash (T-9). No randomness, no environment reads.
10. **No score synthesis:** the builder emits no numeric field not present in the input, no counts-as-scores, no completeness percentage (Contract §8.1 — even RCI's `evidence_completeness_pct` is *not* replicated here; that is RCI's own field, not the snapshot's).

## 12. Contract Validator Rejection Rules

`validate_snapshot()` rejects (structured rule id + reason, no repair) when any of the following holds — implementing Engineering Contract §6.5 in full, restricted to Phase 1's `live` type:

- **V-1:** missing/empty `market`, `horizon`, `symbol`, `contract_version`, or `generated_at`.
- **V-2:** `market` not in `{IN, US}`; `horizon` not in `{short, medium, long}`; `snapshot_type` not `live`; unknown enum value anywhere in status/tier/claim_type.
- **V-3:** any material evidence item lacking `owner` or `evidence_id`; duplicate `evidence_id` within one snapshot.
- **V-4:** a numeric factual claim (`claim_type: fact` with non-null `value`) whose `value` is not a typed number/bool/str matching its declared shape — no numeric claim without a typed source value.
- **V-5:** any non-`SUPPORTED` status with a missing/empty `reason`.
- **V-6:** cross-scope mixing — any evidence item whose `market` or `horizon` differs from the snapshot scope (no approved comparison context exists in Phase 1, so *any* mixing rejects).
- **V-7:** missing or malformed `snapshot_id`/`snapshot_hash`; recomputed hash ≠ stored hash.
- **V-8:** user-specific data present — any key in `consumer_context`/`evidence_items` matching the portfolio/paper-trade denylist (`user_id`, `holdings`, `trade_id`, …) inside a `live` (global) snapshot.
- **V-9:** an `owner` not in the §9 registry, or an owner→domain pairing outside the fixed mapping table.
- **V-10:** `evidence_items` present but `availability.owner_statuses` inconsistent with them (an owner whose items are all `SUPPORTED` cannot be mapped `UNAVAILABLE`, and vice versa).

An invalid snapshot produces a rejection result only; the validator never mutates, fills in, or re-derives a field (Contract §3.1 ContractValidator prohibition).

## 13. Stop Conditions

Implementation halts, and the gap is documented per Contract §1.1 (never shortcut), if any of the following is encountered:

1. Any deliverable turns out to require **modifying any existing production module** — including "just one import" in `prediction_engine.py` or a router.
2. Representing a real `predict()` field faithfully would require **computing a new metric, threshold, or classification** the owner did not supply.
3. A **conflict between this spec and the Engineering Contract** (or a higher governing source) is discovered — the spec yields; the conflict is documented and this spec is amended before coding continues.
4. The immutability/no-mutation tests (T-3/T-4/T-5) cannot be made to pass **without weakening their assertions**.
5. The full pre-existing backend suite fails for any reason attributable to this sprint's changes.
6. Scope pressure to add a call site, flag, endpoint, UI, LLM, or persistence "while we're here" — each is a named deferred phase (§4), not a permissible widening.
7. Fixture construction turns out to require **calling production endpoints or live providers** — fixtures must be hand-authored from already-documented shapes instead.

## 14. Test Plan

New tests live in `backend/tests/unit/` (T-1…T-9) and `backend/tests/regression/` (T-10…T-14), following SES-003's four-category layout. Coverage maps to Engineering Contract §14.1's Phase-1-applicable rows:

| # | Test | Proves |
|---|---|---|
| T-1 | Schema construction for **every** evidence state, tier, and claim type; frozen-dataclass immutability (`FrozenInstanceError` on assignment) | §8/§9/§10 shapes exist as specified |
| T-2 | Builder happy path: a fully-populated `predict()`-shaped fixture (both markets × three horizons) → `COMPLETE` snapshot; every owner mapped per the fixed table; spot-check exact values (signal, confidence, trade levels) survive copy unchanged | §11 rules 1, 5 |
| T-3 | **Input immutability**: deep-compare fixture before/after build (equality + identity audit of nested dicts) | §11 rule 2 |
| T-4 | **Snapshot immutability**: constructed snapshot rejects attribute assignment; nested containers are tuples/frozen | §8 note |
| T-5 | **Cache-safety analogue**: builder output shares zero object identity with input sub-objects (`is not` for every nested dict) — mutation of the snapshot's source after build cannot alter the snapshot, and vice versa | Contract §5.4 |
| T-6 | Degraded inputs: absent engine keys → `MISSING`; `None` engines → `UNAVAILABLE`; IN Financial Strength → `NOT_APPLICABLE` with structural reason; every non-supported item carries a reason | §10 mapping |
| T-7 | **Legacy firewall**: Daily-Picks-legacy-shaped dict (`growth_score`/`valuation_score` floats) yields no fabricated modern evidence | §11 rule 6; Contract §5.2 |
| T-8 | Scope mismatch: input dict `market`/`symbol`/`horizon` disagreeing with caller scope raises `ScopeMismatchError` | §11 rule 4 |
| T-9 | Determinism: identical input + scope → identical `snapshot_hash`; any single field change → different hash | §11 rule 9 |
| T-10 | **Static no-provider/no-engine-import**: parse the three new modules' ASTs; assert no import of `yfinance`, `requests`, `httpx`, `psycopg`, `screener_data`, `sec_edgar_adapter`, `market_data`, `prediction_engine`, any `*_engine`, any `*_adapter`, any router | §11 rule 3; Contract §5.2 |
| T-11 | **No-call guarantee**: builder runs with `PredictionEngine.predict` monkeypatched to raise; network-level socket guard active during the whole unit suite for these modules | Contract §11.2 rule 2 |
| T-12 | **Static non-interference**: no module under `backend/services/` or `backend/api/` imports `research_analyst` (mirrors RCI Sprint #003's proven pattern) | Phase 1 zero-call-site scope |
| T-13 | Validator: one dedicated rejecting fixture per rule V-1…V-10, plus one fully-valid snapshot accepted | §12 complete |
| T-14 | **No-regression**: full pre-existing backend suite passes byte-identically (no skips added, no fixtures altered) | SES-001 no-regressions |

No LLM evaluation gate applies (no LLM exists in this phase — Contract §14.2 activates in 008C+).

## 15. Explicitly Deferred Phases (restated, binding)

Phase 1 delivers **data structures + builder + validator only**. The following are deferred, each requiring its own spec and approval, and none may be partially smuggled into Phase 1: **(a)** the API route / flag-gated `/predict` composer (Phase 2); **(b)** all frontend UI; **(c)** all LLM work — renderer, AnswerGuard, prompts, evaluation suite (008C+); **(d)** all persistence — `PersistedIntelligenceSnapshot`, tables, Daily Picks capture, change comparison; **(e)** the Portfolio-aware Analyst — `PortfolioContextSnapshot`, any user-context join (blocked on Epic 007 and separate authorization). Until those phases exist, no capability may be represented as a working AI Equity Research Analyst (Contract §16/§17).
