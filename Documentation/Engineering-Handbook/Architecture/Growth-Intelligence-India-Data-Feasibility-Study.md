# Growth Intelligence — India Data Feasibility Study (Epic 003, Sprint #002)

**Status:** Evidence-gathering only. No production code, tests, providers, scoring, or thresholds were modified — per this sprint's explicit "no implementation" rule. Every finding below is grounded in a **live, real fetch against screener.in for 85 actual Indian companies** (exceeding the 70-company minimum), run via this codebase's own existing `fetch_screener_data()` function — not assumption, not the prior design study's reasoning alone.

**Methodology:** 85 NSE-listed companies, hand-selected (not randomly sampled, since accurate segment/sector labeling required knowing each company's actual profile) to cover every segment and sector named in the sprint brief — see the Sample Composition table below. Fetched live, one HTTP request per symbol with a 1.1-second delay between requests (matching this codebase's own existing `fundamentals_refresh.py` rate-limiting convention), no authentication used (`SCREENER_EMAIL`/`SCREENER_PASSWORD` unset). **85/85 fetches succeeded** (`available: True`) — zero outright fetch failures, though several fields are structurally absent for specific company types (detailed below, not a fetch failure).

---

## Sample Composition

| Dimension | Coverage |
|---|---|
| **Total companies** | 85 |
| **Large Cap** | 45 |
| **Mid Cap** | 33 |
| **Small Cap** | 7 |
| **Cyclical** | 15 |
| **Secular growers** | 13 |
| **Turnarounds** | 5 (LUPIN, BIOCON, YESBANK, SUZLON, BHEL) |
| **High growth** | 18 |
| **Declining businesses** | 6 (COALINDIA, WIPRO, IDEA, RPOWER, DISHTV, JPASSOCIAT) |
| **PSUs** | 18 |
| **Private companies** | 67 |
| **Sectors** | Financials (13), Industrials (16), Materials (14), Consumer (9), IT (8), Pharma (7), Energy (5), Real Estate (5), Utilities (6), Telecom (2) |

*(Tags overlap — e.g. a company can be both `large_cap` and `cyclical`; totals across tags exceed 85.)*

---

## 1. Metric Availability Matrix

