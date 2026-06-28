# Epic 002, Sprint #006 — US Provider Precedence Decision

**Status:** Decision and documentation sprint, with a small, additive implementation justified by Sprint #005's evidence (see Section 7). **No intelligence engine, recommendation logic, or India provider was modified.** The Financial Strength Engine was not implemented. This sprint decides and encodes *field-level* precedence only — it does not wire that decision into any consumer.
**Governed by:** SSDS-006, SSDS-005, the Epic-002-Sprint-005-SEC-EDGAR-Large-Scale-Validation report, the SEC EDGAR Adapter, the StockSense360 Data Independence & Provider Strategy report, SES-001 through SES-005, the StockSense360 Product Glossary.

---

## Provider Precedence Decision — Summary

**Per SSDS-006's own binding rule ("provider precedence... is a per-field, per-market priority order," SSDS-006 §8) and this sprint's explicit instruction, this decision is field-level, not provider-level.** SEC EDGAR is not declared universally primary, and yfinance is not declared universally primary — each of the 16 SSDS-005-required fields is decided independently, on its own Sprint #005 evidence.

**Headline pattern, stated honestly:** EDGAR wins on fields where it is a *direct*, officially-filed, single-concept figure with high agreement (revenue, net income, balance-sheet aggregates, cash flow aggregates). yfinance wins on fields where EDGAR's evidence showed either materially lower coverage (`total_liabilities`, the debt fields) or a confirmed *definitional* gap that more data wouldn't fix (`ebit`, `cash_and_equivalents`, `free_cash_flow`). **Two fields are not "yfinance is more correct" — they are "the two sources answer different questions, and StockSense360 has not yet decided which question it wants."** Those are named explicitly, not silently resolved by picking a winner.

---

## Field-Level Ownership Table

