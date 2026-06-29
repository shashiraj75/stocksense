# SSDS-008 — StockSense360 Valuation Intelligence Engine

**Status:** Design Study only. No production code modified — per this sprint's explicit "no engine implementation, no scoring, no threshold calibration" rule, this document, its companion Methodology Comparison/Research Report, and the metric catalogue are the entirety of this sprint's output.
**Governed by:** SES-001 through SES-005, mirroring SSDS-007's own structure (the most recently proven template), since that design study's discipline — Evidence Checkpoint, Methodology Checkpoint before metric selection, explicit scope boundaries against every existing engine — is exactly what this sprint's brief asks for again.

> **Update (Epic 004, Sprint #002 — India Data Feasibility Study):** Live evidence against 113 real Indian companies found this document's India-side Cross-Market Feasibility Assessment was **too pessimistic for three specific items**, because Sprint #001 evaluated India through screener.in alone and never tested yfinance's own India coverage (which `prediction_engine.py` already uses as the *base* India source). **Forward P/E (100%), Dividend Sustainability / payout ratio (100%), and a ~5-year historical valuation band are all feasible for India** — corrected upward from this document's "Not currently available" / "Unconfirmed" ratings. This document's original text is deliberately left unchanged, per the "do not silently revise" rule; the corrected record is [Valuation Intelligence — India Data Feasibility Study](../Architecture/Valuation-Intelligence-India-Data-Feasibility-Study.md). The Evidence Checkpoint, Methodology Checkpoint, and all engine-overlap findings below are **unaffected and confirmed** by Sprint #002's evidence.

---

## Evidence Checkpoint (Mandatory — performed before any methodology or metric work below)

Reviewed Epic 001 (Business Quality), Epic 002 (Financial Strength), and Epic 003 (Growth Intelligence) closure records directly, confirming each engine's exact scope boundary by reading their actual category lists (not from memory):

- **Business Quality** — *"is this fundamentally an outstanding business worthy of long-term ownership?"* Categories: Profitability & Capital Efficiency, Balance Sheet Strength, Earnings Quality, **Capital Allocation & Shareholder Treatment** (confirmed at `business_quality_engine.py:395` — reads `payoutRatio`/dividend/corporate-actions data), Durable Competitive Position.
- **Financial Strength** — *"could this company survive a downturn and service its obligations?"* Categories (confirmed at `financial_strength_engine.py`'s five `_*` functions): Liquidity Adequacy, Leverage & Capital Structure, Debt-Servicing Capacity, Balance Sheet Resilience, Cash Flow Durability Under Stress.
- **Growth Intelligence** — *"is this company's revenue, earnings, and cash flow growing — and is that growth real, durable, and not bought at shareholders' expense?"* Categories: Revenue Growth, Profit Growth, EPS Trend, Growth Durability, Operating Profit Growth, Reinvestment Efficiency, Margin Trend.

**No engine answers "is the current price reasonable given the fundamentals."** Quality, survivability, and growth are all questions about the *business*; valuation is a question about the *price paid for that business* — a categorically distinct axis none of the three existing engines occupies. **No contradiction exists; the existing architecture remains valid**, and Valuation Intelligence is confirmed as genuinely new territory, not a duplicate.

### Two named, nuanced cases (the same kind SSDS-005/SSDS-007 each found and resolved explicitly, not silently)

1. **Dividend Yield/Sustainability vs. Business Quality's Capital Allocation category.** Both touch the same underlying data (`payoutRatio`, dividend history) — but ask different questions. Business Quality's existing category asks *"is management allocating capital well (dividends vs. buybacks vs. reinvestment, dilution discipline)?"* — a quality-of-management judgment, independent of price. Valuation Intelligence's Dividend Yield metric asks *"is the income return on the current price attractive, and is that yield likely sustainable given payout ratio and cash generation?"* — a price-relative judgment. **Resolution: Valuation Intelligence reads payout-ratio/dividend data as an input to its own yield-vs-price computation; it does not recompute or duplicate Business Quality's capital-allocation-quality verdict, and Business Quality's category is unchanged by this engine's existence.**
2. **A pre-existing `VALUATION` threshold-registry entry already exists** (`thresholds.py`'s `ValuationThresholds`, instantiated as `VALUATION`) — confirmed by direct inspection to be owned by Multibagger's static scorecard checklist (`PE_QUALITY_COMPOUNDER_MAX = 35.0`, `EV_EBITDA_QUALITY_COMPOUNDER_MAX = 20.0`), not an intelligence engine. **This is the exact same naming-collision pattern Epic 003 found and resolved with `GROWTH`/`GROWTH_INTELLIGENCE`** (SSDS-007's own Open Question #1). Resolution, stated now rather than left open: Valuation Intelligence's eventual threshold registry must be a **separately-named entry** (e.g., `VALUATION_INTELLIGENCE`), never a reuse or rename of the existing `VALUATION` — Multibagger's scorecard checklist is unaffected by this engine's existence, exactly as `GROWTH`'s own checklist-owning role was left untouched by `GROWTH_INTELLIGENCE`.

### One additional boundary, found while researching this sprint's required metric list

**Financial Strength's "Cash Flow Durability Under Stress" category projects a stressed *future* cash flow to test survival; a Discounted Cash Flow valuation projects future cash flow to estimate *fair value*.** Same modeling technique (a forward cash-flow projection), different purpose and different question. Resolution: if Valuation Intelligence implements a DCF-style intrinsic-value metric, it must build its own projection for valuation purposes — it must not read or repurpose Financial Strength's stress-scenario output, whose specific assumptions (a deliberately adverse "Earnings Shock") are wrong for a fair-value estimate by design.

---

## Methodology Checkpoint (Mandatory — completed before any metric is proposed)

Nine major valuation philosophies, compared on the seven dimensions the brief requires, in full before any preference is stated. This comparison is the gating step the brief names explicitly ("do not choose a preferred methodology until this comparison is complete") — the recommended scope at the end of this document follows *from* this table, not before it.

| Philosophy | Strengths | Weaknesses | Data requirements | Reliability | Applicability | India suitability | US suitability |
|---|---|---|---|---|---|---|---|
| **Absolute valuation** (a single estimated fair-value number, e.g. DCF, Graham Formula) | Produces a direct, intuitive "worth ₹X / $X" answer; forces explicit assumptions into the open | Extremely sensitive to discount-rate and terminal-growth assumptions — small input changes produce large output swings; a known, well-documented weakness of DCF specifically | Multi-year cash-flow history, a defensible growth-rate assumption, a discount rate (cost of capital) | **Low-to-Moderate** — directionally useful, not precise; this engagement's own discipline (evidence over assumption) cautions against presenting a DCF output with false precision | Best for mature, stable-cash-flow businesses; weak for cyclicals, early-stage, or loss-making companies | **Feasible**, with screener.in's 12-13yr OCF history giving a reasonable basis for a growth/terminal-rate assumption | **Feasible**, yfinance's `freeCashflow` plus 4-5yr history is thinner than India's but workable |
| **Relative valuation** (comparing a multiple to peers/sector/market) | Simple, widely understood, doesn't require projecting the future; the most commonly used approach in real equity research | A whole sector can be overvalued together (every peer comparison still "cheap" relative to an expensive group) — relative, not absolute, cheapness | A peer/sector universe with comparable multiples already computed | **Moderate-High** — the standard, most defensible day-to-day approach precisely because it doesn't require forecasting | Universally applicable across business types, the most broadly usable philosophy here | **High** — this codebase's own `sector_quality_applicability.py` taxonomy and the existing stock universe already support sector grouping | **High** — same taxonomy reused, per SSDS-006/SSDS-007's own "reuse, don't reinvent" precedent |
| **Intrinsic valuation** (an estimate of true economic worth independent of market price — overlaps with Absolute, but specifically anchored in owner-earnings/EPV-style reasoning rather than a pure multi-year DCF) | Forces a clean separation between "what management actually earns for owners" and "what GAAP/accounting reports" — Bruce Greenwald's Earnings Power Value (EPV) is a respected, narrower-assumption alternative to full DCF | Still requires a discount-rate assumption; "owner earnings" itself requires judgment calls (maintenance vs. growth CapEx split) this codebase has no clean data source to automate | Normalized historical earnings, a maintenance-CapEx estimate, a discount rate | **Moderate** — narrower assumption set than full DCF, but the CapEx-split judgment call is itself a real weakness | Best for stable, low-growth-assumption businesses (EPV explicitly assumes zero growth, a deliberate simplifying choice) | **Feasible**, with the same caveats as Absolute valuation | **Feasible**, same caveats |
| **Asset-based valuation** (Price/Book, Price/Tangible Book, Price/NAV) | Simple, balance-sheet-anchored, doesn't depend on earnings quality or forecasting at all | Increasingly weak for asset-light/intangible-heavy businesses (software, brands) where book value understates true worth; strong only for asset-heavy sectors (financials, real estate, industrials) | Balance sheet — total equity, intangibles/goodwill (for tangible book), real-estate-specific NAV inputs | **Sector-dependent** — strong for financials/real estate, weak-to-meaningless for tech/services | Narrower than Relative valuation — best reserved for the sectors it actually fits | **High** for financials/real estate (screener.in's balance-sheet depth already confirmed in Epic 003); **Low** elsewhere | **High** for financials/real estate (same yfinance `priceToBook`/`bookValue` fields confirmed live); **Low** elsewhere |
| **Earnings-based valuation** (P/E, Forward P/E, PEG, Earnings Yield, CAPE) | The most familiar, most widely reported multiple family; PEG specifically adjusts for growth, addressing the single biggest criticism of raw P/E | Earnings can be manipulated/distorted by one-time items, accounting choices, or a cyclical trough/peak — the exact reason Business Quality's own Earnings Quality category exists as a *separate* concern | Trailing/forward EPS, a growth-rate assumption (for PEG), a multi-year earnings history (for CAPE-style smoothing) | **Moderate-High** for P/E and PEG (standard, well-understood); **Lower** for CAPE in markets/sectors without deep multi-decade history | Best for profitable, earnings-stable businesses; weak/undefined for loss-making companies | **High** for P/E/PEG (screener.in's `pe_ratio` confirmed direct; growth rate already available from Growth Intelligence's own output — see Scope Boundary above); **Low-Moderate** for CAPE (10-year+ smoothed-earnings history not confirmed available) | **High** for P/E/PEG/Forward P/E (yfinance confirms `trailingPE`, `forwardPE`, and even a pre-computed `trailingPegRatio` directly, confirmed live); **Moderate** for CAPE (yfinance's own history is shallower than a true Shiller-style CAPE needs) |
| **Enterprise-value-based valuation** (EV/EBIT, EV/EBITDA, EV/Sales, EV/FCF) | Capital-structure-neutral — compares businesses fairly regardless of how much debt vs. equity they carry, unlike P/E; the standard approach for comparing companies across different leverage profiles | Requires care with EV's own construction (market cap + debt − cash); EBITDA specifically can mask real capital intensity (depreciation is a real economic cost even though it's "added back") | Market cap, total debt, cash, EBIT/EBITDA/Sales/FCF | **Moderate-High** — EV/EBITDA and EV/Sales are both standard, well-understood; EV/FCF is less commonly reported but arguably more honest about real cash economics | Best for comparing companies across different debt levels or capital structures; particularly useful for capital-intensive or leveraged businesses | **Feasible** — `ev_ebitda` already derived in this codebase (`market_cap_cr + borrowings - EBITDA`, confirmed at `screener_data.py:626-631`); EV/Sales derivable the same way; EV/FCF needs the same FCF-approximation caveat Epic 003's Feasibility Study already named (OCF − total investing CF, not isolated CapEx) | **High** — yfinance confirms `enterpriseValue`, `enterpriseToEbitda`, `enterpriseToRevenue` directly (confirmed live); EV/FCF computable from the same `freeCashflow` field already confirmed available |
| **Market multiple approaches** (broad-market P/E, market-cap-to-GDP, equity-risk-premium-implied valuation) | Useful for macro/market-cycle context — "is the *market* itself expensive," distinct from any single stock | Not stock-specific at all; tells you about the market regime, not whether *this* company is mispriced relative to peers | Index-level aggregated data, a long historical series | **Moderate** for context, **not applicable** as a per-stock signal on its own | A *context* input to other approaches, not a standalone per-stock metric | **Unconfirmed** — no existing provider in this codebase's Data Fabric supplies Nifty-level aggregated P/E/valuation history; would need new provider work | **Unconfirmed** — same gap; S&P 500-level aggregates aren't currently fetched anywhere in this codebase either |
| **Sector-relative valuation** (a stock's multiple vs. its own sector's median/percentile) | Directly addresses Relative valuation's "the whole sector might be overvalued" weakness by at least contextualizing within the *peer* group the market actually compares against | Still doesn't tell you if the sector itself, as a whole, is over/undervalued versus history or other sectors | A sector-classified universe with each peer's own multiples computed | **Moderate-High** — a meaningful refinement of plain Relative valuation, reusing infrastructure this codebase already has | Same broad applicability as Relative valuation, with better precision | **High** — this codebase's existing IN/US stock universes plus `sector_quality_applicability.py`'s taxonomy directly support sector-percentile computation | **High** — same reasoning |
| **Historical valuation bands** (a stock's own multiple today vs. its own 3/5/10-year range) | Controls for the fact that some businesses structurally deserve a premium multiple (quality, growth) by comparing each stock only to *itself* over time, not to unrelated peers | A stock can be "cheap relative to its own history" while its own history was itself a multi-year overvaluation (e.g., a structurally de-rating business) — doesn't validate that the historical band itself was fairly priced | Multi-year historical multiple series (price ÷ trailing earnings/book/sales at each point in time), not just today's snapshot | **Moderate** — useful context, not a standalone verdict | Best as a secondary, contextualizing signal alongside Relative/Earnings-based valuation, not a primary standalone approach | **Unconfirmed** — screener.in's confirmed multi-year arrays (Epic 003's Feasibility Study) are for *fundamentals* (sales, profit, OCF), not historical price-multiple series; would require deriving a historical P/E series from historical price × historical EPS, not yet confirmed feasible from current scraped data | **Unconfirmed** — yfinance's `.history()` gives price series, and historical EPS exists in `.financials`, but the codebase has no existing logic that aligns the two into a point-in-time historical multiple series; this is new derivation work, not yet validated |

**This comparison's own conclusion, stated honestly per its own gating purpose:** no single philosophy is sufficient alone, and this matches the brief's own explicit "do not reduce valuation to simple ratios alone" instruction directly. **Relative valuation (sector-relative specifically) and Earnings/Enterprise-Value-based multiples are the strongest combination by this comparison's own evidence** — highest reliability, highest India/US feasibility, and the most direct reuse of this codebase's existing sector taxonomy and Data Fabric. Absolute/Intrinsic valuation (DCF/EPV) is feasible but should be a secondary, lower-confidence signal given its acknowledged sensitivity to unobservable assumptions — never the sole basis for a verdict. Asset-based valuation should be sector-gated (financials/real estate), not applied universally. Market-multiple context and historical valuation bands are both named as **genuinely unconfirmed feasibility** today, not assumed available — each would need its own dedicated feasibility check before implementation, mirroring Epic 003's own "India Feasibility Study" precedent rather than assuming transferability.

---

## Purpose & Motivating Question

Valuation Intelligence answers: **"is this stock currently trading below, near, or above its fair value?"** — distinct from Business Quality (is it a good business), Financial Strength (will it survive), and Growth Intelligence (is it growing) per the Evidence Checkpoint above. It is the natural companion to Growth Intelligence specifically, since "growth at a reasonable price" is a well-established equity-research pairing (named in EPIC-003's own Closure Report §16) — but this design study does not assume that pairing is implemented; it only confirms the two engines' scope boundaries are clean.

## Engine Responsibilities

Exclusively owned by Valuation Intelligence, per the Methodology Checkpoint's own conclusion:

1. **Earnings-based multiples** — P/E, Forward P/E, PEG, Earnings Yield (the inverse of P/E, useful for direct comparison against bond yields — a real, named macro-context use case).
2. **Enterprise-value-based multiples** — EV/EBIT, EV/EBITDA, EV/Sales, EV/FCF.
3. **Asset-based multiples**, sector-gated — Price/Book, Price/Tangible Book (financials/real estate primarily).
4. **Cash-flow-based valuation** — Price/Cash Flow, Free Cash Flow Yield, Owner Earnings (EPV-style), Cash Return.
5. **Income-based valuation** — Dividend Yield, Dividend Sustainability (reading, not duplicating, Business Quality's capital-allocation data per the Evidence Checkpoint's named boundary).
6. **Relative valuation** — sector-median and industry-percentile positioning, reusing `sector_quality_applicability.py`.
7. **Absolute/intrinsic valuation**, as a secondary, lower-confidence signal — a DCF-style and/or Graham-Formula-style estimate, explicitly bounded and never the sole basis for a verdict, per the Methodology Checkpoint's own conclusion.
8. **Market-context overlay** (interest-rate sensitivity, inflation effects, growth regime, market cycle) — named in the brief's Research section; this study's own finding (below) is that the *data* for this is largely unconfirmed today, not that the *concept* is out of scope.

**Explicitly not owned** (named per the Evidence Checkpoint, not silently assumed): Earnings quality (Business Quality's territory), survival/solvency (Financial Strength's), growth-rate computation itself (Growth Intelligence's — Valuation Intelligence *reads* Growth Intelligence's output for PEG-style growth-adjusted multiples, never recomputes its own independent growth estimate, per SES-001's "one computation, one owner").

## Design Philosophy

Four binding commitments, the first three carried forward unchanged from SSDS-003/SSDS-005/SSDS-007 (all three validated under real production pressure), the fourth new to this engine specifically and directly required by the Methodology Checkpoint's own conclusion:

1. **Provider independence.** The engine never imports yfinance/screener.in/SEC EDGAR by name — only a pre-shaped adapter-built `fields` dict, exactly mirroring Growth Intelligence's own pattern.
2. **Evidence over assumption.** Every metric in the catalogue below is rated `[DIRECT]`/`[DERIVED/PROVEN]`/`[DERIVED/SUPPORTED]`/`[UNAVAILABLE]` using the same discipline India Business Quality and Growth Intelligence's own catalogues established — no metric is assumed production-ready by appearing in this document.
3. **Confidence-only Prediction Engine integration** (when/if implemented) — never overriding the BUY/HOLD/SELL signal, mirroring Financial Strength's and Growth Intelligence's own integration pattern, gated by the same kind of market-specific evidence threshold Epic 003 required before its own India-only decision.
4. **No single-ratio reduction.** Per the brief's own explicit rule and the Methodology Checkpoint's own conclusion: Valuation Intelligence's verdict must synthesize across *multiple* philosophies (at minimum, relative/sector-relative plus one of earnings- or enterprise-value-based), never collapse to one ratio against one static cutoff — directly distinguishing this engine's design from the *pre-existing* Multibagger scorecard's own simple "P/E < 35" checklist item, which this engine does not replace or migrate (per the Evidence Checkpoint's named threshold-collision resolution).

## Proposed Architecture

```
                    ┌─────────────────────────────────────────┐
                    │         Provider Adapter Layer            │
                    │  (US: yfinance .info — trailingPE,         │
                    │   forwardPE, priceToBook, enterpriseValue,  │
                    │   enterpriseToEbitda/Revenue, dividendYield, │
                    │   payoutRatio, freeCashflow, trailingPegRatio│
                    │   — all confirmed live;                      │
                    │   IN: screener.in — pe_ratio, book_value,     │
                    │   dividend_yield_pct, market_cap_cr, derived  │
                    │   ev_ebitda/price_to_sales — all confirmed)   │
                    └────────────────┬────────────────────────┘
                                     │
                    ┌────────────────▼────────────────────────┐
                    │           Resolution Layer                 │
                    │  (sector-relative percentile computation,   │
                    │   reusing sector_quality_applicability.py's  │
                    │   existing taxonomy — no new sector model)   │
                    └────────────────┬────────────────────────┘
                                     │
                    ┌────────────────▼────────────────────────┐
                    │    valuation_intelligence_engine.py          │
                    │  compute_valuation_intelligence(               │
                    │      symbol, fields, sector_bucket, market)  │
                    │  -> EngineResponse                           │
                    │  (provider-independent; reads Growth          │
                    │   Intelligence's own output for PEG-style      │
                    │   growth-adjusted metrics, never recomputes)  │
                    └────────────────┬────────────────────────┘
                                     │
                    ┌────────────────▼────────────────────────┐
                    │         PredictionEngine (future)            │
                    │  _apply_valuation_intelligence_adjustment   │
                    │  (confidence-only, bounded cap — pattern      │
                    │   not yet authorized for implementation;      │
                    │   named here only as the eventual shape,      │
                    │   mirroring Growth Intelligence's own           │
                    │   Sprint #006-#007 precedent)                  │
                    └───────────────────────────────────────────┘
```

Structurally identical to Growth Intelligence's own architecture (provider adapter → resolution → engine → EngineResponse → future confidence-only integration), confirming the Data Fabric pattern transfers a fourth time without modification — the same conclusion EPIC-003's own Closure Report already drew for the third transfer.

## Metric Catalogue

For each metric: type, US/India source, missing-data handling — mirroring the exact rating discipline SSDS-007's own catalogue established.

| # | Metric | Type | US Source | India Source | Missing-Data Handling |
|---|---|---|---|---|---|
| 1 | Trailing P/E | `[DIRECT]` | `info["trailingPE"]`, confirmed live | `pe_ratio`, confirmed direct (`screener_data.py:333`) | Omitted from `fields` if not a positive, finite number (a loss-making company's P/E is undefined, not zero) |
| 2 | Forward P/E | `[DIRECT]` for US | `info["forwardPE"]`, confirmed live | **`[UNAVAILABLE]`** — no forward-estimate field confirmed in screener.in's scraped data | Named cross-market asymmetry, not smoothed over |
| 3 | PEG Ratio | `[DIRECT]` for US; `[DERIVED/SUPPORTED]` for India | `info["trailingPegRatio"]`, confirmed live, pre-computed by yfinance itself | Computed from `pe_ratio` ÷ Growth Intelligence's own `profit_growth_3y_pct` output (per the Evidence Checkpoint's "read, don't recompute" rule) | If Growth Intelligence's growth figure is itself unavailable/negative, PEG is undefined — not approximated with a fabricated growth assumption |
| 4 | Earnings Yield (1/P/E) | `[DERIVED/PROVEN]`, both markets | Trivial reciprocal of #1 | Trivial reciprocal of #1 | Same missing-data handling as #1 |
| 5 | Enterprise Value | `[DIRECT]` for US; `[DERIVED/PROVEN]` for India | `info["enterpriseValue"]`, confirmed live | `market_cap_cr + borrowings_annual_cr[-1]` (confirmed existing derivation, `screener_data.py:626-631` — omits cash subtraction, a named, pre-existing simplification this study did not invent) | If `market_cap_cr` or debt is missing, omitted, not estimated |
| 6 | EV/EBITDA | `[DIRECT]` for US; `[DERIVED/PROVEN]` for India | `info["enterpriseToEbitda"]`, confirmed live | `ev_ebitda`, confirmed already derived and stored (`screener_data.py:626-631`) | Same as #5 |
| 7 | EV/EBIT | `[DERIVED/SUPPORTED]`, both markets | Computed from `enterpriseValue` ÷ EBIT (EBIT itself confirmed available from the existing US Financial Strength adapter's 16-field schema, per Epic 002) | Computed the same way from `operating_profit_annual_cr[-1]` (already confirmed available, ex-banks/NBFCs — same population gap Growth Intelligence's own Feasibility Study found) | Inherits Growth Intelligence's confirmed bank/NBFC gap for India |
| 8 | EV/Sales | `[DIRECT]` for US; `[DERIVED/PROVEN]` for India | `info["enterpriseToRevenue"]`, confirmed live | `price_to_sales`-equivalent already derived (`screener_data.py:635-636`); EV/Sales itself trivially computable from #5 ÷ revenue | None confirmed missing |
| 9 | EV/FCF | `[DERIVED/SUPPORTED]`, both markets | `enterpriseValue` ÷ `freeCashflow` (both confirmed live) | `ev` (per #5) ÷ FCF-approximation (OCF − total investing CF) — **inherits the exact FCF-imprecision Growth Intelligence's own India Feasibility Study already named** (no isolated CapEx line exists in the scraper) | Confidence-penalized for India given the inherited imprecision, not treated as equally reliable to the US figure |
| 10 | Price/Book | `[DIRECT]`, both markets | `info["priceToBook"]`, confirmed live | `market_cap_cr` ÷ (`book_value` × shares, derivable from `equity_capital_cr`/`face_value`) | Sector-gated per the Methodology Checkpoint's own conclusion — surfaced with full confidence for FINANCIAL/REAL_ESTATE sector buckets, reduced confidence elsewhere |
| 11 | Price/Tangible Book | `[DERIVED/SUPPORTED]` for US (book value − intangibles/goodwill, both in yfinance's balance sheet); **`[UNAVAILABLE]`** for India (no intangibles/goodwill line confirmed scraped) | — | — | Named cross-market asymmetry |
| 12 | Price/NAV (real estate specific) | `[UNAVAILABLE]`, both markets | No asset-level (property-by-property) appraisal data exists in any provider this codebase uses | — | Named as a permanent limitation for the Real Estate sector bucket specifically, not a near-term gap |
| 13 | Price/Cash Flow | `[DERIVED/SUPPORTED]`, both markets | `marketCap` ÷ Operating Cash Flow (both confirmed available) | `market_cap_cr` ÷ `operating_cf_annual_cr[-1]` (confirmed available, including for banks/NBFCs per Growth Intelligence's own finding — OCF, unlike Operating Profit, is universal) | High confidence even for the bank/NBFC population, a genuine advantage over the EV/EBIT-family metrics above |
| 14 | Free Cash Flow Yield | `[DIRECT]`-derivable for US (`freeCashflow` ÷ `marketCap`, both confirmed live); `[DERIVED/SUPPORTED]` for India (same FCF-approximation caveat as #9) | — | — | Same India confidence penalty as #9 |
| 15 | Owner Earnings (Buffett-style: net income + depreciation − maintenance CapEx) | `[DERIVED/SUPPORTED]`, both markets, with a named, real limitation | Net income/D&A both confirmed available both markets; **maintenance-vs-growth CapEx split is not derivable from any provider this codebase uses** — total CapEx (via investing CF) is the closest available proxy, a known, named overstatement of the "maintenance only" concept this metric is supposed to isolate | Same proxy limitation, both markets | Confidence-penalized in both markets equally — this is not a cross-market asymmetry, it's a universal data-availability ceiling on this specific metric |
| 16 | Cash Return (FCF ÷ Enterprise Value) | `[DERIVED/PROVEN]`, both markets | Trivial computation from already-confirmed #5 and FCF | Trivial computation from already-confirmed #5 and FCF-approximation | Inherits #9's India-specific confidence penalty |
| 17 | Dividend Yield | `[DIRECT]`, both markets | `info["dividendYield"]`, confirmed live | `dividend_yield_pct`, confirmed direct (`screener_data.py:336-337`) | None confirmed missing |
| 18 | Dividend Sustainability | `[DERIVED/SUPPORTED]`, both markets | `payoutRatio`, confirmed live, read (not recomputed) alongside Business Quality's own capital-allocation category per the Evidence Checkpoint's named boundary | `payoutRatio`-equivalent not confirmed directly available from screener.in; derivable from dividend-per-share history if scraped (**unconfirmed**, named honestly) | India-side derivation unconfirmed, not assumed solvable |
| 19 | Sector-relative percentile (any multiple above, vs. sector peers) | `[DERIVED/PROVEN]`, both markets, reusing existing infrastructure | `sector_quality_applicability.py`'s existing taxonomy plus the existing US stock universe | Same taxonomy, existing India stock universe | No new gap — this is the Methodology Checkpoint's own highest-confidence recommendation, and it requires no new data source at all |
| 20 | DCF-style intrinsic value estimate | `[DERIVED/SUPPORTED]`, both markets, with the Methodology Checkpoint's own named sensitivity caveat | Multi-year FCF history (yfinance, ~4-5yr) + an assumed discount rate/terminal growth | Multi-year OCF history (screener.in, 12-13yr — *more* historical depth than US, a genuine India advantage for this one metric) + the same assumed-rate caveat | Always a secondary, lower-confidence signal per the Methodology Checkpoint's own conclusion, never the sole basis for a verdict in either market |
| 21 | Graham Formula (intrinsic value ≈ EPS × (8.5 + 2g), a simplified growth-adjusted earnings multiple) | `[DERIVED/PROVEN]`, both markets | EPS (confirmed available) + Growth Intelligence's own growth-rate output (read, not recomputed) | Same | A simpler, lower-assumption alternative to full DCF — Benjamin Graham's own formula, not invented by this study |
| 22 | Earnings Power Value (EPV) | `[DERIVED/SUPPORTED]`, both markets, with #15's same maintenance-CapEx limitation | Normalized earnings (multi-year average, available) ÷ discount rate, zero-growth assumption (per EPV's own deliberate design) | Same | Same confidence treatment as #15, since it shares the same underlying limitation |
| 23 | Market-context overlay (interest-rate sensitivity, inflation effects, growth regime, market cycle) | **`[UNAVAILABLE]`**, both markets, confirmed not silently assumed | No Nifty-level or S&P-500-level aggregated historical valuation series exists in this codebase's current Data Fabric; `services/global_context.py` (confirmed existing, used elsewhere in `prediction_engine.py`) provides some market-regime signal already, but not specifically valuation-relevant aggregates | Same gap | Named as a genuine, unresolved data-source gap requiring its own feasibility spike — not assumed solvable by extending an existing provider |

## Provider Evaluation

| Provider | What it adds | Limitations | Recommendation |
|---|---|---|---|
| **yfinance (US, existing)** | The single richest valuation-field source confirmed live this sprint: `trailingPE`, `forwardPE`, `priceToBook`, `enterpriseValue`, `enterpriseToEbitda`, `enterpriseToRevenue`, `dividendYield`, `payoutRatio`, `freeCashflow`, and even a pre-computed `trailingPegRatio` — an unusually complete field set requiring almost no new derivation work | Multi-year depth for DCF-style projections remains the same ~4-5yr ceiling Growth Intelligence's own Sprint #003 already confirmed | **Primary source for US, with minimal new adapter work** — most metrics are `[DIRECT]`, a stronger starting position than Growth Intelligence had for this same provider |
| **screener.in (India, existing)** | `pe_ratio`, `book_value`, `dividend_yield_pct`, `market_cap_cr` confirmed direct; `ev_ebitda`/`price_to_sales` already derived by existing code; the 12-13yr OCF depth (confirmed by Growth Intelligence's own Feasibility Study) is a genuine *advantage* over US for DCF-style projections specifically | Forward P/E, tangible-book intangibles split, payout-ratio-style dividend-sustainability data, and historical multiple-band series are all unconfirmed | **Use for what it has; name the rest as unconfirmed**, mirroring Growth Intelligence's own evidence-first discipline rather than assuming parity with US |
| **A market-aggregate provider** (Nifty/S&P-500-level historical valuation series, for the Market-Context overlay) | Would enable Metric #23, currently `[UNAVAILABLE]` | Not evaluated this sprint — genuinely new provider territory, exactly as Growth Intelligence's own Guidance-Consistency gap was left for a dedicated future spike rather than guessed at | **Recommend a narrow, dedicated feasibility spike before committing**, not blocking the rest of this engine's scope on it |

## Confidence Model

Two components, directly reusing Growth Intelligence's own proven two-part design (data-completeness gate plus a second, metric-specific reliability dimension) rather than inventing a third pattern:

1. **Data completeness** — same `MIN_DATA_COMPLETENESS_PCT`-style gate convention every prior engine uses, computed over the metric catalogue's confirmed-available fields.
2. **Methodology-confidence weighting** — a genuinely new dimension this engine needs that none of the three prior engines required in quite this form: per the Methodology Checkpoint's own findings, a sector-relative or earnings/EV-multiple-based verdict should carry materially higher confidence than a DCF-style or EPV-style verdict for the *same* company, since the comparison's own reliability ratings differ by philosophy, not just by data availability. Proposed approach, **not yet calibrated**: weight each philosophy's contribution to the combined verdict by its own Methodology-Checkpoint reliability rating (Relative/Sector-Relative and Earnings/EV-based: full weight; Asset-based: full weight only within its sector gate; Absolute/Intrinsic: reduced weight always) — an Open Question for the implementation sprint, not resolved here.

## Sector Adjustment Strategy

Reuses `sector_quality_applicability.py`'s existing taxonomy for two distinct purposes, both already proven by Growth Intelligence's own precedent: (1) gating Asset-based valuation to FINANCIAL/REAL_ESTATE buckets, per the Methodology Checkpoint's own conclusion; (2) computing sector-relative percentiles for Metric #19. No new sector model proposed.

## Explainability Philosophy

Mirrors Growth Intelligence's own proven pattern exactly: every metric that contributes to the verdict names its real, computed value in the explanation (e.g., *"EV/EBITDA 14.2x vs. sector median 18.6x — trading at a discount to peers"*), strengths/weaknesses ranked by contribution magnitude with a minimum-notable-contribution filter (per Growth Intelligence's own Sprint #004 finding, applied proactively here rather than re-discovered), and a clear distinction in the explanation between which philosophies (Relative, Earnings/EV, Asset, Absolute/Intrinsic) contributed to a given verdict and at what confidence — directly surfacing the Methodology Checkpoint's own multi-philosophy synthesis requirement to the end user, not hiding it.

## Validation Strategy

Mirrors the now-four-times-proven StockSense360 lifecycle exactly (Business Quality, Financial Strength, and Growth Intelligence each followed it; this is not a new sequence invented for this engine):

```
Design Study (this document)
     ↓
Feasibility Study (India-specific gaps named above: Forward P/E, Price/Tangible Book,
     Dividend Sustainability, historical valuation bands, market-context aggregates —
     each needs its own live-data confirmation pass, mirroring Epic 003 Sprint #002 exactly)
     ↓
Engine Implementation (provider-independent engine + both adapters, mirroring
     growth_intelligence_engine.py's exact shape)
     ↓
Calibration (live validation against a real-company universe, at the same scale
     discipline Epic 003 established — 100+ companies per market minimum, explicitly
     checking for false positives/negatives across compounders, cyclicals, asset-heavy
     financials/real-estate, and richly-valued growth names)
     ↓
Outcome Validation (the same real-forward-return methodology Sprint #005 proved
     out — directly relevant here, since "is the price reasonable" is the single
     valuation question most naturally checked against subsequent returns)
     ↓
Integration Readiness Decision (market-by-market, exactly as Epic 003 Sprint #006
     decided India-only for Growth Intelligence — no assumption that Valuation
     Intelligence's own India/US split will mirror Growth Intelligence's; decided
     fresh, from this engine's own evidence, when that evidence exists)
     ↓
Prediction Engine Integration (confidence-only, if and when authorized)
     ↓
Daily Picks Validation (empirical ranking-invariance proof, mirroring Sprint #008's
     own methodology exactly — already proven transferable)
     ↓
Epic Closure
```

## Cross-Market Feasibility Assessment

| Capability | US Feasibility | India Feasibility |
|---|---|---|
| Trailing P/E, Earnings Yield, EV/EBITDA, EV/Sales, Price/Book, Dividend Yield, Price/Cash Flow, Sector-relative percentile | **High** — all `[DIRECT]` or trivially derived, confirmed live | **High** — all `[DIRECT]` or already-derived in existing code |
| Forward P/E | **High** | **Not currently available** — no forward-estimate field confirmed |
| PEG Ratio | **High** (pre-computed by yfinance itself) | **Feasible**, contingent on Growth Intelligence's own growth-rate output being available for the same company (inherits that engine's own confirmed coverage gaps, e.g. banks/NBFCs) |
| EV/EBIT, EV/FCF, Free Cash Flow Yield, Cash Return, Owner Earnings, EPV | **High-Moderate** — all derivable from confirmed-available fields, with Owner Earnings/EPV sharing a universal (not India-specific) maintenance-CapEx data ceiling | **Moderate** — same derivations, with an *additional*, India-specific FCF-approximation imprecision (inherited directly from Growth Intelligence's own confirmed finding) on top of the universal Owner-Earnings/EPV ceiling |
| Price/Tangible Book | **Feasible** (intangibles/goodwill confirmed in yfinance's balance sheet) | **Not currently available** |
| Price/NAV | **Not feasible**, either market | **Not feasible**, either market |
| Dividend Sustainability | **High** (`payoutRatio` confirmed direct) | **Unconfirmed** |
| DCF-style intrinsic value, Graham Formula | **Feasible**, shallower (~4-5yr) statement depth | **Feasible**, *deeper* (12-13yr) OCF depth — a genuine India advantage for this one category, the inverse of Growth Intelligence's own US-advantage pattern for raw data depth |
| Historical valuation bands | **Unconfirmed** — would need new point-in-time-multiple derivation logic | **Unconfirmed** — same, plus the same gap |
| Market-context overlay (rates/inflation/regime/cycle) | **Unconfirmed** — no aggregated valuation-specific series in the current Data Fabric | **Unconfirmed** — same |

**Structural difference worth naming explicitly, mirroring Growth Intelligence's own precedent of naming its analogous asymmetry**: US has the broader, more complete *current-snapshot* field set (PEG, Forward P/E, tangible book, payout ratio all pre-computed); India has *deeper historical depth* for the one category (DCF/intrinsic-value projection) where more years of history directly improves estimate quality. Neither market is uniformly "easier" — the advantage inverts by category, a genuinely new finding this study surfaces rather than assumes from Growth Intelligence's own US-favoring pattern.

## Known Limitations & Out-of-Scope Items (named up front, per SES-001 §1)

- Market-context overlay (Metric #23) — no data source confirmed in either market; a dedicated feasibility spike is required, not assumed solvable.
- Price/NAV — not feasible in either market with any provider this codebase has access to; a permanent limitation, not a deferred TODO.
- Historical valuation bands — unconfirmed in either market; would require new derivation logic neither provider currently supports out of the box.
- Forward P/E, Price/Tangible Book, Dividend Sustainability — all confirmed gaps specific to India, named individually rather than bundled into one vague "India has less data" statement.
- Owner Earnings/EPV's maintenance-CapEx split — a universal (both-market) data-availability ceiling, not solvable by either provider as currently scraped/fetched.
- No threshold calibration of any kind has been performed — every metric's "strong"/"weak" banding remains entirely unspecified, per this sprint's explicit rule.

## Open Questions (carried forward from this Design Study, not resolved here)

1. Exactly how should the Confidence Model's methodology-weighting scheme combine philosophies with different reliability ratings into one verdict — a fixed weighting scheme, or itself sector/context-dependent? Not resolved here; requires the implementation sprint's own calibration evidence.
2. Should Valuation Intelligence's eventual India/US confidence-integration scope mirror Growth Intelligence's own India-only decision, or could US's richer current-snapshot field set support a different split? Explicitly not pre-judged — Epic 003's own Sprint #006 decision was reached from Growth Intelligence's *own* outcome-validation evidence, and this engine's eventual decision must be reached the same way, from its own evidence, not by analogy.
3. Is the existing `ev_ebitda`/`enterpriseValue` derivation's omission of cash (confirmed as a pre-existing simplification in `screener_data.py`, not introduced by this study) material enough to warrant a future correction, or immaterial in practice? Not measured this sprint.
4. Should Dividend Sustainability's India-side gap be addressed by a new scraper field (mirroring Growth Intelligence's own `opm_annual_pct` addition precedent) or accepted as a permanent asymmetry? Pending a dedicated look, not decided here.

## List of Assumptions

1. The provider-independence, evidence-over-assumption, and confidence-only-integration commitments are unchanged from all three prior engines — no new philosophical departure proposed.
2. `sector_quality_applicability.py`'s existing taxonomy is reused as-is for both the Asset-based sector gate and sector-relative percentiles — no new sector model.
3. The `EngineResponse`/`Grade` contract is the target output shape, consistent with all three prior engines.
4. Growth Intelligence's own output is read (not recomputed) for every growth-adjusted valuation metric (PEG, Graham Formula) — this design study assumes that read-access pattern is architecturally straightforward (the same way Growth Intelligence itself reads pre-fetched ticker/screener data), not yet implemented or proven at the code level.
5. No metric in this catalogue is assumed production-ready by virtue of appearing in this document — every rating explicitly requires further validation during implementation, per this sprint's own evidence-over-opinion rule.

## Implementation Roadmap (proposed sequencing, mirroring Growth Intelligence's own proven sprint cadence)

| Sprint | Scope |
|---|---|
| **Sprint #002 (proposed next)** | India-specific feasibility study, targeting this study's own named "Unconfirmed" rows (Forward P/E, Price/Tangible Book, Dividend Sustainability, historical valuation bands) — mirroring Epic 003 Sprint #002 exactly |
| **Sprint #002 or #003 (parallel-able)** | US-side metric implementation of the highest-confidence subset (Earnings-based, Enterprise-Value-based, Sector-relative — all `[DIRECT]` or near-`[DIRECT]`) |
| **Sprint #003 or #004** | India-side implementation, scoped to whatever the feasibility study confirms |
| **Sprint #004+** | Absolute/Intrinsic valuation (DCF, Graham, EPV) as the explicitly secondary, lower-confidence category — implemented after the higher-confidence philosophies, never gating them |
| **Sprint #005+** | Calibration against a real-company universe (100+ per market minimum) |
| **Sprint #006+** | Outcome validation against real forward returns — the single most directly relevant validation step for a *valuation* engine specifically, more so than for any prior engine |
| **Sprint #007+** | Integration Readiness Decision, reached fresh from this engine's own evidence |
| **Separate, lower-priority, parallel-able track** | Market-context overlay provider-feasibility spike |

---

## Final Recommendation

**Further design work is required before coding begins — specifically, a narrowly-scoped feasibility study for the four named India-specific "Unconfirmed" items, mirroring Epic 003's own precedent exactly.** This is not a recommendation to delay Epic 004 broadly: the US-side implementation of the highest-confidence metric subset (Earnings-based and Enterprise-Value-based multiples, Sector-relative percentiles — all confirmed `[DIRECT]` or near-`[DIRECT]` this sprint, a *stronger* starting position than Growth Intelligence had for the equivalent US-side work) can begin in parallel with that feasibility study, mirroring the exact "US first, India confirmed-or-deferred by evidence" sequencing both Epic 002 and Epic 003 already proved out. What should **not** happen is committing to full-scope, both-market, all-philosophy implementation against this design study's metric catalogue as written — Absolute/Intrinsic valuation in particular should remain explicitly secondary and lower-confidence, per the Methodology Checkpoint's own conclusion, not implemented with equal weight to Relative/Earnings/EV-based valuation from day one.

---

*This document is a Design Study only. No production code, tests, providers, or intelligence engines were modified in producing it — every metric proposal is grounded in direct citation of existing codebase evidence (file:line references, or live-confirmed yfinance field checks performed during this sprint) or explicit financial-research reasoning, and every limitation is named rather than assumed away, per this sprint's own "evidence over assumptions" rule. No engine implementation, scoring, threshold calibration, or consumer integration was performed.*
