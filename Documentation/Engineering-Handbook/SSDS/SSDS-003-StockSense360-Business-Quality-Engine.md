# SSDS-003 — StockSense360 Business Quality Engine

**Status:** Active — governing. Specification only; no implementation in this sprint.
**Governed by:** SES-001 through SES-005, the StockSense360 Product Glossary, SSDS-000.
**Sprint:** #003 — opens the Investment Intelligence Layer of StockSense360.

---

## Phase 1 — Architecture Validation

**Documents and code reviewed:** SSDS-000, SES-001–005, the StockSense360 Product Glossary, SEAR-001, ROADMAP.md, `backend/services/prediction_engine.py`, `backend/services/quality_factors.py` (full file, 1,830 lines), `backend/services/multibagger_scorecard.py`, `backend/services/screener_data.py`, `backend/services/us_fundamentals.py`, `backend/services/thresholds.py`, `backend/services/engine_contract.py`.

**Verdict: SSDS-000 remains the approved baseline architecture. No redesign of SSDS-000 is required.** Three things are missing *underneath* it — additive, not architecture-changing — and are documented below as the minimum required scope for next sprint's implementation, not as a deficiency in SSDS-000 itself.

### Finding 1 — `quality_factors.py` is not the Business Quality Engine; it's broader

SSDS-000 §3 already states the Business Quality Engine is "Established as a behavior, distributed across `quality_factors.py` and `multibagger_scorecard.py`," with "no single class or file is literally named 'Business Quality Engine.'" Direct review of `compute_all_quality_factors()` (the function that actually combines everything) confirms *why*, precisely: it blends **14 dimensions**, only **5 of which** are genuinely "is this a durable, well-run business" questions:

| Dimension (existing code) | Is this a Business Quality question? |
|---|---|
| `buffett` (Buffett/Munger checklist) | **Yes** |
| `altman` (Altman Z-Score) | **Yes** |
| `accruals` (Sloan accruals) | **Yes** |
| `quality_metrics` (Piotroski F-Score, ROIC) | **Yes** |
| `corporate_actions` (dividends, dilution, buybacks) | **Yes** |
| `earnings_revision` | No — analyst-sentiment momentum, belongs with News & Sentiment / Technical-adjacent signals. |
| `institutional` (% held) | No — smart-money-flow proxy, not a business-durability question. |
| `inst_flow` (volume/price divergence, MFI, OBV) | No — pure technical/flow signal. |
| `relative_strength` (stock vs. Nifty 50) | No — momentum, explicitly a Technical Analysis Engine concept per the Glossary. |
| `sector_strength` (sector index momentum) | No — momentum, same as above. |
| `valuation` (PEG, EV/EBITDA, sector-relative PE) | No — this is the Glossary's separate **Valuation Score** concept, not business quality. A great business can be a bad investment at the wrong price; conflating the two is exactly the kind of "duplicated/blended scoring" pattern SEAR-001 flagged elsewhere. |
| `risk_management` (drawdown, volatility, Sharpe) | No — this is the Glossary's **Risk Score**, already deliberately kept distinct from quality. |
| `liquidity` | No — market-microstructure, not business quality. |
| `mf_trend` | No — institutional-flow proxy, same bucket as `inst_flow`. |

**This is a real, evidence-based finding, not a stylistic preference:** the existing module's 14-dimension blend conflates four *different* Glossary-established concepts (Business Quality, Valuation Score, Risk Score, momentum/flow signals that feed the Technical Analysis Engine) into one number. Building "the Business Quality Engine" by simply renaming `compute_all_quality_factors()` would inherit that conflation, not fix it.

**Minimum required scope (not a redesign):** the Business Quality Engine specified below is a **new, narrower deterministic aggregation** that reuses the 5 already-Business-Quality-relevant functions (`buffett_munger_score`, `altman_zscore_signal`, `sloan_accruals_signal`, `quality_metrics_score`, `corporate_actions_score`) as building blocks, adds the metrics named in this spec that don't exist yet (margins, FCF/OCF ratios, cash conversion, working-capital efficiency, Beneish M-Score), and explicitly **excludes** the other 9 existing dimensions from its own score. Those 9 dimensions are not deleted or deprecated — they continue to belong to the Prediction Engine's broader composite and/or the Glossary's Technical Analysis Engine / Risk Score / Valuation Score, exactly as SSDS-000 already describes. This finding does not contradict SSDS-000; it adds the precision SSDS-000's "highest-level" scope deliberately left for a §3-level document like this one.

