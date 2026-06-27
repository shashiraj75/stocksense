# SSDS-004 — StockSense360 India Fundamentals Data Strategy

**Status:** Strategy proposal. No production code modified in producing this document.
**Objective:** the safest data architecture for Indian fundamentals so the Business Quality Engine (SSDS-003) can support Indian stocks without relying on yfinance as the primary source.
**Governed by:** SES-001 through SES-005, SSDS-000, SSDS-003, the StockSense360 Product Glossary.

---

## 1. Review of the Current Indian Fundamentals Refresh Flow

Two distinct paths exist today, confirmed by reading the code, not assumed:

### Path A — Live prediction flow (`prediction_engine.py`, used by every IN prediction)
1. `yf.Ticker(symbol + ".NS").info` — raw yfinance, confirmed sparse/stale for IN (the entire reason the other two layers exist).
2. `augment_info_with_screener(info, symbol)` (`screener_data.py`) — overrides/fills `trailingPE`, `returnOnEquity`, `returnOnCapitalEmployed`, `revenueGrowth`, `earningsGrowth`, `sector`, `industry`, `debtToEquity`, `promoterHolding`, `heldPercentInsiders`, `heldPercentInstitutions`, plus a `_screener_data` sub-dict carrying additional fields (see §2).
3. **BSE fallback** (`bse_data.py`'s `get_bse_fundamentals`) — triggered only when yfinance returns fewer than 3 of 6 key fields (a "merged/renamed company" detector). Already returns yfinance-`.info`-shaped keys: `marketCap`, `trailingPE`, `trailingEps`, `bookValue`, `returnOnEquity`, `debtToEquity`, `dividendYield`, `totalRevenue`, `netIncomeToCommon`, `profitMargins`, `revenueGrowth`. **Confirmed already integrated and working** — this is not a new source to propose, it's an existing one most of this document's recommendations should route through.

### Path B — Nightly refresh (`fundamentals_refresh.py`, feeds `stock_fundamentals_cache` for Multibagger)
Calls `fetch_screener_data(symbol)` directly — **no yfinance `Ticker` is ever constructed in this path.** This is the architectural fact that made Sprint #005's Multibagger integration US-only (see Sprint #005's report) and is the central constraint this entire strategy must design around.

**Both paths are confirmed live, working, production code today** — this document proposes how to extend them, not replace them.

---

## 2. What Currently Comes From Screener.in

Confirmed by reading `screener_data.py`'s scraper directly (770 lines), not inferred:

| Category | Fields scraped today |
|---|---|
| Identity/classification | `company_name`, `sector_name`, `industry_name`, `broad_sector`, `broad_industry` |
| Valuation | `market_cap_cr`, `current_price`, `pe_ratio`, `book_value`, `dividend_yield_pct`, `face_value` |
| Profitability | `roce_pct`, `roe_pct` |
| Banking-specific | `net_npa_pct`, `gross_npa_pct`, `nim_pct`, `casa_ratio_pct`, `capital_adequacy_ratio_pct`, `nii_cr` |
| Growth | `sales_growth_3y_pct`, `sales_growth_ttm_pct`, `profit_growth_3y_pct`, `profit_growth_ttm_pct` |
| P&L history | `sales_annual_cr`, `operating_profit_annual_cr` (an EBIT proxy), `opm_pct`, `interest_latest_cr`, `depreciation_latest_cr` |
| Shareholding | `promoter_holding_pct` (+ quarterly history), `fii_holding_pct`, `dii_holding_pct`, `public_holding_pct`, `promoter_pledge_pct` |
| Cash flow | `operating_cf_annual_cr`/`operating_cf_latest_cr`, `investing_cf_annual_cr`/`investing_cf_latest_cr` |
| Balance sheet (**liabilities side only**) | `equity_capital_cr`, `reserves_annual_cr`/`reserves_latest_cr`, `borrowings_annual_cr`/`borrowings_latest_cr`, `total_liabilities_annual_cr`, `fixed_assets_annual_cr` (the one asset-side line scraped) |
| Quarterly | `latest_quarter_revenue_cr`/`quarterly_revenue_cr`, `latest_quarter_pat_cr`/`quarterly_pat_cr` |
| Derived | `debt_to_equity_pct`, `interest_coverage_ratio`, `ebitda_cr`, `ev_ebitda` |

**Confirmed, explicit, in-code limitation:** the scraper does not pull screener.in's expandable "Other Assets" sub-table (`screener_data.py:609-611`'s own comment: *"no cash netting since screener.in doesn't expose a clean top-level Cash row (it's nested inside an expandable 'Other Assets' sub-table we don't scrape)"*). This sub-table is where Current Assets, Cash, Investments, and Receivables would live — **this single scraping gap is the root cause of most of §3's findings below.**

