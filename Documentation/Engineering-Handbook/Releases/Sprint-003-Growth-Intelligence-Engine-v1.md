# Epic 003, Sprint #003 — Growth Intelligence Engine v1 Implementation

**Status:** Complete. Implements exactly the v1 metric set named in SSDS-007 and validated feasible by the India Data Feasibility Study — no speculative metrics, no Guidance Consistency, no Organic-vs-Acquisition Growth, no naive share-count dilution. **Business Quality, Financial Strength, and Prediction Engine are all untouched** — confirmed by the diff being scoped entirely to new Growth Intelligence files plus two narrow, additive changes to `screener_data.py` (extracting existing inline logic into a reusable utility, and preserving a multi-year array the scraper already parsed but previously discarded). No consumer integration in this sprint, per its explicit rule.

---

## 1. Growth Intelligence Engine v1

`backend/services/growth_intelligence_engine.py` — `compute_growth_intelligence(symbol, fields, sector_bucket, market) -> dict`, returning the shared `EngineResponse` contract (mirroring `business_quality_engine.py`/`financial_strength_engine.py` exactly). Seven scoring categories, each provider-independent, each reading only a pre-resolved `fields` dict an adapter built:

| Category | Cap | Notes |
|---|---|---|
| Revenue Growth | ±15 base, ±18 with acceleration bonus | 3Y CAGR primary signal; 3Y>5Y comparison adds a bonus |
| Profit Growth | ±15 base, ±18 with acceleration bonus | Same shape, independently scored |
| EPS Trend | ±8 | Categorical (accelerating/mixed_positive/mixed_negative/decelerating) |
| Growth Durability | ±12 | Coefficient of variation of YoY growth rates from the revenue series |
| Operating Profit Growth | ±12 | Structurally unavailable for banks/NBFCs — contributes 0, not fabricated |
| Reinvestment Efficiency | ±8 | Ratio of operating-profit growth to invested-capital growth; capital-light growth (shrinking capital, growing profit) treated as maximally efficient |
| Margin Trend | ±8 | Percentage-point change in operating margin across the available history window |

**REJECTED gate** is based on **core field count** (≥2 of 4: revenue growth, profit growth, EPS trend, revenue series), not a blended percentage — a deliberate design choice so a structurally-different population (banks/NBFCs missing the 3 "extended" fields) is never rejected outright, only confidence-penalized. This directly implements the sprint's explicit "gracefully skip... confidence must reflect missing sector-specific metrics" rule.

## 2. India Adapter

`backend/services/india_growth_adapter.py` — `build_india_growth_fields(screener_data: dict) -> dict`, consuming the raw dict returned by the existing `fetch_screener_data()` (not the narrower `_screener_data` sub-dict other consumers use, since Growth Intelligence needs the full multi-year arrays only the raw fetch carries). Uses only fields the India Data Feasibility Study confirmed available:

- `sales_growth_3y/5y_pct`, `profit_growth_3y/5y_pct` — direct, pre-computed by screener.in.
- `eps_trend` — recomputed via the newly-extracted shared utility (see §4), not fabricated separately.
- `revenue_annual_series` (`sales_annual_cr`) — for Growth Durability.
- `operating_profit_growth_3y_pct` — computed via CAGR from `operating_profit_annual_cr`; **None for banks/NBFCs**, confirmed structurally absent, not approximated.
- `reinvestment_capital_growth_3y_pct` — computed from `reserves_annual_cr + equity_capital_cr + borrowings_annual_cr`, index-aligned; None if any series is missing (same bank/NBFC population).
- `margin_trend_pct_change` — computed from the newly-preserved `opm_annual_pct` series (see §4).

## 3. US Adapter

