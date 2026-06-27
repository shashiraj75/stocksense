# Sprint #004a — Business Quality Engine Calibration: Sprint Report

**Scope:** the two fixes recommended by the Production Readiness Validation's Phase 9 (B1, B2), implemented exactly as scoped — no broader redesign. Both fixes re-validated against the same 53-company live dataset used to discover the original defects, with before/after numbers, not just claims.

---

## Fixes Implemented

### Fix 1 (Recommendation B1): Altman Z-Score / Sloan Accruals `totalAssets` fallback

**Root cause (confirmed in the validation):** `altman_zscore_signal()` and `sloan_accruals_signal()` both gated on `info.get("totalAssets")`, a field yfinance's `.info` dict never populates for any ticker, in either market. Both functions silently returned "unavailable" for every real company tested.

**Fix:** added `_total_assets_fallback(ticker)` to `services/quality_factors.py` — reads the same `ticker.balance_sheet` DataFrame already fetched successfully elsewhere in the file (`quality_metrics_score`, `corporate_actions_score`, and the Business Quality Engine's own new metrics already used it). Both functions gained an optional `ticker=None` parameter; when supplied and `info` lacks `totalAssets`, the fallback is used. **Fully backward compatible** — any existing caller that doesn't pass `ticker` gets byte-identical behavior to before.

**Call sites updated to pass `ticker` (already had it in scope, just not threading it through):**
- `quality_factors.py`'s own `compute_all_quality_factors()` — so the existing, broader quality blend benefits too, not just the new engine.
- `prediction_engine.py`'s `_deep_fundamental_score()`.
- `business_quality_engine.py`'s `compute_business_quality()`.

### Fix 2 (Recommendation B2): Piotroski F-Score financial-sector discount

**Root cause (confirmed in the validation):** the Piotroski F-Score (reused via `quality_metrics_score`, weighted at `cap=12`, the largest single input to Profitability & Capital Efficiency) has no sector-awareness — several of its 9 sub-checks (declining leverage, improving asset turnover, improving gross margin) are inapplicable or backwards for a balance-sheet-driven business model.

**Fix:** added `BUSINESS_QUALITY.PIOTROSKI_FINANCIAL_SECTOR_WEIGHT = 0.5` to `services/thresholds.py`. `business_quality_engine.py`'s profitability calculation now applies this 0.5× weight to the Piotroski contribution specifically for the `FINANCIAL` sector bucket — a discount, not a full exemption (per the validation's explicit "do not weaken the working exemption mechanism" guidance, and because some Piotroski sub-checks — ROA-improving, cash-vs-accrual-earnings — remain meaningful for a bank). `quality_metrics_score()` itself was **not modified** — the discount is applied only at the point of use inside `business_quality_engine.py`, so its other, unrelated consumer (`compute_all_quality_factors()`) is unaffected.

---

## Re-Validation: Before vs. After (same 53-company live dataset)

| Metric | Before | After |
|---|---|---|
| Altman Z-Score computed (of non-rejected companies) | **0/46 (0%)** | **48/48 (100%)** |
| Sloan Accruals Ratio computed | **0/46 (0%)** | **48/48 (100%)** |
| "Quality Compounder" style ever assigned | **0/46** | **12/48 (25%)** |
| Total rejected (insufficient_data + hard gate) | 7 | 5 |
| Hard quality gate ever fired | **0/55, ever** | **4/55** (IDEA, HON, ORCL, LCID) |
| YESBANK vs. HDFCBANK score gap | 0 (55 = 55, identical) | **2 points** (50 vs. 52) |
| YESBANK vs. ICICIBANK score gap | 0 (55 = 55, identical) | **6 points** (50 vs. 56) |
| Companies previously insufficient_data-rejected, now scored | — | SIEMENS, INTC, PTON, RIVN all now produce real scores (RIVN: 43/watch) |

**Top 10 scorers, post-fix** — 6 of 10 now correctly carry "Quality Compounder": BRITANNIA (83), GOOGL (82), MSFT (82), AAPL (81), ASIANPAINT (81), INFY (81), COST (80), META (80, Quality Compounder), LLY (79), ADBE (78).

**Bottom 10, post-fix:** RIVN (43, now scored instead of omitted), BAJAJFINSV (48), BAJFINANCE/YESBANK (50), HDFCBANK (52), JPM/GS (53), KOTAKBANK (55), ICICIBANK/PAYTM (56).

---

## New Finding Surfaced By This Fix (not silently fixed — reported, per scope discipline)