---

## 3. What Business Quality Engine Fields Are Missing for Indian Stocks

Evaluated against SSDS-003's actual metric requirements and the engine's real code (`business_quality_engine.py`, `quality_factors.py`), not assumed — distinguishing what's *genuinely* missing from what's available-but-not-yet-wired:

| Metric | Status for IN | Evidence |
|---|---|---|
| **Sloan Accruals — Net Income** | **Already available** | `sloan_accruals_signal` already has a screener fallback: `quarterly_pat_cr` annualized (confirmed in `quality_factors.py`). |
| **Sloan Accruals — Operating CF** | **Already available** | Same function already falls back to `_screener_data["operating_cf_latest_cr"]`. |
| **Sloan Accruals — Total Assets** | **Genuinely missing, but likely cheaply derivable** | No current/total-assets field is scraped. **High-confidence hypothesis, not yet verified:** screener.in's "Total Liabilities" line, in standard Indian balance-sheet presentation, is the balancing total (Assets = Liabilities + Equity by definition) — `total_liabilities_annual_cr` may already equal Total Assets without any new scraping. **Must be verified against a real screener.in page before relying on it** (Open Question, §9). |
| **Altman Z-Score — X1 (Working Capital)** | **Genuinely missing** | Requires Current Assets and Current Liabilities specifically (not just totals) — both live inside the unscraped "Other Assets" sub-table. No fallback exists. |
| **Altman Z-Score — X2 (Retained Earnings)** | **Likely available, not yet verified** | `reserves_latest_cr` is plausibly the same accounting concept (Reserves & Surplus ≈ Retained Earnings in Indian B/S presentation) — needs a sign/definition check, not a new scrape. |
| **Altman Z-Score — X3 (EBIT)** | **Already available, not yet wired** | `operating_profit_latest_cr` is already explicitly documented in-code as "(EBIT proxy)" — this is a wiring gap, not a data gap. |
| **Altman Z-Score — X4 (Market Cap / Total Liabilities)** | **Already available** | `market_cap_cr` and `total_liabilities_annual_cr`/`borrowings_latest_cr` are both already scraped. |
| **Altman Z-Score — X5 (Revenue/Assets, non-IN model only)** | N/A for IN | The IN model (Z') doesn't use X5 — not a gap. |
| **Cash Conversion Ratio (new SSDS-003 metric)** | **Genuinely broken for IN today — confirmed, not hypothesized** | `business_quality_engine.py`'s `_compute_cash_conversion()` reads only top-level `info.get("operatingCashflow")`/`info.get("netIncome")` — **it has no `_screener_data` fallback at all**, unlike `sloan_accruals_signal`. The raw data this metric needs (`operating_cf_latest_cr`, `quarterly_pat_cr`) is already scraped and already sitting in `_screener_data` — this is a pure wiring gap in code this strategy document is explicitly not allowed to fix yet. |
| **Asset Turnover (new SSDS-003 metric)** | **Genuinely missing** | Needs `ticker.balance_sheet`'s "Total Assets" row, which doesn't exist for IN tickers (yfinance has essentially no balance-sheet coverage for NSE-listed companies — the entire reason screener.in exists in this codebase). Same Total-Assets gap as Sloan/Altman. |
| **Working Capital Trend (new SSDS-003 metric)** | **Genuinely missing** | Same Current Assets/Liabilities gap as Altman's X1. |
| **Beneish M-Score (new SSDS-003 metric)** | **Genuinely, substantially missing** | Needs Receivables and SG&A specifically — neither is scraped by screener.in today, and neither is a balancing-identity derivation like Total Assets. This is the metric with the largest real gap for IN. |
| **Piotroski F-Score (existing, via `quality_metrics_score`)** | **Pre-existing, unrelated gap** | Uses `ticker.financials`/`.balance_sheet` directly (yfinance) with no screener fallback at all today — a different, pre-existing condition, not introduced or addressed by this strategy. Named here for completeness, not proposed for a fix in this document. |
| **Buffett/Munger checklist (existing, via `buffett_munger_score`)** | Mostly usable today | Operates primarily on `info`-level fields already augmented by screener.in. |
| **Corporate Actions (existing, via `corporate_actions_score`)** | Partially usable | Dividend history works (`ticker.dividends`, yfinance-covered even for IN in most cases); buyback/dilution detection needs `ticker.cashflow`, same yfinance-coverage gap as Piotroski. |

**Headline finding: roughly half of what SSDS-003 needs is either already available or cheaply wireable from data already scraped. The remaining gap concentrates in exactly two places — the balance sheet's asset side (Current Assets, Cash, Receivables) and Beneish's SG&A requirement — both stemming from the single, named, deliberate scraping gap in §2.**

---

## 4. Source Evaluation

| Source | What it offers | Reliability/coverage | Licensing posture |
|---|---|---|---|
| **Screener.in** (current, primary) | Everything in §2; the asset-side gap is closeable by scraping the same page's "Other Assets" sub-table — no new source needed for that specific gap. | Proven in production today; rate-limited, scrape-based (not an official API) — already the subject of "politeness delay" handling in `fundamentals_refresh.py`. | **Personal/internal-use scraping of a third-party site carries inherent ToS risk** — this codebase already accepts that risk for the fields it scrapes today; expanding the scrape (the "Other Assets" sub-table) is the same risk profile, not a new one, but should be confirmed against Screener.in's current Terms of Service before expanding (Open Question, §9 — this document does not have access to Screener.in's current ToS and is not making a legal determination). |
| **NSE corporate filings** (XBRL financial results, shareholding pattern) | The fix for the genuine gaps: full balance sheets (current assets/liabilities, receivables), official quarterly/annual results. NSE already has two integrations in this codebase (`nse_client.py` for quotes, `nse_fii_dii.py`, `nse_pledge.py`) — confirms NSE access patterns are already a known, working quantity, not a cold start. | NSE's XBRL filings are official, mandated disclosures — generally the **most authoritative** fundamentals source available, by definition (companies file these themselves). No bulk/structured API is currently used in this codebase for financial-statement XBRL specifically — would require new integration work, not zero-cost. | NSE is the exchange of record; redistribution constraints are a real, named open question (**explicitly flagged by this task's own context** as having "licensing and redistribution constraints") — internal computational use (deriving a score, not republishing the raw filing) is a materially different legal question than redistribution, and this document does not have the standing to resolve it (Open Question, §9). |
| **BSE filings / BSE API** | **Already integrated and working** (`bse_data.py`) for several core fields. Same XBRL-filing concept as NSE for the gap-closing fields, with a real, currently-unused BSE API client already in this codebase to potentially extend. | Already proven as a working fallback in production (Path A, §1) — lower integration risk than NSE filings, since the client already exists. | Same category of open question as NSE — BSE is also an exchange of record; same internal-use-vs-redistribution distinction applies. |
| **Paid data vendors** (e.g., a commercial fundamentals API) | Could close every gap (Beneish's SG&A/Receivables, full balance sheets, multi-year history) in one integration, with an actual support contract and defined data-licensing terms — the only source category in this table where licensing terms would be *contractually explicit* rather than inferred from a public website's general ToS. | Highest reliability/completeness of any option; cost and vendor-selection are the trade-offs. | **Cleanest licensing path** by construction — a paid vendor's contract defines exactly what's permitted. Requires actual vendor evaluation/procurement, out of this document's scope to select. |
| **yfinance** | Already in use; explicitly **not** to be the primary source per this task's objective. | Confirmed in this same codebase's own comments and Sprint #004's findings to have rate-limit and completeness issues for NSE-listed companies specifically (the entire reason screener.in/BSE exist as IN sources). | No specific IN-redistribution concern beyond yfinance's general terms — not the binding constraint for IN; data completeness is. |

---

## 5. Proposed Provider Abstraction

```
IndiaFundamentalsProvider (interface)
    ├── ScreenerProvider        — wraps the existing screener_data.py scraper (current primary)
    ├── BSEFilingsProvider      — wraps the existing bse_data.py client (current, proven fallback)
    ├── NSEFilingsProvider      — NEW, not yet built — XBRL financial-results / shareholding-pattern access
    ├── FuturePaidProvider      — NEW, not yet built — placeholder for a commercial vendor, pending procurement
    └── FallbackProvider        — wraps yfinance specifically as the last resort, never the first
```

**Design intent, consistent with SES-002 §5 ("new, single-purpose modules are preferred over adding another unrelated concept to an existing god-file"):** each provider is a thin adapter normalizing its source's native shape into one common, yfinance-`.info`-shaped output (since `business_quality_engine.py` and `quality_factors.py` already speak that vocabulary, and `bse_data.py` already proves this normalization pattern works in practice). **This is a proposed interface, not implemented in this document** — per the task's explicit "do not implement code yet."

**Priority order is fixed, not per-field:** a provider chain tries `ScreenerProvider` → `BSEFilingsProvider` → `NSEFilingsProvider` (once built) → `FuturePaidProvider` (once procured) → `FallbackProvider` (yfinance), stopping at the first provider that returns the needed field — exactly the existing pattern already proven in Path A (`augment_info_with_screener` → BSE fallback), generalized rather than invented fresh.

---

## 6. Provider Priority Matrix and Field Mapping

| SSDS-003 field needed | Primary provider | Fallback 1 | Fallback 2 | Notes |
|---|---|---|---|---|
| ROE, ROCE, revenue/profit growth, D/E, promoter holding | `ScreenerProvider` | `BSEFilingsProvider` | `FallbackProvider` (yfinance) | **Already working today, unchanged by this proposal.** |
| Sloan Net Income, Sloan OCF | `ScreenerProvider` (`quarterly_pat_cr`, `operating_cf_latest_cr`) | `BSEFilingsProvider` (`netIncomeToCommon`, none for OCF today) | `FallbackProvider` | Already wired for Sloan specifically; **not** yet wired for Cash Conversion (a code gap, not a data gap — see §3). |
| Total Assets (Sloan, Altman, Asset Turnover) | `ScreenerProvider`, **pending verification** of the `total_liabilities_annual_cr` balancing-identity hypothesis | `NSEFilingsProvider` (once built) | `FuturePaidProvider` | Single highest-leverage item in this entire matrix if the hypothesis holds — closes 3 metrics' gaps at once for near-zero new scraping. |
| Retained Earnings (Altman X2) | `ScreenerProvider` (`reserves_latest_cr`, pending sign/definition verification) | `NSEFilingsProvider` | `FuturePaidProvider` | |
| EBIT (Altman X3) | `ScreenerProvider` (`operating_profit_latest_cr`) — **already scraped, just needs wiring** | — | — | Not a sourcing problem at all; a code-wiring gap, out of this document's "no code changes" scope. |
| Working Capital / Current Assets / Current Liabilities (Altman X1, Working Capital Trend) | `NSEFilingsProvider` (once built) — full balance sheet | `ScreenerProvider`, if the "Other Assets" sub-table is added to the existing scraper (an extension of the *existing* source, not a new one) | `FuturePaidProvider` | The single largest genuine, unresolved gap. |
| Receivables, SG&A (Beneish M-Score) | `NSEFilingsProvider` (XBRL results include these) | `FuturePaidProvider` | — | Screener.in's main fundamentals page does not carry these at all, even in the unscraped sub-table, to the best of this document's research — **needs direct confirmation**, not assumed (Open Question, §9). |

---

## 7. Caching Strategy

Proposed, extending the **already-proven** pattern (`stock_fundamentals_cache`, nightly refresh) rather than inventing a new one:

- **Nightly refresh:** the existing `fundamentals_refresh.py` cadence is reused, unchanged in *schedule* — providers are added to the call chain it already orchestrates (per-symbol), not a new job.
- **Stale data handling:** identical to the existing convention — `updated_at` per row, `last_refreshed()` already exposed to the frontend; no change proposed.
- **Failure handling:** identical to the existing per-provider try/except pattern already used for screener.in/BSE in Path A — a provider failing for one symbol must never block the chain from trying the next provider, exactly as today's BSE fallback already behaves.
- **Confidence impact:** per SSDS-003 §5's existing, already-implemented rule (`MIN_DATA_COMPLETENESS_PCT`, confidence tied to the fraction of mandatory metrics present) — **no new confidence mechanism is needed.** A field sourced from a lower-priority provider (e.g., `NSEFilingsProvider` instead of `ScreenerProvider`) does not need its own separate confidence discount under this design; SSDS-003's existing completeness-based confidence already captures "how much real data did we get," which is the variable that actually matters to a downstream consumer. **Recommendation: do not add a per-provider confidence weighting** — it would duplicate a mechanism that already exists and was already validated (Sprint #004a/Final Validation), adding complexity without new evidence it's needed.

---

## 8. Licensing and Compliance Notes

**This document does not make legal determinations — it names what needs legal/vendor review, consistent with "evidence over assumptions": absence of a confirmed answer is reported as an open question, not assumed safe or assumed unsafe.**

| Question | Status |
|---|---|
| Can Screener.in-scraped data continue to be used internally (computing scores, not republishing the page)? | **Already the operating assumption of this entire codebase today** (the existing IN refresh/prediction pipeline already does this) — this document does not change that exposure, since it proposes extending an existing scrape (the same page's sub-table), not a new site. |
| Can Screener.in data, or scores derived from it, be redistributed (e.g., shown to a user, exported)? | **Already happening today** (every IN prediction and Multibagger screen result is "redistribution" of Screener.in-derived data to an end user) — this document inherits that existing exposure; it does not introduce a new one. **Flagged for legal review as an existing condition, not a new risk created by this proposal.** |
| Can NSE/BSE corporate-filing data be used internally? | **Likely yes for internal computation** (these are public, mandated disclosures) — but this document explicitly does not have the standing to confirm NSE/BSE's specific terms for systematic/bulk access or redistribution. **Requires legal/vendor review before `NSEFilingsProvider` is built.** |
| Can NSE/BSE-derived scores be shown to end users (the same redistribution question as Screener.in)? | **Same open question as above** — NSE is explicitly named in the task's own context as having "licensing and redistribution constraints." **Do not build `NSEFilingsProvider` against any bulk/structured NSE endpoint until this is reviewed.** |
| What would a paid vendor's contract permit? | **Unknown until a vendor is selected and a contract reviewed** — by construction, this is the one source category where the answer would be explicit and unambiguous, which is itself a reason to weight it favorably in §9's recommendation despite the cost. |

---

## 9. Risks and Open Questions

1. **Unverified hypothesis (highest priority to resolve):** does Screener.in's "Total Liabilities" line equal Total Assets by balance-sheet identity for the companies this codebase covers? If yes, this single verification (not a new scrape, not a new provider) closes the Sloan/Altman/Asset-Turnover Total-Assets gap for IN at near-zero cost. If no, this gap requires `NSEFilingsProvider` or expanded scraping instead.
2. **Unverified hypothesis:** does `reserves_latest_cr` correctly represent Retained Earnings for Altman's X2, including sign convention for companies with accumulated losses?
3. **Confirmed but unscoped-for-this-document code gap:** `_compute_cash_conversion()` has no IN/`_screener_data` fallback at all, unlike its sibling `sloan_accruals_signal`. This is real, fixable, and **not addressed here** per the "no code changes" rule — named explicitly as a near-term, low-risk fix candidate for whichever sprint eventually implements this strategy.
4. **Legal/licensing review not yet done** for NSE/BSE bulk/structured access and for Screener.in's current ToS — both named in §8, neither resolved by this document.
5. **Screener.in's "Other Assets" sub-table scrape is unbuilt** — even if pursued, its actual content/coverage hasn't been inspected line-by-line against what Beneish/Working-Capital need; may only partially close the gap.
6. **No NSE/BSE XBRL-results integration exists today** for *financial-statement* data specifically (only quotes, FII/DII flow, and pledge data are currently integrated from NSE) — `NSEFilingsProvider` is a genuinely new build, not a re-wire of something that already exists, unlike `BSEFilingsProvider`.
7. **Paid vendor cost/selection is entirely unscoped** — this document identifies the category as an option, not a specific vendor or cost estimate.

---

## 10. Recommendation: Should Sprint #006 Implement an India Business Quality Adapter, or Wait?

**Recommendation: a narrow, two-stage path — neither a full "implement now" nor an unconditional "wait."**

**Stage 1 (low-risk, high-confidence, recommended for Sprint #006 itself):** verify the two hypotheses in Open Questions #1 and #2 against real Screener.in data (a research task, not a "production code change" — pulling a few real pages and checking the numbers by hand or with a throwaway script satisfies "evidence over assumptions" without touching `services/`). **This alone determines whether most of the IN gap closes almost for free, or requires the larger NSEFilingsProvider build.** Doing this first, before committing to any provider build, is the single highest-leverage, lowest-risk next step.

**Stage 2 (conditional on Stage 1's findings):**
- **If the Total-Assets and Retained-Earnings hypotheses hold:** the safest near-term path is **extending `ScreenerProvider`'s existing scrape** (adding the "Other Assets" sub-table parse, and wiring the already-scraped `operating_profit_latest_cr`/`total_liabilities_annual_cr`/`reserves_latest_cr` into the fields `business_quality_engine.py` already reads) — no new provider, no new licensing exposure beyond what already exists, and it would close the Sloan/Altman/Cash-Conversion/Asset-Turnover gaps for IN almost entirely. This would be the actual implementation content of a future sprint, not Sprint #006 itself per this task's "do not implement code" rule.
- **If the hypotheses don't hold:** the IN gap is real and substantial (full balance sheet, Beneish's Receivables/SG&A) and **does** require either `NSEFilingsProvider` (pending the legal review named in §8) or a paid vendor — a materially larger, slower undertaking that should not be started until that legal review is complete.

**Do not implement an India Business Quality Adapter into any production consumer in Sprint #006 regardless of Stage 1's outcome** — per this task's explicit scope, and because SSDS-003's own Final Production-Readiness Validation and Sprint #005's report both already established the pattern of validating a calibration change against the same live dataset *before* any consumer integration. The same discipline applies here: verify the data hypotheses, then (if they hold) implement and *re-validate* the IN-side metric fixes in isolation, then only *after* that, consider wiring an IN consumer — mirroring exactly the US sequence (Sprint #004 → #004a → final validation → Sprint #005), not skipping steps because IN is a smaller lift than building a new provider.

---

*This document is a strategy proposal. No production code was modified in producing it. No Business Quality Engine integration into any Indian consumer was performed or proposed for immediate implementation.*
