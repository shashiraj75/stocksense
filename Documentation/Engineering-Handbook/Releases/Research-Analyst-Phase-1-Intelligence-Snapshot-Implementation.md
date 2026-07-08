# Sprint Report — AI Research Analyst Phase 1: Intelligence Snapshot Foundation

**Date:** 2026-07-08
**Epic:** 008 (governed by the frozen Epic 008B Engineering Contract)
**Specification:** [Phase 1 Sprint Specification](../Architecture/AI-Equity-Research-Analyst-Phase-1-Intelligence-Snapshot-Sprint-Specification.md) — implementation explicitly approved before this sprint began
**Result:** All six deliverables complete. 62 new tests; full suite 1440/1440 (previous baseline 1378 + 62 new, zero regressions). Zero production call sites, zero existing production modules modified.

## 1. Files Changed

**New production package (additive, no call site):**
- `backend/services/research_analyst/__init__.py` — explicit exports, no side effects.
- `backend/services/research_analyst/intelligence_snapshot.py` — frozen dataclasses (`LiveIntelligenceSnapshot`, `SnapshotScope`, `SnapshotTimestamps`, `SnapshotAvailability`, `EvidenceItem`), the seven-state `EvidenceState` and three-tier `EvidenceTier` enums, the owner→domain registry, canonical serialization, and the deterministic SHA-256 content hash (computed over every field except `snapshot_hash` itself). `snapshot_type="live"` only; the other four types are reserved vocabulary.
- `backend/services/research_analyst/snapshot_builder.py` — `build_live_intelligence_snapshot(prediction_result, *, symbol, market, horizon)`. One up-front deep copy; zero assignments into caller objects; fixed mapping tables only; typed `UnsupportedScopeError`/`ScopeMismatchError` rejections; per-owner failure isolation; no clock calls for content fields.
- `backend/services/research_analyst/contract_validator.py` — `validate_snapshot()` implementing rejection rules V-1…V-10; collects all violations with rule id + reason; never repairs.

**New tests:**
- `backend/tests/fixtures/research_analyst_fixtures.py` — hand-authored `predict()`-shaped fixtures.
- `backend/tests/unit/test_intelligence_snapshot_contract.py` (12 tests) — all seven states/three tiers/six claim types constructable; frozen-dataclass and nested-mapping immutability; JSON serialization.
- `backend/tests/unit/test_snapshot_builder.py` (21 tests) — both markets × three horizons; INR/NSE vs USD/US-composite separation; exact value survival; input immutability (deep-compare before/after); zero shared object identity; degraded inputs for MISSING/UNAVAILABLE/NOT_APPLICABLE/FEATURE_DISABLED/EXECUTION_ERROR; STALE never emitted; legacy Daily Picks firewall; scope rejection; hash determinism.
- `backend/tests/unit/test_contract_validator.py` (18 tests) — one dedicated rejecting case per rule V-1…V-10 (tampered snapshots re-hashed so each rule is isolated from V-7), valid IN/US and degraded-but-honest snapshots accepted.
- `backend/tests/regression/test_research_analyst_non_interference.py` (6 tests) — AST-level import allowlist for the package (stdlib + itself only); named prohibited imports absent; **no module under `backend/services` or `backend/api` imports `research_analyst`** (>50 files scanned); builder succeeds with `PredictionEngine.predict` monkeypatched to raise and `socket.socket` blocked; real shared `_pred_cache` entry identity- and byte-unchanged by a build, including a Daily-Picks-style concurrent-reader check.

**Docs:** this report; INDEX entry.

## 2. Architecture Changes

None to any existing component. The new package is a leaf: it imports nothing but stdlib and itself, and nothing imports it. Dependency direction matches Contract §3.2 exactly (owner output → snapshot), with the report/narrative/consumer layers still absent by design.

## 3. Deviations from the Phase 1 Specification

Disclosed per the spec's own §6 requirement:

1. **Socket guard is per-test, not suite-wide.** The spec's T-11 sketch said "network-level socket guard active during the whole unit suite"; a suite-wide guard would touch shared test infrastructure (`conftest.py`), which is outside this sprint's file allowlist. Implemented as a `socket.socket` monkeypatch inside the T-11 test itself. Narrower than sketched; the AST allowlist test covers the structural half suite-wide.
2. **Engine evidence extraction is minimal by choice:** `score` (fact) and `grade` (owner_conclusion) only — the spec's fixed table permitted exactly this; strengths/weaknesses/risks lists remain available verbatim in `engine_outputs` without per-item evidence IDs until the assembler phase defines their presentation contract.
3. No other schema field, rule, or behavior deviates from the spec's §8–§12.

## 4. Risks

- **The builder's mapping table is shape-coupled, not import-coupled.** It reads documented `predict()` result keys; if a future Prediction Engine sprint renames a key, snapshots degrade honestly (MISSING) rather than break — but no test in *their* suite will flag it. Mitigation deferred to Phase 2, where a composer call site justifies a shared-shape regression test.
- **`FEATURE_DISABLED` is nearly unreachable today.** Engines currently return their full output regardless of kill-switch state, so the builder emits this state only for an explicit marker shape no producer emits yet. The state is tested via constructed inputs; its first real producer arrives with a future engine/contract change.
- **Immutability is Python-deep, not absolute** (frozen dataclasses + `MappingProxyType` + tuples). `object.__setattr__` can still defeat it; the contract's protection against a hostile writer is the hash (V-7), which any content tamper invalidates.

## 5. Migration Notes

- The hash and serialization live in `intelligence_snapshot.py` (`canonical_serialization`, `compute_snapshot_hash`); builder and validator share them, so they cannot drift — any future serialization change is a contract-version bump.
- `HORIZONS` excludes `investment` and the builder/validator reject it; widening is a deliberate contract change, never a silent mapping.
- Fixture provenance: `tests/fixtures/research_analyst_fixtures.py` mirrors the result block at the end of `PredictionEngine.predict()` (the `result = {...}` assembly), authored by reading that code — **no production endpoint was called to capture it** (spec stop condition 7 respected).

## 6. Testing Status

- New: 62 tests across the four files above; all pass.
- Full backend suite: **1440/1440 passing locally** (previous baseline 1378 + 62 new — exact arithmetic, no skips added, no fixtures altered). CI to be confirmed green on push.
- Structural guarantees are tests, not promises: T-10 (allowlist), T-11 (no compute/fetch), T-12 (no production import), real-`_pred_cache` byte-safety.

## 7. Recommendations for the Next Sprint

1. **Phase 2 design spec** (before code): the deterministic `ResearchReportAssembler` plus the flag-gated, copy-on-read composer call site at the `/predict` boundary — mirroring `compose_prediction_response_with_rci` exactly, disabled by default.
2. Phase 2 should add the **shared-shape regression test** binding the builder's mapping table to `predict()`'s real output keys (risk 1 above).
3. Persistence, UI, LLM, and portfolio scopes remain deferred with their own approval gates (spec §15) — nothing in this sprint changes that.