| Metric (from SSDS-007) | India Availability | Evidence |
|---|---|---|
| Revenue growth (3Y/5Y/10Y CAGR) | **Available Directly** | `sales_growth_3y_pct`: 85/85 (100%). `sales_growth_5y_pct`: 85/85 (100%). `sales_growth_10y_pct`: 85/85 (100%) — screener.in computes all three pre-aggregated, exceeding SSDS-007's original 3Y/5Y-only scope. |
| Profit growth (3Y/5Y/10Y CAGR) | **Available Directly, with one named edge case** | `profit_growth_3y_pct`: 84/85 (98.8%). `profit_growth_5y_pct`: 83/85 (97.6%). `profit_growth_10y_pct`: 82/85 (96.5%). The one 3Y gap: **DISHTV** (a named declining/distressed company in the sample) — CAGR is mathematically undefined when the base-period profit is negative or zero, exactly the edge case a declining-business segment exists to surface. |
| EPS growth (quantitative) | **Not Available** (unchanged from SSDS-007) | No numeric EPS series anywhere in the scraped data — confirmed by direct inspection of the full field set returned for all 85 companies. |
| EPS growth (categorical trend) | **Available, Derivable** | `eps_trend` is computed in a *separate* enrichment function (`augment_info_with_screener`, `screener_data.py:628-712`), not in `fetch_screener_data()` itself — this study's first pass against the raw fetch function showed 0/85, which looked like a gap but was a methodology artifact (calling the wrong function), not a real data gap. Recomputing the identical logic directly against the already-fetched `quarterly_pat_cr` arrays confirms **85/85 (100%) computable** once the correct function is used: 11 accelerating, 45 mixed_positive, 24 mixed_negative, 5 decelerating. |
| Operating profit (EBIT) growth | **Available Directly for 75/85 (88.2%); structurally absent for banks/NBFCs specifically** | `operating_profit_annual_cr`: present for all non-bank, non-NBFC companies (12-13 years of history). **Absent for exactly 10 companies, all banks or NBFCs** (HDFCBANK, ICICIBANK, SBIN, KOTAKBANK, AXISBANK, BAJFINANCE, YESBANK, RECLTD, PFC, IRFC) — confirmed this is a presentation-format issue, not a missing-data issue: banks don't report a "Sales"/"Operating Profit" line in the conventional P&L sense screener.in scrapes (interest income/expense is structured differently). **Important precision beyond the original Design Study's "Financials sector" framing**: fee-based financial-services companies in the sample that aren't banks/NBFCs — **CDSL, MCX, IEX** (exchange/depository businesses) — **do** have this field. The real boundary is "bank/NBFC" specifically, not "Financials sector" broadly. |
| Free cash flow growth | **Partially Derivable, for non-bank/NBFC companies** | `operating_cf_annual_cr` is 100% available (even for banks). But no separate CapEx line exists anywhere in the scraper's parsing — confirmed by direct source inspection (`screener_data.py`, cash-flow section, lines 487-514): only "operating" and "investing" cash-flow totals are parsed, no "capital expenditure"/"fixed assets purchased" line is isolated. FCF can only be approximated as OCF − *total* investing cash flow, which conflates true CapEx with M&A spend, financial-investment purchases/sales, and other investing activity — a real, structural imprecision, not a scraping gap that could be fixed by reading the page more carefully. |
| Share count dilution | **Derivable, but requires corporate-action awareness** | No direct share-count field, but `equity_capital_cr` (100% available, 12-13yr history) combined with `face_value` (100% available) yields `share_count = equity_capital_cr / face_value`. **Concrete corporate-action evidence found in this sample**: RELIANCE's `equity_capital_cr` series is `[2943, 2948, 2959, 5922, 5926, 6339, 6445, 6765, 6766, 6766, 13532, 13532]` — two clear step-jumps (2959→5922, 6766→13532) consistent with bonus issues/face-value splits, not organic dilution. A naive year-over-year dilution-rate calculation would misread these as one-time ~100% "dilution" events. **A corporate-action-aware calculation is required**, not optional — confirmed necessary by this sample, not theorized. |
| Organic vs. acquisition growth | **Not Available** (unchanged from SSDS-007) | No segment-level or M&A-specific data found anywhere in the scraped fields, across all 85 companies. Confirms the original Design Study's conclusion with a larger sample. |
| Margin expansion (trend) | **Partially Derivable** | `opm_pct` is present for 75/85 (88.2%, same banks/NBFC gap as Operating Profit) but **stored as latest-year-only** — confirmed by direct source inspection (`screener_data.py:444`: `data["opm_pct"] = vals[-1]`) even though the underlying scraped table row (`vals`) contains the full multi-year series before being discarded down to the last element. **This is a real, specific, fixable-with-code-change gap**: the multi-year margin trend exists on the screener.in page and is parsed into memory, but the current code throws away everything except the most recent value before returning it. |
| Guidance consistency | **Not Available** (unchanged from SSDS-007) | No estimate/guidance data found anywhere in the scraped fields. Confirms the original Design Study's conclusion. |
| Reinvestment efficiency (incremental ROIC) | **Derivable for 75/85 (non-bank/NBFC)**, contingent on Operating Profit Growth's same availability | Computable from EBIT growth + invested-capital change (`borrowings_annual_cr` + `reserves_annual_cr` + `equity_capital_cr`, all ≥88% available) for the same population that has Operating Profit. Inherits the bank/NBFC gap. |
| Growth durability / cyclicality | **Derivable** | Computable from the variance of `sales_annual_cr`/`profit_growth` series, 100%/98.8% available respectively — no new gap beyond what feeds it. |
| Forecast confidence / trend persistence | **Derivable** | Same — depends only on already-available multi-year series. |

## 2. Provider Coverage Matrix

| Provider | Role confirmed this sprint | Coverage |
|---|---|---|
| **screener.in** (existing, sole IN provider) | The only data source exercised — confirmed sufficient for the majority of the metric catalogue, contrary to the original Design Study's more pessimistic "roughly half unconfirmed" framing (see Recommendation below for why this study's conclusion is more optimistic). | 85/85 symbols fetched successfully; field-level coverage detailed in the Availability Matrix above. |
| **NSE / BSE direct** | **Not investigated as a separate provider** — out of scope per the sprint's "do not introduce paid providers" rule and because screener.in itself sources from NSE/BSE filings; querying NSE/BSE directly would only matter if screener.in's coverage proved insufficient, which it largely does not (see findings above). | N/A |
| **Existing StockSense360 Postgres cache** (`stock_fundamentals_cache`) | **Confirmed insufficient for this study's purposes** — it stores only single-value/CAGR fields, not the underlying multi-year arrays `fetch_screener_data()` actually returns. The nightly refresh job (`fundamentals_refresh.py`) fetches the full live data but discards the arrays before persisting. This is a real, separate finding: **the live scrape already has the depth Growth Intelligence needs; the existing cache pipeline simply isn't storing it.** Implementing Growth Intelligence will need either a new cache table (storing the arrays) or a live-fetch-on-demand pattern — a design decision for the implementation sprint, not resolved here. |

