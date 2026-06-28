# SSDS-005 Data Validation Report — Financial Strength Intelligence

**Status:** Research and feasibility report. No production code modified. No engine implemented, no thresholds tuned, no scoring calibrated — per this sprint's explicit scope.
**Epic:** 002 — Financial Strength Intelligence, Sprint #001 (data-feasibility study, the immediate next action named by [SSDS-005](../SSDS/SSDS-005-StockSense360-Financial-Strength-Intelligence-Engine.md) and [MASTER-ROADMAP.md](../../MASTER-ROADMAP.md) §4).
**Method:** live data pulled directly from production code paths (`services.screener_data.fetch_screener_data()` for India, `yfinance.Ticker.info`/`.balance_sheet`/`.cashflow`/`.financials` for US) — not estimated, not assumed, not read from cached fixtures. Every coverage number below is the output of a script run in this engagement, reproducible against the same tickers.
**Relationship to prior work:** this report tests SSDS-005's Data Requirements section and the Design Study's Open Questions against live evidence, mirroring Sprint #006's methodology for Business Quality Intelligence exactly (per the Validation Strategy SSDS-005 commissions).

---

## Executive Summary

**Headline result: the two markets are not symmetrically ready.** US data feasibility is strong and broad; India data feasibility is split into two very different stories — fields the existing scraper already captures are solid, but the specific fields Financial Strength needs most (current assets, current liabilities, cash & equivalents, debt-maturity split) are **confirmed, structurally absent** — the same gap SSDS-004 named for Business Quality Intelligence over a year of Epic 001's work, now reconfirmed unchanged and shown to be more damaging to Financial Strength than it was to Business Quality, because Financial Strength's Liquidity Adequacy category depends on exactly these fields.

**A second, operationally critical finding, encountered directly during this study and not anticipated in SSDS-005:** unauthenticated access to screener.in is **not viable at any meaningful batch volume**. This study's own live run was IP-blocked (connection refused) by screener.in after approximately 12–15 requests in under two minutes, with no `SCREENER_EMAIL`/`SCREENER_PASSWORD` credentials available in this engagement's environment. This is not a new risk — `services/screener_data.py`'s own docstring already states authentication exists "so that requests from cloud IPs are not blocked" — but this study is the first time in this engagement that unauthenticated behavior was directly observed and confirmed, rather than assumed safe. **This blocked completion of the full ≥70-company live verification for India within this session** (15 of 74 attempted companies were verified with real data before the block; the remaining 59 are reported as untested, not as failures).

**What this report can say with full confidence, evidence-backed:**
- **US:** every Financial Strength category has a usable data path for the large majority of companies tested (70/70, zero errors), with one clear, structurally-explained exception — the FINANCIAL sector (banks/NBFC/insurance) has **no** current-ratio/quick-ratio/working-capital data at all, at either the `.info` or balance-sheet-statement level, confirmed across 19/19 financial-sector US companies. This is not a defect; it mirrors exactly why Business Quality's `is_financial` exemption exists, and confirms the same exemption is required for Financial Strength's Liquidity Adequacy category.
- **India:** for the 15 companies verified before the block, fields the scraper already captures (total debt, total equity, EBIT proxy, EBITDA, interest expense, OCF, 12-year multi-year history) are present at 80–93%, **except for financial-sector companies**, where the same fields drop to near-zero (0/3 live financial-sector samples had EBIT, EBITDA, debt, or interest-coverage data) — confirming the FINANCIAL exemption is needed on the India side too, and confirming it earlier and more severely than the US side. **Current assets, current liabilities, cash & equivalents, and debt-maturity split are 0% available for every India company tested — a structural fact, confirmed by direct source-code reading of `screener_data.py`, not a sampling artifact.**

**Go/No-Go (full detail in §9):** **Conditional Go for US**, **No-Go for India until the structural Liquidity Adequacy gap is closed or the category is redesigned for India**, and **No-Go for any further live study in this environment until `SCREENER_EMAIL`/`SCREENER_PASSWORD` are available.**