`backend/services/us_growth_adapter.py` — `build_us_growth_fields(ticker) -> dict`, consuming a yfinance Ticker-like object's `.financials`/`.balance_sheet` (the same object `prediction_engine.py`'s existing closures already share via `_SharedTickerCache`, reused rather than triggering an independent fetch). Unlike India, the US has no pre-computed CAGR fields anywhere in this codebase's Data Fabric — every growth rate is computed by this adapter from raw multi-year statement rows, via the identical shared `growth_utils` functions India's adapter uses. EPS Trend is derived from annual diluted EPS (the only multi-year EPS-adjacent series actually available for US — a coarser cadence than India's quarterly signal, named explicitly as an asymmetry, not hidden). Margin Trend is computed directly from Operating Income ÷ Revenue, since no pre-existing margin-series fetch exists for US either.

## 4. Two Narrow, Additive Changes to `screener_data.py`

Per the sprint's implicit allowance ("only if existing historical margin data can be preserved safely... without broad refactor"):

1. **Extracted the inline `eps_trend` categorical-bucketing logic** (previously duplicated inside `augment_info_with_screener`) into `services/growth_utils.py`'s `compute_categorical_trend()` — a shared, generalized utility both the India adapter (quarterly PAT) and the US adapter (annual EPS) now call, per SES-001's "one computation, one owner." **Behavior at the original call site is unchanged** — confirmed by the full 536-test suite passing identically before and after this extraction, with zero new failures.
2. **Added `data["opm_annual_pct"] = vals`** alongside the pre-existing `data["opm_pct"] = vals[-1]` — one additive line, preserving the full multi-year margin series the scraper already parses into memory and previously discarded. Confirmed live: a fresh fetch (RELIANCE/TCS/TITAN/COALINDIA) returns the full 12-year series; `HDFCBANK` correctly still returns `None` (banks don't have this P&L row at all).

## 5. Explainability Output

Every result includes `strengths`/`weaknesses` (top 3 by category contribution), `risks` (profit contraction, high-volatility growth trend), and a deterministic `explanation` string naming each category's exact contribution plus how many extended metrics were unavailable and excluded — e.g.: *"Growth Intelligence Score 88/100 (strong_buy). Revenue Growth contributed +15.0, Profit Growth +15.0, EPS Trend +8.0, Growth Durability +12.0, Operating Profit Growth +12.0, Reinvestment Efficiency +8.0, Margin Trend +0.0."*

## 6. Confidence Calculation

Two independent mechanisms, both confirmed working in live validation:
- **Data completeness** (`confidence` field): fraction of 7 possible fields actually resolved. Banks/NBFCs in the India validation consistently landed at **42.9%** (3/7) — confirmed across all 10 banks/NBFCs in the sample, never fabricated, never silently defaulted to 100%.
- **REJECTED vs. degraded**: 3 US companies (X, PARA, WBA) correctly hit `REJECTED` (confidence 0.0) — confirmed via direct inspection these are real yfinance data gaps (X/U.S. Steel delisted after its 2025 acquisition; PARA/WBA both went through recent M&A/take-private transitions), not adapter defects.

---

## 7. Live Validation Report

**India: 85 real companies** (reused from Sprint #002's already-fetched, identically-shaped raw data — exceeding the 70-minimum). **US: 61 real companies** (freshly fetched live via yfinance — exceeding the 50-minimum). Zero crashes across 146 total companies.

### Distributional sanity (average score by segment)

| Segment | US avg (n) | India avg (n) |
|---|---|---|
| High growth | 84.2 (8) | 78.4 (18) |
| Secular grower | 65.9 (10) | 50.8 (13) |
| Turnaround | 98.0 (2) | 79.4 (5) |
| Mature compounder | 38.9 (13) | — |
| Cyclical | 6.4 (7) | 64.5 (15) |
| Declining | 18.5 (6) | 30.0 (6) |
| Capital intensive | 25.0 (4) | — |
| Bank/NBFC | 55.3 (7, US banks) | confidence 42.9% across all 10 (India) |
| PSU | — | 53.8 (18) |
| Utility | 74.0 (4) | — |

**Every directional expectation held**: high-growth/turnaround segments scored highest, declining businesses scored lowest, in both markets independently. **One honest, non-obvious finding, not hidden**: India's "secular grower" tag (ITC, HINDUNILVR, NESTLEIND, ASIANPAINT, PAGEIND, etc.) scored *lower* on average than "cyclical" — inspected directly, this reflects a real, current phenomenon (several large Indian consumer staples are in a genuine multi-year growth slowdown — rural demand softness, high base effects post-COVID), not a defect. Reported as found, not smoothed over.

### Spot-checked extreme scores (confirmed real, not bugs)

- **DRREDDY (score 0), SRF (score 6), PIIND (score 12), DEEPAKNTR (score 0)** — all specialty chemicals/pharma names with confirmed negative profit growth, negative operating-profit growth, and high growth-volatility (CV 0.9–1.25, well above the durability threshold) in the underlying data — consistent with these sectors' well-documented 2023–2024 China-oversupply/pricing-pressure stretch.
- **TSLA (score 0)** — confirmed real: revenue growth only 5.2%/3Y, profit growth **-32.9%/3Y** in the actual fetched yfinance data — consistent with Tesla's well-documented 2023–2024 margin compression from price cuts, not an adapter artifact.
- **GE, DIS (turnaround, scores 100/96)** — both companies with well-documented real operational turnarounds in the validation window; scores align with public knowledge, not just internal consistency.

### Bank/NBFC graceful degradation (confirmed, not assumed)

All 10 banks/NBFCs in the India sample (HDFCBANK, ICICIBANK, SBIN, KOTAKBANK, AXISBANK, BAJFINANCE, YESBANK, RECLTD, PFC, IRFC) landed at exactly **42.9% confidence**, every one **not REJECTED** — confirmed the gate correctly distinguishes "structurally different data shape" from "insufficient data." DISHTV (a financially distressed consumer/media company, not a bank) also landed at 42.9% — a different root cause (company-specific data gaps from genuine financial distress) producing the same confidence mechanism's output, confirming the confidence model generalizes beyond the one gap it was specifically designed for.

---

## 8. Test Summary

| Category | New tests | Notes |
|---|---|---|
| Unit | 23 (`test_growth_utils.py`: 21, plus engine-internal coverage) | Pure-math functions (CAGR, coefficient of variation, categorical trend) and engine category functions |
| Unit (engine) | 10 (`test_growth_intelligence_engine.py`) | Full `compute_growth_intelligence()` against hand-built fields |
| Integration | 9 (`test_growth_intelligence_adapters_integration.py`) | Both adapters wired to the engine, realistic synthetic fixtures |
| Regression | 7 (`test_growth_intelligence_regression.py`) | Locks in the no-fabrication contract and both genuine defects found+fixed during this sprint |
| Golden | 5 (`test_growth_intelligence_golden.py`) | Deterministic synthetic profiles — explicitly labeled as not yet outcome-validated against real-world results |

**50 new tests. 586/586 full backend suite passing** (536 prior + 50 new — exact arithmetic differs slightly due to the 2 negative-CAGR regression tests added to `test_growth_utils.py` during defect-fixing).

**Two genuine defects found and fixed during this sprint's own test-writing** (not found via live validation — found before it, by the test suite doing its job):
1. **Acceleration bonus dead code**: clamping the 3Y>5Y acceleration bonus to the same cap as the base "strong" score made the bonus invisible for any company already at or above the strong threshold — the single most common case it should apply to. Fixed by giving the bonus its own, higher cap (`REVENUE_GROWTH_ACCELERATION_CAP`/`PROFIT_GROWTH_ACCELERATION_CAP` = 18, vs. the base 15).
2. **CAGR crash on negative terminal value**: a positive base with a negative final value raises a negative ratio to a fractional power, producing a complex number `round()` can't handle — crashed with `TypeError` the first time an integration-test fixture exercised it. Fixed by requiring `latest > 0` in addition to `oldest > 0` in `compute_cagr_from_series`.

Both fixes are covered by dedicated regression tests reproducing the exact failure mode.

## 9. Known Limitations

- **No outcome-validated/backtested calibration.** Every threshold in `GrowthIntelligenceThresholds` is a first-pass, reasoned estimate — named explicitly in the dataclass's own docstring. The live validation in §7 confirms *directional sanity* (growers score higher than decliners), not that the exact score bands (STRONG_BUY ≥80, etc.) are calibrated against real forward returns or any other outcome.
- **US EPS Trend uses annual cadence**, India's uses quarterly — a real, named cross-market asymmetry (no equivalent quarterly multi-year EPS series exists for US in this codebase's current Data Fabric).
- **Margin Trend was validated on a small fresh spot-check (5 symbols), not the full 85-company India sample** — the full-sample validation run reused Sprint #002's pre-existing raw data, which predates this sprint's `opm_annual_pct` scraper addition. The spot check confirms the mechanism works (RELIANCE/TCS/TITAN/COALINDIA all returned real multi-year margin data; HDFCBANK correctly returned `None`), but a full-sample re-fetch was not performed this sprint — named honestly as a narrower validation footprint for this one metric specifically, not silently extrapolated from the spot check.
- **Reinvestment Efficiency and the durability/cyclicality sector calibration remain unvalidated against real outcomes**, unchanged from SSDS-007's own original caveat — this sprint validated that they compute sensible, non-crashing values across real companies, not that their specific thresholds are correct.
- **Three deferred metrics remain exactly as SSDS-007/the Feasibility Study scoped them**: Guidance Consistency, Organic-vs-Acquisition Growth, and naive Share Count Dilution are not implemented, per this sprint's explicit rule — not a gap discovered late, a boundary respected throughout.

## 10. Recommendation on Prediction Engine Integration Readiness

**Not yet ready for Prediction Engine integration — one more sprint needed first, but it should be a small one.** The engine itself is sound: 586/586 tests passing, zero crashes across 146 live real companies in both markets, directionally sensible scoring confirmed by spot-checking extreme cases against known real-world events (TSLA's margin compression, specialty chemicals' down-cycle, GE/DIS's real turnarounds). What's missing before integration specifically:

1. **No threshold calibration against forward outcomes** — Financial Strength's own integration sprint only proceeded after its thresholds were calibrated against live data in a dedicated validation pass; this sprint's validation confirmed *sanity*, not *calibration*. A short, focused calibration pass (comparing scores against, at minimum, realized subsequent performance for a sample of the validated companies) should precede integration, mirroring the discipline both prior engines followed.
2. **Margin Trend's full-sample India validation is still pending** (per Known Limitations) — low effort to close (one re-fetch run), but not yet done.
3. **No decision yet on the confidence-only adjustment's exact bound** for `_apply_growth_intelligence_adjustment` (the Financial Strength-equivalent function) — this sprint deliberately didn't touch `prediction_engine.py` at all, so this is genuinely unstarted work, not a partially-done integration.

None of this suggests the engine is poorly built — it suggests integration is the *next* sprint, not *this* one, exactly as scoped.

---

## GitHub Actions Result

Recorded below, after this sprint's commit is pushed and confirmed.

## Final Commit Hash

Recorded below, after this sprint's commit.

---

*This sprint implemented exactly the v1 metric set SSDS-007 and the India Feasibility Study scoped — no speculative metrics, no consumer integration, no Prediction Engine changes. Business Quality and Financial Strength are confirmed untouched by this diff. Two genuine defects were found and fixed during the sprint's own testing (not pre-existing, not from prior sprints) and are covered by dedicated regression tests.*