## 3. Historical Coverage Matrix

| Field | Min years | Max years | Avg years | Consistency |
|---|---|---|---|---|
| `sales_annual_cr` (and `operating_profit_annual_cr`, same rows) | 12 (where available) | 13 | 12.0 | Consistent depth across companies that have it at all — no partial/truncated series observed among the 75 with data. |
| `operating_cf_annual_cr` / `investing_cf_annual_cr` | 10 | 12 | 12.0 | 100% present, including all 10 banks/NBFCs that lack the P&L-style fields — cash-flow-statement data is universally available regardless of sector presentation format. |
| `quarterly_revenue_cr` / `quarterly_pat_cr` | 13 | 13 | 13.0 | Fully consistent, 100% present. |
| `equity_capital_cr` | 12 | 13 | 12.0 | 100% present, but **not corporate-action-adjusted** (see RELIANCE example above) — depth is consistent, but raw values require interpretation before use. |

**Missing values:** Beyond the bank/NBFC structural gap (not a missing-value problem, a different-data-shape problem), the only individual missing value found in this sample was DISHTV's `profit_growth_3y_pct` (mathematically undefined CAGR base) — a single, explainable case, not a systemic data-quality problem.

**Survivorship bias:** Not directly testable from this sample, since all 85 selected companies are currently listed and screener.in only carries currently-listed (or recently-delisted-but-still-indexed) companies — a structural characteristic of the data source itself, named here as a known limitation rather than measured, since measuring it would require a sample of *delisted* companies this study did not attempt to source (e.g. genuinely bankrupt/delisted names like the original Design Study's hypothetical "JETAIRWAYS" case, which this study could not confirm is even still resolvable on screener.in and did not test, to avoid fetch errors against a likely-dead page outside this study's live-fetch budget).