### Finding 2 — No general sector-adaptation framework exists; only one binary flag

Direct grep across `prediction_engine.py` and `quality_factors.py` confirms exactly one sector-adaptation mechanism exists today: an `is_financial` boolean (`any(k in sector or k in industry for k in ("financial", "bank", "insurance", "nbfc"))`), used to exempt financial-sector stocks from a small number of specific checks (the OCF hard-reject, the extreme-leverage hard-reject). There is **no general per-sector metric-applicability table** — nothing that says, e.g., "gross margin is not a meaningful metric for a bank" beyond that one binary flag.

There **is** a reusable foundation already in the codebase: `quality_factors.py`'s `SECTOR_INDICES` / `_SECTOR_KEYWORD_MAP` / `STOCK_SECTOR` (10 buckets: IT, Bank, Finance, Pharma, Auto, FMCG, Metal, Energy, Realty, Infra), built for sector-momentum comparison, not for metric adaptation — but its sector vocabulary is a good starting taxonomy to extend rather than replace. **Confirmed gap:** "Telecom" does not exist anywhere in this taxonomy today.

**Minimum required scope (not a redesign):** a new, additive sector-applicability table (specified in §4 below) is needed before the Business Quality Engine can do real industry adaptation. This is a net-new, narrow module (consistent with SES-002 §5's "new, single-purpose modules are preferred over adding another unrelated concept to an existing god-file"), not a change to SSDS-000's architecture.

### Finding 3 — Beneish M-Score does not exist anywhere; everything else requested mostly does

Confirmed by direct search: no Beneish M-Score implementation exists anywhere in the codebase, consistent with SEAR-001's Quality Gates finding ("Distress and quality scoring exist; fraud-risk scoring specifically does not"). Conversely, ROE, ROCE, ROIC, gross/operating/net margin (via `profitMargins`/derived), FCF, OCF, D/E, interest coverage, revenue growth, EPS growth, dividend quality, and share dilution **already have working implementations** somewhere in `quality_factors.py`, `screener_data.py`, or `us_fundamentals.py` — these are reuse targets, not new builds. Asset turnover and cash-conversion-style ratios exist partially (inside Piotroski's `quality_metrics_score`) but are not exposed as standalone, named metrics. Working-capital-efficiency as a distinct named metric does not exist (only as an intermediate term inside the Altman Z-Score calculation). See §3 for the full per-metric status.

### Architecture Validation Summary

**SSDS-000 requires no changes.** Two additive, narrowly-scoped pieces of groundwork are needed before/during next sprint's implementation, neither of which changes the platform's high-level architecture:
1. Scope the Business Quality Engine as a new aggregation over a *subset* of existing functions plus new metrics — not a rename of `compute_all_quality_factors()`.
2. Build a sector-applicability table extending the existing sector taxonomy (adding Telecom) — not a redesign of how sectors are classified elsewhere in the system.

---

## Phase 2 — SSDS-003 Specification

### Purpose

The Business Quality Engine answers exactly one question: **"Is this fundamentally an outstanding business worthy of long-term ownership?"** It does not generate BUY/HOLD/SELL recommendations — that remains the Prediction Engine's and, downstream, the Recommendation output's job (per the Glossary, "Recommendation Engine" is not a separate component; see SSDS-000 §3). The Business Quality Engine's responsibility ends at producing a structured, evidence-backed quality assessment that the Prediction Engine, Ranking & Filtering, and (in the future) Portfolio Copilot consume as one input among several.

---

## 1. Investment Philosophy

The Business Quality Engine is built on the following principles, synthesized from durable, widely-taught investment ideas without adopting any single investor's proprietary checklist as-is:

- **Long-term ownership over short-term trading.** The Engine deliberately ignores price momentum, technical signals, and short-term sentiment — those already belong to the Technical Analysis Engine and News & Sentiment Engine (Finding 1). A business's quality does not change because its stock moved 5% this week.
- **Capital preservation first.** A business that destroys capital (negative ROIC, deteriorating balance sheet, accounting red flags) cannot be "high quality" regardless of its growth story — this is why hard quality gates exist (§2) rather than treating every factor as a soft, averageable input.
- **Quality before valuation.** The Engine explicitly does not price the business — see Finding 1's distinction from the Glossary's separate Valuation Score. A wonderful business at a terrible price is still a wonderful business; the Engine's job is to answer the business-quality half of that equation only.
- **Evidence over opinion.** Every sub-score traces to a specific, named financial metric computed from real data — never a holistic, unexplainable judgment. This directly extends SES-001 §3 (Evidence over Assertion) into the investment-logic layer itself.
- **Explainability first.** Per SES-004 and the Glossary's Explainability Layer entry, every score must be traceable to its contributing evidence — this is a hard requirement on the Engine's output shape (§7), not an afterthought.
- **Deterministic scoring.** Every sub-score and the combined score are computed by deterministic formulas over real financial data — never generated. This matches SSDS-000 §7's hard architectural principle ("AI assists, never invents") exactly; the Business Quality Engine has zero LLM involvement in its scoring path.
- **AI-assisted analysis where appropriate.** The *only* place AI-in-the-generative-sense could ever appear in this Engine's lifecycle is in a future natural-language *summary* of an already-computed, deterministic result (consistent with SSDS-000 §7's narrate-don't-invent boundary) — never in computing the score itself. This is a Future Expansion Note (§10), not a current design element.

