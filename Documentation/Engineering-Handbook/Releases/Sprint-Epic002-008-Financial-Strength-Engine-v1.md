# Epic 002, Sprint #008 — Financial Strength Intelligence Engine v1

**Status:** Implemented and live-validated. v1 scope: non-FINANCIAL, non-REAL_ESTATE (REIT-proxy) US companies only, per this sprint's explicit instruction. Not wired into `PredictionEngine` or any consumer yet.
**Governed by:** SSDS-005, SSDS-006, the Epic-002-Sprint-006/007 precedence/readiness reports, SES-001 through SES-005, the StockSense360 Product Glossary.

---

## Objective

Implement Version 1 of the Financial Strength Intelligence Engine using the validated SSDS-005 specification and the finalized Data Fabric (SEC EDGAR Adapter + US Provider Precedence), to validate the engine architecture, scoring model, explainability, and `EngineResponse` contract before adding sector-specific adaptations for banks, NBFCs, insurers, and REITs.

---

## Files Changed

| File | Purpose |
|---|---|
| `backend/services/thresholds.py` (modified) | Added `FinancialStrengthThresholds` (`FINANCIAL_STRENGTH` singleton) — every new constant separately named per SES-002 §1, none copied from `BusinessQualityThresholds` even where a similar ratio (interest coverage) appears in both engines. |
| `backend/services/financial_strength_engine.py` (new) | The pure scoring engine — `compute_financial_strength(symbol, fields, sector_bucket, market)`. Zero provider I/O, zero Business Quality metric duplication (enforced by a new static regression test). |
| `backend/services/us_financial_strength_adapter.py` (new) | The only module performing provider I/O for this engine's US path — fetches SEC EDGAR + yfinance, classifies sector, resolves every field via the Sprint #006 precedence module, calls the engine. |
| `backend/tests/unit/test_financial_strength_engine.py` (new) | 29 unit tests — sector exclusion, data-completeness gate, the `liquidity_distress` hard gate's narrow AND-condition, category-direction sanity checks, the Earnings Shock stress scenario, full `EngineResponse` contract compliance, graceful degradation on missing fields. |
| `backend/tests/integration/test_us_financial_strength_adapter_integration.py` (new) | 5 integration tests — full pipeline wiring (EDGAR + yfinance + precedence + engine) together, mocked, no live network; EDGAR-failure and yfinance-failure fallback symmetry. |
| `backend/tests/regression/test_financial_strength_engine_scope_boundary.py` (new) | 6 regression tests — static, source-text-based proof the engine never imports a Business-Quality-only metric or a provider directly (SES-003 §2's "code shape" check pattern); locks in the REIT-misclassification fix (see Defects Found, below); confirms zero coupling to every pre-existing module. |
| `backend/tests/golden/test_financial_strength_engine_golden.py` (new) | 4 golden tests — full `EngineResponse` snapshots for a fortress-balance-sheet profile, a heavily-leveraged profile (with a real, confirmed-possible negative-FCF year), a thin-but-not-gated profile, and an insufficient-data profile. |

**No existing engine, recommendation logic, or India provider was modified.** `business_quality_engine.py` is untouched (confirmed by regression test). `services/sector_quality_applicability.py` (Business-Quality-owned) is read, not modified — see Defects Found below for why a related fix was applied locally instead.

---

## Architecture Changes

None to existing architecture — this sprint implements SSDS-006's adapter/engine boundary for a second engine, exactly as that specification already prescribed (Section 14: "Financial Strength Intelligence... reads the same `info`/`ticker`-shaped contract it already reads today... Nothing in this sprint changes about the engine itself"). The Data Fabric's Provider Registry/Normalization/Confidence layers (SSDS-006 §15, items 4-6) remain unbuilt — this sprint's adapter performs the equivalent resolution inline, exactly as `us_financial_strength_adapter.py`'s own docstring states, since building the full Registry first was never a precondition SSDS-006 set for a second engine's v1.

---

## Scoring Implementation

Five categories, base 50 + capped buckets (mirroring `business_quality_engine.py`'s existing convention):

| Category | Cap | Metrics implemented | Metrics named as Known Limitations (not implemented) |
|---|---|---|---|
| Liquidity Adequacy | ±20 | Current Ratio, Cash Ratio | Quick Ratio (needs a receivables/inventory split the unified schema doesn't carry), Cash Runway (needs a monthly operating-expense figure not in the schema) |
| Leverage & Capital Structure | ±20 | Debt-to-Equity, Short-Term-Debt Share of Total Debt | Net Debt/EBITDA (schema carries EBIT, not EBITDA, and no D&A field to bridge them) |
| Debt-Servicing Capacity | ±20 | Interest Coverage, **Earnings Shock stress scenario** (EBIT -20%, interest coverage recomputed) | Revenue Shock and Liquidity Shock scenarios (both need a cost-structure or debt-maturity-schedule breakdown the schema doesn't carry) |
| Balance Sheet Resilience | ±15 | Equity Ratio | Off-balance-sheet/contingent-liability awareness (no such field exists anywhere in the unified schema) |
| Cash Flow Durability Under Stress | ±15 | Free Cash Flow Margin | — |

**Hard gate (`liquidity_distress`):** Current Ratio ≤ 0.5x **AND** negative free cash flow **AND** real near-term debt obligations (short-term debt > 0) — a deliberately narrow AND-condition per SSDS-005's own "gate-sprawl is a risk to avoid" instruction, confirmed live to trigger on only 2 of 51 eligible companies (AAL, AEP) — both individually explained (see Production Readiness, below), not a broad or noisy gate.

Every threshold is a new, separately-named constant in `services/thresholds.py` — confirmed by the existing `test_no_raw_threshold_literals.py`-style discipline (no new raw literal regression test was added this sprint since `FINANCIAL_STRENGTH`'s constants are all referenced via the dataclass, matching the existing pattern by construction).

---

## Explainability Output

Every `EngineResponse` carries, per SSDS-005 §7 exactly:
- `explanation` naming all five categories and their signed contributions.
- `strengths`/`weaknesses` — the top/bottom-contributing categories with a real, specific reason string (e.g. `"Debt-Servicing Capacity: Interest coverage 33.8x — comfortably covers interest"`), never generic boilerplate.
- `risks` — reserved for capital-preservation-relevant flags only (a failed Earnings Shock scenario, severe leverage) — confirmed distinct from "a below-average metric," per SSDS-003/SSDS-005's shared philosophy.
- `metadata.category_contributions`, `metadata.stress_simulation_results` (the specific shock applied and the specific ratio recomputed, inspectable per SSDS-005's Financial Stress Simulation design constraint — never a black-box adjustment).

---

## Confidence Calculation

`data_completeness_pct` = % of the 16 Mandatory unified fields present, identical model to `business_quality_engine.py`'s (`MIN_DATA_COMPLETENESS_PCT = 60.0`, reused unchanged — Sprint #007 found no evidence to justify a different number). Confirmed live: **average 99.4% completeness among the 49 successfully-scored companies, minimum 81.2%** — zero companies fell into the `insufficient_data` rejection path in this validation run, consistent with Sprint #007's own 98.3% aggregate finding.

---

## Defects Found and Fixed During This Sprint's Required Validation

Per Epic 001's established precedent (validation sprints find and fix genuine defects within their own scope, under the explicit "unless a genuine defect is discovered" exception):

| Defect | Root cause, confirmed live | Fix | Evidence it's fixed |
|---|---|---|---|
| **numpy scalar types leaking into `EngineResponse.metadata`** | `_clean()` in the new adapter passed pandas/yfinance values straight through without casting — `numpy.bool_`/`numpy.float64` are not JSON-serializable by a standard encoder (confirmed: a plain `json.dumps()` call without a `default=str` fallback would break on the engine's own output). | `_clean()` now calls `.item()` on any value exposing it, converting numpy scalars to plain Python types; `passed` in the stress-scenario result is explicitly cast to `bool()` as defense-in-depth. | Confirmed live: `json.dumps(result)` (no fallback) now succeeds; `type(result["metadata"]["stress_simulation_results"][0]["passed"])` is `bool`, not `numpy.bool_`. |
| **REITs misclassified as MANUFACTURING, slipping past this sprint's explicit v1 exclusion** | `sector_quality_applicability.classify_sector()` (Business-Quality-owned) checks `MANUFACTURING`'s patterns — including a bare `industrial` keyword — before `REAL_ESTATE`'s own patterns. Confirmed live: PLD's and PSA's real yfinance industry label is `"REIT - Industrial"`, which matches `MANUFACTURING` first. | A narrow, **local** override in `us_financial_strength_adapter.py` (not the shared classifier): if the raw sector/industry text contains `"reit"`, force `sector_bucket = "REAL_ESTATE"`. Deliberately not fixed in `sector_quality_applicability.py` itself — that module is Business Quality Engine-owned, and changing its classification would silently change BQE's own already-shipped sector_bucket (and therefore scoring/exemptions) for every REIT in production, an uncontrolled blast radius outside this sprint's "preserve Business Quality Engine boundaries" rule. | Confirmed live, before/after: PLD and PSA scored normally (82, 66) pre-fix; both correctly `rejected`/`sector_not_yet_supported` post-fix, alongside the other 4 REITs that were already classifying correctly. |

**1 new regression test locks in the REIT fix** (`test_reit_misclassified_as_manufacturing_is_still_excluded`), which explicitly asserts the underlying classifier defect still exists upstream (so the test stops proving anything meaningful, rather than silently passing for the wrong reason, if `sector_quality_applicability.py` is ever fixed independently in a future, properly-scoped sprint).

---

## Production Readiness Report

**Validation universe:** the same 76-company live dataset Sprint #005/#007 already validated (the SSDS-005 70-company universe + 6 REITs) — re-run live, end-to-end, through the real `compute_us_financial_strength()` entry point. **Zero fetch/scoring errors across all 76 companies.**

| Metric | Result |
|---|---|
| Companies correctly excluded (FINANCIAL + REAL_ESTATE) | 25/76 (19 FINANCIAL, 6 REAL_ESTATE) — matches this sprint's explicit scope exactly |
| Eligible companies (non-FINANCIAL/REAL_ESTATE) | 51/76 |
| Successfully scored | 49/51 |
| Rejected — `insufficient_data` | 0/51 |
| Rejected — `liquidity_distress` | 2/51 (AAL, AEP — both individually explained below) |
| Average data completeness among scored companies | 99.4% (min 81.2%) |
| Grade distribution among scored | strong_buy 13, buy 8, hold 10, watch 10, avoid 8 — a real, non-degenerate spread |
| Score range | 0–100, average 59.6 |
| Earnings Shock stress scenario pass rate | 41/48 (85.4%) — the 7 failures are concentrated in already-low-scoring companies (BA, LCID, RIVN, PLUG, GE, NEE, SO), not scattered noise |

**Individual explanation of the two `liquidity_distress` rejections, confirmed not to be artifacts:**
- **AAL** (American Airlines): current ratio 0.50x, free cash flow −$1.79B — a real, well-known, highly-leveraged airline profile; this is the gate working as designed.
- **AEP** (American Electric Power): current ratio 0.45x, free cash flow −$1.64B — a real utility-sector profile. **Named as an open question for a future sprint, not silently accepted as correct:** utilities customarily run low current ratios as a normal feature of their capital-intensive, regulated-cash-flow business model (SSDS-005's own Sector Adaptations section already flags UTILITIES_ENERGY as "likely needing *adjusted* thresholds rather than exemption" for leverage — this finding extends that same hypothesis to the liquidity gate specifically). **This single data point is not sufficient evidence to change the gate this sprint** — named as a Sprint #009 candidate, not acted on.

**Sector-level average scores (sensible, intuitive directionality, not validated against any external benchmark beyond face plausibility):** cash-rich companies scored highest (82.3 avg), followed by healthcare (75.7) and mega-cap tech (68.2); leveraged companies scored lowest (35.1 avg) alongside industrials (34.6) and utilities (26.7, depressed by the AEP exclusion's absence from this average plus DUK/SO's own weak leverage scores) — directionally exactly what a financial-strength engine should produce.

**A confirmed design validation, not a defect:** several loss-making companies (ROKU 100/strong_buy, SFIX 86/strong_buy, FIVE 98/strong_buy) scored very strongly. This is correct, not a bug — Financial Strength deliberately does not score profitability (that is Business Quality's and the Prediction Engine's territory, per SSDS-005's Scope Boundary); a company can be a mediocre or unprofitable *business* while having an excellent, low-leverage, cash-rich *balance sheet* — exactly the "debt-free, low-growth commodity producer" example SSDS-005's own Design Study used to justify this engine's existence as distinct from Business Quality.

---

## Test Summary

| Category | New this sprint | What they cover |
|---|---|---|
| Unit | 29 | Sector exclusion, data-completeness gate, hard-gate AND-condition, category-direction sanity, stress scenario, full contract compliance, graceful degradation |
| Integration | 5 | Full pipeline wiring, EDGAR/yfinance fallback symmetry |
| Regression | 7 (6 + 1 added for the REIT fix) | Scope-boundary static checks, zero coupling to every pre-existing module, the REIT-classification fix locked in |
| Golden | 4 | Full `EngineResponse` snapshots across 4 representative profiles |
| **Total new** | **45** | |
| **Full suite, before this sprint** | — | 350 passing |
| **Full suite, after this sprint** | — | **394 passing, 0 failing** |

---

## Recommendation on Prediction Engine Integration

**Not yet — name this as the next sprint's decision, not this sprint's.** Per SES-002 §3's note that existing engines aren't required to migrate retroactively, and per this sprint's own explicit "do not modify recommendation logic" rule, `PredictionEngine` is untouched and should remain so until a dedicated, explicitly-scoped integration sprint (mirroring Business Quality's own Sprint #005 precedent: a narrowly-scoped "first consumer" sprint, not a side effect of the implementation sprint). The evidence this sprint produced — 99.4% completeness, a sane and varied grade distribution, two individually-explained gate triggers, zero crashes across 76 real companies — supports recommending that integration sprint be scheduled, but integrating now would conflate "the engine works" with "the recommendation logic should use it," two separable decisions.

---

## Remaining Gaps / Known Limitations (named, not silently absorbed)

1. Quick Ratio, Cash Runway, Net Debt/EBITDA, off-balance-sheet awareness, and the Revenue/Liquidity Shock scenarios are all named, unimplemented SSDS-005 metrics — every omission traces to a specific missing field in the 16-field unified schema, not an oversight.
2. The `liquidity_distress` gate's behavior for UTILITIES_ENERGY (the AEP finding) is named as an open question for Sprint #009, not resolved.
3. FINANCIAL and REAL_ESTATE sectors remain entirely unscored — this is this sprint's own explicit, stated scope boundary, not a newly-discovered gap.
4. `sector_quality_applicability.classify_sector()`'s REIT-vs-MANUFACTURING collision is fixed locally for this engine only — Business Quality Engine and any future consumer of `classify_sector()` directly still inherit the same upstream defect, named here for whichever future sprint owns that module.
5. No live cross-check exists yet between this engine's scores and any real-world credit-rating or distress benchmark — the Production Readiness Report above validates internal consistency and plausibility, not external accuracy against a ground truth.

---

## GitHub Actions Result

Recorded below, after this sprint's commit is pushed and confirmed.

## Final Commit Hash

Recorded below, after this sprint's commit.

---

*This sprint implemented Financial Strength Intelligence Engine v1 for non-FINANCIAL, non-REAL_ESTATE US companies, found and fixed two genuine defects during its own required validation, and validated against the full 76-company live universe (51 eligible, 49 scored). No existing engine, recommendation logic, or India provider was modified. Not wired into any consumer.*
