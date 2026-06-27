# Sprint #007 — India Business Quality Adapter

**Status:** Complete. Implementation sprint. Enables the StockSense360 Business Quality Engine (SSDS-003) to operate on Indian companies through the existing, Sprint #006-validated screener.in data pipeline — no new data provider introduced.

**Governing inputs:** SSDS-003 (Business Quality Engine), SSDS-004 (India Fundamentals Data Strategy), the [India Fundamentals Data Validation & Derivation Study](../Architecture/India-Fundamentals-Data-Validation-Study.md), and the Sprint #006 findings.

---

## Implementation Summary

The India adapter mirrors the US adapter pattern established in Sprint #004/#005 (`us_fundamentals.py`'s `_build()`): a thin transformation layer that maps provider data into the Business Quality Engine's existing input model and calls the **unmodified** `compute_business_quality()`. It contains zero engine scoring logic of its own.

**New module:** [`backend/services/india_business_quality_adapter.py`](../../../backend/services/india_business_quality_adapter.py)
- `build_india_info(screener)` — maps screener.in fields into the yfinance-`.info`-shaped dict the engine reads, applying the Sprint #006-validated derivations. Every field is tagged in-code with its provenance: `[DIRECT]`, `[DERIVED/PROVEN]`, `[DERIVED/SUPPORTED]`, or `[UNAVAILABLE]`.
- `compute_india_business_quality(symbol, screener, market="IN")` — the single entry point. Takes the already-fetched screener dict (no re-fetch), constructs a lazy yfinance `Ticker` (for the engine's own Piotroski/Asset-Turnover `ticker.balance_sheet` access), passes an empty `df` (confirmed in Sprint #004/#005 that the engine's `df`-consuming functions don't use it), and returns the standard `EngineResponse`. Returns `None` (never a guessed result) if screener data is unavailable.

**Cache integration:** [`fundamentals_refresh.py`](../../../backend/services/fundamentals_refresh.py) (the IN nightly job) now calls the adapter on the screener data it already fetched, injecting `business_quality_score`/`_grade`/`_style`/`_confidence` into the row before upsert — at refresh time, exactly like the US side, so request-time latency is unchanged. [`fundamentals_cache.py`](../../../backend/services/fundamentals_cache.py) gained a `business_quality_confidence` column (additive `ADD COLUMN IF NOT EXISTS`, surfaced for both markets) wired through `FIELD_MAP` and `_SELECT_COLS`.

### Data Mapping (Phase 3)

| Engine input | Provenance | Source / derivation |
|---|---|---|
| sector, industry | `[DIRECT]` | `sector_name`, `industry_name` |
| returnOnEquity, returnOnCapitalEmployed | `[DIRECT]` | `roe_pct`/`roce_pct` ÷ 100 |
| debtToEquity, trailingPE, revenueGrowth | `[DIRECT]` | `debt_to_equity_pct`, `pe_ratio`, `sales_growth_ttm/3y_pct` |
| marketCap, totalRevenue, totalDebt | `[DIRECT]` | `market_cap_cr`, `sales_latest_cr`, `borrowings_latest_cr` × 1e7 (Crore→Rupees) |
| operatingCashflow | `[DIRECT]` | `operating_cf_latest_cr` × 1e7 |
| netIncome | `[DIRECT]` | Σ(last 4 `quarterly_pat_cr`) × 1e7 (TTM) |
| **totalAssets** | **`[DERIVED/PROVEN]`** | latest `total_liabilities_annual_cr` (Assets = Liabilities + Equity; 97% cross-check match in Sprint #006) |
| **ebit / operatingIncome** | `[DERIVED/SUPPORTED]` | `operating_profit_latest_cr` (screener's own "EBIT proxy") |
| **retainedEarnings** | `[DERIVED/SUPPORTED]` | `reserves_latest_cr` (Reserves & Surplus) |
| workingCapital, currentAssets/Liabilities | `[UNAVAILABLE]` | not scraped — left absent, never fabricated |
| Beneish Receivables / SG&A | `[UNAVAILABLE]` | confirmed total gap; out of scope this sprint |

### Confidence Handling (Phase 4)

Confidence is the engine's own data-completeness signal, unchanged. Because the adapter supplies the proven/supported derivations, confidence is **not** reduced where a validated derivation is used — it is reduced only where data genuinely cannot be obtained (e.g. Beneish). Live result: uniform **91.7%** across all 65 validated companies (the missing ~8.3% is precisely the one absent metric, Beneish).

### A genuine defect, discovered and fixed (in scope under the "unless a defect is discovered" rule)

Live adapter data surfaced a real classification gap in [`sector_quality_applicability.py`](../../../backend/services/sector_quality_applicability.py): screener.in's actual Indian sector/industry labels weren't all recognized by the engine's keyword classifier, so some real Indian companies silently fell through to the `OTHER` bucket and lost their sector-aware metric treatment. Two additive keyword fixes (no existing match changed, no redesign):
- **FMCG:** added `fast moving consumer goods` and `packaged foods` — without them, NESTLEIND and BRITANNIA classified as `OTHER`.
- **UTILITIES_ENERGY:** broadened `power generation` to bare `power` — without it, POWERGRID (`"Power - Transmission"`) classified as `OTHER` while NTPC/TATAPOWER did not.

Confirmed by re-running the full 65-company universe: all 5 FMCG names and POWERGRID now classify correctly; existing US classification and all 44 prior sector tests still pass. The remaining 6 `OTHER` companies (LT, ASIANPAINT, TITAN, PAGEIND, TRENT, KAJARIACER) are genuinely consumer-discretionary/conglomerate names with no matching bucket in the SSDS-003 taxonomy — correctly `OTHER`, not defects, and deliberately not force-fit.

---

## Validation Summary (Phase 7)

Re-ran the adapter across the **same 65-company validated India universe** from Sprint #006, through the real production path (`fetch_screener_data` → adapter → real engine):

| Metric | Result |
|---|---|
| Companies processed | 65/65 |
| Errors | **0** |
| Hard-gate / insufficient-data rejections | **0** |
| Altman Z-Score available | **65/65 (100%)** |
| Sloan Accruals available | 65/65 (100%) |
| **Cash Conversion available** | **65/65 (100%)** — up from the study's 97%; the adapter's explicit promotion of OCF/Net Income to top-level keys closed the 2-company gap the study had flagged as a wiring observation |
| Asset Turnover available | 51/65 (78%) — concentrates in non-financial names; sector-appropriate, not a regression |
| Beneish M-Score available | 0/65 (0%) — the known, out-of-scope gap |
| Confidence (min/avg/max) | 91.7 / 91.7 / 91.7 — stable |

**No new false positives, no new false negatives:** zero rejections across the universe, and genuine-distress behavior is preserved (YESBANK still resolves to the distress Altman zone). **Stability vs the Sprint #006 study path:** adapter scores track the study within a mean delta of −3.6 points (range −11 to +4, median absolute delta 4); 49/65 grades match exactly, the rest are single-step boundary shifts. The small conservative bias is expected and explainable — the adapter is the *purer* screener-only path and does not lean on raw yfinance `.info` enrichment, which all prior findings showed is unreliable for Indian fundamentals.

---

## Test Coverage Summary (Phase 6)

68 new tests, all passing; **262/262** in the full suite.

- **Unit** (`tests/unit/test_india_business_quality_adapter.py`) — mapping/rescaling, the proven and supported derivations, deliberate omission of unavailable fields (never fabricated), Crore→Rupee conversion, entry-point guards, fail-soft behavior, empty-`df`/no-price-fetch.
- **Integration** (`tests/integration/test_india_adapter_engine_integration.py`) — adapter output driving the real unmodified engine to a valid EngineResponse; the proven derivations making Altman actually compute; a strong FMCG not rejected; wrapper-equals-direct-engine equivalence.
- **Regression** (`tests/regression/test_india_refresh_business_quality_wiring.py`) — the refresh loop still upserts every row when BQ returns None; all four fields injected on success; no double screener fetch; BQ cache columns consistent across `FIELD_MAP`/`_SELECT_COLS`. Plus the updated Sprint #005 cross-check (`test_business_quality_multibagger_integration.py`) reflecting Sprint #007's intentional IN wiring while still asserting no inline `yf.Ticker` in the refresh loop.
- **Golden** (`tests/golden/test_india_adapter_sector_golden.py`) — all 12 requested sectors (Banking, NBFC, Insurance, IT, FMCG, Pharma, Manufacturing, Utilities, Telecom, Energy, Real Estate, Turnaround): valid response, stable sector-bucket classification, Altman computability, and Beneish-absence-never-rejects.

---

## Performance Summary (Phase 8)

- **No request-time latency change** — all BQ computation happens during the nightly cache refresh; the Multibagger screen still serves instantly from Postgres.
- **No extra provider calls** — the adapter reuses the screener dict the refresh loop already fetched (asserted by `test_refresh_does_not_fetch_screener_twice`); the yfinance `Ticker` is constructed lazily and `df` is empty, so no price-history fetch.
- **No duplicate calculations** — one engine call per symbol per refresh.

---

## Files Changed Summary

| File | Change |
|---|---|
| `backend/services/india_business_quality_adapter.py` | **New** — the adapter (mapping + entry point). |
| `backend/services/fundamentals_refresh.py` | Wired the adapter into the IN nightly loop (additive; fail-soft). |
| `backend/services/fundamentals_cache.py` | Added `business_quality_confidence` column + FIELD_MAP/_SELECT_COLS wiring (additive migration). |
| `backend/services/sector_quality_applicability.py` | Defect fix: 3 additive classifier keywords for real Indian labels. |
| `backend/tests/unit/test_india_business_quality_adapter.py` | **New** unit tests. |
| `backend/tests/integration/test_india_adapter_engine_integration.py` | **New** integration tests. |
| `backend/tests/regression/test_india_refresh_business_quality_wiring.py` | **New** regression tests. |
| `backend/tests/golden/test_india_adapter_sector_golden.py` | **New** golden tests (12 sectors). |
| `backend/tests/regression/test_business_quality_multibagger_integration.py` | Updated one Sprint #005 test whose US-only premise Sprint #007 supersedes. |

---

## Remaining Risks

1. **Beneish M-Score remains unavailable for India** (Receivables/SG&A not scraped) — known, accepted, explicitly out of scope. The hard gate degrades gracefully: absence never rejects.
2. **Asset Turnover at 78%** via the adapter path — sector-concentrated and sector-appropriate, but worth monitoring once a full refresh cycle runs at universe scale.
3. **Retained-Earnings-via-Reserves (`[DERIVED/SUPPORTED]`)** is supported by indirect evidence, not independently cross-checked like Total Assets was — carried forward from Sprint #006 as an open verification item, non-blocking.
4. **Altman financial-sector-exemption gap** (pre-existing, engine-level, tracked since the Final Production-Readiness Re-Validation) applies to IN exactly as to US — some banks score near genuinely weaker institutions. Not an adapter concern; not fixed here.
5. **Score stability at full universe scale** — validated on 65 companies; the small conservative bias vs the study path is expected, but full-universe behavior should be confirmed after the first production refresh.

---

## Recommendations

**Is the India Business Quality Adapter ready for production?** **Yes.** Zero errors and zero spurious rejections across the full validated universe, 100% availability on three of the four targeted metrics (Altman, Sloan, Cash Conversion) and sector-appropriate coverage on the fourth, stable confidence, complete explainability (every derived field tagged with provenance), full test coverage across all four categories, and fail-soft wiring that can never cost the IN refresh a row. It writes to the cache only; no consumer reads the IN BQ fields yet, so the production blast radius is a populated-but-unread column until a consumer is deliberately switched on.

**Is the StockSense360 Business Quality Engine now fully operational across both India and US markets?** **Operationally, yes — with one explicit, symmetric caveat.** Both markets now populate Business Quality scores through their respective refresh jobs (US via `us_fundamentals.py`, IN via this adapter), using the same unmodified engine. The caveats are identical on both sides: Beneish M-Score depends on data neither pipeline fully supplies, and the Altman financial-sector-exemption gap is market-independent. Within the scope validated, the engine is genuinely cross-market.

**Recommended next sprint:** wire the IN Business Quality fields into the Multibagger Quality Compounder scorecard's promotion/red-flag logic (the scorecard already reads these fields market-agnostically from Sprint #005 — only the IN cache population was missing, which this sprint delivers), then let one full IN refresh cycle run in production before turning the consumer on. This mirrors the deliberate validate-before-consume discipline used for the US side.

---

## Final Commit Hash

**`ecfcab4`** — pushed to `main`. GitHub Actions (Backend Tests): green.