Fixing Recommendation B1 made Altman Z-Score computable for financial-sector companies for the first time — and this exposed a **third, previously-invisible issue**: Altman Z-Score has no `is_financial`-aware exemption *of its own* inside `business_quality_engine.py` (only the D/E and interest-coverage components were ever exempted for the `FINANCIAL` bucket; Altman's contribution to Balance Sheet Strength runs unconditionally). Concretely:

- **BAJAJFINSV** now shows `altman_zone: "distress"` (Z=0.82) and **BAJFINANCE** shows `"grey"` (Z=1.75) with elevated accruals (15.1%) — both widely-regarded financial compounders, both naturally leveraged as part of their normal NBFC business model, not because they are actually distressed. The hard gate's AND-condition correctly did *not* reject either of them (neither fully meets both the distress-zone AND aggressive-accruals conditions simultaneously) — but their Balance Sheet Strength contribution is being penalized for capital structure that is normal for their business model, the same conceptual problem Fix 2 addressed for Piotroski, just not yet addressed for Altman.
- **HON (Honeywell) and ORCL (Oracle)** — two large, well-regarded, non-financial companies — were hard-rejected (`distress_and_aggressive_accruals`) in this re-validation. Both are known for substantial debt-funded share buybacks, a capital structure pattern that can make a mature, healthy large-cap look "distressed" by Altman's traditional formula (which is sensitive to the market-cap/book-equity ratio) without reflecting genuine business risk. **This is a credible new false positive**, not yet investigated to a root cause the way Findings A/B were.

**This was not fixed in this sprint** — Recommendation B1/B2 were the two authorized fixes; extending the financial-sector exemption to Altman, or investigating the HON/ORCL false positive, would be a third and fourth change beyond what was scoped as "two fixes, tightly scoped calibration." Both are named here as explicit follow-up items, not silently absorbed or silently left undocumented.

---

## Test Coverage

10 new tests added, all passing:
- `tests/unit/test_quality_factors_calibration_fix.py` (7 tests) — `_total_assets_fallback` in isolation, backward-compatibility (no-ticker-supplied behavior unchanged), and the fallback actually computing real values for both `altman_zscore_signal` and `sloan_accruals_signal`.
- `tests/unit/test_business_quality_engine.py` (+3 tests, `TestPiotroskiFinancialSectorDiscount`) — confirms the discount is applied (not a full exemption) and reduces the Piotroski penalty's magnitude for the `FINANCIAL` bucket specifically.
- 2 existing tests updated: monkeypatched lambdas in the hard-gate tests needed an additional `ticker=None` parameter to match the new function signatures (mechanical, not a behavior change to the tests' intent).
- `tests/conftest.py`'s `business_quality_info` fixture extended with `marketCap`/`ebit`/`retainedEarnings` and a consistent `operatingCashflow` (was inconsistent with `netIncome` before — never mattered until Altman/Sloan could actually compute from both).

**Full suite: 167/167 passing** (up from 157 before this sprint). Full app import (`from api.main import app`) confirmed clean after both fixes.

---

## Files Changed

| File | Change |
|---|---|
| `backend/services/quality_factors.py` | Added `_total_assets_fallback()`; added optional `ticker` parameter to `altman_zscore_signal()` and `sloan_accruals_signal()`; wired the fallback into both; updated `compute_all_quality_factors()`'s call sites to pass `ticker`. |
| `backend/services/business_quality_engine.py` | Applied `PIOTROSKI_FINANCIAL_SECTOR_WEIGHT` discount to the Profitability category's Piotroski contribution; passed `ticker` to the now-ticker-aware `altman_zscore_signal`/`sloan_accruals_signal` calls. |
| `backend/services/prediction_engine.py` | Passed `ticker` to `altman_zscore_signal`/`sloan_accruals_signal` in `_deep_fundamental_score()` (same backward-compatible signature change, now benefiting from the fallback too). |
| `backend/services/thresholds.py` | Added `BUSINESS_QUALITY.PIOTROSKI_FINANCIAL_SECTOR_WEIGHT = 0.5`, justified in-code with the live-data evidence. |
| `backend/tests/conftest.py` | Extended `business_quality_info` fixture for internal consistency now that Altman/Sloan compute real values from it. |
| New: `backend/tests/unit/test_quality_factors_calibration_fix.py` | 7 tests. |
| Modified: `backend/tests/unit/test_business_quality_engine.py` | +3 tests, 2 existing tests' monkeypatch signatures updated. |
| New: this report. |

---

## Recommendations for the Next Calibration Pass

1. **Investigate the Altman financial-sector exemption gap and the HON/ORCL false positive**, named above — both are new, real findings from this fix, not yet root-caused to the same depth as Findings A/B.
2. **Re-run the full Phase 1–9 validation** (not just this targeted before/after) once the above is addressed, to confirm no further regressions before any production consumer integration is reconsidered.
3. The original validation's Recommendation B3 (recalibrate `MIN_DATA_COMPLETENESS_PCT`'s denominator) and B4 (US interest coverage) remain open — not addressed in this sprint, which was deliberately scoped to B1/B2 only.

**Verdict on this sprint's own scope:** both authorized fixes are implemented, tested, and confirmed via live re-validation to resolve the issues they were meant to resolve (Altman/Accruals availability: 0%→100%; Quality Compounder firing: 0→25%; YESBANK/HDFCBANK convergence: resolved). One new, real finding was surfaced and is reported, not fixed, consistent with "tightly scoped calibration, not broad redesign."
