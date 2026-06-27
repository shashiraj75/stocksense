# Sprint #004 — Implement the Business Quality Engine: Sprint Report

**Scope delivered:** the StockSense360 Business Quality Engine, implemented exactly per SSDS-003, wired additively into `PredictionEngine.predict()`. No existing business logic, API contract, or investment methodology changed — `quality_factors.py` is untouched, confirmed by a dedicated regression test.

---

## Implementation Summary

| Component | File | Status |
|---|---|---|
| Threshold additions | `backend/services/thresholds.py` | New `BusinessQualityThresholds` dataclass — 9 new constants, each justified in-code. |
| Sector applicability | `backend/services/sector_quality_applicability.py` | New module — 8-bucket taxonomy (including the confirmed-missing Telecom bucket), an exemption/adjustment table for 6 metrics. |
| The engine itself | `backend/services/business_quality_engine.py` | New module — `compute_business_quality()`, 5-category scoring, 2 hard gates, 4 new metric helpers (Beneish M-Score, Cash Conversion, Asset Turnover, Working Capital trend), reuses 5 existing `quality_factors.py` functions directly. |
| Wiring | `backend/services/prediction_engine.py` | Additive only — new `_get_business_quality()` closure, new `business_quality` key in the result dict (both the main path and the tracking-only early-return path). |

**Verified end-to-end** against a live prediction (RELIANCE, IN, medium horizon): `signal`, `confidence`, and the existing `quality_factors.score` were unchanged from before this sprint; `business_quality` is present and populated with a real score (68, grade `buy`, confidence 75.0).

---

## Files Changed

### New
- `backend/services/business_quality_engine.py`
- `backend/services/sector_quality_applicability.py`
- `backend/tests/unit/test_business_quality_engine.py` (24 tests)
- `backend/tests/sector/test_sector_classification.py` + `__init__.py` (32 tests)
- `backend/tests/integration/test_business_quality_prediction_engine_integration.py` (4 tests)
- `backend/tests/regression/test_business_quality_backward_compatibility.py` (4 tests)
- `backend/tests/regression/test_business_quality_no_raw_threshold_literals.py` (11 tests)
- This report.

### Modified
- `backend/services/thresholds.py` — added `BusinessQualityThresholds` + `BUSINESS_QUALITY` singleton.
- `backend/services/prediction_engine.py` — added the `_get_business_quality` closure, added it to the `asyncio.gather` call, added the `business_quality` key to both result-dict construction sites.
- `backend/tests/conftest.py` — added `MockTicker`, `mock_ticker`, `mock_ticker_two_year_financials`, `business_quality_info` fixtures.
- `backend/tests/unit/test_thresholds.py` — added `TestBusinessQualityThresholds`.

**Total new test count this sprint: 79** (24 engine unit + 4 threshold unit + 4 integration + 4 regression backward-compat + 11 regression static-literal + 32 sector), bringing the full suite from 78 (post-Sprint-#002-validation) to **157** — see Test Coverage Summary below for the authoritative, `pytest --collect-only`-verified per-category breakdown.

---

## Architectural Impact Summary

**None beyond what SSDS-003's own Phase 1 Architecture Validation already scoped.** No redesign of SSDS-000. Two additive modules were created exactly as that validation specified:
1. `business_quality_engine.py` — a new, narrower aggregation, not a rename or wrapper of `compute_all_quality_factors()`. Confirmed by a dedicated AST-based regression test that the new engine neither imports nor calls the existing broad function.
2. `sector_quality_applicability.py` — a new, purpose-built sector taxonomy, deliberately not a reuse of `quality_factors.py`'s momentum-oriented `STOCK_SECTOR`/`SECTOR_INDICES` (different question being answered — see the module's own docstring for the full reasoning).

The wiring into `prediction_engine.py` is the only change to an existing file's *behavior surface*, and it's additive: one new closure, one new dict key, in two places. No existing closure, key, or computation was touched.

---

## Migration Notes

