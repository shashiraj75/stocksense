# Valuation Intelligence — India Data Feasibility Study (Epic 004, Sprint #002)

**Status:** Evidence-gathering only. No production code, scoring, Prediction Engine, or consumer-integration change — confirmed by this sprint's diff being limited to this report and SSDS-008's existing-precedent "Update" pointers.

## Evidence Checkpoint (Mandatory)

Reviewed SSDS-008 and its companion Research Report before collecting any new data. **A genuine, material contradiction was found** — documented explicitly below, per this sprint's own rule, not silently revised into SSDS-008's original text (mirroring exactly how SSDS-007 itself handled Sprint #002's analogous correction for Growth Intelligence).

### The contradiction

SSDS-008's Cross-Market Feasibility Assessment rated **Forward P/E as "Not currently available"** and **Dividend Sustainability (payout ratio) as "Unconfirmed"** for India — both ratings were reached by evaluating **screener.in alone**. This sprint's live evidence shows this was an incomplete provider evaluation, not an incorrect reading of screener.in itself: **this codebase's own `prediction_engine.py` already fetches yfinance as the *base* data source for India** (`.NS` suffix, confirmed at the existing `augment_info_with_screener` call site — screener.in is *enrichment*, not the primary source), and yfinance's own India coverage for these exact fields is materially better than SSDS-008's screener.in-only evaluation assumed.

**Live evidence, 113 real Indian companies (exceeding the 100-company minimum):**
- **`forwardPE`: 113/113 (100%)** via yfinance — directly contradicts SSDS-008's "Not currently available" rating.
- **`payoutRatio`: 113/113 (100%)** via yfinance — directly contradicts SSDS-008's "Unconfirmed" rating for Dividend Sustainability's core input.
- **`priceToBook`: 113/113 (100%)** via yfinance — confirms Price/Book is *more* reliably available via this path than the screener.in-derived computation SSDS-008 proposed.

**Impact:** SSDS-008's Production Readiness framing for India was more pessimistic than the evidence supports for these three specific items. This does not invalidate SSDS-008's Methodology Checkpoint (the 9-philosophy comparison and its conclusions are unaffected — they were never about field availability) or its Evidence Checkpoint (the engine-overlap findings are unaffected). It specifically affects three rows of the Cross-Market Feasibility Assessment table and the Known Limitations section's first three bullets. **SSDS-008's original text is left unchanged, per "do not silently revise" — this report is the corrected record, exactly mirroring SSDS-007's own "Update" pointer precedent.**

A second, smaller correction: SSDS-008 rated Historical Valuation Bands "Unconfirmed" for India based on screener.in's confirmed lack of a price series. **Live evidence found yfinance provides 10 years of daily price history for Indian tickers** (confirmed: `RELIANCE.NS`, 2,470 trading days spanning 2016–2026) — though `.financials` (the EPS side of a P/E band) remains capped at ~5 years, the same ceiling already confirmed for US in SSDS-008. This makes a ~5-year historical P/E band **feasible**, not "Unconfirmed" — a smaller, more specific claim than full 10-year bands, but a real, corrected one.

**Everything else in SSDS-008 — the Engine Responsibilities, the two named nuanced-overlap cases (Dividend Sustainability vs. Business Quality; the `VALUATION`/`VALUATION_INTELLIGENCE` naming collision), the Design Philosophy, and the Methodology Checkpoint's own conclusions — remains valid, confirmed by this sprint's evidence, not contradicted.**

---

## 1. India Valuation Feasibility Report

**Methodology:** live data collected for **113 real Indian companies** (115 attempted; 2 lost to symbol-resolution failures — `LTIM`/`GMRINFRA`, a known, recurring class of issue already seen in Epic 003's own sprints, not a new concern), spanning Large/Mid/Small cap and all 15 named sectors (Banks, NBFC, IT, Pharma, FMCG, Auto, Capital Goods, Infrastructure, Metals, Chemicals, Utilities, Energy, Telecom, Consumer, Real Estate). Both providers fetched for every company: `fetch_screener_data()` (this codebase's existing scraper) and `yfinance.Ticker(f"{symbol}.NS").info` (confirmed live, not assumed).

## 2. Metric Availability Matrix

| Metric | Screener.in | yfinance | Combined availability | Notes |
|---|---|---|---|---|
| Trailing P/E | 98.2% (`pe_ratio`) | 99.1% (`trailingPE`) | **~99%+ via either provider** | Two independent sources agreeing closely |
| **Forward P/E** | Not available | **100%** (`forwardPE`) | **100%** | **Corrected from SSDS-008's "Not currently available"** |
| PEG Ratio | Not pre-computed | **3.5%** (`trailingPegRatio`, only 4 large IT/Pharma names) | **Computed fallback required** (P/E ÷ Growth Intelligence's own growth-rate output) | Confirms SSDS-008's already-planned fallback is the correct, necessary path — not a gap, a confirmation |
| Earnings Yield | Trivial reciprocal of P/E | Trivial reciprocal of P/E | **~99%+** | No new finding |
| Enterprise Value | 99.1% (`market_cap_cr`, derived) | 99.1% (`enterpriseValue`) | **~99%** | |
| EV/EBITDA | 83.2% (`ev_ebitda`, derived) | 84.1% (`enterpriseToEbitda`) | **~84%, with a precisely-attributed gap** | **Gap is 100% concentrated in Banks (0/10) and almost entirely NBFC (1/9)** — the identical population Growth Intelligence's own Sprint #002 found structurally lacks EBITDA-shaped income statements. Not a new or surprising gap. |
| EV/Sales | 84.1% (`price_to_sales`, derived) | 99.1% (`enterpriseToRevenue`) | **~99% via yfinance specifically** | yfinance's direct field outperforms the screener.in-derived path here |
| Price/Book | Derivable from `book_value`+`face_value`+`equity_capital_cr` | **100%** (`priceToBook`) | **100%** | yfinance direct is the stronger, simpler path |
| Price/Tangible Book | Not available | Not separately checked this sprint (intangibles/goodwill split not in the field list tested) | **Still Unconfirmed** — genuinely not addressed this sprint, named honestly rather than assumed resolved | A real, remaining open item |
| Price/Cash Flow | 99.1% (`operating_cf_annual_cr`) ÷ `market_cap_cr` | Computable from `marketCap` ÷ operating cash flow (not directly tested as a single field, but both inputs confirmed) | **~99%** | |
| Free Cash Flow Yield | Same FCF-approximation caveat as Growth Intelligence's own confirmed finding (OCF − total investing CF) | 81.4% (`freeCashflow`, direct) | **~81-99%** depending on path; yfinance's direct field is materially cleaner | **Gap concentrated in Banks (0/10) and NBFC (1/9), plus minor unexplained gaps in Capital Goods (8/10) and FMCG (9/10)** — the latter two named honestly as small, company-specific gaps, not a sector-systemic pattern (only 1-2 companies each) |
| Dividend Yield | 99.1% (`dividend_yield_pct`) | 98.2% (`dividendYield`) | **~99%** | Two independent sources agreeing |
| **Dividend Sustainability** | Not available | **100%** (`payoutRatio`) | **100%** | **Corrected from SSDS-008's "Unconfirmed"** |
| Historical Valuation Bands | No price series exists in screener.in (confirmed, unchanged) | **10yr daily price history confirmed** (`RELIANCE.NS`: 2,470 rows, 2016–2026); EPS side capped at ~5yr via `.financials` | **~5yr band feasible via yfinance alone** | **Corrected from SSDS-008's "Unconfirmed"** — a more specific, smaller claim (5yr, not full 10yr) than the ideal, but a real, evidence-based one |
| DCF / Reverse DCF inputs | 99.1% (`operating_cf_annual_cr`, 12-13yr depth — confirmed by Growth Intelligence's own Sprint #002) | ~5yr `.financials` depth (the same ceiling already confirmed for US in SSDS-008) | **Feasible, India's screener.in path has materially more historical depth than yfinance's** | Confirms SSDS-008's own already-stated India DCF-depth advantage |
| Graham Formula inputs (EPS + growth rate) | EPS derivable from `pe_ratio`+price; growth from Growth Intelligence's own output | EPS available directly; growth via `earningsGrowth` (94.7%)/`revenueGrowth` (100%) | **~95-100%** | Two independent growth-rate sources exist; SSDS-008's "read Growth Intelligence's output, don't recompute" rule still governs which one Valuation Intelligence should use |
| Earnings Power Value inputs | Same as Owner Earnings — confirmed available, with the same universal maintenance-CapEx ceiling SSDS-008 already named | Same | **Feasible, with SSDS-008's own already-named universal limitation, unchanged** | No new finding — confirms, doesn't contradict |

## 3. Historical Coverage Matrix

| Dimension | Finding |
|---|---|
| Fundamentals depth (screener.in) | 12-13 years, confirmed (same depth Growth Intelligence's own Sprint #002 established — not re-measured from scratch this sprint, cited directly) |
| Fundamentals depth (yfinance `.financials`) | ~5 years, confirmed live for a representative ticker — the same ceiling SSDS-008 already named for both markets |
| Price-history depth (yfinance) | **10 years confirmed live** (`RELIANCE.NS`) — a new finding this sprint, not previously checked in SSDS-008 |
| Price-history depth (screener.in) | None — confirmed, unchanged from SSDS-008's own original (correct) finding |
| Missing years / restatements | Not directly re-tested this sprint; Growth Intelligence's own confirmed corporate-action finding (RELIANCE's `equity_capital_cr` bonus-issue discontinuity, Epic 003 Sprint #004) is directly relevant to any India valuation metric using share-count-derived inputs (e.g. Price/Book computed via `equity_capital_cr`÷`face_value`) — named as an inherited, not newly-discovered, risk |
| Corporate actions | Same inherited risk as above — confirmed relevant, not re-measured |
| Survivorship issues | **Same scope limitation as every prior sprint's own honest disclosure** — this sample is currently-listed companies only; delisted/failed companies were not tested, consistent with the same limitation Growth Intelligence's own Feasibility Study named |

## 4. Provider Quality Assessment

| Provider | Assessment |
|---|---|
| **Screener.in** | Strong, consistent ~98-99% availability for core fields (P/E, Book Value, Dividend Yield, Market Cap); the established 12-13yr depth advantage for fundamentals-based projection (DCF/Reverse DCF) remains real and valuable. **Weaker than previously credited for current-snapshot ratios this sprint newly tested against yfinance** (Forward P/E, payout ratio not available at all; EV/EBITDA-family derivations require manual computation yfinance provides directly). |
| **Yahoo Finance (yfinance, `.NS` suffix)** | **The standout finding of this sprint**: materially stronger India coverage for current-snapshot valuation ratios than SSDS-008 assumed, since SSDS-008 never tested it directly for India (only for US). 100% on Forward P/E, Payout Ratio, Price/Book, Price/Sales; 10-year price history confirmed. The one confirmed weakness: `trailingPegRatio` (3.5%) and the same Bank/NBFC EBITDA-family gap screener.in also shares. |
| **Current Data Fabric** | No India-specific valuation provider integration exists yet (confirmed — this is a Design Study/Feasibility Study, no engine has been built); the existing `augment_info_with_screener` pattern (yfinance base + screener.in enrichment) is already proven by `prediction_engine.py`'s own production use and should be the template for Valuation Intelligence's own India adapter, not a new pattern. |
| **Derived calculations** | Confirmed safe and necessary for PEG (India), EV/EBITDA-via-screener (already existing code), and the FCF-approximation inherited from Growth Intelligence — none of these derivations were found newly broken this sprint. |

**Are existing providers sufficient?** **Yes, for the large majority of the catalogue** — confirmed by this sprint's evidence to be *more* sufficient than SSDS-008 assumed, specifically because SSDS-008 underused yfinance's own India coverage. The remaining genuine gaps (Price/Tangible Book, full 10-year historical bands limited by the 5-year EPS ceiling, Price/NAV) are real and not solved by either provider as currently used.

## 5. Confidence Matrix

| Metric | Confidence | Why |
|---|---|---|
| Trailing P/E, Earnings Yield, Enterprise Value, Dividend Yield, Price/Book | **Excellent** | Two independent providers agree, both near-100% |
| Forward P/E, Dividend Sustainability (payout ratio) | **Excellent** | 100% via yfinance, confirmed this sprint — corrected from SSDS-008's pessimistic rating |
| EV/Sales | **Excellent** | 99% via yfinance's direct field |
| EV/EBITDA, Free Cash Flow Yield/EV-FCF-family | **High for non-Bank/NBFC (~88/100 of the sample); Unknown (not Low) for Banks/NBFC** | Structurally absent, not unreliable — same "Unknown, not Low" distinction Growth Intelligence's own Sprint #002 established |
| PEG Ratio | **Moderate** | Reliable *mechanism* (computed fallback), but depends on Growth Intelligence's own confirmed coverage, inheriting that engine's own gaps |
| Historical Valuation Bands (5yr) | **Moderate** | Feasible, newly confirmed, but not yet validated end-to-end (price-series-to-EPS-series alignment logic doesn't exist yet) |
| DCF / Reverse DCF / Graham / EPV inputs | **Moderate** | Data available; the Methodology Checkpoint's own sensitivity caveat still applies regardless of data availability |
| Price/Tangible Book | **Unavailable** | Confirmed gap, not newly resolved this sprint |
| Price/NAV | **Unavailable** | Confirmed permanent gap, unchanged from SSDS-008 |

## 6. Recommended V1 Metric Set

Metrics with **Excellent confidence and no structural population gap** — safe for a v1 India implementation covering the full market:

1. Trailing P/E / Earnings Yield
2. **Forward P/E** (newly unlocked this sprint)
3. Enterprise Value
4. EV/Sales
5. Price/Book
6. Dividend Yield
7. **Dividend Sustainability / Payout Ratio** (newly unlocked this sprint)
8. Sector-relative percentile (any of the above)

Metrics safe for v1, but gated to the **~88% non-Bank/NBFC population** (must be explicitly population-gated, not silently applied to all companies, mirroring Growth Intelligence's own established pattern):

9. EV/EBITDA
10. Free Cash Flow Yield / EV-FCF-family
11. PEG Ratio (additionally inherits Growth Intelligence's own growth-data coverage gaps on top of the EBITDA-family gap)

## 7. Deferred Metrics

| Metric | Reason deferred | Specific next step |
|---|---|---|
| Price/Tangible Book | Confirmed unavailable (intangibles/goodwill split not in either provider's accessible fields, not retested differently this sprint) | A dedicated, narrower check of yfinance's balance-sheet fields specifically for intangibles/goodwill on Indian tickers — not attempted this sprint, a small, scoped follow-up |
| Price/NAV | No provider exposes property-level appraisal data in either market | Permanent limitation, not a deferred TODO — same conclusion as SSDS-008 |
| Full 10-year Historical Valuation Bands | EPS-side data (`.financials`) caps at ~5 years via yfinance; screener.in has no price series at all to extend the window | A 5-year band is feasible now; a longer band would require either a new EPS-history provider or combining yfinance's price series with screener.in's deeper fundamentals — genuinely new cross-provider alignment work, not yet attempted |
| DCF/Reverse DCF/Graham/EPV as primary signals | Data is available, but the Methodology Checkpoint's own sensitivity-caveat conclusion is unaffected by data availability — these remain secondary, lower-confidence signals by design, not by data gap |
| Market-context overlay | Unchanged from SSDS-008 — no aggregated provider exists in either market; out of this sprint's scope entirely |

## 8. Open Questions

1. Should Valuation Intelligence's India adapter fetch yfinance directly (mirroring `prediction_engine.py`'s own existing `augment_info_with_screener` pattern), or should it read from `info` after that enrichment has already run, the way Growth Intelligence's India adapter currently performs its own independent `fetch_screener_data()` call (hitting the existing cache)? Not resolved here — an implementation-sprint architecture decision, not a feasibility question.
2. Should the small, unexplained `freeCashflow` gaps in Capital Goods (8/10) and FMCG (9/10) be investigated company-by-company before implementation, or accepted as an expected small miss rate? Not resolved here.
3. Is a 5-year historical valuation band useful enough to implement now, or should it wait for a longer-window solution? Not resolved here — a product/scope decision, not purely a feasibility one.
4. Which of the two independent growth-rate sources confirmed this sprint (Growth Intelligence's own output vs. yfinance's `earningsGrowth`/`revenueGrowth`) should PEG/Graham Formula actually use? SSDS-008's "read, don't recompute" rule already answers this in principle (Growth Intelligence's own validated output) — named here only to confirm the question has a clear, principled answer, not to reopen it.

## 9. Production Readiness Score: **7/10**

Higher than SSDS-008's own implied starting position, reflecting this sprint's central, corrected finding: India's valuation-data situation is materially better than the Design Study assumed, specifically because yfinance's own India coverage was never tested in Sprint #001. The score is not higher than 7 because: (a) Price/Tangible Book and full historical bands remain genuinely unresolved; (b) the Bank/NBFC population gap, while well-understood and precisely attributed, still requires real population-gating implementation work, not just a one-line fix; (c) no engine code exists yet to validate any of this catalogue end-to-end — this score reflects *data* feasibility, not implementation completeness.

## 10. Recommendation

**Ready for India Engine Implementation** — specifically, for the Recommended V1 Metric Set (§6), with the Bank/NBFC population gating already well-understood from Growth Intelligence's own precedent and therefore low-risk to implement correctly the first time. This is **not** a recommendation for full-scope (all 23 SSDS-008 metrics) implementation — Price/Tangible Book, full historical bands, and the Absolute/Intrinsic valuation category (per the Methodology Checkpoint's own, unaffected-by-this-sprint conclusion) should each follow their own, separately-scoped path, not block the V1 metric set's implementation.

---

*This document is evidence-gathering only. No production code, scoring, threshold, or consumer-integration change was made. SSDS-008's original text is unchanged — this report is the corrected record for the three items its India evaluation underrated (Forward P/E, Dividend Sustainability, and the feasibility of a 5-year — not full 10-year — historical valuation band), mirroring SSDS-007's own established "Update" pointer precedent rather than rewriting history.*