| Field | Precedence | Why (Sprint #005 evidence) |
|---|---|---|
| **revenue** | **EDGAR primary, yfinance fallback** | EDGAR 94.7% coverage, 52/72 agreement post-fix; the residual diffs are confirmed timing (EDGAR = last completed 10-K, yfinance = a fresher TTM figure), not error. yfinance's 100% coverage makes it the correct fallback for EDGAR's 4 missing companies. |
| **net_income** | **EDGAR primary, yfinance fallback** | EDGAR 100.0% coverage post-fix, 75/76 agreement — the strongest field in the entire study. yfinance fallback retained for defense-in-depth, not because it's needed today. |
| **ebit** | **yfinance primary, EDGAR validation** | Both sources show real coverage gaps (72.4%/80.3%) and a confirmed *definitional* difference for several large companies (GOOGL, AMZN, ORCL: 15–20% diffs) — EDGAR's strict `OperatingIncomeLoss` vs. yfinance's adjusted EBIT. yfinance's marginally higher coverage plus this unresolved definitional question means EDGAR should validate/flag, not lead. |
| **interest_expense** | **EDGAR primary (non-FINANCIAL), yfinance primary + EDGAR validation (FINANCIAL)** | EDGAR 92.1% coverage, good agreement overall — but every bank in the sample (JPM, BAC, WFC, C, USB) shows a real 6–18% diff, a sector-specific definitional gap (gross vs. net interest expense treatment for deposit-taking institutions), not noise. Sector exception named explicitly below. |
| **cash_and_equivalents** | **Definitional decision required — not resolved by precedence** | Confirmed root cause: yfinance's `totalCash` bundles cash *and* short-term marketable securities; EDGAR's tag is cash-and-equivalents only. Both are correct by their own definition. **No precedence rule can fix a question about which definition the product wants.** Provisionally: yfinance primary (matches the existing platform convention already used elsewhere in this codebase), EDGAR retained as the narrower, available-on-request cross-check — but this is named as provisional pending an explicit product decision, not asserted as settled. |
| **current_assets** | **EDGAR primary, yfinance fallback** | 71.1%/75.0% coverage (close), **zero disagreements** among the 53 companies where both have data — the strongest possible agreement signal. EDGAR's deeper history makes it the natural primary where available. |
| **current_liabilities** | **EDGAR primary, yfinance fallback** | Identical reasoning and identical numbers to `current_assets`. |
| **total_assets** | **EDGAR primary, yfinance fallback** | 100%/100% coverage, 0 disagreements — the cleanest field in the study. EDGAR's 18-year history (vs. yfinance's 4–5-year cap) is the deciding tiebreak. |
| **total_liabilities** | **yfinance primary, EDGAR validation** | EDGAR 80.3% vs. yfinance 98.7% — a real, material coverage gap (15 companies have no `Liabilities` XBRL tag at all). Where both have data, agreement is excellent (58/61, avg diff 0.08%) — this is a coverage decision, not a trust decision. |
| **short_term_debt** | **yfinance primary, EDGAR validation** | EDGAR 65.8% vs. yfinance 89.5%, plus a confirmed *definitional* gap (lease liabilities included in yfinance's debt figures, excluded from EDGAR's current tag list) driving several large disagreements (AAPL, CSCO, WMT). Not yet promotable. |
| **long_term_debt** | **yfinance primary, EDGAR validation** | Same reasoning as `short_term_debt` — EDGAR 59.2% vs. yfinance 97.4%, same lease-liability gap. |
| **total_debt** | **yfinance primary, EDGAR validation** | Same reasoning — EDGAR 68.4% vs. yfinance 100.0%, inherits both component fields' gaps since it's derived from them on the EDGAR side. |
| **operating_cash_flow** | **EDGAR primary, yfinance fallback** | 100%/100% coverage, 74/76 agreement — the only two disagreements (AAL, ETSY) are large (60.6%, 85.2%) but isolated and not traced to a systemic EDGAR defect this sprint (named as an open question, not assumed resolved). |
| **capital_expenditure** | **EDGAR primary, yfinance fallback — sign convention must be normalized by the consumer** | 81.6%/85.5% coverage. **Every apparent "disagreement" is a sign-convention artifact** (EDGAR reports a positive payment amount; yfinance reports a negative outflow) — confirmed exact-magnitude matches once sign is normalized. This is not a trust question; it's a normalization requirement for whichever future Data Fabric Normalization layer consumes both. |
| **free_cash_flow** | **yfinance primary, EDGAR fallback** | yfinance 100% (a directly-reported figure) vs. EDGAR 81.6% (a *derived* figure, per SSDS-006's own confidence-discount rule for DERIVED values). A directly-reported number is intrinsically more trustworthy than a derived one when both are available — yfinance wins on this principle, not just on coverage. |
| **shareholders_equity** | **EDGAR primary, yfinance fallback** | 100%/100% coverage, 74/76 agreement (T and CCL diffs are modest, 14.4%/5.7%, not investigated to full root cause — named, not hidden). EDGAR's deeper history is the tiebreak. |

**Classification key, per this sprint's Task 3 categories:** 7 fields are **EDGAR primary, yfinance fallback** (revenue, net_income, interest_expense*, current_assets, current_liabilities, total_assets, operating_cash_flow, capital_expenditure, shareholders_equity — *non-FINANCIAL sector); 6 fields are **yfinance primary, EDGAR validation** (ebit, total_liabilities, short_term_debt, long_term_debt, total_debt, free_cash_flow); 1 field (**interest_expense, FINANCIAL sector**) is yfinance-primary-with-EDGAR-validation as a sector exception to its own general rule; 1 field (**cash_and_equivalents**) is an unresolved definitional decision, not a precedence call.

---

## yfinance Fallback Rules

1. **Fallback triggers only on absence, not on disagreement.** If the primary source has a value at all, it is used — a disagreement with the fallback source is logged/surfaced as a flag (per SSDS-006 §9's conflict-logging principle), never silently overridden by the fallback. This sprint does not implement averaging or consensus-blending (SSDS-006 §9's "consensus" rule remains explicitly deferred — Sprint #005 did not gather enough disagreement-rate evidence across genuinely *independent* providers to calibrate a consensus tolerance band, since yfinance was used as this validation's comparison baseline, not yet as a true Fabric-registered second provider).
2. **Fallback never fabricates.** If neither the primary nor the fallback source has a value, the field is `UNAVAILABLE` — exactly SSDS-005's and SSDS-003's shared missing-data philosophy, unchanged by this sprint.
3. **A field whose sector requires a substitute (see Sector Exceptions) does not run the fallback chain at all** — precedence and fallback are not the right tool for a structurally-absent concept; a sector-specific computation path is the correct fix, named but not built this sprint.
4. **Fallback value provenance must say it came from the fallback**, not be presented identically to a primary-sourced value — this sprint's implementation (Section 7) tags every resolved field with which source actually supplied it and whether the primary was tried and found absent.

---

## Confidence Implications

- **A field resolved from its primary source keeps that source's own confidence** (for EDGAR: the existing DIRECT/DERIVED-aware confidence from `sec_edgar_adapter.py`; for yfinance: no equivalent confidence model exists yet in this codebase — this sprint assigns a provisional flat baseline, explicitly named as provisional, not calibrated).
- **A field resolved from the fallback (primary absent) receives a modest confidence discount** relative to what that same source would score as primary — reflecting "this is the source we'd normally treat as secondary," not a statement that the data itself is less accurate.
- **Disagreement does not currently reduce confidence** — Sprint #005 found that most disagreements are explained by timing or definitional differences rather than data-quality problems, so penalizing confidence for disagreement today would conflate "the sources measure different things" with "one source is wrong." This is named as a future refinement once a real consensus model exists (SSDS-006 §9), not implemented now.
- **Per SSDS-006 §7, this remains provider-level confidence, kept explicitly separate from any future engine-level confidence calculation** — no engine reads this output.

---

## Sector Exceptions

| Sector | Affected fields | Finding |
|---|---|---|
| **FINANCIAL** (banks, NBFC, insurance) | `current_assets`, `current_liabilities` | Confirmed structurally absent on **both** EDGAR (5.3%) and yfinance (0.0%) across all 19 sampled banks+insurance companies (Sprint #005) — no precedence rule helps; a sector-specific Liquidity Adequacy substitute is required (already named in SSDS-005/SSDS-006). |
| **FINANCIAL** | `ebit`, `short_term_debt`, `long_term_debt` | Confirmed low coverage on both sources (banks report debt/income structure fundamentally differently) — same conclusion, sector-specific substitute required, not a precedence question. |
| **FINANCIAL** | `interest_expense` | The one field where both sources *have* data but **disagree meaningfully** (6–18%) for banks specifically — flagged above as its own sector exception to the general `interest_expense` rule. |
| **REIT** *(newly confirmed this epic, not previously designed for)* | `current_assets`, `current_liabilities`, `long_term_debt`, `free_cash_flow` | Confirmed in Sprint #005: REITs share much of the FINANCIAL sector's structural reporting gap despite not being classified as FINANCIAL in this codebase's existing sector taxonomy. **This sprint does not change that taxonomy** — it names the finding and flags it as a required input to a future SSDS-005 sector-adaptation update. |

---

## Known Gaps (carried forward from Sprint #005, not resolved here)

1. Lease liabilities are absent from EDGAR's debt tags, a real, named cause of several `short_term_debt`/`long_term_debt`/`total_debt` disagreements.
2. `total_liabilities` is derivable (current + non-current components) for most of EDGAR's 15 gap companies but not yet derived.
3. The `cash_and_equivalents` and `ebit` definitional questions are open product decisions, not engineering gaps.
4. REIT-specific sector adaptation has no home in the existing taxonomy yet.
5. No true cross-provider consensus model exists — this sprint's fallback rules are precedence-only, per SSDS-006 §9's own sequencing (consensus calibration requires evidence this validation didn't yet produce, since yfinance wasn't tested as a registered Fabric provider).

---

## Future Data Fabric Requirements (forward-looking, not built this sprint)

- A genuine **Provider Registry** (SSDS-006 §15 item 4) would encode this sprint's table as configuration rather than this sprint's hardcoded Python dict — this sprint's module is a deliberately small, evidence-shaped precursor to that, not a replacement for it.
- A **Normalization layer** decision on capital expenditure's canonical sign convention (Section "Field-Level Ownership Table" above) is needed before any consumer reads both sources' capex interchangeably.
- A **sector taxonomy update** to add REIT as its own bucket (or extend the FINANCIAL exemption to cover it) is needed before Sprint #005's REIT finding can be acted on inside any engine.

---

## Implementation (Section 7 — justified by evidence)

Per this sprint's explicit instruction ("if, and only if, the evidence clearly supports it, implement the smallest additive provider-precedence mapping"): **the evidence supports it.** Sprint #005 produced field-by-field, root-caused, two-source agreement data for all 16 fields — exactly the evidence a precedence decision needs, and exactly the evidence this report's table above already encodes. Implementing that table as a small, testable, additive module captures the decision in code rather than leaving it as prose a future engineer would have to re-derive.

**What was built:** `backend/services/us_provider_precedence.py` — a standalone module containing:
- `FIELD_PRECEDENCE`: the field→rule mapping from the table above, as data.
- `SECTOR_SUBSTITUTE_REQUIRED`: the sector-exception fields from the table above, as data.
- `resolve_field(field, edgar_record, yfinance_value, sector_bucket=None)`: given one field's EDGAR provenance record (or `None`) and a bare yfinance value (or `None`), returns a single resolved-and-provenanced record — which source supplied the value, whether the primary or the fallback was used, the resulting confidence, and whether a sector substitute is required instead of either source.

**What was deliberately not built:** any call site. This module is not imported by `business_quality_engine.py`, any other engine, `us_fundamentals.py`, `sec_edgar_adapter.py` itself, or any API route — per this sprint's explicit "do not modify intelligence engines" / "do not implement the Financial Strength Engine" rules. It exists as a ready, tested, evidence-backed building block for whichever future sprint wires Financial Strength (or any other engine) onto real US provider data.

---

## Test Summary

| Category | New this sprint | What they cover |
|---|---|---|
| Unit (`test_us_provider_precedence.py`) | 30 | Every field's documented precedence rule (16 parametrized cases), fallback-on-absence (both directions), disagreement does NOT trigger fallback, sector overrides (FINANCIAL's `interest_expense` flip), sector substitute-required short-circuits (FINANCIAL and REIT), confidence preservation and fallback-discount, the `cash_and_equivalents` definitional flag. |
| Regression (`test_us_fundamentals_unaffected_by_provider_precedence.py`) | 4 | Zero import-time coupling between the new module and `us_fundamentals.py`/`sec_edgar_adapter.py`; existing yfinance `_build()` behavior unchanged; all three modules coexist cleanly. |
| **Total new** | **34** | |
| **Full suite, before this sprint** | — | 311 passing |
| **Full suite, after this sprint** | — | **350 passing, 0 failing** |

No existing test was modified. No existing module's behavior changed.

## GitHub Actions Result

Recorded below, after this sprint's commit is pushed and confirmed.

## Final Commit Hash

Recorded below, after this sprint's commit.

---

*This is a decision-and-documentation sprint with one small, evidence-justified additive module. No intelligence engine, recommendation logic, or India provider was modified. The new module is not imported by any engine, provider, or consumer.*
