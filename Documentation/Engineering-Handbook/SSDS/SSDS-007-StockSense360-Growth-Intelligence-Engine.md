# SSDS-007 — StockSense360 Growth Intelligence Engine

**Status:** Design Study only. No production code modified — this document, the supporting research report, and the metric catalogue are the entirety of this sprint's output, per its explicit "no feature implementation should begin" rule.
**Governed by:** SES-001 through SES-005, mirroring the SSDS-005 (Financial Strength) precedent's structure exactly, since that design study is the most recently proven template for a new scoring engine in this codebase.

---

## Purpose & Motivating Question

Growth Intelligence answers a question no existing engine answers: **"is this company's revenue, earnings, and cash flow growing — and is that growth real, durable, and not bought at shareholders' expense?"** This is deliberately distinct from:

- **Business Quality** — "is this fundamentally an outstanding business worthy of long-term ownership?" (profitability, balance-sheet strength, earnings quality, capital allocation discipline, competitive position). Business Quality already contains a narrow growth-*acceleration* check (3Y vs. 5Y sales/profit CAGR comparison, confirmed at `business_quality_engine.py:365-370`) — that single check stays where it is (see Scope Boundary below); everything else about growth's magnitude, quality, durability, and capital cost is new territory.
- **Financial Strength** — "could this company survive a downturn and service its obligations?" (liquidity, leverage, debt-servicing capacity). Growth and survival are orthogonal questions; a company can be growing fast and financially fragile (most pre-profitability growth stocks), or financially strong and not growing at all (mature cash cows).
- **Technical Analysis / Recommendation Intelligence** (`prediction_engine.py`'s existing technical/sentiment blend) — price-action and market-sentiment signals, not fundamental growth trajectory.
- **News Sentiment** — qualitative, event-driven, not a structural growth assessment.
- **Risk Intelligence** (not yet built) — will eventually own volatility/regime/concentration risk; growth durability is a different kind of risk (business-model risk, not market risk).

## Scope Boundary vs. Other Engines

### Metrics that remain exclusively where they are today — non-duplication rule

- **Business Quality Engine's growth-acceleration check** (`business_quality_engine.py:365-370`, comparing 3Y vs. 5Y CAGR to flag accelerating vs. decelerating growth) stays in Business Quality. It answers a quality-of-trend question embedded in BQE's existing "Capital Allocation Discipline" category, not a standalone growth assessment — duplicating it here would violate SES-001's "one computation, one owner" principle the same way SSDS-005 protected Business Quality's existing ROE/ROCE ownership from Financial Strength.
- **Multibagger Scorecard's existing growth checklist items** (`multibagger_scorecard.py:26-61` — "Sales growth > 12% (3Y)", "Profit growing both 3Y and 5Y", the Anti-Loss red flag for negative 3Y profit growth) stay in the scorecard. Growth Intelligence, once built, is a candidate to eventually *replace* these ad hoc checklist items with a richer engine output (mirroring how Business Quality became a first consumer of Multibagger in Epic 001) — but that migration is explicitly **out of scope for this design study** and for Sprint #002; the scorecard's existing logic is not touched by Growth Intelligence's introduction.
- **`GrowthThresholds` / `GROWTH`** (`thresholds.py:92-104`) is the *existing* threshold registry entry for the Multibagger scorecard's growth checks and the turnaround-exception threshold used directly inside `prediction_engine.py`'s quality gate. Growth Intelligence needs its own threshold dataclass (see Confidence & Calibration below) — **reusing or renaming `GROWTH` is explicitly out of scope**; this is flagged as an Open Question (whether Growth Intelligence's eventual thresholds should literally equal `GROWTH`'s values, or be independently calibrated and only coincidentally similar) rather than resolved here.

### The one nuanced case: revenue/profit growth percentages themselves

`sales_growth_3y_pct`, `sales_growth_5y_pct`, `profit_growth_3y_pct`, `profit_growth_5y_pct` (India, via `_screener_data`) and `earningsGrowth`/`revenueGrowth` (US, via yfinance `.info`) are **read** by Business Quality's acceleration check and Multibagger's scorecard today, but neither *owns* them as a first-class, explainable, confidence-aware metric — they're consumed as raw inputs to a narrower, single-purpose check each. Growth Intelligence becomes the **canonical owner** of interpreting these growth rates as a standalone signal (magnitude, quality, durability, sector-context); the existing consumers' narrower uses are unaffected and not migrated in this design study.

## Engine Responsibilities

Growth Intelligence exclusively owns:

1. **Revenue growth quality** — magnitude, consistency, and whether growth is decelerating/accelerating/stable across the available history window.
2. **EPS growth** — and explicitly, the *divergence* between EPS growth and revenue growth (margin expansion or share buybacks inflating EPS faster than the underlying business, vs. dilution suppressing it).
3. **Operating profit growth** — distinct from revenue growth, since operating leverage (or its absence) is itself information.
4. **Free cash flow growth** — the cash-conversion-adjusted view of growth; a company can grow revenue/earnings on paper while FCF stagnates or declines (working-capital consumption, capitalized costs).
5. **Share count dilution** — whether per-share growth metrics are being earned by the business or diluted away by financing activity.
6. **Organic vs. acquisition-driven growth** — a quality distinction this design study can only partially resolve given data constraints (see Data Requirements and Known Limitations).
7. **Margin expansion** — whether growth is accompanied by improving, flat, or eroding margins.
8. **Guidance consistency** — whether management's own forward guidance has historically been met, beaten, or missed (data-source-dependent; see Provider Evaluation).
9. **Capital allocation in service of growth** — reinvestment rate and its apparent payoff (distinct from Business Quality's broader capital-allocation-discipline category, which looks at dividends/buybacks/M&A holistically; Growth Intelligence's narrower lens is specifically "is reinvested capital producing growth").
10. **Growth durability and cyclicality adjustment** — distinguishing a structurally growing business from one riding a cyclical upswing.
11. **Forecast confidence and historical persistence** — how much weight a given company's growth trend deserves, given how volatile or consistent it has been historically.

## Design Philosophy

Three binding commitments, carried forward unchanged from SSDS-003/SSDS-005's own three commitments, since both prior engines validated this philosophy under real production pressure:

1. **Provider independence.** The engine never imports or references yfinance, screener.in, or SEC EDGAR by name — it receives a pre-shaped input dict (or, following Financial Strength's more mature pattern, a pre-resolved `{field_name: {value, confidence, ...}}` dict from a market-specific adapter) and computes purely from that shape.
2. **Evidence over assumption.** Every metric definition below states explicitly whether its formula is `[DIRECT]` (1:1 provider field), `[DERIVED/PROVEN]` (formula validated against an independent source, mirroring the India Business Quality adapter's own "Total Assets via balance-sheet identity, 97% match" precedent), `[DERIVED/SUPPORTED]` (justified by indirect evidence, not independently cross-checked), or `[UNAVAILABLE]` (an acknowledged gap, never guessed at). No metric is implemented in a future sprint without first passing through this same evidence discipline — this design study proposes the catalogue; it does not certify every entry as production-ready.
3. **Confidence-only Prediction Engine integration.** Growth Intelligence influences `PredictionEngine`'s confidence score only, within a bounded cap, exactly like Financial Strength's `_apply_financial_strength_adjustment` (`prediction_engine.py:872-941`) — it never overrides the BUY/HOLD/SELL signal or the composite score. A company with weak growth is not auto-rejected; it's a lower-confidence BUY (or unaffected HOLD/SELL), unless growth deterioration is severe enough to warrant the same kind of capped-confidence treatment Financial Strength's `liquidity_distress` hard gate uses (see Hard Quality Gate, below) — and even then, only confidence is capped, never the signal itself.

## Proposed Architecture

```
                    ┌─────────────────────────────────────────┐
                    │         Provider Adapter Layer            │
                    │  (US: yfinance .financials + SEC EDGAR    │
                    │   multi-year XBRL concepts;                │
                    │   IN: screener.in _screener_data,          │
                    │   already-computed 3Y/5Y CAGR fields)      │
                    └────────────────┬────────────────────────┘
                                     │
                    ┌────────────────▼────────────────────────┐
                    │           Resolution Layer                 │
                    │  (per-field precedence where both US        │
                    │   providers expose a field — mirroring       │
                    │   us_provider_precedence.py's existing       │
                    │   pattern; IN has only one provider per      │
                    │   field today, so resolution there is        │
                    │   pass-through, not precedence-arbitration)  │
                    └────────────────┬────────────────────────┘
                                     │
                    ┌────────────────▼────────────────────────┐
                    │    growth_intelligence_engine.py            │
                    │  compute_growth_intelligence(                │
                    │      symbol, fields, sector_bucket, market)  │
                    │  -> EngineResponse                           │
                    │  (never imports a provider; pure function    │
                    │   of its shaped input dict)                  │
                    └────────────────┬────────────────────────┘
                                     │
                    ┌────────────────▼────────────────────────┐
                    │         PredictionEngine                    │
                    │  _apply_growth_intelligence_adjustment()    │
                    │  (confidence-only, bounded cap — same        │
                    │   pattern as Financial Strength's own        │
                    │   adjustment function)                       │
                    └───────────────────────────────────────────┘
```

This is structurally identical to Financial Strength's architecture (`us_financial_strength_adapter.py` → `compute_financial_strength()` → `_apply_financial_strength_adjustment()`), reusing a now-twice-proven pattern rather than re-deriving one, per the Master Roadmap's own stated intent for Epic 003 ("Reuse the now-twice-proven Data Fabric pattern").

## Metric Catalogue

For each metric: formula type, data source per market, limitations, confidence implications, and missing-data handling. Full per-metric research narrative (why each metric matters, financial-research grounding) is in the companion Research Report; this catalogue is the structured, implementation-facing summary.

| # | Metric | Type | US Source | IN Source | Missing-Data Handling |
|---|---|---|---|---|---|
| 1 | Revenue growth (3Y CAGR) | `[DERIVED/PROVEN for IN, DERIVED/SUPPORTED for US]` | Computed from `.financials`' "Total Revenue" row (≤5yr) or SEC EDGAR's `Revenue`/`RevenueFromContractWithCustomerExcludingAssessedTax` concept (17+yr) | `_screener_data["sales_growth_3y_pct"]` — already computed by screener.in, validated methodology (`screener_data.py:408-424`, CAGR over ≥4 annual values) | Field omitted from `fields` dict if <4 years of history available (US) or screener.in didn't return it (IN, rare) — engine treats as a missing input to its data-completeness gate, not a zero. |
| 2 | Revenue growth (5Y CAGR) | Same as above | Same, requires ≥6yr SEC EDGAR history or 5yr yfinance (rarely available) | `_screener_data["sales_growth_5y_pct"]` — `[DIRECT]` | Same — US 5Y CAGR will frequently be unavailable from yfinance alone; SEC EDGAR is the more reliable source for this specific metric. |
| 3 | EPS growth (3Y/5Y) | `[DERIVED/SUPPORTED]` | Computed from `.financials`' "Diluted EPS" row or SEC EDGAR's `EarningsPerShareDiluted` | No direct equivalent — `_screener_data["eps_trend"]` is categorical, not a CAGR (`screener_data.py:699-713`) | **IN-specific limitation**: no quantitative EPS CAGR available from screener.in today; the engine must either compute one from net-income CAGR ÷ share-count-change (a derived approximation, `[DERIVED/SUPPORTED]` at best) or accept `eps_trend`'s categorical signal as a lower-confidence substitute. Named explicitly as a cross-market asymmetry, not silently smoothed over. |
| 4 | Operating profit (EBIT) growth (3Y CAGR) | `[DERIVED/SUPPORTED]` | `.financials`' "Operating Income" row or SEC EDGAR's `OperatingIncomeLoss` | No direct screener.in field; would need deriving from `operating_profit_latest_cr` if multi-year history is scraped (currently only latest-year is confirmed available per the India Business Quality adapter's own `[DERIVED/SUPPORTED]` EBIT mapping) | **IN-specific gap, named not guessed**: multi-year operating-profit history for India is not confirmed available in the current screener.in scrape; this metric may need to degrade to "latest-year operating margin vs. revenue growth" as a proxy, or be marked `[UNAVAILABLE]` for IN pending a data-feasibility study. |
| 5 | Free cash flow growth (3Y CAGR) | `[DERIVED/SUPPORTED]` | FCF = Operating Cash Flow − CapEx, both available multi-year via SEC EDGAR (`NetCashProvidedByOperatingActivities`, `PaymentsToAcquirePropertyPlantAndEquipment`) or yfinance `.cashflow` | `_screener_data["operating_cf_annual_cr"]` (confirmed available, 4-year list, per the research report) minus CapEx — CapEx multi-year availability for IN not yet confirmed | Same pattern as #4 — flagged for a feasibility check before implementation, not assumed available. |
| 6 | Share count dilution rate (annualized, 3Y) | `[DERIVED/SUPPORTED]` | Share count at T vs. T-3Y from `.balance_sheet`'s "Ordinary Shares Number" row (yfinance) or SEC EDGAR's `CommonStockSharesOutstanding` | Not currently scraped from screener.in; would require a new field | **IN gap**: no current data source for this metric in India. Marked `[UNAVAILABLE]` for IN until a feasibility study confirms screener.in (or an alternative provider) exposes multi-year share-count history. |
| 7 | EPS growth vs. revenue growth divergence | `[DERIVED/SUPPORTED]` | Computed from #1 and #3 above | Computed from #1 and `eps_trend` (qualitative only, given #3's IN gap) | Confidence-weighted down for IN given the categorical (not quantitative) EPS input — this divergence check is inherently lower-confidence for India than for the US under current data availability. |
| 8 | Organic vs. acquisition-driven growth | `[UNAVAILABLE]`, named explicitly | No reliable automated signal — would require parsing M&A activity from cash-flow statement's "Acquisitions, net" line plus qualitative filing review; not feasible from numeric provider fields alone | Same — `[UNAVAILABLE]` | This is the one design-goal item this study cannot propose a confident metric for from existing or readily-addable data sources. Recommend treating as an explicit, permanent limitation of the engine's first version, not a future-sprint TODO — confirming or refuting this would need transaction-level M&A data this codebase has no provider for today. |
| 9 | Margin expansion (gross/operating margin trend) | `[DERIVED/SUPPORTED]` | Gross margin = `.info["grossMargins"]` trend (already read elsewhere, e.g. `business_quality_info` fixture) or computed from `.financials` revenue/COGS history; operating margin from EBIT/revenue | India: gross/operating margin computable from screener.in's existing revenue + operating-profit fields if multi-year history confirmed (see #4's caveat) | Same multi-year-availability caveat as #4 for IN. |
| 10 | Guidance consistency (beat/meet/miss history) | `[UNAVAILABLE]` for both markets today | No current provider in this codebase exposes analyst-estimate-vs-actual history; yfinance's `.calendar`/earnings-estimate fields exist but are not currently fetched or validated anywhere in this codebase | Same — no India equivalent either | Named as a genuine data-source gap requiring a **new** provider evaluation (see Provider Evaluation) before this metric could move past `[UNAVAILABLE]`. Not assumed solvable within the existing Data Fabric without new work. |
| 11 | Reinvestment efficiency (incremental ROIC — growth in invested capital vs. growth in operating profit) | `[DERIVED/SUPPORTED]`, US only with confidence | Computable from EBIT growth (#4) and invested-capital change (total debt + equity, both in the existing 16-field unified schema from Financial Strength's adapter) | Same multi-year operating-profit caveat as #4 limits this for IN | A genuinely new derived metric, not previously computed anywhere in this codebase — flagged for explicit validation against named companies before trusting its output, per this engagement's "validate before integrate" discipline. |
| 12 | Growth durability / cyclicality adjustment | `[DERIVED/SUPPORTED]`, sector-dependent | Standard-deviation or coefficient-of-variation of YoY growth rates across the available history window, contextualized by `sector_quality_applicability.classify_sector()`'s existing sector taxonomy (already reused by both Business Quality and Financial Strength) | Same approach, same sector taxonomy reuse | Cyclicality adjustment is a **modifier** on the growth-magnitude score, not a separate category — a cyclical sector (e.g. commodities, autos) showing high growth should be scored with lower durability confidence than a structurally growing sector (e.g. software) showing the same magnitude. This requires sector-specific calibration (see Sector Adjustment Strategy) before it can be trusted quantitatively. |
| 13 | Forecast confidence / historical persistence | `[DERIVED/SUPPORTED]` | Derived from the same coefficient-of-variation calculation as #12, applied specifically to weight how much the engine's own `confidence` field should reflect the growth trend's historical reliability | Same | This is explicitly the mechanism that feeds the engine's own `confidence` output field — distinct from data-completeness confidence, this is *trend-reliability* confidence (a company with 3 years of wildly oscillating growth gets lower confidence than one with steady, persistent growth, even with identical CAGR). |

## Provider Evaluation

| Provider | What it adds for Growth Intelligence | Limitations | Recommendation |
|---|---|---|---|
| **yfinance (US, existing)** | `.financials`/`.cashflow`/`.balance_sheet` multi-year history (≤5yr); `.info["earningsGrowth"]`/`["revenueGrowth"]` as a fast, pre-computed (if often stale) fallback | Capped history depth; no pre-computed CAGR; `earningsGrowth` is frequently null or stale (already a known limitation cited in `quality_factors.py`'s existing PEG-ratio fallback logic) | **Use, as today** — already proven, already integrated, zero new provider-integration cost. The engine should prefer computing its own CAGR from `.financials` history over trusting `earningsGrowth`/`revenueGrowth` directly, given their known staleness. |
| **SEC EDGAR (US, existing)** | 17+ years of XBRL history for revenue, net income, EPS, share count — the single richest multi-year data source already integrated into this codebase (via `sec_edgar_adapter.py`, proven in Financial Strength's adapter) | Quarterly/annual filing lag (typically a few weeks to ~75 days after period end, standard for 10-K/10-Q); some smaller companies' XBRL tagging is inconsistent for less-common concepts | **Primary source for US multi-year growth metrics** — reuses the exact provider this codebase already validated and integrated for Financial Strength, at zero new integration risk. The 16-field unified schema's existing precedence pattern (`us_provider_precedence.py`) should be extended with growth-specific fields (multi-year revenue/EPS/share-count series, which the existing 16-field schema does not carry as *series* — it carries single-point values) — this is new adapter work, not a new provider. |
| **screener.in (India, existing)** | Already-computed 3Y/5Y sales/profit CAGR, already validated as `[DIRECT]`/proven methodology; 4-year OCF history | No EPS CAGR (only qualitative trend); no confirmed multi-year operating-profit or share-count history; no guidance/estimate data at all | **Use for what it has; explicitly gap the rest.** Per Design Philosophy's evidence-over-assumption commitment, this study does not propose scraping additional fields from screener.in without a dedicated feasibility check (mirroring SSDS-004's own precedent of running a feasibility study before committing to a data strategy) — this is named as a required Sprint #002 (or earlier) prerequisite, not assumed solvable here. |
| **A new provider for analyst-estimate/guidance data (e.g. yfinance's own `.calendar`/`.earnings_estimate`, or a dedicated estimates provider)** | Would enable the Guidance Consistency metric (#10), currently `[UNAVAILABLE]` | Not currently integrated anywhere in this codebase; unknown reliability/cost/rate-limit characteristics until evaluated | **Recommend a dedicated, narrow feasibility spike** before committing — this is genuinely new provider territory, unlike everything else in this catalogue which reuses existing, already-trusted providers. Until that spike happens, Guidance Consistency should ship (if at all) as `[UNAVAILABLE]`/out-of-scope for v1, not blocked on. |

## Confidence Strategy

Two distinct confidence concepts, both required, neither substituting for the other (this distinction is new relative to Business Quality/Financial Strength, which only needed the first):

1. **Data completeness confidence** — identical in kind to Business Quality's and Financial Strength's existing `MIN_DATA_COMPLETENESS_PCT` gate pattern (`thresholds.py`'s `BUSINESS_QUALITY.MIN_DATA_COMPLETENESS_PCT` / `FINANCIAL_STRENGTH.MIN_DATA_COMPLETENESS_PCT`, both 60.0): what fraction of the metric catalogue above actually resolved to a real value for this symbol, given market and data-availability constraints (e.g. a US small-cap with only 3 years of SEC EDGAR history available has lower completeness than a large-cap with the full 17-year series).
2. **Trend-reliability confidence** (genuinely new to this engine, per Metric #13) — given the data that *is* available, how consistent/persistent has the growth trend been. A company with 100% data completeness but wildly oscillating year-over-year growth should still receive a lower overall confidence than one with the same completeness and a smooth, persistent trend.

The engine's final `confidence` field (per `EngineResponse`) should combine both — proposed approach (not yet calibrated, flagged for the implementation sprint): completeness as a hard floor/gate (mirroring the existing `MIN_DATA_COMPLETENESS_PCT` REJECTED-grade pattern), trend-reliability as a continuous modifier within the confidence range above that floor. Exact weighting between the two is an **Open Question**, not resolved by this design study — it requires the same kind of live-data calibration Business Quality's Sprint #004a recalibration and Financial Strength's data-feasibility study both used, not an a priori guess.

## Sector Adjustment Strategy

Reuses the existing `sector_quality_applicability.classify_sector()` taxonomy (already shared by Business Quality and Financial Strength — confirmed via the research report's adapter-pattern findings) rather than inventing a new sector model. Growth Intelligence's sector-specific need is narrower than either prior engine's: primarily the **cyclicality adjustment** (Metric #12) needs sector context (a cyclical sector's high growth deserves a durability discount a structural-growth sector's identical number doesn't). Proposed approach, **not yet calibrated**: a per-sector-bucket multiplier or additive adjustment to the durability-confidence calculation, mirroring Financial Strength's own sector-specific soft adjustments (e.g. its differing treatment of FINANCIAL-sector interest expense precedence) rather than introducing an entirely new sector model. Exact multipliers are an implementation-sprint calibration task, explicitly not invented here without data.

## Cross-Market Feasibility

**Update (Sprint #002 — [India Data Feasibility Study](../Architecture/Growth-Intelligence-India-Data-Feasibility-Study.md)):** the "Unconfirmed" India rows below were resolved by a live fetch against 85 real Indian companies. The actual findings are **more permissive** than this table's original framing — the structural gap is specifically banks/NBFCs (not "Financials" broadly), and margin trend/dilution are solvable with scoped engineering work, not blocked on data availability. The table below is left as originally written, as the historical record of this design study's pre-evidence assumptions; the feasibility study is the current source of truth for India availability.

| Capability | US Feasibility | India Feasibility (original Design Study assumption — see update above for resolved findings) |
|---|---|---|
| Revenue growth (3Y/5Y CAGR) | **High** — SEC EDGAR provides ample history; straightforward to compute | **High** — already computed and validated by screener.in |
| EPS growth (quantitative) | **High** — SEC EDGAR/yfinance both expose multi-year diluted EPS | **Low/Unconfirmed** — only a qualitative trend exists today; quantitative CAGR would need a new derivation or new data source, unvalidated |
| Operating profit / FCF growth (multi-year) | **High** — both available via SEC EDGAR | **Unconfirmed** — only latest-year figures confirmed available per the existing India Business Quality adapter; multi-year history not yet confirmed scrapeable from screener.in |
| Share count dilution | **High** — both providers expose historical share counts | **Not currently available** — no existing field; would need new scraping work |
| Margin expansion | **High** | **Unconfirmed**, same caveat as operating-profit growth |
| Guidance consistency | **Unconfirmed** — no current provider integrated for this in either market | **Unconfirmed**, same |
| Organic vs. acquisition growth | **Not feasible from current data** in either market | Same |

**Structural difference worth naming explicitly:** India's data source (screener.in) front-loads growth-CAGR computation at the provider level (the 3Y/5Y figures arrive pre-computed and already validated), while the US relies on this engine computing CAGR itself from raw multi-year statement data. This means the *engine's own CAGR-computation code* will only ever be exercised by US data in practice — a real testing implication: unit tests for the CAGR-computation logic should use synthetic/US-shaped fixtures, while India-path tests should focus on correctly consuming the pre-computed fields rather than re-deriving them. This mirrors the Business Quality Engine's own established pattern of differently-shaped logic per market sharing one engine core.

## Implementation Roadmap (proposed sequencing for Sprint #002+)

1. **Data-feasibility study** (mirroring SSDS-004's and Financial Strength's own sequencing) — specifically targeting the "Unconfirmed" rows in Cross-Market Feasibility above: does screener.in actually expose (or can it be made to expose) multi-year operating-profit, FCF, and share-count history for India? This is the single highest-priority open question blocking confident IN-side scope commitment.
2. **US-side metric implementation** (revenue/EPS/operating-profit/FCF growth CAGR, dilution rate, margin trend) — highest feasibility, can proceed without waiting on the IN feasibility study's outcome, mirroring Epic 002's own "US-only first" sequencing precedent (Financial Strength shipped US-only, India deferred).
3. **Threshold calibration** against a live-data validation set (mirroring Business Quality's 55/65-company studies and Financial Strength's 76-company studies) — both for the growth-magnitude grade bands and the trend-reliability confidence weighting.
4. **India-side implementation**, scoped exactly to whatever the feasibility study (step 1) actually confirms is available — never assumed equal to the US scope.
5. **Sector adjustment calibration** for the cyclicality/durability modifier, requiring its own live-data evidence pass per sector bucket.
6. **PredictionEngine integration** (`_apply_growth_intelligence_adjustment`), narrowly scoped exactly like Financial Strength's own integration sprint, with before/after evidence that no pre-existing consumer behavior changed.
7. **Guidance Consistency feasibility spike** (separate, lower-priority, can run in parallel with steps 2-4 since it depends on a different, currently-unevaluated data source) — resolves whether Metric #10 ever leaves `[UNAVAILABLE]` status, or is permanently scoped out.

## Validation Methodology

Mirrors the now-twice-proven sequence from Section 7 of the Master Roadmap exactly: **Design Study (this document) → SSDS Specification (this document, combined per this sprint's own structure) → Implementation → Validation → Consumer Integration → Epic Closure.** Specifically for Validation:

- A live-data validation pass against a named-company universe (50+ per market, per Epic 001/002's own established bar), deliberately including adversarial cases: a company in apparent secular decline (growth metrics should score low, not be fooled by a one-quarter bounce), a cyclical company at a cycle peak (durability/cyclicality adjustment should discount it, not score it as structurally strong), and a company growing via heavy dilution (EPS-vs-revenue divergence and dilution-rate metrics should both flag it).
- Explicit confirmation that adding Growth Intelligence to `PredictionEngine` changes no pre-existing test's expected output for symbols where Growth Intelligence data is unavailable (graceful degradation, mirroring Financial Strength's own "`None` input → confidence unchanged" contract).

## Production Readiness Assessment

**Status at time of writing (Sprint #001): not ready for implementation.** The specific, named blockers:

1. **India's data feasibility is genuinely unconfirmed** for roughly half the metric catalogue (operating-profit/FCF multi-year history, share-count/dilution, quantitative EPS growth) — this is not a calibration question, it's an open "does the data exist at all in a usable form" question that must be answered before any IN-side implementation commitment, exactly the kind of question SSDS-004's own feasibility-study precedent exists to answer first.
2. **Two genuinely new derived metrics** (Reinvestment Efficiency / incremental ROIC, and the trend-reliability confidence model) have no prior implementation or validation precedent anywhere in this codebase — they are reasoned proposals grounded in financial-research logic, not yet evidence-tested against real company data.
3. **Guidance Consistency has no current data source** in either market — a real, named gap, not a calibration detail.
4. **Sector-specific cyclicality calibration** requires its own live-data evidence pass and cannot be responsibly hardcoded from this design study's reasoning alone.

None of this means Growth Intelligence is poorly scoped — it means the **next sprint should be the India data-feasibility study + US-side metric implementation in parallel**, exactly as sequenced in the Implementation Roadmap above, not full implementation across both markets simultaneously.

**Update (Sprint #002 — completed):** Blocker #1 has been resolved by live evidence — see the [India Data Feasibility Study](../Architecture/Growth-Intelligence-India-Data-Feasibility-Study.md), which fetched real data for 85 Indian companies and found India's data situation **materially better than this design study assumed**: revenue/profit growth and EPS-trend are excellent across the entire market including banks; the only structural gap is operating-profit-dependent metrics for banks/NBFCs specifically (not "Financials" broadly); margin trend and share-count dilution are each one scoped engineering task away from being usable, not blocked on data availability. Blockers #2-4 remain open, unaffected by Sprint #002's India-specific scope.

## Testing Strategy (for the eventual implementation sprint)

Mirrors Epic 001/002's proven four-category structure (unit/integration/regression/golden) exactly:

- **Unit:** each metric's formula tested in isolation against synthetic fixtures (CAGR computation, dilution-rate computation, divergence calculation) — independent of any live provider.
- **Integration:** the full `compute_growth_intelligence()` call exercised against realistic US (synthetic multi-year `.financials`-shaped) and IN (`_screener_data`-shaped) fixtures, mirroring `business_quality_info`/`in_market_info` fixture conventions already established in `tests/conftest.py`.
- **Regression:** locking in graceful-degradation behavior (missing fields → lower completeness confidence, never a crash) and the `PredictionEngine` integration's "confidence-only, never signal-altering" contract.
- **Golden:** snapshot tests against known-growth and known-decline named companies, once a live validation pass exists to generate trustworthy golden values.

## Known Limitations & Out-of-Scope Items (named up front, per SES-001 §1)

- Organic vs. acquisition-driven growth (Metric #8) — no feasible automated signal from current/foreseeable data sources; treated as a permanent limitation, not a deferred TODO.
- Guidance Consistency (Metric #10) — no current data source in either market; requires a dedicated provider-feasibility spike before it can move past `[UNAVAILABLE]`.
- India-side operating-profit/FCF multi-year history, EPS quantitative growth, and share-count/dilution — all explicitly unconfirmed pending a feasibility study; this design study does not assume they will turn out to be available.
- Reinvestment Efficiency and trend-reliability confidence weighting — both genuinely new, unvalidated derived metrics; proposed here on financial-research grounding alone, explicitly flagged as requiring live-data validation before being trusted in production.
- Migrating Business Quality's growth-acceleration check or Multibagger's growth checklist items onto the new engine — explicitly out of scope for this design study and likely for the first implementation sprint too; named as a future possibility, not a commitment.

## Future Sprint Roadmap

| Sprint | Scope |
|---|---|
| **Sprint #002 (proposed next)** | India data-feasibility study (parallel-able with US-side work below) |
| **Sprint #002 or #003 (parallel-able with feasibility study)** | US-side metric implementation (highest-feasibility subset: revenue/EPS/operating-profit/FCF growth, dilution, margin trend) |
| **Sprint #003 or #004** | Threshold calibration against live-data validation set (US) |
| **Sprint #004+ (gated on feasibility study's outcome)** | India-side implementation, scoped to whatever the feasibility study confirms |
| **Sprint #005+** | Sector adjustment / cyclicality calibration |
| **Sprint #006+** | `PredictionEngine` integration (`_apply_growth_intelligence_adjustment`) |
| **Separate, lower-priority, parallel-able track** | Guidance Consistency provider-feasibility spike |

## Open Questions (carried forward from this Design Study, not resolved here)

1. Should Growth Intelligence's threshold dataclass reuse `GROWTH`'s existing numeric values (`SALES_GROWTH_3Y_QUALITY_COMPOUNDER_MIN_PCT = 12.0`, etc.) as a starting point, or be independently calibrated from scratch even if it coincidentally lands on similar numbers? This design study takes no position — it only confirms the new dataclass must be a separate, independently-named registry entry, not a rename or extension of `GROWTH`.
2. Exact weighting between data-completeness confidence and trend-reliability confidence in the engine's final `confidence` output — requires live-data calibration, not an a priori formula.
3. Whether India's EPS-growth gap should be addressed by (a) deriving an approximation from net-income CAGR ÷ share-count change, (b) accepting the qualitative `eps_trend` signal at a lower confidence weight, or (c) treating it as `[UNAVAILABLE]` for IN entirely — this design study lays out all three options without choosing one, pending the feasibility study's findings.
4. Whether and when Business Quality's growth-acceleration check or Multibagger's growth checklist items should eventually be migrated to consume Growth Intelligence's output instead of their own narrower logic — explicitly deferred, not a Sprint #002 question.
5. Sector-bucket-specific cyclicality multipliers' exact values — requires a dedicated calibration pass per sector, not invented in this design study.

## List of Assumptions

1. The provider-independence, evidence-over-assumption, and confidence-only-integration commitments from Business Quality and Financial Strength apply unchanged to Growth Intelligence — no new philosophical departure is proposed.
2. `sector_quality_applicability.classify_sector()`'s existing taxonomy is reused as-is for the cyclicality adjustment; no new sector model is proposed.
3. The `EngineResponse`/`Grade` contract (`engine_contract.py`) is the target output shape, consistent with both prior engines.
4. SEC EDGAR and yfinance (US) and screener.in (India) remain the only providers in scope for this design study's proposed v1 metric set — the Guidance Consistency provider-feasibility spike is named as a separate, future, lower-priority track, not assumed to expand v1's scope.
5. No metric in this catalogue is assumed production-ready by virtue of appearing in this document — every `[DERIVED/SUPPORTED]` or `[UNAVAILABLE]` entry explicitly requires further validation during implementation, per this sprint's own evidence-over-opinion rule.

## Update to INDEX.md

Recorded in the commit accompanying this document — adds SSDS-007 to the SSDS table alongside SSDS-000 through SSDS-006.

---

## Final Recommendation

**Further design work is required before coding begins — specifically, a narrowly-scoped India data-feasibility study and nothing else.** This is not a recommendation to delay Epic 003 broadly: the US-side metric implementation (the highest-feasibility roughly half of the metric catalogue) can begin as Sprint #002 in parallel with that feasibility study, mirroring Epic 002's own proven "US first, India confirmed-or-deferred by evidence" sequencing — exactly the precedent the Master Roadmap itself already names as the intended Epic 003 approach. What should **not** happen is committing to full-scope, both-market implementation against this design study's metric catalogue as written, since roughly half of the India-side rows are honestly marked "Unconfirmed," not "confirmed available." Recommend Sprint #002 be scoped as: (a) the India data-feasibility study, and (b) US-side implementation of the highest-feasibility metric subset (revenue/EPS/operating-profit/FCF growth CAGR, dilution rate, margin trend) — run in parallel, each independently evidence-gated before its own next step, exactly as Epic 001 and Epic 002 both proved out.

---

*This document is a Design Study only. No production code, tests, providers, or intelligence engines were modified in producing it — every metric proposal is grounded in direct citation of existing codebase evidence (file:line references confirmed via direct reading, not invented) or explicit financial-research reasoning, and every limitation is named rather than assumed away, per this sprint's own "evidence over opinion" rule.*