**Restatement issues:** Not directly observable from a single point-in-time fetch (would require re-fetching the same company's historical data at two different past dates to detect a restatement) — named as an unresolved methodology limitation of this study, not as "no restatements occur."

**Corporate action effects:** **Confirmed present and material** — see RELIANCE's `equity_capital_cr` step-jumps above. This is the single most concrete, evidence-backed finding under this heading: any dilution-rate or per-share-metric calculation implemented without corporate-action detection will produce misleading results for at least some real, large-cap companies (not just an edge case for obscure small-caps).

## 4. Data Quality Assessment

| Metric | Quality Rating | Evidence |
|---|---|---|
| Revenue growth (3Y/5Y/10Y) | **Excellent** | 100% availability, 12-13yr consistent depth, pre-computed and validated methodology (CAGR), confirmed by this engagement's own prior India Fundamentals Data Validation Study for the underlying balance-sheet-identity-style derivations elsewhere in this codebase. |
| Profit growth (3Y/5Y/10Y) | **Excellent**, with one named exception class | 98.8%/97.6%/96.5% availability; the one gap (DISHTV) is a mathematically explainable edge case (undefined CAGR base), not a data-quality defect. |
| EPS growth (categorical) | **Good** | 100% computable, but inherently coarser (4-bucket categorical) than a numeric CAGR — appropriately rated below "Excellent" for that reason, not for any reliability concern. |
| EPS growth (quantitative) | **Unavailable** | No data exists to rate. |
| Operating profit growth | **Excellent for 88.2% of companies; Unavailable for the remaining 11.8%** (banks/NBFCs) | Rated per-population, not as one blended number — averaging across a structural gap would misrepresent both populations. |
| Free cash flow growth | **Acceptable** | Directionally usable (OCF is real and well-covered) but the investing-CF-as-CapEx-proxy structurally overstates or understates true FCF depending on how much non-CapEx investing activity a given company has in a given year — confirmed imprecise by source-code inspection, not assumed. |
| Share count dilution | **Poor, without corporate-action handling; Acceptable, with it** | The underlying data is complete and consistent, but the RELIANCE example proves a naive calculation would be actively misleading, not just imprecise — this is a "Poor" rating for the metric *as a naive calculation*, explicitly distinct from "Poor" meaning the data itself is bad. |
| Margin expansion (trend) | **Poor, as currently coded; Good, if the scraper is extended** | The data exists on the source page and is even parsed into memory — it's discarded by one line of existing code (`vals[-1]`) before being returned. Rated "Poor" for *today's* availability, with an explicit, low-effort path to "Good" named. |
| Organic vs. acquisition growth | **Unavailable** | No data exists to rate. |
| Guidance consistency | **Unavailable** | No data exists to rate. |
| Reinvestment efficiency | **Good for 88.2% of companies; Unavailable for the rest** | Same population split as Operating Profit Growth, since it depends on that metric directly. |
| Growth durability / cyclicality | **Excellent** | Depends only on the already-Excellent revenue/profit growth series. |

## 5. Confidence Assessment

| Metric | Confidence | Rationale |
|---|---|---|
| Revenue growth | **High** | Large sample (85/85), consistent depth, pre-validated methodology. |
| Profit growth | **High** | Same, with the DISHTV-style edge case being a known, handleable (not mysterious) failure mode. |
| EPS growth (categorical) | **Medium** | Mechanism confirmed reliable, but the categorical bucketing's actual predictive/explanatory value for Growth Intelligence's purposes has not been validated against outcomes — that validation is implementation-sprint work, not this study's. |
| EPS growth (quantitative) | **Unknown** | No data exists; "Unknown" rather than "Low," since there's nothing to even form a low-confidence estimate from. |
| Operating profit growth | **High** for the 88.2% population; **Unknown** for banks/NBFCs (not "Low" — there is no path to this metric for them with this provider, not a weak one) |
| Free cash flow growth | **Medium** | Mechanically reliable but a known, structural imprecision (investing-CF-as-CapEx-proxy) caps it below High regardless of sample size. |
| Share count dilution | **Medium**, contingent on corporate-action handling being implemented | The underlying data is High-confidence; the *naive* calculation is Low-confidence; this is rated for the metric *as it should be built* (with corporate-action awareness), not as a naive first pass. |
| Margin expansion | **Low**, as currently available; would become **High** with a small, specific code change (stop discarding the array) |
| Reinvestment efficiency | **Medium** | A genuinely new, never-before-computed-in-this-codebase derived metric — data availability is High where Operating Profit is available, but the metric's own validity (does it actually correlate with anything useful) is unvalidated, per the original Design Study's own caveat, unchanged by this sprint. |
| Growth durability/cyclicality | **Medium** | Data is High-confidence; the *calibration* of what counts as "durable" vs. "cyclical" per sector bucket is unresolved, same caveat the Design Study already named. |

## 6. Sector Assessment

| Sector | n in sample | Operating Profit / Margin availability | Notable findings |
|---|---|---|---|
| **Financials** | 13 | 3/13 (the 3 are CDSL/MCX/IEX — exchanges/depositories, not banks/NBFCs) | **The single most important sector-level finding of this study**: "Financials" is not a uniform gap. Banks and NBFCs (10/13 of this sector in the sample) structurally lack Operating Profit/Sales/Margin data via screener.in's P&L scraping; non-bank financial-services companies do not share this gap. Any sector-bucket model for Growth Intelligence needs a finer split than the existing `sector_quality_applicability.py`'s single `FINANCIAL` bucket (confirmed by direct code inspection to be a single, undifferentiated bucket for "Banks, NBFCs, Insurance" today) — Growth Intelligence will need its own, more granular distinction for this specific metric, not inherited wholesale from the existing taxonomy. |
| **Utilities** | 6 | 6/6 | No gap found. |
| **Energy** | 5 | 5/5 | No gap found. |
| **IT** | 8 | 8/8 | No gap found. |
| **Pharma** | 7 | 7/7 | No gap found, including for the turnaround cases (LUPIN, BIOCON) in this sector. |
| **Consumer** | 9 | 9/9 | No gap found. |
| **Industrials** | 16 | 16/16 | No gap found, including PSU defense names (BEL, HAL) and turnaround case (BHEL). |
| **Real Estate** | 5 | 5/5 | No gap found. |
| **Materials** | 14 | 14/14 | No gap found, including cyclical names (TATASTEEL, JSWSTEEL, HINDALCO, SAIL, NMDC). |
| **Telecom** | 2 | 2/2 | No gap found, including the declining case (IDEA/Vodafone Idea). |

**Conclusion:** the only sector-driven structural gap in this entire study is the bank/NBFC subset of Financials — every other named sector, including cyclicals, PSUs, turnarounds, and declining businesses across Industrials/Materials/Telecom/Pharma, has full Operating-Profit-level data availability.

## 7. Recommended Metric Set for Version 1

Metrics with **High data confidence and no structural population gap** (safe for a v1 India implementation covering the full market, not just non-financial companies):

1. Revenue growth (3Y/5Y/10Y CAGR) — Excellent quality, High confidence, 100% coverage.
2. Profit growth (3Y/5Y/10Y CAGR) — Excellent quality, High confidence, ~97-100% coverage with one explainable edge case.
3. EPS growth (categorical trend) — Good quality, Medium confidence, 100% coverage.
4. Growth durability / cyclicality (variance-based) — Excellent quality, Medium confidence (pending sector calibration), 100% coverage.

Metrics **safe for v1, but only for the ~88% non-bank/non-NBFC population** (must be explicitly gated by sector/company-type, not silently applied to all companies):

5. Operating profit (EBIT) growth.
6. Reinvestment efficiency (incremental ROIC).

## 8. Deferred Metrics

| Metric | Reason deferred | What would need to change |
|---|---|---|
| Margin expansion (trend) | Currently only latest-year value is retained by the scraper, despite the underlying multi-year data being parsed into memory and then discarded. | A small, specific code change to `screener_data.py`'s P&L parsing (stop truncating to `vals[-1]`) — a real, scoped implementation task for whenever Growth Intelligence is actually built, not a research gap. |
| Free cash flow growth | Structural imprecision (no isolated CapEx line; only total investing cash flow). | Either accept the approximation explicitly (with a named confidence penalty) or investigate whether screener.in's page exposes a more granular cash-flow breakdown this scraper isn't currently parsing — not confirmed either way by this study. |
| Share count dilution | Corporate-action discontinuities (confirmed present, not hypothetical) would corrupt a naive calculation. | A corporate-action detection/adjustment step is required before this metric is trustworthy — scoped implementation work, not a data-availability gap. |
| EPS growth (quantitative) | No data source. | Would require either a new provider or a different derivation (e.g., net-income CAGR ÷ share-count-change, itself dependent on solving the dilution metric's corporate-action problem first). |
| Organic vs. acquisition growth | No data source, confirmed by this sprint's larger sample, consistent with the original Design Study. | Would require a fundamentally different data source (segment-level or transaction-level M&A data) not available from any free provider this study could identify. |
| Guidance consistency | No data source for either market, confirmed unchanged. | Requires a dedicated provider-feasibility spike, as the Design Study already recommended — this sprint did not attempt one, per its own "do not introduce paid providers" scope and the spike's own lower-priority sequencing. |

## 9. Production Readiness Score: **6/10**

Materially **higher** than what the original Design Study's "roughly half unconfirmed" framing implied — live evidence shows the core growth metrics (revenue, profit, EPS-trend, durability) are in genuinely excellent shape for the *entire* market including banks, while the gaps are narrower and more specific than originally framed (operating-profit-dependent metrics for banks/NBFCs specifically, not "Financials" broadly; margin trend is a known, small code fix away rather than a data gap; dilution is solvable with corporate-action handling rather than blocked). The score is not higher than 6 because: (a) two of the v1-safe metrics still require population-specific gating logic that adds real implementation complexity, not just a threshold tweak; (b) three deferred metrics each require genuine, separately-scoped engineering work (scraper extension, corporate-action detection, CapEx isolation) before they're safe, not just a calibration pass; (c) sector-level calibration for durability/cyclicality remains entirely unresolved, unchanged from the Design Study.

## 10. Recommendation

**Ready with reduced metric set.**

Specifically: India implementation can proceed for the four Recommended Metrics (§7, items 1-4) across the *entire* India universe today, and for the two population-gated metrics (§7, items 5-6) for the ~88% of companies that aren't banks/NBFCs — this is a substantially more permissive starting point than the original Design Study anticipated, now that live evidence replaces its more cautious "roughly half unconfirmed" framing. The three Deferred Metrics (§8) should wait for their own, specifically-scoped engineering work (none of which is "further research required" in the open-ended sense — each has a concrete, named next step), not an open-ended further-investigation sprint. This recommendation is **not** "Ready for full India implementation" (Guidance Consistency and Organic-vs-Acquisition growth remain genuinely unresolved with no near-term path), and it is **not** "Further provider research required" (the evidence gathered this sprint is sufficient to scope a v1 implementation confidently, without needing another data-feasibility pass first).

---

*This document is evidence-gathering only. No production code, tests, providers, scoring logic, or thresholds were modified — confirmed by this sprint's diff being limited to this report and its companion raw-data artifacts, not any file under `backend/`. Every finding above is grounded in a live fetch against 85 real Indian companies, run via this codebase's own existing `fetch_screener_data()` function, with source-code citations for every claim about what the scraper does or doesn't currently parse.*