---

## Methodology

**Inputs reviewed before any data was pulled:** SSDS-005 (full), the Financial Strength Intelligence Design Study, EPIC-001 Closure Report, SSDS-000, SSDS-003, SSDS-004, the StockSense360 Product Glossary, SES-001 through SES-005 — per this sprint's explicit instruction.

**Live data collection:**
- **India:** called `services.screener_data.fetch_screener_data(symbol)` directly — the exact function the production nightly refresh and live-prediction paths both call — for 74 NSE-listed companies. No mocking, no fixtures.
- **US:** called `yfinance.Ticker(symbol).info`, `.balance_sheet`, `.cashflow`, and `.financials` directly for 70 US-listed companies — the same library `us_fundamentals.py` already depends on.
- **No production code was modified.** No write to `stock_fundamentals_cache` or any other table occurred. No threshold was tuned. No scoring logic was touched.

**Sample composition (target: ≥70 per market, per this sprint's brief):**

| Market | Target | Attempted | Verified with live data | Notes |
|---|---|---|---|---|
| India | ≥70 | 74 | **15** | 59 untested due to the screener.in IP-block encountered mid-run (see Executive Summary and §6). Not reported as "unavailable" — reported honestly as "not verified this session." |
| US | ≥70 | 70 | **70** | Full target met, zero errors. |

Both ticker lists were built to cover every category named in this sprint's brief: Large Cap, Mid Cap, Small Cap, Banks, NBFC, Insurance, Telecom, Pharma, Energy, Utilities, IT, Consumer, Industrials, loss-making companies, highly-leveraged companies, and cash-rich companies. Every category has at least one representative in both the full attempted list and (for India) the 15-company verified subset, **except** that the 15-company India subset under-represents Insurance (1), NBFC (1), and contains no Telecom, Pharma, or pure Consumer names — named explicitly here as a sampling limitation, not glossed over.

**Why this report does not simply re-run with delays to reach 70:** a deliberate choice, not an oversight. Retrying against a host that has just rate-limited/blocked this IP, without the credentials the production system actually uses, would not produce evidence representative of how the system runs in production — it would only describe this sandboxed session's specific network conditions. Per SES-001 §3 (evidence over assertion) and SES-004 §6 (honesty about gaps), the correct response to an environment limitation is to name it and recommend the credentialed re-run as the next concrete step (§10), not to manufacture a larger sample that doesn't reflect real conditions.

---

## Field Inventory

Every field SSDS-005's Data Requirements section names, mapped to the category it serves and the raw source field(s) checked in this study:

| SSDS-005 category | Required field | India raw source checked | US raw source checked |
|---|---|---|---|
| Liquidity Adequacy | Current assets | *(confirmed absent — see §5)* | `balance_sheet` row `"Current Assets"` |
| Liquidity Adequacy | Current liabilities | *(confirmed absent)* | `balance_sheet` row `"Current Liabilities"` |
| Liquidity Adequacy | Cash & equivalents | *(confirmed absent)* | `info["totalCash"]` |
| Liquidity Adequacy | Current ratio / quick ratio | *(not derivable — see §5)* | `info["currentRatio"]`, `info["quickRatio"]` |
| Leverage & Capital Structure | Total debt | `borrowings_latest_cr`/`_annual_cr` | `info["totalDebt"]` |
| Leverage & Capital Structure | Debt maturity split (short vs. long-term) | *(confirmed absent)* | `balance_sheet` rows `"Long Term Debt"`, `"Current Debt"` |
| Leverage & Capital Structure | Multi-year debt trend | `borrowings_annual_cr` (list) | `balance_sheet` multi-column history |
| Leverage & Capital Structure | Debt-to-Equity | `debt_to_equity_pct` | `info["debtToEquity"]` |
| Debt-Servicing Capacity | EBIT | `operating_profit_latest_cr`/`_annual_cr` (proxy) | `financials` row `"EBIT"` |
| Debt-Servicing Capacity | EBITDA | `ebitda_cr` | `info["ebitda"]` |
| Debt-Servicing Capacity | Interest expense | `interest_latest_cr` | `financials` rows `"Interest Expense"`/`"Interest Expense Non Operating"` |
| Debt-Servicing Capacity | Interest coverage (direct) | `interest_coverage_ratio` | *(not a direct field — derived)* |
| Balance Sheet Resilience | Total equity | `reserves_latest_cr` + `equity_capital_cr` | `balance_sheet` row `"Stockholders Equity"` |
| Balance Sheet Resilience | Working capital | *(confirmed absent — only an Altman-internal intermediate exists, per SSDS-004)* | `balance_sheet` row `"Working Capital"` |
| Cash Flow Durability Under Stress | Operating cash flow | `operating_cf_latest_cr`/`_annual_cr` | `info["operatingCashflow"]`, `cashflow` row `"Operating Cash Flow"` |
| Cash Flow Durability Under Stress | Free cash flow | *(not directly scraped — derivable from OCF + investing CF, unverified precision)* | `info["freeCashflow"]`, `cashflow` row `"Free Cash Flow"` |
| Cash Flow Durability Under Stress | Capital expenditure | *(not separately scraped)* | `cashflow` row `"Capital Expenditure"` |

---

## Coverage Tables

### India — overall, 15 verified companies (the only honestly-measurable sample this session)

| Field | Coverage | Notes |
|---|---|---|
| Total debt (`borrowings_latest_cr`) | 12/15 (80.0%) | The 3 missing are exactly the 3 financial-sector names in the sample (see §5). |
| Total equity (`reserves` + `equity_capital`) | 14/15 (93.3%) | 1 missing (SBILIFE — insurance; see §5). |
| EBIT proxy (`operating_profit_latest_cr`) | 12/15 (80.0%) | Same 3 financial-sector gaps. |
| EBITDA (`ebitda_cr`) | 12/15 (80.0%) | Same. |
| Interest expense (`interest_latest_cr`) | 14/15 (93.3%) | 1 missing (SBILIFE). |
| Operating cash flow | 14/15 (93.3%) | 1 missing (SBILIFE). |
| Free cash flow (OCF + investing CF, derivable) | 14/15 (93.3%) | A derivation, not a direct field — precision not independently verified this study. |
| Multi-year EBIT history (≥3 years) | 12/15 (80.0%) | Where present, 12 years deep (`operating_profit_annual_cr`) — far exceeding yfinance's 4–5-year US cap. |
| Multi-year debt history (≥3 years) | 12/15 (80.0%) | Same 12-year depth. |
| Interest coverage (direct field) | 12/15 (80.0%) | Same 3 financial-sector gaps. |
| Debt-to-equity (direct field) | 12/15 (80.0%) | Same. |
| **Current assets** | **0/15 (0.0%)** | **Confirmed structurally absent — see §5.** |
| **Current liabilities** | **0/15 (0.0%)** | **Confirmed structurally absent.** |
| **Cash & equivalents** | **0/15 (0.0%)** | **Confirmed structurally absent.** |
| **Debt maturity split** | **0/15 (0.0%)** | **Confirmed structurally absent.** |
| **Free cash flow (direct field)** | **0/15 (0.0%)** | Not a scraped field at all; only the derivation above exists. |

### India — financial-sector sub-sample (3/15: ICICIBANK, BAJFINANCE, SBILIFE)

| Field | Coverage | 
|---|---|
| Total equity | 2/3 (66.7%) |
| Interest expense, OCF | 2/3 (66.7%) — both present for ICICIBANK/BAJFINANCE, both absent for SBILIFE |
| Total debt, EBIT, EBITDA, interest coverage, D/E | **0/3 (0.0%)** |

**This is the same pattern SSDS-004 already confirmed for Business Quality's `debt_to_equity_pct`/`borrowings_latest_cr` fields (78% overall, with "the 14 missing are exactly the financial-sector companies")** — this study reconfirms it independently for Financial Strength's own required fields, with a smaller but directionally identical sample.

### US — overall, 70/70 verified companies

| Field | Coverage |
|---|---|
| Total debt (`info`) | 70/70 (100.0%) |
| Total cash (`info`) | 70/70 (100.0%) |
| Current ratio (`info`) | 58/70 (82.9%) |
| Quick ratio (`info`) | 58/70 (82.9%) |
| EBITDA (`info`) | 57/70 (81.4%) |
| Free cash flow (`info`, direct) | 56/70 (80.0%) |
| Operating cash flow (`info`) | 69/70 (98.6%) |
| Debt-to-equity (`info`) | 52/70 (74.3%) |
| Current assets (balance sheet) | 51/70 (72.9%) |
| Current liabilities (balance sheet) | 51/70 (72.9%) |
| Long-term debt (balance sheet) | 68/70 (97.1%) |
| Current (short-term) debt (balance sheet) | 63/70 (90.0%) |
| Total equity (balance sheet) | 70/70 (100.0%) |
| Working capital (balance sheet) | 51/70 (72.9%) |
| Free cash flow (cashflow statement) | 70/70 (100.0%) |
| Capital expenditure (cashflow statement) | 61/70 (87.1%) |
| Interest paid (cashflow statement) | 60/70 (85.7%) |
| Operating cash flow (cashflow statement) | 70/70 (100.0%) |
| Interest expense (income statement) | 67/70 (95.7%) |
| EBIT (income statement) | 55/70 (78.6%) |
| Balance-sheet history depth | min 4, max 5, avg 4.94 years |
| Cash-flow history depth | min 4, max 5, avg 4.96 years |

### US — FINANCIAL sector sub-sample (19/70: banks, NBFC, insurance)

| Field | Coverage | Missing symbols |
|---|---|---|
| Current assets (statement) | **0/19 (0.0%)** | All 19 |
| Current liabilities (statement) | **0/19 (0.0%)** | All 19 |
| Working capital (statement) | 0/19 (0.0%) | All 19 |
| Current ratio (`info`) | 0/19 (0.0%) | All 19 |
| Quick ratio (`info`) | 0/19 (0.0%) | All 19 |
| Long-term debt (statement) | 19/19 (100.0%) | None |
| Total debt (`info`) | 19/19 (100.0%) | None |
| Free cash flow (statement) | 19/19 (100.0%) | None |

**This is the single cleanest confirmed finding in this study:** US financial-sector companies have full debt and cash-flow coverage but **zero** liquidity-ratio coverage at any level, for any of the 19 companies sampled — not a partial gap, not a sampling artifact, a complete and consistent absence that matches the FINANCIAL-sector balance-sheet structure (no conventional "current assets/liabilities" split in a bank's statement) rather than a data-source defect.

### Sector/segment breakdown — selected fields, US (full table; India table omitted beyond §5/financial-sector above due to the 15-company sample size making most segment cuts too small to be statistically meaningful — naming this limitation rather than presenting noisy percentages as fact)

| Field | Large Cap | Mid Cap | Small Cap | Loss-making | Leveraged |
|---|---|---|---|---|---|
| Current assets (statement) | 81.6% | 57.9% | 69.2% | 80.0% | 100.0% |
| Current liabilities (statement) | 81.6% | 57.9% | 69.2% | 80.0% | 100.0% |
| Long-term debt (statement) | 100.0% | 100.0% | 84.6% | 90.0% | 100.0% |
| Interest expense (statement) | 94.7% | 100.0% | 92.3% | 90.0% | 100.0% |
| Free cash flow (`info`, direct) | 89.5% | 63.2% | 76.9% | 90.0% | 91.7% |

**Reading this table:** coverage does not degrade for loss-making or highly-leveraged companies relative to the overall average — the gap is sector-structural (FINANCIAL), not condition-dependent (distress, leverage). This is good news for the Financial Stress Simulation specifically: the companies most interesting to stress-test (leveraged, loss-making) are not the companies with worse data coverage.

---

## Provider Comparison

| Provider | Market | What it offers for Financial Strength | Reliability observed this study | Licensing posture |
|---|---|---|---|---|
| **screener.in** (`screener_data.py`) | India | Total debt, equity (derivable), EBIT proxy, EBITDA, interest expense, OCF, 12-year multi-year history for all of the above. **Does not offer:** current assets, current liabilities, cash & equivalents, debt-maturity split, direct FCF. | **Unauthenticated: confirmed unreliable at any batch volume — IP-blocked after ~12–15 requests in this study.** Authenticated behavior not tested this session (no credentials available) — per the module's own docstring, authentication exists specifically to avoid this. | Same open ToS question SSDS-004 already named, unchanged by this study. |
| **yfinance** (`.info`) | US (primary), India (secondary) | Aggregate ratios (current ratio, quick ratio, D/E, EBITDA, FCF) at 74–100% coverage for US. For India: confirmed elsewhere (SSDS-004) to be structurally sparse/stale — not re-tested in this study since SSDS-004's finding already stands. | Reliable for US in this study — 70/70 successful calls, zero errors, no rate-limit issues observed (unlike the earlier raw-endpoint probe in this engagement, which did hit a 429 on a different, non-yfinance-library endpoint). | No India-specific concern beyond yfinance's general terms; not the binding constraint for India (data completeness is). |
| **yfinance statements** (`.balance_sheet`, `.cashflow`, `.financials`) | US | The deeper line items `.info` doesn't carry: current assets/liabilities, debt-maturity split, working capital, EBIT, interest expense, capex — at 73–100% coverage, 4–5 years of history. | Reliable for US in this study. **Not currently used by `us_fundamentals.py` for these specific fields** — a wiring opportunity, not a data gap (see §7). | Same as `.info` row. |
| **BSE** (`bse_data.py`) | India (existing fallback) | Confirmed elsewhere (SSDS-004) to supply `.info`-shaped fields equivalent to a subset of screener.in's; not independently re-tested for Financial Strength's specific fields this study (current assets/liabilities/debt-maturity are not part of its documented field set either, per SSDS-004 §1). | Not tested this study. | Same exchange-of-record open question SSDS-004 already named. |
| **NSE** (`nse_client.py`, `nse_fii_dii.py`, `nse_pledge.py`) | India | Quotes, FII/DII flow, pledge data — confirmed by SSDS-004 to have **no** financial-statement/XBRL integration today. Not the source of any field tested in this study. | Not applicable — no current code path exists to test. | Licensing/redistribution review still unresolved per SSDS-004 §8/§9, unchanged. |
| **Existing `stock_fundamentals_cache` / refresh jobs** | Both | Currently caches Business Quality Engine outputs and the fields feeding them; carries **none** of Financial Strength's required new fields today (debt-maturity split, current assets/liabilities for India, EBIT/interest-expense-as-standalone-cached-fields). | Confirmed by direct schema reading (SSDS-000 §5) — no code change needed to add columns later, but nothing is cached today. | N/A. |

---

## Gap Analysis

| Metric/field | Status |
|---|---|
| US: total debt, total equity, OCF, FCF (statement), interest expense, long-term/current debt split | **Already fully supported** — no new work needed. |
| US: current assets/liabilities, working capital, EBIT (statement) | **Reliable but incomplete (73–79%)** — needs a fallback path (e.g., derive EBIT from EBITDA − D&A where the direct row is absent) before being treated as Mandatory. |
| US: current ratio / quick ratio / liquidity for FINANCIAL sector | **Impossible via current providers, by design of bank financial statements** — needs a FINANCIAL-sector-specific Liquidity Adequacy computation (e.g., loan-to-deposit, regulatory capital ratios), not a missing-data problem solvable by trying harder with yfinance. |
| India: total debt, equity, EBIT proxy, EBITDA, interest expense, OCF (non-financial sector) | **Already supported via the existing scraper, conditional on authenticated access** — 80–93% in this study's verified subset. |
| India: EBIT, EBITDA, interest coverage, D/E for FINANCIAL sector | **Confirmed unreliable** (0/3 live) — needs an alternative, sector-specific path (the screener fields already scraped for banking — `net_npa_pct`, `capital_adequacy_ratio_pct`, `nim_pct`, `casa_ratio_pct` — are the natural substitute inputs, unused by any current engine). |
| India: current assets, current liabilities, cash & equivalents, debt-maturity split | **Confirmed unreliable/unsupported today** — the same gap SSDS-004 named (the unscraped "Other Assets" sub-table) and the central blocker for Liquidity Adequacy and the maturity-split dimension of Leverage & Capital Structure. **Needs alternative providers or redesign — see §9.** |
| India: free cash flow (direct) | **Needs redesign, not new scraping** — already derivable today from `operating_cf_latest_cr` + `investing_cf_latest_cr` (a `FuturePaidProvider`/new-scrape is not required for this one); precision of the derivation is unverified, not unavailable. |
| Both markets: Financial Stress Simulation's three shock scenarios | **Not separately assessed by this study** — they recompute existing ratios (interest coverage, cash runway) under a hypothetical shock, so their feasibility is entirely inherited from the underlying ratios' feasibility above, not an independent data question. |

---

## Risk Assessment

| Risk | Severity | Evidence | Mitigation named |
|---|---|---|---|
| Unauthenticated screener.in access is unusable at batch volume | **High — operational, not architectural** | Directly observed in this study: IP-blocked after ~12–15 requests; confirmed by direct `curl` to screener.in returning connection-refused afterward, while general internet connectivity (Google) remained fine. | Use `SCREENER_EMAIL`/`SCREENER_PASSWORD` for any future feasibility/validation/production batch work — already the documented design of `screener_data.py`; this study simply could not exercise it. |
| India Liquidity Adequacy category has no data path today | **High — architectural** | 0/15 (0.0%) across every India company tested, consistent with the structural absence confirmed by direct source reading. | See §9 — extend the existing scrape, pursue NSE/BSE filings, or redesign the category for India specifically. |
| India debt-maturity split has no data path today | **High — architectural** | Same 0/15 finding. | Same options as above; this is the Design Study's own named top-priority open question, now confirmed rather than merely suspected. |
| FINANCIAL-sector companies lack universal liquidity/leverage signals on both markets | **Medium — anticipated, now confirmed, not newly discovered** | US: 0/19 current ratio/current assets. India: 0/3 EBIT/EBITDA/D-E/interest-coverage. | Sector-specific exemption, exactly as SSDS-005's Sector Adaptations section already proposed as a hypothesis — this study converts that hypothesis to a confirmed requirement. |
| US EBIT (statement) coverage gap (78.6%) | **Low–Medium** | 15/70 companies lack the direct `"EBIT"` row. | A derivation fallback (EBITDA − D&A) is a normal, low-risk addition, not a new provider. |
| Free cash flow derivation precision (India) unverified | **Low** | Derivation exists (OCF + investing CF) but accuracy not independently cross-checked this study (unlike the Total-Assets-via-identity check Sprint #006 performed for Business Quality). | Name as a follow-up cross-check in Sprint #002, mirroring Sprint #006's own methodology. |
| Sample size for India segment-level statistics is small (n=15, uneven across segments) | **Medium — methodological** | Named explicitly in §3/§4; this report does not claim segment-level precision it does not have. | A credentialed re-run (§10) is the direct fix. |

---

## Recommended Architecture — Provider Hierarchy

### India (proposed; gated on the credentialed re-run in §10 confirming these percentages at full sample size)

```
1. ScreenerProvider (authenticated) — total debt, equity, EBIT proxy, EBITDA,
   interest expense, OCF, multi-year history (non-FINANCIAL sectors only;
   confirmed 80–93% in this study's verified subset)
2. BSEFilingsProvider — fallback for the same fields, already proven in
   Path A for Business Quality; not independently re-tested for Financial
   Strength's specific fields this study
3. [GAP — no current provider] — current assets, current liabilities,
   cash & equivalents, debt-maturity split: requires either (a) extending
   ScreenerProvider's scrape to the "Other Assets" sub-table (lowest cost,
   per SSDS-004's existing recommendation), or (b) NSEFilingsProvider /
   a paid vendor if (a) proves insufficient
4. FINANCIAL-sector override — use screener.in's existing banking-specific
   fields (net_npa_pct, capital_adequacy_ratio_pct, nim_pct, casa_ratio_pct)
   as the Debt-Servicing-Capacity input for banks/NBFC/insurance, rather
   than the universal EBIT/interest-coverage path (confirmed unavailable
   for this sector)
5. FallbackProvider (yfinance) — last resort, confirmed structurally sparse
   for India by SSDS-004; not re-tested here
```

### US (proposed; confirmed at full 70-company sample)

```
1. yfinance .info — current ratio, quick ratio, total debt, total cash,
   EBITDA, FCF, D/E, OCF (74–100% coverage, non-FINANCIAL sectors)
2. yfinance statements (.balance_sheet / .cashflow / .financials) —
   current assets/liabilities, debt-maturity split, working capital, EBIT,
   interest expense, capex (73–100% coverage, 4–5-year history) — fills
   exactly the fields .info doesn't carry; not currently wired into
   us_fundamentals.py for these fields (a wiring task, not a data gap)
3. FINANCIAL-sector override — Liquidity Adequacy is not computable via
   current-ratio/quick-ratio/working-capital for banks/NBFC/insurance
   (confirmed 0/19); needs a bank-specific substitute (e.g., a
   regulatory-capital or loan-to-deposit-style signal), not a different
   provider — yfinance simply does not carry the underlying concept for
   this sector, because banks' financial statements don't have it either
4. No second data provider is currently needed for US — Finnhub remains
   the existing quote/profile fallback (SSDS-000 §6), not validated here
   as a fundamentals source for these specific fields
```

---

## Go / No-Go Recommendation

| Scope | Recommendation | Why |
|---|---|---|
| **US implementation** | **Conditional Go** | Strong data coverage (70/70 companies, 73–100% across nearly every required field) with one well-understood, design-anticipated exception (FINANCIAL-sector liquidity) and one minor gap (EBIT-statement fallback). Conditional only on: (a) building the FINANCIAL-sector liquidity substitute, (b) wiring `.balance_sheet`/`.cashflow`/`.financials` access into the US adapter (not currently done), (c) the EBIT-derivation fallback. |
| **India implementation** | **No-Go, as scoped today** | Liquidity Adequacy has zero data path (0/15, structurally confirmed) and debt-maturity split has zero data path (0/15, structurally confirmed) — two of five Scoring Categories cannot function for India without new work. Proceeding to implementation now would mean shipping an India engine that silently can't score 40% of its own category framework, which neither matches Epic 001's validate-before-integrate discipline nor SSDS-005's own confidence model (which would correctly reject most India companies as `insufficient_data` under the existing 60%-of-Mandatory-metrics rule once Liquidity Adequacy's metrics are marked Mandatory). |
| **This study's own completeness** | **No-Go on treating this report as final for India until re-run with credentials** | 59 of 74 targeted India companies were not verified due to the screener.in IP-block; the 15-company subset is real and evidence-based but materially smaller than the ≥70 this sprint's brief required, and several requested segments (Telecom, Pharma, Consumer) have zero representation in the verified subset. |
| **Overall epic sequencing** | **Do not proceed to implementation for either market yet** | Per SSDS-005's own Validation Strategy and the Future Sprint Roadmap it specifies: implementation is gated on this feasibility study's findings being resolved, not merely produced. The findings above name concrete, scoped follow-up work (§10) rather than blocking the epic indefinitely. |

---

## Recommended Sprint #002 Scope

Per SSDS-005's own Future Sprint Roadmap, implementation remains gated on this study's findings being resolved. The concrete, scoped follow-up work this study identifies:

1. **Re-run this study's India leg with `SCREENER_EMAIL`/`SCREENER_PASSWORD` credentials** in a production-equivalent environment, against the full 74-company list already built for this study (or larger), to replace the 15-company subset with a real ≥70-company verified sample — the single highest-priority action, since almost every other India finding in this report is provisional until sample size is restored.
2. **Investigate scraping screener.in's "Other Assets" sub-table** (per SSDS-004's existing, still-open recommendation) specifically for Current Assets, Current Liabilities, and Cash & Equivalents — determine whether this closes India's Liquidity Adequacy gap at near-zero new infrastructure cost, mirroring the Total-Assets-via-balance-sheet-identity precedent Sprint #006 already proved for Business Quality.
3. **Determine whether debt-maturity split (short vs. long-term debt) is obtainable from screener.in at all** — this study confirmed the field is absent from `fetch_screener_data()`'s current output but did not attempt a targeted scrape of screener.in's raw per-line-item balance sheet table to check whether the maturity split exists on the page and is simply unparsed (as opposed to genuinely unpublished). This distinction determines whether the fix is "parse more of a page already being fetched" or "build a new provider."
4. **Design and validate a FINANCIAL-sector-specific Liquidity Adequacy and Debt-Servicing Capacity computation path** for both markets — using the banking-specific fields already scraped for India (`net_npa_pct`, `capital_adequacy_ratio_pct`, `nim_pct`, `casa_ratio_pct`) and an equivalent US bank-specific ratio set — rather than assuming the universal current-ratio/EBIT logic extends to this sector, which this study confirms it does not.
5. **Build and validate an EBIT-derivation fallback for the US ~21% statement gap** (e.g., EBITDA minus Depreciation & Amortization), tested against the same 15 US companies currently missing the direct `"EBIT"` row.
6. **Independently cross-check the India free-cash-flow derivation's precision** (`operating_cf_latest_cr` + `investing_cf_latest_cr` as an FCF proxy) against an independent source, mirroring Sprint #006's own cross-check methodology for the Total-Assets identity, before treating it as a Mandatory metric.
7. **Only after 1–6 produce evidence, finalize SSDS-005's illustrative category caps, sector-adaptation rules, and Financial Stress Simulation thresholds into real, calibrated values** — this remains explicitly out of scope for both this study and SSDS-005 itself, per both documents' own stated rules.

**Implementation of `financial_strength_engine.py` remains not recommended until at minimum items 1–4 produce evidence** — items 5–6 affect data quality/precision rather than category feasibility and could plausibly run in parallel with early implementation scaffolding, but not with any live scoring.

---

## Conclusion

This study converts SSDS-005's data-availability assumptions from proposals into evidence, for both markets, using the actual production code paths. **The single most consequential finding is that Financial Strength's data requirements expose a real asymmetry between the two markets that Business Quality Intelligence's own experience did not fully predict:** Business Quality's metrics survived India's data limitations because none of its categories depended on current assets/liabilities or debt-maturity granularity; Financial Strength's Liquidity Adequacy category depends on exactly that, and that data does not exist in India's current pipeline. This is precisely the kind of finding the Design Study's "evidence over assumption" commitment existed to surface before implementation, not after.

---

*This document is a feasibility study only. No code was written or modified in producing it. No engine was implemented. No threshold was tuned. No scoring was calibrated. The credentialed re-run this report recommends (§10) has not been performed.*