---

## 2. Business Quality Framework

### Evaluation Categories

Five categories, directly answering Finding 1's "is this a Business Quality question" filter — each category maps to a specific, named investment concern:

| Category | Investment Question | Existing Building Block |
|---|---|---|
| **Profitability & Capital Efficiency** | Does this business earn attractive returns on the capital it deploys? | `quality_metrics_score` (ROIC, Piotroski) — extend with ROE/ROCE/margins (already computed elsewhere; not yet assembled into this category). |
| **Balance Sheet Strength** | Could this business survive a downturn without distress? | `altman_zscore_signal` — extend with D/E, interest coverage (already computed elsewhere). |
| **Earnings Quality** | Are reported profits real cash, or accounting artifacts? | `sloan_accruals_signal` — extend with a new Beneish M-Score check, cash conversion ratio (new). |
| **Capital Allocation & Shareholder Treatment** | Does management compound shareholder capital responsibly, or dilute/waste it? | `corporate_actions_score` (dividends, dilution, buybacks). |
| **Durable Competitive Position** | Does this business have a moat — sustained capital efficiency over time, not a one-year blip? | `buffett_munger_score` — extend with multi-year ROIC/margin trend consistency (new — see §5 for the data-history caveat). |

### Sub-Factors

See §3 for the complete metric-to-category mapping with mandatory/optional/sector-specific status.

### Weighting Methodology

Each category contributes a capped point range to a 0–100 combined score, following the same "base + capped buckets" pattern already proven in `prediction_engine.py`'s `_fundamental_score` (base 50, each bucket ±N around zero) rather than inventing a new aggregation style:

| Category | Cap (± around base 50) | Rationale |
|---|---|---|
| Profitability & Capital Efficiency | ±20 | The single most direct evidence of business quality (a business either earns good returns on capital or it doesn't). |
| Balance Sheet Strength | ±15 | Necessary but not sufficient — a fortress balance sheet with no profitability isn't "quality," just safe. |
| Earnings Quality | ±15 | Distrust of unverified profitability — this category exists specifically to catch businesses whose Profitability score might otherwise be overstated by aggressive accounting. |
| Capital Allocation & Shareholder Treatment | ±10 | Important but most often a confirming signal rather than the primary driver. |
| Durable Competitive Position | ±15 | Distinguishes a business with one good year from one with a genuine, multi-year moat. |
| Sector adjustment | applied before capping, not a separate bucket | See §4 — a sector-specific multiplier/exemption applied to the metrics that don't translate across industries (e.g. a bank's structurally different leverage profile), not a bonus/penalty bucket of its own. |

Combined score = `50 + Σ(category_score)`, clamped to `[0, 100]` — identical clamping convention to existing engines, for consistency.

### Hard Quality Gates