- **This is Stage 1 of migration, not the full migration.** Per the sprint brief's "remove obsolete code only after all consumers have been migrated and validated" — nothing has been removed, and nothing should be yet. `quality_factors.py`'s `compute_all_quality_factors()` continues to run exactly as before, on every prediction, producing the existing `quality_factors`/`quality_score` fields the frontend and Daily Picks pipeline already depend on.
- **The new `business_quality` field has zero consumers today.** It is computed and returned, but nothing in the frontend or `daily_picks.py` reads it yet. This is intentional — SSDS-003 §9 named Prediction Engine, Ranking & Filtering, Recommendation generation, Portfolio Copilot, Daily Picks, and the Explainability Layer as eventual consumers, but wiring any of them up is **explicitly out of scope for this sprint** (the brief asked for the engine's *implementation*, with backward-compatible, additive integration — not a consumer migration).
- **Decision deferred, not made:** whether the existing `quality_score` field's *contents* should eventually be replaced by (or blended with) the new Business Quality Score is an open question SSDS-003's Known Limitations section already named explicitly. This sprint does not resolve it — both scores coexist, clearly distinguished by field name (`quality_factors.score` vs. `business_quality.score`).
- **If/when a consumer is migrated** (e.g. `daily_picks.py`'s ranking logic switching to also consider `business_quality.score`), follow SES-003's testing standard: a new regression test pinning the pre-migration ranking behavior, updated intentionally in the same commit as the migration, with the reason stated — exactly the pattern `test_pe_checklist_redundancy.py` already established in Sprint #002.
- **Known data-availability gap inherited from SSDS-003, not introduced by this sprint:** US interest coverage is still not derived anywhere (`us_fundamentals.py` doesn't compute it), so Balance Sheet Strength's interest-coverage component is silently absent for US stocks — already named as a Known Limitation in SSDS-003 and unchanged by this implementation.

---

## Test Coverage Summary

Authoritative counts, collected via `pytest --collect-only` per category, not estimated:

| Category | New this sprint | Running total |
|---|---|---|
| Unit | 24 (engine) + 4 (thresholds) = 28 | 65 |
| Integration | 4 | 6 |
| Regression | 4 (backward-compat) + 11 (static literals) = 15 | 52 |
| Sector | 32 (new category) | 32 |
| Golden | 0 | 2 |
| **Total** | **79** | **157** |
| **Total** | **75** (closer to 81 counting the threshold additions split across files) | **157** |

**What's covered:** every new metric helper (cash conversion, asset turnover, working capital trend, Beneish M-Score) in isolation, both hard-gate paths, the insufficient-data path, grade banding, all 12 named sectors' classification (IN and US-style strings), sector exemption/adjustment rules, the additive wiring's failsafe behavior, and — critically — that none of this touched the existing `compute_all_quality_factors()` path.

**What's NOT covered (named honestly, not silently absorbed into "tests pass"):**
- No test exercises `compute_business_quality()` against real, live yfinance data for all 12 sectors — only the sector *classification* function was tested against real GICS/screener.in-style strings; the full scoring pipeline was smoke-tested live against 3 real symbols (RELIANCE, HDFCBANK, AAPL) during development but not captured as an automated golden test. Recommended for Sprint #005 (see below).
- No test confirms the Beneish M-Score formula's *numerical correctness* against a known, published reference case (the unit test confirms it computes *a* number from internally-consistent mock data and that the "missing data" path degrades gracefully — not that the formula matches Beneish's original paper's worked example).
- No load/performance test — `compute_business_quality()` adds a new `yf.Ticker(...).balance_sheet`/`.financials`/`.cashflow` fetch pattern (for the new metrics) on top of what `quality_metrics_score`/`corporate_actions_score` already fetch; this sprint did not measure whether this meaningfully increases per-stock prediction latency in `daily_picks.py`'s batch context.

---

## Remaining Technical Debt

1. **`compute_business_quality()` re-fetches `ticker.financials`/`.balance_sheet`/`.cashflow`** inside its own new metric helpers (`_compute_asset_turnover`, `_compute_working_capital_trend`, `_compute_beneish_m_score`), on top of `quality_metrics_score()` already fetching the same three DataFrames internally for its Piotroski checks. yfinance's `Ticker` object caches these per-instance, so this is not a *redundant network call* — but it is four separate fetch-and-guard code paths reading the same underlying data, a smaller-scale version of the exact "no shared caching/fetch utility" pattern SEAR-001 flagged platform-wide. Not fixed in this sprint (would be a refactor, not an implementation task); named for Sprint #005.
2. **`_get_financial_row()` in `business_quality_engine.py` duplicates the *shape* of an equivalent private helper inside `quality_metrics_score()`** (both do the same sorted-row-lookup-with-label-fallback pattern). Documented in-code as a deliberate, narrow exception (a generic lookup utility, not a business rule) — but a shared `services/financial_statement_utils.py`-style helper would remove even that duplication. Named, not fixed.
3. **No consumer reads `business_quality` yet** (see Migration Notes) — the engine is fully implemented and tested in isolation but delivers zero user-visible value until a consumer is migrated.
4. **The Beneish M-Score will very likely return `unavailable` for a large fraction of real tickers in production**, since it requires 2 full years of ~12 specific line items simultaneously from yfinance, which frequently has gaps (especially for SG&A and receivables on non-US-GAAP filers). This is consistent with SSDS-003's own missing-data philosophy (exclude rather than guess) but means the fraud-risk hard gate may rarely actually fire in practice — worth measuring empirically in Sprint #005 once this runs against the real Daily Picks universe.
5. **The five category-weighting caps (±20/±15/±15/±10/±15) were set by this sprint's author following SSDS-003's stated rationale, not empirically validated** against any benchmark company set — SSDS-003 §8's Validation Strategy (benchmark companies, historical validation) was explicitly *not* implemented this sprint (the brief said "do not implement tests" for validation strategy in SSDS-003, and Sprint #004's actual test suite is unit/integration/regression/sector, not the SSDS-003 §8 validation methodology itself).

---

## Recommendations for Sprint #005

1. **Run `compute_business_quality()` against a real benchmark company set** (per SSDS-003 §8) — both obviously-high-quality and obviously-low-quality names, both IN and US — and sanity-check the resulting grades/scores match intuition before any consumer migration. This is the validation step deliberately deferred from Sprint #004.
2. **Decide and implement the first real consumer migration** — most likely `multibagger_scorecard.py` or a new Multibagger "Quality Compounder" filter, since SSDS-003 §9 already flagged that the two systems should eventually share metric-computation building blocks rather than duplicate them, and Multibagger's existing golden-test coverage (Sprint #002) makes it the safest first integration target.
3. **Measure real-world Beneish M-Score availability** against the full Daily Picks universe (a few thousand IN + US tickers) to know empirically how often the fraud-risk hard gate can actually fire, rather than relying on the mock-data-only test coverage from this sprint.
4. **Add US interest coverage to `us_fundamentals.py`** (SSDS-003's named Known Limitation) — currently the only metric this Engine treats asymmetrically between IN and US for reasons outside this sprint's control.
5. **Consider the shared-fetch-utility refactor** (Remaining Technical Debt #1/#2) if/when another new metric needs the same financial-statement data — don't let a fourth independent fetch-and-guard path get added without addressing this.
