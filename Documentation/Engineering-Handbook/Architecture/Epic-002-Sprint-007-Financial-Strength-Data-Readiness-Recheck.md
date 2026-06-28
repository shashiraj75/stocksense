# Epic 002, Sprint #007 — Financial Strength Data Readiness Re-Check

**Status:** Validation report only. **No code was modified this sprint** — the re-check used the existing, unmodified `services/us_provider_precedence.py` (Sprint #006) and the existing, unmodified Sprint #005 live dataset (76 US companies; no new live calls were needed since that dataset already contains both EDGAR and yfinance values for every field). The Financial Strength Engine was not implemented. No recommendation logic, India provider, or consumer was touched.
**Governed by:** SSDS-005, SSDS-006, the Epic-002-Sprint-006-US-Provider-Precedence-Decision report, `us_provider_precedence.py`, SES-001 through SES-005.

---

## Method

Sprint #006's `resolve_field()` function was run, for real, against every one of Sprint #005's 76 US companies' actual EDGAR and yfinance field values (the same dataset the Sprint #005/#006 reports already used — no new live network calls were made, since re-running the resolution logic against already-fetched data is sufficient to answer this sprint's question and avoids an unnecessary repeat of SEC EDGAR/yfinance traffic). For each of the 16 SSDS-005-required fields, every company's sector bucket (FINANCIAL, REIT, or none) was passed to `resolve_field()` exactly as a future engine integration would, and the **effective availability** (value present after precedence + fallback) was measured — this is the number that actually matters for SSDS-005's confidence model, not either source's standalone coverage in isolation.

India was not re-tested live this sprint, per the explicit instruction to re-check India "using the previous SSDS-005 Data Validation Report and Data Independence Strategy" rather than implement or re-run anything India-side.

---

## Field-Level Readiness Table (US, post-precedence)

| Field | Effective availability (after precedence+fallback) | Sector-substitute-required (FINANCIAL/REIT) | Residual gap beyond sector | SSDS-005 category | Production-ready? |
|---|---|---|---|---|---|
| revenue | **100.0%** | 0 | 0 | (informational) | **Yes** |
| net_income | **100.0%** | 0 | 0 | Profitability (informational in SSDS-005) | **Yes** |
| ebit | 75.0% | 19 | 0 | Debt-Servicing Capacity | **Yes, for non-FINANCIAL/REIT** |
| interest_expense | 98.7% | 0 | 1 | Debt-Servicing Capacity | **Yes** |
| cash_and_equivalents | **100.0%** | 0 | 0 | Liquidity Adequacy | **Yes — pending the definitional decision named in Sprint #006** |
| current_assets | 67.1% | 25 | 0 | Liquidity Adequacy | **Yes, for non-FINANCIAL/REIT (100% there)** |
| current_liabilities | 67.1% | 25 | 0 | Liquidity Adequacy | **Yes, for non-FINANCIAL/REIT (100% there)** |
| total_assets | **100.0%** | 0 | 0 | Balance Sheet Resilience | **Yes** |
| total_liabilities | **100.0%** | 0 | 0 | Balance Sheet Resilience | **Yes** |
| short_term_debt | 71.1% | 19 | 3 | Leverage & Capital Structure | **Yes, for non-FINANCIAL/REIT (96% there)** |
| long_term_debt | 64.5% | 25 | 2 | Leverage & Capital Structure | **Yes, for non-FINANCIAL/REIT (96% there)** |
| total_debt | **100.0%** | 0 | 0 | Leverage & Capital Structure | **Yes** |
| operating_cash_flow | **100.0%** | 0 | 0 | Cash Flow Durability | **Yes** |
| capital_expenditure | 86.8% | 0\* | 10 | Cash Flow Durability | **Yes, with a newly-identified sector pattern — see below** |
| free_cash_flow | **100.0%** | 0 | 0 | Cash Flow Durability | **Yes** |
| shareholders_equity | **100.0%** | 0 | 0 | Balance Sheet Resilience | **Yes** |

\* `capital_expenditure` is not currently in `SECTOR_SUBSTITUTE_REQUIRED` — see "A New Finding" below; its 10 residual-unavailable companies are concentrated in exactly the same FINANCIAL/REIT pattern as the fields that *are* flagged, which this report treats as evidence for a future addition, not a defect in the existing logic.

**Aggregate finding:** across all 76 companies, excluding the fields each company's own sector correctly requires a substitute for, **average applicable-field completeness is 98.3%** (FINANCIAL sector: 96.2%; REIT: 96.1%; all other sectors: 99.4%). **Zero of 76 companies fall below 60%** — the threshold SSDS-005 §5 already specifies as its `insufficient_data` rejection gate. This is the single most decisive number in this report: **once a sector's structurally-absent fields are correctly treated as exempted rather than simply missing (the same treatment SSDS-003 already gives Business Quality's financial-sector D/E exemption), every company in this sample — including every bank, insurer, and REIT — clears SSDS-005's own existing confidence bar.**

---

## Comparison Against the Original SSDS-005 Data Validation Report

| Dimension | SSDS-005 Report (Sprint #001, yfinance-only baseline) | This re-check (post-precedence, EDGAR+yfinance combined) |
|---|---|---|
| US sample size | 70 companies | 76 companies (same 70 + 6 REITs) |
| Liquidity Adequacy (current assets/liabilities) | Not separately scored; implicitly limited to whatever yfinance's `.balance_sheet` provided | 100% for non-FINANCIAL/REIT companies (was already close to this via yfinance alone, but now cross-validated against an independent official source) |
| Leverage & Capital Structure (debt fields) | yfinance-only, no cross-check | 96–100% for non-FINANCIAL/REIT, with the lease-liability scope gap now *named and understood* rather than invisible |
| Debt-Servicing Capacity (EBIT, interest expense) | yfinance-only | interest_expense at 98.7% (FINANCIAL sector correctly routed to yfinance, avoiding EDGAR's confirmed bank-specific disagreement); ebit at 100% for applicable companies |
| Cash Flow Durability | yfinance-only, generally strong already | Unchanged in substance (yfinance remains primary or fallback for every cash-flow field) but now has EDGAR as a genuine cross-check, not just a hopeful future addition |
| Overall US readiness score (Sprint #001) | **8/10** | See below |

**What actually changed, stated precisely:** the original SSDS-005 report's 8/10 US score was based on yfinance alone, with SEC EDGAR named only as a future possibility. This re-check doesn't contradict that score — it **substantiates and slightly strengthens** it, by adding a second, officially-sourced, deeper-history provider that agrees with yfinance on every field that matters at a near-100% rate (per Sprint #005/#006's own evidence), and by giving the FINANCIAL-sector gap independent, two-source confirmation rather than resting on yfinance's word alone.

---

## A New Finding (named, not acted on this sprint)

Re-checking `capital_expenditure`'s residual 10-company gap (the only field with unavailability *outside* the existing sector-substitute list) shows: **8 of those 10 are banks/insurers, and the other 2 are REITs** — exactly the same FINANCIAL/REIT concentration the existing `SECTOR_SUBSTITUTE_REQUIRED` table already encodes for five other fields. This strongly suggests `capital_expenditure` belongs in that same table (banks/insurers/REITs genuinely don't have a traditional PP&E-capex concept the way an industrial company does). **This is not treated as a "genuine defect" under this sprint's rule 8** — the existing precedence *rule* for `capital_expenditure` (EDGAR primary, yfinance fallback) is still correct for every company that does report it; what's missing is a sector exemption, which is an *addition*, not a *fix* to something wrong. Per this sprint's explicit "validation sprint, not implementation sprint" framing, this is named as a recommended Sprint #008 input, not implemented here.

---

## Remaining US Gaps

| Gap | Status |
|---|---|
| **FINANCIAL-sector liquidity substitute** | Still not built. Confirmed again this sprint: `current_assets`/`current_liabilities`/`ebit`/`short_term_debt`/`long_term_debt` remain structurally absent for all 19 banks/insurers regardless of precedence — precedence logic correctly routes around the gap (marks `sector_substitute_required`) but does not close it. The screener-side banking fields (NPA/CAR/NIM/CASA) named in SSDS-006 as the eventual substitute basis for India's banks are US-side untested; a future sprint needs the *US* equivalent (e.g., regulatory capital ratios from bank-specific XBRL tags) — not yet investigated. |
| **REIT-specific handling** | Still not built. Confirmed again: REITs share the same five-field gap pattern as FINANCIAL, now also implicated in the new `capital_expenditure` finding above. No sector-adaptation taxonomy update has been made (named in Sprint #006 as future work, unchanged). |
| **`total_liabilities` gap** | **Closed by precedence, not by new data.** yfinance's 98.7%+fallback coverage means the field is effectively 100% available post-precedence — the underlying EDGAR-side gap (15 companies missing the tag) is unchanged, but no longer matters because the fallback chain handles it correctly. |
| **Debt-maturity (`short_term_debt`/`long_term_debt`) split gaps** | Substantially mitigated by precedence (96% effective for non-FINANCIAL/REIT) but the underlying lease-liability scope question (named in Sprint #006) remains unresolved — a company's *debt* figure including or excluding lease liabilities is still an open definitional question for whichever engine eventually consumes it. |
| **Cash definition ambiguity (`cash_and_equivalents`)** | Still named, still unresolved. 100% effective availability post-precedence (yfinance is primary and always has it in this sample), but *which* of the two genuinely different concepts (cash-only vs. cash+short-term-investments) the Liquidity Adequacy category should use is a product decision Sprint #006 named and this sprint does not resolve. |
| **Any remaining missing fields** | The 3 (`short_term_debt`)/2 (`long_term_debt`)/1 (`interest_expense`) residual non-sector gaps are isolated, small-cap-specific (MRNA, FIVE, SFIX, PSA) — not systemic, not investigated to full root cause this sprint, named honestly rather than absorbed into the aggregate percentage. |

---

## India Readiness Re-Check (no live re-test, per this sprint's scope)

Per the SSDS-005 Data Validation Report and the Data Independence & Provider Strategy report — **both unchanged since their original publication, and not re-tested this sprint**:

- Liquidity Adequacy (current assets, current liabilities, cash & equivalents) remains **0% confirmed available** from screener.in — a structural gap, confirmed by direct source-code reading (the unscraped "Other Assets" sub-table), not a sampling artifact.
- Debt-maturity split remains **0% confirmed available** — the Design Study's own top-named open question, never resolved.
- Screener.in's unauthenticated access remains confirmed unreliable at batch volume (the IP-block this engagement directly observed) — **no credentialed re-run has been performed since Sprint #001's recommendation that one happen.**
- The recommended next research step (whether screener.in's "Other Assets" sub-table contains the missing fields) **has not been attempted** — this remains the single highest-leverage unresolved question for India, unchanged across three sprints (#001, #002, #007).

**India readiness is unchanged: still blocked on the same two categories (Liquidity Adequacy in full, the maturity-split dimension of Leverage & Capital Structure), with zero new evidence gathered since Sprint #001 — because no India-side work has been performed in the intervening sprints, all of which were explicitly US-focused.**

---

## Revised Go/No-Go Recommendation

| Scope | Recommendation | Basis |
|---|---|---|
| **US implementation** | **Go, for non-FINANCIAL/REIT companies** (the large majority of any realistic US universe) | 98.3%/99.4% average applicable-field completeness, zero companies below SSDS-005's own 60% threshold, every field has a validated, evidence-based precedence rule, two genuine adapter defects already found and fixed (Sprint #005), and the remaining gaps are named, scoped, and small. This is a materially stronger position than the original Sprint #001 "Conditional Go." |
| **US implementation — FINANCIAL/REIT sectors** | **Conditional Go, gated on the sector-substitute computation path** | The data gap (current_assets/current_liabilities/ebit/debt-split) is structural and confirmed on two independent sources now, not a coverage problem precedence can fix. SSDS-005 already names this as required category-design work; this sprint adds REIT to the same requirement and a `capital_expenditure` candidate addition. |
| **India implementation** | **No-Go, unchanged from Sprint #001** | Zero new evidence; the same two structural category gaps remain unaddressed because no India-side engineering or research has occurred since Sprint #001 named the next step. |
| **Overall epic sequencing** | **Proceed US-first** | See below. |

---

## US-First vs. Wait-for-India

**Recommend proceeding US-first, explicitly mirroring Epic 001's own precedent.** Epic 001's Sprint #005 ("Business Quality Engine → Multibagger Integration") was named **US-only by evidence-based necessity** — not silently scoped down, but stated as a finding, with the India gap closed two sprints later (#006 research, #007 implementation) once the right evidence existed. This epic is in the identical position: US data readiness is now substantially validated (this sprint) while India's readiness is **unchanged and unaddressed** since Sprint #001 — not because India is harder in principle, but because every subsequent sprint in this epic chose to invest in US provider depth (SEC EDGAR) rather than India research. That was a reasonable sequencing choice sprint-by-sprint, but it means India's gap is not "still being worked on slowly" — it is **untouched**, and pretending otherwise would misrepresent this epic's actual history. Proceeding US-first, exactly as Epic 001 did, is the honest, evidence-matched recommendation — not a default, but a repeat of a pattern that already worked once in this engagement.

---

## Recommended Sprint #008 Scope

1. **Design and validate the FINANCIAL-sector (and REIT) Liquidity Adequacy / Debt-Servicing-Capacity substitute computation path for the US** — the single largest remaining blocker to a *complete* (not just non-FINANCIAL) US implementation. Investigate US bank-specific XBRL tags (regulatory capital, loan-to-deposit) as the substitute basis, mirroring the India-side NPA/CAR/NIM approach SSDS-006 already named.
2. **Add `capital_expenditure` to `SECTOR_SUBSTITUTE_REQUIRED`** for FINANCIAL and REIT, based on this sprint's confirmed 8-of-10/10-of-10 sector concentration — a small, evidence-justified addition to the existing module, not a new architecture.
3. **Resolve the `cash_and_equivalents` definitional question** as an explicit product decision (cash-only vs. cash+short-term-investments) before any engine reads that field.
4. **Only after 1–3, begin SSDS-005's actual scoring-category implementation for US non-FINANCIAL/REIT companies** — the data foundation this sprint confirms is ready; the scoring logic itself (category caps, the Financial Stress Simulation thresholds) remains entirely unbuilt and unvalidated, per SSDS-005's own explicit deferral.
5. **Separately, if and when India work resumes:** restore authenticated Screener.in access and run the "Other Assets" sub-table research task named in Sprint #001/#002 — still the single highest-leverage unresolved India question, three sprints later.

**Do not begin Financial Strength Engine implementation itself until at minimum item 1 (or an explicit, named decision to launch US-only excluding FINANCIAL/REIT, mirroring Epic 001's Sprint #005 precedent exactly) is resolved.**

---

## Test Summary

No code was changed this sprint. The full suite was re-run to confirm the environment is unchanged: **350/350 passing**, identical to Sprint #006's final state.

## GitHub Actions Result

Not applicable — no code changed, no push made for this sprint's documentation-only change beyond what's recorded below.

## Final Commit Hash

Recorded below, after this sprint's documentation commit.

---

*This is a validation sprint. No code was modified — the existing, unmodified provider-precedence module was run against the existing, unmodified Sprint #005 dataset. The Financial Strength Engine was not implemented. No recommendation logic, India provider, or consumer was touched.*