Following the existing `_quality_gate` pattern in `prediction_engine.py` (a deliberately minimal, 4-check hard reject, not a sprawling list): a stock fails the Business Quality Engine's gate — receiving a `REJECTED` grade outright rather than a low score — only for:
1. Altman Z-Score in the distress zone **and** Sloan accruals indicating aggressive earnings management simultaneously (either alone is a soft penalty per the table above; both together is a hard reject — a deliberately narrow AND-condition, matching SEAR-001's critique that the *existing* hard gate is appropriately minimal and that gate-sprawl is a risk to avoid repeating).
2. A new Beneish M-Score result above the conventional manipulation-likelihood threshold (see §3) — fraud-risk evidence is treated as disqualifying, not merely score-reducing, consistent with the philosophy that capital preservation comes first.
3. Sector-exempted businesses (financials, per the existing `is_financial` flag) skip gate checks 1–2's leverage-dependent components exactly as `prediction_engine.py`'s existing exemption already does — not a new exemption mechanism, reuse of the existing one.

### Soft Quality Adjustments

Everything in the five categories' tables (§3) that isn't one of the three hard-gate conditions above is a soft, capped-bucket adjustment — consistent with SEAR-001's finding that the *existing* system already distinguishes hard-reject from soft-penalty mechanisms, and this Engine should not blur that distinction further.

---

## 3. Metrics

Status legend: **Mandatory** (always computed, contributes to the score whenever data exists) · **Optional** (computed when available; absence reduces confidence, not the score) · **Sector-specific** (only meaningful/applied for certain sectors, per §4).

| Metric | Category | Why it matters | Status | Existing building block |
|---|---|---|---|---|
| ROE | Profitability & Capital Efficiency | Capital efficiency from the equity holder's view. | Mandatory | `info.get("returnOnEquity")`, already read in `prediction_engine.py`/`quality_factors.py`. |
| ROCE | Profitability & Capital Efficiency | Capital efficiency independent of capital structure — comparable across leverage levels. | Mandatory | `info.get("returnOnCapitalEmployed")` (IN), derived EBIT-based calc (US, `us_fundamentals.py`). |
| ROIC | Profitability & Capital Efficiency | The closest proxy to "does this business earn more than its cost of capital" — Munger's actual moat test. | Mandatory | `quality_metrics_score`'s existing ROIC calc (NOPAT / invested capital). |
| Gross Margin | Profitability & Capital Efficiency | Pricing power / cost structure — a primary moat signal. | Optional | Not currently assembled as a standalone field; derivable from yfinance's `grossMargins`. |
| Operating Margin | Profitability & Capital Efficiency | Operating efficiency net of overhead. | Optional | `info.get("operatingMargins")` — available, not currently used in `quality_factors.py`. |
| Net Margin | Profitability & Capital Efficiency | Bottom-line profitability. | Optional | `info.get("profitMargins")` — already used elsewhere in the codebase (e.g. `prediction_engine.py`'s quality gate), not yet in a Business Quality context. |
| Free Cash Flow | Earnings Quality | Cash the business can actually deploy/return, after maintaining itself. | Mandatory | `info.get("freeCashflow")` — already read in `prediction_engine.py`'s risk penalty. |
| Operating Cash Flow | Earnings Quality | Whether reported earnings convert to cash. | Mandatory | Already read in multiple places (`operatingCashflow`/`operatingCashflows`, screener.in's `operating_cf_latest_cr`). |
| Debt to Equity | Balance Sheet Strength | Leverage / financial fragility. | Mandatory, sector-specific | `info.get("debtToEquity")`, already centralized in `services/thresholds.py`. |
| Interest Coverage | Balance Sheet Strength | Ability to service debt from operating earnings. | Mandatory | screener.in's `interest_coverage_ratio` (IN) — confirmed not currently derived for US; see Known Limitations. |
| Cash Conversion (OCF / Net Income) | Earnings Quality | Direct check on whether profit is real cash, not just an accounting number. | Mandatory (new) | Partially implicit inside Piotroski's accrual-quality checks; not exposed as a standalone ratio today — **new metric to formalize**. |
| Asset Turnover | Profitability & Capital Efficiency | Revenue generated per unit of assets — efficiency of asset base. | Optional (new, exposed) | Computed internally inside Piotroski's P9 check (`quality_metrics_score`) but not exposed as a standalone score input — **new: expose as its own line item**. |
| Working Capital Efficiency | Earnings Quality | Detects deteriorating receivables/inventory discipline. | Optional (new) | Only exists today as an intermediate term (`X1 = Working Capital / Total Assets`) inside the Altman Z-Score formula — **new: a standalone working-capital-trend check**. |
| Revenue Growth | (Informational only — not scored in this Engine) | Growth is a Prediction Engine / Multibagger concept already; the Business Quality Engine treats growth *consistency*, not magnitude, as the relevant durability signal (see Durable Competitive Position). | Mandatory, informational | `info.get("revenueGrowth")`, screener.in's `sales_growth_3y_pct`/`5y_pct` — reused for the trend-consistency check only, not re-scored as its own growth bucket (avoiding the duplication SEAR-001 flagged with growth thresholds existing redundantly elsewhere). |
| EPS Growth | (Informational only — same rationale as Revenue Growth) | Same as above. | Mandatory, informational | screener.in's `profit_growth_3y_pct`/`5y_pct`, `eps_trend`. |
| Share Dilution | Capital Allocation & Shareholder Treatment | Issuing new shares dilutes existing holders — a capital-allocation discipline signal. | Mandatory | Already implemented in `corporate_actions_score` (line ~645, "Significant equity dilution" check). |
| Dividend Quality | Capital Allocation & Shareholder Treatment | Consistency/growth of dividends signals capital-allocation discipline (not all quality businesses pay dividends — absence is neutral, not negative). | Optional | Already implemented in `corporate_actions_score` (dividend track record + growth-trend logic). |
| Piotroski F-Score | Profitability & Capital Efficiency / Earnings Quality (composite) | A 9-point composite already covering several of the above signals in one number — used as a cross-check against this Engine's own category scores, not double-counted as a separate bucket. | Mandatory | `quality_metrics_score`'s existing 9-point Piotroski implementation — full reuse. |
| Altman Z-Score | Balance Sheet Strength | Validated distress-prediction model. | Mandatory, hard-gate-eligible | `altman_zscore_signal` — full reuse, including its existing financial-sector exemption. |
| Beneish M-Score | Earnings Quality | Closes the confirmed gap (Finding 3 / SEAR-001) between "we score distress and quality" and "we screen for fraud." | Mandatory (new), hard-gate-eligible | **New** — no existing implementation anywhere in the codebase. Standard 8-variable formula (DSRI, GMI, AQI, SGI, DEPI, SGAI, TATA, LVGI); a manipulation-likelihood score above the conventional -1.78 threshold contributes to the hard gate per §2. |

**Recommended additional metric, not in the brief's list:** **Interest Coverage for US stocks** — confirmed not currently derived (`us_fundamentals.py` derives ROCE but not interest coverage); needed for the Balance Sheet Strength category to function symmetrically across both markets. Flagged as a Known Limitation (§Known Limitations) rather than silently assumed solved.

---

## 4. Industry Adaptation

### Sectors Covered

Extending the existing 10-bucket taxonomy (`quality_factors.py`'s `SECTOR_INDICES`/`STOCK_SECTOR`) with the sectors named in this task's brief that don't already exist in it:

| Brief's sector | Existing taxonomy bucket | Status |
|---|---|---|
| Banks | `Bank` | Already exists. |
| NBFCs | `Finance` | Already exists (NBFC keyword already mapped to `Finance`). |
| Insurance | `Finance` | Already exists (insurance keyword already mapped to `Finance`). |
| FMCG | `FMCG` | Already exists. |
| IT | `IT` | Already exists. |
| Pharma | `Pharma` | Already exists. |
| Manufacturing | `Infra` (capital-goods/engineering keywords already map here) | Already exists, named differently. |
| Capital Goods | `Infra` | Already exists, same bucket as Manufacturing. |
| Utilities | `Energy` (electric-utilities keyword already maps here) | Already exists, named differently. |
| Energy | `Energy` | Already exists. |
| **Telecom** | — | **Confirmed gap (Finding 2) — new bucket required.** |
| Real Estate | `Realty` | Already exists. |

### Metric Applicability Table

| Metric | Universal | Requires sector adjustment | Ignore for |
|---|---|---|---|
| ROE / ROCE / ROIC | Yes | Banks/NBFCs/Insurance: compare against sector-relative norms, not the universal threshold (leverage is structurally part of their business model, not a risk signal the way it is for a manufacturer) | — |
| Debt to Equity | — | Banks/NBFCs/Insurance need a fundamentally different leverage lens (already partially handled by the existing `is_financial` exemption — extend, don't replace) | Banks/NBFCs/Insurance (the *universal* D/E thresholds in `services/thresholds.py` do not apply; a sector-specific leverage check, if any, is out of this spec's scope and a Future Expansion item) |
| Operating Cash Flow | — | Banks: loans disbursed count as operating outflows under Ind-AS, exactly as already documented in `prediction_engine.py`'s `is_financial` comment — reuse that documented rationale, don't re-derive it. | Banks/NBFCs/Insurance (reuse the existing exemption) |
| Gross Margin | — | IT/Pharma/FMCG: meaningful and comparable. Banks/NBFCs/Insurance/Utilities/Energy: not a meaningful concept in their financial-statement structure. | Banks, NBFCs, Insurance, Utilities, Energy |
| Asset Turnover | Manufacturing/Capital Goods/Real Estate: asset-heavy businesses where this is highly diagnostic | IT/Pharma: less diagnostic (asset-light models) — treat as Optional rather than Mandatory for these sectors | — |
| Interest Coverage | Utilities/Energy/Telecom/Real Estate: typically capital-intensive, debt-funded — highly relevant | — | Banks/NBFCs/Insurance (debt is their raw material, not leverage risk in the same sense) |
| Working Capital Efficiency | Manufacturing/FMCG: highly diagnostic (inventory/receivables discipline) | IT: less diagnostic (minimal inventory) | Banks/NBFCs/Insurance, Utilities (different balance-sheet structure entirely) |
| Dividend Quality | All sectors | — | None — absence remains neutral for any sector, including growth-stage IT/Pharma names that reasonably pay no dividend |

**Minimum required addition (Finding 2):** a new `services/sector_quality_applicability.py`-style module (naming illustrative, not prescriptive — implementation sprint decides) encoding the table above, extending rather than replacing the existing `STOCK_SECTOR`/`_SECTOR_KEYWORD_MAP` taxonomy, with the new Telecom bucket added.

---

## 5. Data Quality

- **Missing data handling:** a metric with no available data is excluded from its category's score entirely (the category's combined score is computed only over metrics that *do* have data, matching the existing `_fundamental_score` pattern of `if value is not None:` guards throughout the codebase) — never defaulted to a neutral/zero value, which would silently bias the score.
- **Stale data handling:** reuse the existing per-source cache-TTL pattern (no new staleness concept introduced) — fundamentals refresh nightly (`stock_fundamentals_cache`), consistent with the rest of the Selection Engine.
- **Conflicting data handling:** where IN's screener.in-sourced figure and yfinance's figure for the same concept disagree (a real, confirmed risk per SEAR-001 Section 2 — e.g. OCF sourced two different ways), prefer the screener.in figure for IN stocks (matches existing precedent in `prediction_engine.py`, which already prefers `_screener_data` fields where available) and log the discrepancy via `services.logging_config` rather than silently picking one — a new, named gap this spec surfaces rather than resolves silently.
- **Confidence reduction rules:** the Engine's `confidence` field (per the `EngineResponse` contract, §6) is reduced proportionally to the fraction of Mandatory metrics missing — not a fixed penalty, since "two missing optional metrics" and "two missing mandatory metrics" are very different confidence situations.
- **Minimum acceptable data completeness:** if fewer than 60% of a sector's Mandatory metrics (per §4's applicability table — not the universal list, since sector-exempted metrics don't count against completeness) are available, the Engine returns `grade = REJECTED` with `metadata.rejection_reason = "insufficient_data"` rather than a low-confidence score — consistent with the philosophy that a confidently-low score and an unknowably-low score must never look the same to a downstream consumer.

---

## 6. Engine Output

Complies with `services/engine_contract.py`'s `EngineResponse` exactly (score, grade, confidence, strengths, weaknesses, risks, explanation, metadata) — no new contract invented:

| EngineResponse field | Business Quality Engine specifics |
|---|---|
| `score` | The 0–100 combined Business Quality Score from §2. |
| `grade` | Uses the existing `Grade` enum. Business Quality doesn't need the full BUY/SELL vocabulary (it isn't a recommendation) — maps onto `STRONG_BUY`→"Exceptional," `BUY`→"High Quality," `HOLD`→"Adequate," `WATCH`→"Below Average," `AVOID`→"Poor," `REJECTED`→hard-gate failure or insufficient data. (Reusing the existing enum rather than adding a parallel one, per SES-002 §3's instruction not to invent new ad hoc dict shapes.) |
| `confidence` | Per §5's data-completeness-driven calculation. |
| `strengths` | The highest-contributing Mandatory metrics, by category. |
| `weaknesses` | The lowest-contributing Mandatory metrics, by category — distinct from `risks` (see below). |
| `risks` | Specifically: anything flagged by Earnings Quality (Beneish, accruals) or Balance Sheet Strength (Altman) — i.e., risks are reserved for capital-preservation concerns, not just "a below-average metric," matching the philosophy in §1. |
| `explanation` | A short narrative synthesizing the category scores — deterministically templated, not generated (§1, §10). |
| `metadata` | `sector`, `sector_bucket` (per §4), `data_completeness_pct`, `rejection_reason` (if applicable), `piotroski_score`, `altman_z`, `beneish_m` (new), `accruals_ratio`. |

**Two fields requested in the brief that are not native `EngineResponse` fields — both added via `metadata`, not via a contract change, per SES-002 §3's instruction that contract changes are a separate, deliberate decision:**
- `metadata.suitable_investment_style` — e.g. "Quality Compounder," "Deep Value Candidate," "Turnaround Watch" — a categorical label derived from the category-score pattern (e.g. high Profitability + high Balance Sheet Strength + lower growth consistency → "Quality Compounder").
- `metadata.suggested_holding_horizon` — "Long" by default (this Engine's entire reason for existing is long-term ownership per §1), downgraded to "Medium" only if Durable Competitive Position scores poorly (a business without a moat is less suited to multi-year holding regardless of its current profitability).

---

## 7. Explainability

Every `EngineResponse` from this Engine must populate:
- **Why the score was assigned** — `explanation`, synthesizing all five category scores.
- **Strongest positive contributors** — `strengths`, the top 2–3 Mandatory metrics by category contribution.
- **Largest weaknesses** — `weaknesses`, the bottom 2–3.
- **Key risks** — `risks`, reserved for capital-preservation-relevant flags only (§6).
- **Missing information** — surfaced via `metadata.data_completeness_pct` plus an explicit list of which Mandatory metrics were unavailable, not just a single aggregate percentage (a user/downstream-engine should be able to see *which* metric was missing, not just *that* something was).
- **Factors that could improve or reduce the score** — a deterministic, templated statement per category (e.g. "ROIC would need to exceed 15% to move Profitability & Capital Efficiency into the next tier") — generated from the same threshold values already driving the score, not a separate explanation system.

---

## 8. Validation Strategy

Per SES-003, and per this task's explicit "do not implement tests" instruction — this section defines the *approach* only.

- **Benchmark companies:** a small, hand-picked set of companies with well-known, broadly-agreed-upon quality reputations (both IN and US, both "obviously high quality" and "obviously low quality" extremes) used as golden-test fixtures once implementation begins — mirroring the pattern already established in `tests/golden/test_multibagger_scorecard_golden.py`.
- **Cross-sector validation:** confirm the Engine produces sensible relative rankings *within* each of the 12 sectors from §4 (a bank should not be penalized for "failing" a metric that's explicitly sector-exempted) — not cross-sector ranking, since a bank and an FMCG company are not directly comparable on this Engine's score by design.
- **Historical validation:** run the Engine against historical fundamentals snapshots (where available via `score_snapshots`/`stock_fundamentals_cache` history) for companies that later experienced a confirmed quality deterioration (e.g. a dividend cut, a credit-rating downgrade) — checking whether the Engine's score trended down *before* the public event, which is the actual test of whether this Engine adds value over just reading headlines.
- **Regression testing:** once implemented, follow SES-003 exactly — unit tests per category function, an integration test for the combined score, a golden test for the full `EngineResponse` shape, and a static regression test (mirroring `test_no_raw_threshold_literals.py`'s pattern) ensuring this Engine's thresholds are migrated into `services/thresholds.py` from day one, not added as a second, parallel hardcoded set.
- **Explainability review:** a manual read-through of a sample of `explanation`/`strengths`/`weaknesses`/`risks` output across the benchmark set, checking that the prose is genuinely informative and not generic boilerplate that happens to be technically true — the same bar SEAR-001 applied when reviewing the existing `reasoning` array's quality.

---

## 9. Integration

| Consumer | How it uses the Business Quality Engine |
|---|---|
| **Prediction Engine** | Receives the Business Quality `score`/`grade` as one input to its own composite (replacing/refining the `quality_score` field already in the API today — a Planned change, not done in this spec). |
| **Ranking & Filtering** | Can filter/rank candidates by Business Quality Score independently of the composite signal — e.g. a future "Quality Compounder" screen filter, distinct from today's Multibagger Screen's own bespoke checklist (`multibagger_scorecard.py` remains a separate, deliberately-distinct rule-based system per the Glossary; this Engine does not replace it, though the two should eventually share metric-computation building blocks rather than duplicate them — a Future Expansion item). |
| **Recommendation generation** | Per the Glossary's "Recommendation Engine" entry (SSDS-000 §3), there is no separate recommendation component today — the Prediction Engine's `signal` field is the recommendation. The Business Quality Engine feeds that signal as an input; it does not generate a recommendation itself, per its stated Purpose. |
| **Portfolio Copilot** | **Future** (per SSDS-000 §3, not implemented today) — when built, Portfolio Copilot would use a holding's Business Quality Score to distinguish "this position is concerning because the business itself has deteriorated" from "this position is concerning only because of concentration risk" — two very different portfolio-advice conclusions. |
| **Daily Picks** | The Ranking & Filtering step (already a Daily Picks consumer per SSDS-000 §4) gains a new, independent quality signal to rank against, distinct from the existing composite score. |
| **Explainability Layer** | The Business Quality Engine's own `explanation`/`strengths`/`weaknesses`/`risks` (§7) feed directly into the existing `reasoning` array assembly pattern in `prediction_engine.py` — same mechanism, additional source. |

---

## 10. Future Expansion

| Direction | Extension point |
|---|---|
| **US Markets** | Already largely supported by this spec's design (every metric names its US data source where it differs) — the main open item is US interest coverage (Known Limitation below), not a structural gap. |
| **ETFs** | A fund's "business quality" doesn't map onto this Engine's company-level metrics — would need a parallel, fund-specific quality framework (expense ratio, tracking error, holdings concentration), not an extension of this one. |
| **Mutual Funds** | Same reasoning as ETFs — `mf_holdings.py` already exists for holdings-disclosure data, a different concern from fund-level quality scoring. |
| **Crypto** | Business Quality as defined here (ROE, ROIC, accruals, dividends) has no equivalent for an asset with no issuer, no financial statements, and no earnings — this Engine does not extend to crypto; `crypto_engine.py`'s existing `_fear_greed`/`_on_chain_proxy` proxies remain the appropriate crypto-specific signals, entirely separate from this spec. |
| **Institutional research** | A natural consumer of this Engine's full category breakdown (not just the headline score) — no new engineering needed, just a future UI/export surface. |
| **AI Copilot** | Per §1 and SSDS-000 §7 — a future conversational layer could narrate this Engine's already-computed category scores in natural language; it must never be allowed to compute or override the score itself. |
| **Custom user preferences** | A user who personally weighs Balance Sheet Strength more heavily than Durable Competitive Position (a genuinely reasonable individual preference) could eventually get a personalized re-weighting of the same five category scores — the categories and their underlying metrics stay fixed; only the combination weights would become user-adjustable. Not in scope for the first implementation. |

---

## List of Assumptions

1. The implementation sprint will build the Business Quality Engine as a new module (or a clearly-separated new function within `quality_factors.py`), not a modification of `compute_all_quality_factors()`'s existing 14-dimension blend — per Finding 1, those two things must remain distinguishable.
2. The sector-applicability table in §4 will be implemented as a new, additive module, reusing (not replacing) the existing `STOCK_SECTOR`/`_SECTOR_KEYWORD_MAP` taxonomy.
3. Beneish M-Score will use the standard, published 8-variable formula and the conventional -1.78 manipulation-likelihood threshold — no proprietary variant.
4. The existing `is_financial` exemption rationale (Ind-AS accounting treatment of bank OCF) is assumed correct and is reused, not re-derived, for this Engine's sector adjustments.

## Known Limitations

1. **US interest coverage is not currently derived anywhere** (`us_fundamentals.py` derives ROCE but not interest coverage) — the Balance Sheet Strength category will be asymmetric between IN and US markets until this is built, and this spec does not resolve it, only names it.
2. **Multi-year history for the Durable Competitive Position category is constrained** by the same yfinance 4-year cap already documented elsewhere in the codebase (US growth figures) — a true "moat over time" check is weaker for US stocks than IN stocks (where screener.in provides longer history), mirroring an already-disclosed, pre-existing asymmetry rather than introducing a new one.
3. **Cross-source data conflicts (§5) are named, not resolved** by this spec — a genuine reconciliation mechanism (beyond "prefer screener.in for IN") is future engineering work.
4. **No live "Business Quality Score" field exists in the API today** — per the Glossary, "Quality Score" (the existing `quality_score` field) is the established name for the *current*, broader blend. Whether the new, narrower Business Quality Engine score replaces that field's contents, or is exposed as a new, separate field, is an implementation-sprint decision this spec does not make — flagged explicitly rather than assumed.

## Future Enhancements

1. Share metric-computation building blocks between this Engine and `multibagger_scorecard.py`'s checklist, rather than maintaining two independent calculations of overlapping concepts (ROCE, D/E, OCF) — a duplication-reduction opportunity, not required for first implementation.
2. A sector-specific leverage framework for financials (today only exempted, never positively scored) — currently out of scope, named as a gap in §4.
3. User-adjustable category weighting (§10).

---

## Update to INDEX.md

Per this task's completion checklist — `INDEX.md`'s "System Design Specifications (SSDS)" section now lists SSDS-003 alongside SSDS-000/001/002 (see the commit accompanying this document).

---

*This document is a specification only. No code was written or implemented in producing it. No existing business logic was modified.*
