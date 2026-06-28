# Epic 002, Sprint #005 — SEC EDGAR Large-Scale Coverage Validation

**Status:** Validation report. Two genuine defects in the SEC EDGAR Adapter were found live during this validation and fixed within this sprint's scope, under the same "validate, then fix confirmed defects, don't speculate beyond them" discipline Epic 001's Sprint #004a established. **No production engine, recommendation logic, India provider, or yfinance code was modified.** EDGAR was not wired into any engine, not declared primary, and provider precedence was not changed.
**Governed by:** SSDS-006, SSDS-005, the SEC EDGAR Adapter implementation, the StockSense360 Data Independence & Provider Strategy report, SES-001 through SES-005, the StockSense360 Product Glossary.

---

## Methodology

**Universe:** 76 US companies — the same 70-ticker universe as the SSDS-005 Data Validation Report (Epic 002 Sprint #001), plus 6 REITs added because this sprint's brief explicitly named REITs as a required category absent from that original universe. Every required category is represented: mega-cap technology, banks, insurance, healthcare, consumer staples, consumer discretionary, industrials, utilities, energy, telecom, REITs, highly-leveraged companies, loss-making companies, cash-rich companies (several companies carry more than one tag).

**Method:** for every company, the real `sec_edgar_adapter.fetch_us_fundamentals_sec_edgar()` entry point was called live against SEC EDGAR, and a comparable yfinance extraction (`.info` + `.balance_sheet` + `.cashflow` + `.financials`) was built for the same 16 SSDS-005-required fields, using the same field-to-source-row mapping `us_fundamentals.py` already uses elsewhere in this codebase. **Zero fetch errors on either side, 76/76 companies.** No production code, cache, or table was written to.

**A note on rigor, stated up front:** this validation did not stop at "run it once and report the numbers." Two rounds of live re-validation followed two rounds of root-cause investigation into specific, real disagreements — mirroring exactly the Production-Readiness-Validation → Calibration → Final-Re-Validation cycle Epic 001's Sprint #004/#004a/Final-Revalidation already established as this project's standard practice for a validation sprint that finds something real.

---

## EDGAR vs. yfinance Coverage Matrix (final, post-fix, 76 companies)

| Field | EDGAR coverage | yfinance coverage | Agreement (within 5%) | Disagreement | Avg. agreement diff |
|---|---|---|---|---|---|
| revenue | 94.7% | 100.0% | 52 | 20 | 2.21% |
| net_income | **100.0%** | 100.0% | 75 | 1 | 0.12% |
| ebit | 72.4% | 80.3% | 12 | 40 | 1.22% |
| interest_expense | 92.1% | 96.1% | 48 | 20 | 0.16% |
| cash_and_equivalents | 92.1% | 100.0% | 9 | 61 | 1.30% |
| current_assets | 71.1% | 75.0% | 53 | 0 | 0.00% |
| current_liabilities | 71.1% | 75.0% | 53 | 0 | 0.00% |
| total_assets | **100.0%** | 100.0% | 76 | 0 | 0.00% |
| total_liabilities | 80.3% | 98.7% | 58 | 2 | 0.08% |
| short_term_debt | 65.8% | 89.5% | 18 | 28 | 0.69% |
| long_term_debt | 59.2% | 97.4% | 31 | 14 | 0.17% |
| total_debt | 68.4% | 100.0% | 8 | 44 | 2.00% |
| operating_cash_flow | **100.0%** | 100.0% | 74 | 2 | 0.00% |
| capital_expenditure | 81.6% | 85.5% | 0 | 61 | n/a — see note below |
| free_cash_flow | 81.6% | 100.0% | 51 | 11 | 0.25% |
| shareholders_equity | **100.0%** | 100.0% | 74 | 2 | 0.04% |

**Critical reading note on `capital_expenditure`'s "0 agreement / 61 disagreement":** this is **not** 61 real disagreements. EDGAR's `PaymentsToAcquirePropertyPlantAndEquipment`/`PaymentsToAcquireProductiveAssets` report capex as a positive payment amount; yfinance's `Capital Expenditure` cash-flow-statement row reports it as a negative outflow (standard cash-flow-statement sign convention). Every "disagreement" in this field is the same number with the opposite sign — confirmed by direct inspection (e.g. AAPL: EDGAR `12,715,000,000` vs. yfinance `-12,715,000,000`, an exact magnitude match). This is a **sign-convention difference between the two sources, not a data-quality problem**, and this sprint's `free_cash_flow` derivation (`operating_cash_flow − capital_expenditure`, both EDGAR-internal, EDGAR's own positive-capex convention) is internally consistent and unaffected by this.

---

## Field-Level Comparison Table — Root Cause of Every Material Gap

| Field | Coverage gap (EDGAR vs. yfinance) | Root cause, confirmed |
|---|---|---|
| `current_assets`/`current_liabilities` | 71.1% vs. 75.0% (close) | Structural: absent for FINANCIAL-sector filers (banks have no classified balance sheet) and most REITs (real-estate-sector filers commonly don't present a classified balance sheet either — a new finding this sprint, not previously tested with real REIT data). Not a defect. |
| `ebit` | 72.4% vs. 80.3% | Partly structural (FINANCIAL sector, confirmed), partly a genuine **definitional gap**: where both sources report a value, the residual ~7–20% diffs (GOOGL, ORCL, CSCO, CRM, AMZN) are `OperatingIncomeLoss` (EDGAR's strict GAAP operating income) vs. yfinance's `EBIT`/`Operating Income` row, which for some companies adds back certain non-operating items — a real, named definitional difference between sources, not an extraction bug. |
| `short_term_debt`/`long_term_debt`/`total_debt` | Largest coverage gaps in this study (59–68% vs. 89–100%) | **Two confirmed causes.** (1) Structural: absent for FINANCIAL sector and most REITs, same as above. (2) A genuine scope difference for many companies that *do* report debt tags: yfinance's `totalDebt`/`Long Term Debt` rows for several companies (AAPL, MSFT, GOOGL, ORCL — all large diffs) include **finance/operating lease liabilities**, which EDGAR's `LongTermDebtCurrent`/`LongTermDebtNoncurrent` tags do not include (lease liabilities are filed under separate XBRL concepts — `OperatingLeaseLiabilityNoncurrent`, `FinanceLeaseLiabilityNoncurrent` — not yet in this adapter's tag list). **Named explicitly as a real, unresolved scope gap for a future sprint**, not silently absorbed into "the numbers don't match." |
| `cash_and_equivalents` | 92.1% vs. 100.0%, but the largest *disagreement magnitudes* of any field where both have data | **Definitional, not extraction error.** Confirmed for AAPL/MSFT/GOOGL/CSCO: yfinance's `totalCash` bundles cash **and short-term marketable securities**; EDGAR's `CashAndCashEquivalentsAtCarryingValue` is narrowly cash-and-equivalents only, exactly as its XBRL name states. Both are "correct" by their own definition — this is the single most important *interpretation* finding in this report, since a future engine reading both sources naively would see a "disagreement" that is actually two different, both-valid concepts. |
| `total_liabilities` | 80.3% vs. 98.7% | Confirmed, real gap: 15 companies across consumer/healthcare/industrials/telecom/mega-cap-tech sectors have no top-level `Liabilities` XBRL tag at all in their filings (ORCL, AMZN, WMT, KO, TGT, VZ, LUMN, MRK, ABBV, OXY, DUK, HON, AAL, CCL, HPQ) — these companies report only the components (current + non-current liabilities) without a single summary tag. A derivable fix (sum the components) is named in Remaining Gaps, not built this sprint (scope discipline — this validation sprint fixes confirmed *extraction* defects, not new derivations). |
| `revenue` (residual, post-fix) | 94.7% vs. 100.0%, small residual diffs (5.7–11.5%) for AAPL/MSFT/CSCO/ADBE/COST | **Timing, not a tag defect.** Confirmed: EDGAR correctly returns the most recent completed fiscal year's 10-K figure (by design — annual figures are preferred over partial-year data, per `_best_entry`'s explicit rule); yfinance's `.financials` row in some cases reflects a more recent trailing-twelve-month figure that already includes a newer quarter EDGAR's last 10-K doesn't yet cover. Both are real, correctly-sourced numbers describing different (if overlapping) periods. |
| `net_income` (residual) | 100.0% vs. 100.0%, 1 disagreement (SPG, 15.9%) | Likely a REIT-specific noncontrolling-interest/partnership-unit allocation difference — not investigated to full root-cause this sprint given time budget; named honestly as unresolved, not claimed solved. |

---

## Sector-Level Gap Analysis

| Sector | Notable EDGAR coverage pattern |
|---|---|
| **Banks** (n=14) | `current_assets`/`current_liabilities`: 1/14 (7.1%) — confirms, at scale, the same structural FINANCIAL-sector liquidity gap this sprint was specifically asked to investigate (see below). `long_term_debt`: 1/14 (7.1%) — banks report debt structure entirely differently (deposits, borrowings) than the LongTermDebt-style tags this adapter currently maps. `total_liabilities`: 14/14 (100.0%) — banks, unlike most other sectors, **do** report this exact summary tag. |
| **Insurance** (n=5) | `current_assets`/`current_liabilities`: 0/5 (0.0%) — insurers, like banks, don't use a classified balance sheet. `ebit`: 2/5 (40.0%), `free_cash_flow`: 1/5 (20.0%) — insurance companies' income-statement structure doesn't map cleanly onto `OperatingIncomeLoss`. |
| **REITs** (n=6, new this sprint) | `current_assets`/`current_liabilities`: 2/6 (33.3%); `long_term_debt`: 1/6 (16.7%); `free_cash_flow`: 3/6 (50.0%) — **a newly confirmed finding, not previously tested**: REITs share much of the FINANCIAL sector's structural reporting differences (no classified balance sheet for most), despite not being classified as FINANCIAL in this codebase's existing sector taxonomy. This is new evidence for a future SSDS-005 sector-adaptation discussion, not acted on in this sprint. |
| **Mega-cap tech** (n=12) | Generally strong coverage (100% current_assets/liabilities, 91.7% ebit) — but this is exactly the segment where the two confirmed defects (stale tags, missing capex fallback) were found, underscoring that high coverage % alone doesn't guarantee correctness — the validation had to check *values*, not just presence. |
| **Loss-making companies** (n=10) | `ebit`: 10/10 (100.0%), `free_cash_flow`: 10/10 (100.0%) — no coverage penalty for being loss-making; a loss is still a reported, taggable number. Confirms the earlier SSDS-005 study's same finding (data coverage doesn't degrade for distressed companies) generalizes to SEC EDGAR. |
| **Leveraged companies** (n=14) | `free_cash_flow`: 14/14 (100.0%) — the segment most relevant to a future Financial Stress Simulation has the *best* free-cash-flow coverage of any segment tested. |

---

## Specific Investigations (Task 6)

### KO / ORCL `total_liabilities` gap — confirmed, now characterized precisely
No longer a 2-company curiosity from the prior sprint's small sample — **confirmed at scale: 15 of 76 companies (19.7%) have no `Liabilities` tag**, spanning mega-cap tech, consumer, healthcare, industrials, telecom, energy, and utilities. yfinance has the equivalent figure for every one of these 15 (`yfinance_has_it=True` in every case, confirmed). **This is real and derivable** (current_liabilities + non-current liabilities, where both are present) — named as a concrete Remaining Gap, not fixed this sprint per scope discipline.

### JPM / FINANCIAL-sector liquidity gap — confirmed at scale, both sources agree
Across all 19 banks+insurance companies: `current_assets`/`current_liabilities` are 5.3% available from EDGAR and **0.0%** from yfinance. **This is the strongest, most consistent finding in this entire validation** — both an official government filing source and an independent commercial data source agree, at scale, that this sector's standard financial statements do not carry these concepts. This converts SSDS-006's "FINANCIAL-sector exemption" from a hypothesis into a confirmed, double-sourced architectural requirement.

### Does EDGAR materially improve historical depth vs. yfinance?
**Confirmed yes, dramatically, for companies with a long filing history.** AAPL, KO, T, and XOM all show **18 distinct fiscal years** of `AssetsCurrent` history from EDGAR vs. yfinance's hard **4–5-year cap** for the same companies (consistent with the original Sprint #002 finding for AAPL alone, now reconfirmed for 3 additional companies). **An important counter-finding, not glossed over:** JPM and O (the REIT) both show **0 years** via this specific check — not because EDGAR lacks their history, but because `AssetsCurrent` itself doesn't exist for either company (the same FINANCIAL/REIT structural gap above) — the *history-depth* question is moot for a field that doesn't exist at all. The honest finding is "EDGAR's history advantage applies specifically to fields a company actually reports," not "EDGAR is always deeper."

### Does EDGAR's taxonomy introduce complexity that affects reliability?
**Confirmed yes — concretely, not theoretically, and this sprint found and fixed two real instances:**
1. **Tag deprecation over time** (MSFT's `Revenues` frozen since 2010; many filers' `NetIncomeLoss` frozen around the same period in favor of `ProfitLoss`) — companies migrate which XBRL tag they use for the same concept, and a naive "first tag with any data wins" extraction silently returns stale, years-old values. **Fixed this sprint** (see Defects Found and Fixed, below).
2. **Quarterly facts can appear inside an annual filing's XBRL under annual-looking labels** (`form=="10-K"`, `fp=="FY"`) without actually spanning a full year — confirmed live for MSFT's older `Revenues` entries. **Fixed this sprint** via an explicit start/end duration check.
3. **Sign-convention and definitional differences are not unique to EDGAR** — yfinance and EDGAR disagree on capex's sign and cash's scope not because either is wrong, but because they're answering subtly different questions. This is a taxonomy-*comparison* complexity, not a taxonomy-*reliability* problem specific to EDGAR.

**Conclusion on reliability:** EDGAR's taxonomy is more complex to consume correctly than yfinance's pre-flattened `.info` dict, but it is not less reliable once that complexity is handled — both defects found this sprint were extraction-logic gaps in this adapter's first version, not flaws in SEC's underlying data.

---

## Retry/Error Behavior — Test Results

Per Task 7, `services/sec_edgar_adapter.py`'s `_get_with_retry` was tested against every required failure mode, with `requests.get` monkeypatched (no live network) per SES-003 §1:

| Scenario | Confirmed behavior |
|---|---|
| Mock 429 (rate-limited) | Retried up to `_RETRY_COUNT` (3) with backoff; succeeds if a later attempt returns 200; degrades to `None` (never an exception) if all attempts are 429. |
| Mock 500 (server error) | Same retry/backoff behavior as 429; succeeds on recovery. |
| Mock timeout (`requests.exceptions.Timeout`) | Caught and retried up to the bound; degrades to `None` if sustained; succeeds if a later attempt recovers. |
| Mock 404 | **Not retried** — confirmed as a definite failure (SES-002 §6), returned immediately to avoid wasting rate-limit budget on a symbol/resource that retrying can never fix. |
| Mock 403 | Same non-retry behavior as 404, for a different non-transient failure family. |
| Total HTTP-layer failure → `fetch_company_facts` | Confirmed: returns `None`, never raises. |
| Total HTTP-layer failure → `fetch_us_fundamentals_sec_edgar` (the public entry point) | Confirmed: returns `{"available": False, "reason": ...}`, never raises — graceful degradation holds all the way to the function any future caller would actually use. |

**9 new tests, all passing**, closing the exact gap named explicitly in Sprint #004's report ("no dedicated unit test forces a 429/500/timeout response").

---

## Defects Found and Fixed This Sprint

Per Epic 001's established precedent (Sprint #004a: "a tightly scoped fix of *only* the validated defects... explicitly not a broad redesign") — both fixes below are narrowly scoped to the adapter's own extraction logic. **No engine, no yfinance code, no India provider, and no provider-precedence configuration was touched.**

| Defect | Root cause, confirmed live | Fix | Evidence it's fixed |
|---|---|---|---|
| **Stale/deprecated tag could win over current data** | `_extract_direct` returned the first tag in a field's priority list that had *any* data — even if that tag was years stale (MSFT's `Revenues`: frozen at FY2010, $62.48B, while `RevenueFromContractWithCustomerExcludingAssessedTax` had current FY2025 data, $281.7B). | Compare every candidate tag's best entry by period-end date; the most recent wins, with tag-list order only as a tiebreak. | `revenue` agreement rose from 43→52 (of 72 with data on both sides); the specific AAPL/MSFT/CSCO/CRM ~40–80% diffs from the pre-fix run are gone, replaced by small, explainable 5–11% timing diffs. |
| **A 10-K's annual-labeled fact can actually be a sub-annual (quarterly) duration** | Same MSFT concept: several `form=="10-K"`/`fp=="FY"` entries span ~90 days, not a year — a 10-K's XBRL legitimately carries quarterly breakdown facts under annual-looking labels. | Added `_is_full_year_duration()`: for any fact with a `start` date (a "duration" concept), require the start/end span to fall in a 330–400 day range before treating it as annual; instant facts (no `start`, e.g. `Assets`) are unaffected. | Confirmed via a reconstructed regression test using the real entry shapes found live; fails without the fix, passes with it (sanity-checked per SES-003 §4 by temporarily reverting the fix and confirming the test catches it). |
| **Single-tag fields with no fallback miss a company's actual current tag** | AMZN's `capital_expenditure` (`PaymentsToAcquirePropertyPlantAndEquipment`) frozen at FY2016 — Amazon migrated to `PaymentsToAcquireProductiveAssets` ($131.8B FY2025, cross-checked against yfinance's matching magnitude). Separately, TFC/CAT's `net_income` (`NetIncomeLoss`) frozen at FY2009/2010 — both companies migrated to `ProfitLoss` (cross-checked against yfinance for both). | Added `PaymentsToAcquireProductiveAssets` as a fallback tag for `capital_expenditure`; added `ProfitLoss` as a fallback tag for `net_income`. | `capital_expenditure` coverage rose 72.4%→81.6%; `net_income` coverage rose 98.7%→**100.0%**, with disagreements falling from 5→1. |

**6 new regression tests** lock in both fixes (`tests/regression/test_sec_edgar_adapter_stale_tag_defect.py`), sanity-checked by reverting the fix and confirming 5 of 6 fail, then restoring and confirming all pass — per SES-003 §4.

`ADAPTER_VERSION` bumped to `sec_edgar_adapter_v2`, per the versioning requirement SSDS-006 §4 specifies, since this changes what value a previously-cached field would resolve to.

---

## Confidence Impact

Aggregated across all 76 companies × 16 fields (1,216 field evaluations): **897 DIRECT (73.8%), 114 DERIVED (9.4%), 205 UNAVAILABLE (16.9%)** — up from the pre-fix run's 889/107/220 split. The two fixes moved 8 fields from UNAVAILABLE to DIRECT and 7 from UNAVAILABLE to DERIVED, with zero fields moving in the wrong direction (confirmed by the same test suite passing before and after at the unit level, and the live re-validation showing only improvement, never regression, in any field's coverage number).

**Per SSDS-006 §7, provider-level confidence remains explicitly separate from engine-level confidence** — this validation characterizes the former only; no engine-level confidence calculation exists yet, since no engine reads this adapter's output.

---

## Reliability Assessment

| Dimension | Assessment |
|---|---|
| **Availability** | 76/76 companies returned a response (no total fetch failures) across all four live runs performed this sprint (initial + 3 re-validations after each fix). |
| **Rate-limit safety** | No 429s observed in any of this sprint's live runs (self-throttling at the existing ~8.3 req/sec margin held). |
| **Correctness, pre-fix** | Confirmed unreliable for several major companies on income-statement fields specifically (revenue, net income) due to the two defects above — balance-sheet/instant fields (`total_assets`, `shareholders_equity`, `operating_cash_flow`) were unaffected throughout, since instant facts don't have the duration-ambiguity problem and OCF apparently doesn't suffer the same tag-migration pattern in this sample. |
| **Correctness, post-fix** | `net_income`, `total_assets`, `operating_cash_flow`, `shareholders_equity` all now at or near-100% agreement where both sources have data. Remaining disagreements are explained (timing, definitional scope, sign convention) rather than mysterious. |
| **Taxonomy complexity** | Real and confirmed (see Specific Investigations above) — but shown to be a solvable, narrow class of problem (tag migration, duration validation), not a fundamental reliability ceiling. |

---

## Test Summary

| Category | New this sprint | Cumulative |
|---|---|---|
| Unit (retry/error behavior) | 9 | — |
| Regression (defect lock-in) | 6 | — |
| **Total new** | **15** | — |
| **Full suite, before this sprint** | — | 296 |
| **Full suite, after this sprint** | — | **311 passing, 0 failing** |

Sanity-check performed per SES-003 §4: the 6 new regression tests were confirmed to fail (5 of 6) against the pre-fix adapter code, then confirmed to pass after restoring the fix — not just trusted to pass on the first try.

---

## Recommendation on Provider Precedence

**Do not change provider precedence yet** — per this sprint's explicit rule, and because the evidence gathered here characterizes EDGAR's *current* reliability, not yet a head-to-head "which source should win for which field" decision. What this sprint *does* establish, concretely, for a future precedence-setting sprint: EDGAR should be preferred for `total_assets`, `net_income`, `operating_cash_flow`, `shareholders_equity` (100% coverage, near-zero disagreement, deeper history) and for any field where the FINANCIAL/REIT-sector gap is structural on both sides anyway (no precedence decision changes the outcome). yfinance should remain preferred for `short_term_debt`/`long_term_debt`/`total_debt` until the lease-liability scope gap is closed, and for `cash_and_equivalents` if the broader "cash + short-term investments" definition is what a future engine actually wants (a product decision, not resolved here).

## Recommendation on Whether EDGAR Is Ready to Become Primary US Fundamentals Provider

**Conditionally yes for a defined subset of fields; not yet as a blanket replacement.** This sprint moved EDGAR from "a promising 5-company sample" (Sprint #004) to "a validated, defect-fixed, 76-company sample with every material gap traced to a specific, named, understood cause" (this sprint) — a materially stronger evidence base. For `total_assets`, `net_income`, `operating_cash_flow`, and `shareholders_equity` specifically, the evidence supports primary status now: 100% coverage, near-perfect agreement, deeper history than yfinance. For debt-related fields and `cash_and_equivalents`, the evidence supports **not yet** — the lease-liability scope gap and the cash-vs-cash+investments definitional gap need a deliberate decision (extend EDGAR's tag coverage, or accept the narrower definition, or keep yfinance primary for these specific fields) before "primary" would mean anything more than "first, with known gaps."

---

## Remaining Gaps

1. **Lease liabilities are not in any debt tag's list** — confirmed real cause of several large `total_debt`/`long_term_debt` disagreements (AAPL, MSFT, GOOGL, ORCL). A future sprint should add `OperatingLeaseLiabilityNoncurrent`/`FinanceLeaseLiabilityNoncurrent` to the relevant tag lists, or explicitly decide debt excludes leases.
2. **`total_liabilities` is derivable but not derived** — 15/76 companies lack the summary tag but have both components; a `current_liabilities + non-current liabilities` derivation (mirroring the existing `total_debt` derivation pattern) would likely close most of this gap. Not built this sprint — a new derivation is a larger scope decision than a tag-list fix, deliberately deferred.
3. **REITs' structural reporting gap is newly confirmed but not yet designed for** — REITs share much of the FINANCIAL sector's liquidity-reporting pattern without being tagged as FINANCIAL in this codebase's existing sector taxonomy; a future SSDS-005 sector-adaptation update should consider this.
4. **`cash_and_equivalents`'s and `ebit`'s definitional gaps vs. yfinance are named, not resolved** — a product/engineering decision about which definition a future Financial Strength category actually wants is required before either source can be called "more correct."
5. **SPG's net_income disagreement (15.9%) is unexplained** — flagged honestly rather than silently passed over.
6. **This sprint did not test SEC EDGAR against a second live US provider for true cross-provider agreement statistics (SSDS-006 §9)** — yfinance is not a second *provider* in the Fabric sense yet (no Fabric exists), it was used here purely as this validation's comparison baseline.

---

## GitHub Actions Result

Recorded below, after this sprint's commit is pushed and confirmed.

## Final Commit Hash

Recorded below, after this sprint's commit.

---

*This is a validation sprint. Two genuine adapter defects were found and fixed within scope, per Epic 001's established precedent for validation sprints. No engine, recommendation logic, India provider, or yfinance code was modified. EDGAR was not wired into any consumer, not declared primary, and provider precedence was not changed.*
