# Sprint Report — AI Research Analyst Phase 2: ReportAssembler and Flag-Gated /predict Composer

**Date:** 2026-07-08
**Epic:** 008 (governed by the frozen Epic 008B Engineering Contract)
**Specification:** [Phase 2 Sprint Specification](../Architecture/AI-Equity-Research-Analyst-Phase-2-Report-Composer-Sprint-Specification.md) — implementation explicitly approved before this sprint began
**Result:** All deliverables complete. 41 net-new tests; full suite **1481/1481** (Phase 1 baseline 1440 + 41, zero regressions); app imports clean. Flag `RESEARCH_ANALYST_V2_ENABLED` defaults off and is **absent from every committed configuration** — nothing user-visible changes until a separate operational enablement decision.

## 1. Files Changed

**New production modules (additive):**
- `backend/services/research_analyst/report_assembler.py` — `assemble_research_report(snapshot) -> ResearchReport | ReportRejection`. Validates the snapshot itself first (fail-closed with the validator's rule ids); assembles the six fixed sections in order (executive_snapshot, current_signal_context, key_evidence, risks_and_invalidation, data_availability, disclaimer); every entry cites evidence IDs + provenance refs; honest-absence texts preserve MISSING/UNAVAILABLE/NOT_APPLICABLE/FEATURE_DISABLED/EXECUTION_ERROR distinctions verbatim; the invalidation subsection renders the fixed "no approved invalidation condition" text (no owner supplies structured conditions today — Contract §4.2 row 10); exports `FORBIDDEN_ADVICE_TOKENS` as the canonical no-advice-language list the tests scan; zero calculations, no clock, no I/O.
- `backend/services/research_analyst/research_composer.py` — `research_analyst_v2_enabled()` (env `RESEARCH_ANALYST_V2_ENABLED`, default off, "1"/"true"/"yes"/"on" fail-safe parser) and `compose_prediction_response_with_research(prediction_result, *, symbol, market, horizon)`: copy-on-read, additive `research_report` key, fail-open Option A (original reference returned unchanged, no key, one bounded log line) on every failure path including validator rejection; internal flag check as defense-in-depth alongside the router gate (either control alone suffices). `os`/`logging` are this module's only additions to the package import surface — the same single flag exception the RCI composer carries.

**Modified production file (exactly one, per spec §3):**
- `backend/api/routers/predictions.py` — one import + one flag-gated call in the cache-hit return path, after the RCI block. The full diff (+13 lines, nothing removed or altered) is reproduced verbatim in this report's §3. No prediction logic, RCI behavior, cache handling, or route contract changed.

**Modified test file (deliberate contract narrowing, per spec §3):**
- `backend/tests/regression/test_research_analyst_non_interference.py` — Phase 1's "zero production importers" assertion narrowed to **exactly one approved importer** (`api/routers/predictions.py`), with the exemption list length asserted `== 1`, unapproved importers failing loudly, and a rot-check that the approved file really does import the package. The package AST allowlist updated for the two new modules (file count 4→6; `os`+`logging` permitted in `research_composer.py` only).

**New tests:**
- `backend/tests/unit/test_report_assembler.py` (17) — byte-identical determinism; six sections in fixed order; every entry cites IDs that exist in the snapshot (no dangling citations); signal values verbatim; decision-unavailable absence with no substitute; state-distinction preservation in data_availability with owner reasons; all-supported coverage text; fixed invalidation honesty; V-1/V-7/type refusals with rule ids; forbidden-token scan and no-probability scan across full/degraded/error-shaped reports; disclaimer always present and last.
- `backend/tests/unit/test_research_composer.py` (16) — flag truthy/falsy/default matrix; flag-off returns the original reference; flag-on returns a new dict with a JSON-safe `research_report` and byte-identical pre-existing keys; input never mutated; fail-open on assembler crash, on ReportRejection, and on scope mismatch; builds with `PredictionEngine.predict` monkeypatched to raise and sockets blocked.
- `backend/tests/regression/test_research_composer_integration.py` (4+) — real `_pred_cache` byte-safety with the flag on; the shared-shape regression (§2 below); router call-site shape (exactly one composer invocation, flag-gated, after the RCI block).

**Docs:** this report; INDEX entries.

## 2. Shared-Shape Regression Test (closes Phase 1's named risk 1)

Implemented exactly per spec §8: AST-parse `services/prediction_engine.py`, collect dict literals containing both `"signal"` and `"generated_at"` keys, select the largest (sanity-anchored on `"weights_used"` — the success-path literal, not the smaller error-path one), and assert (a) every key the builder's mapping tables read exists in it, and (b) the shared fixture declares no key the real literal lacks. A Prediction Engine key rename now fails this package's suite with the key named — zero engine execution, zero network, zero Prediction Engine code touched.

## 3. Exact predictions.py Diff

```diff
@@ imports @@
+from services.research_analyst.research_composer import (
+    compose_prediction_response_with_research, research_analyst_v2_enabled,
+)
@@ cache-hit return path, after the RCI observability block @@
+        # AI Research Analyst Phase 2 (Epic 008B) — the one approved call
+        # site, mirroring the RCI composer above exactly: copy-on-read,
+        # additive `research_report` key only, fail-open by returning the
+        # original result reference unchanged on any failure. Runs AFTER the
+        # RCI block and never reads its output as evidence (deferred
+        # explicitly). Flag defaults off and is absent from every committed
+        # configuration.
+        if research_analyst_v2_enabled():
+            result = compose_prediction_response_with_research(
+                result, symbol=sym, market=market, horizon=horizon)
```

## 4. Architecture Changes

The research_analyst package gains its report/composer layer and its first (and only) production call site, completing the Contract §3.2 dependency chain through "report" while narrative/consumer layers remain absent. Dependency direction unchanged: owner output → snapshot → report → (future). The composer runs after the RCI composer and passes `recommendation_consolidation` through untouched — RCI consumption as evidence remains explicitly deferred.

## 5. Risks

- **The report is live only when the flag is on, and the flag is off everywhere** — the entire Phase 2 surface is dormant in production, exactly as RCI's own Sprint #008 left RCI. The now-familiar consequence applies: the feature's value is unobservable until a separate enablement decision, and that decision inherits the register's standing approval rules.
- **CRYPTO is structurally excluded** (that path returns before the call site, and the builder rejects non-IN/US markets) — correct per contract scope, noted so nobody reads the absence as a defect.
- **The cold-path (202 → background compute → poll) response gains the report only on the subsequent cache-hit request** — the composer sits solely in the cache-hit branch, mirroring RCI. Uniformity with any future non-cache-hit surfaces is a future-phase question.
- **Forbidden-token scanning is a denylist, not a semantic guarantee** — new report wording must keep the scan green, but the list cannot prove the absence of all conceivable advice phrasing; the LLM-era AnswerGuard (008C+) owns the stronger version of this problem.

## 6. Migration Notes

- Adding any second production importer of `research_analyst` fails the non-interference test by design; extend `_APPROVED_IMPORTERS` only with an approved spec.
- Report wording changes must keep `FORBIDDEN_ADVICE_TOKENS` and the no-probability scan green; the token list is exported from `report_assembler.py` as the single source.
- `report_contract_version` ("1.0") is independent of the snapshot's `contract_version`; a change to section meaning or field shape bumps it (Contract §15.1).

## 7. Testing Status

41 net-new tests (37 in new files + amendments in the non-interference suite); targeted run green; **full suite 1481/1481 locally** (baseline 1440 + 41, no skips added); `api.main` imports cleanly. No production endpoint was called at any point; all fixtures remain hand-authored. CI to be confirmed green on push.

## 8. Recommendations for the Next Step

1. **Operational decision, not code:** whether/when to enable `RESEARCH_ANALYST_V2_ENABLED` in a controlled environment — noting the same interaction Epic 005 Sprint #008 surfaced for RCI: with the Valuation Intelligence kill switches still off, the report's valuation evidence renders that engine's output as-is while its confidence influence stays dormant.
2. **Phase 3 candidates** (each its own spec): the full 15-section report; UI typing/rendering of `research_report` (copy spec first, per the Sprint #010/#011 precedent); persistence design study for Daily Picks snapshots.
3. Bubble Territory / Bubble Risk remains a future separate design item, deliberately not touched in this sprint.
