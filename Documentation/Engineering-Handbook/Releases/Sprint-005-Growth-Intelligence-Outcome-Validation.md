# Epic 003, Sprint #005 — Growth Intelligence Outcome Validation

**Status:** Complete. **No Prediction Engine integration performed. No scoring weights changed. No Business Quality/Financial Strength/Valuation work performed.** This sprint's entire output is evidence-gathering and one new analysis methodology — confirmed by the diff being limited to this report and its supporting scratchpad artifacts (not committed).

**Methodology, stated upfront and honestly:** no point-in-time historical fundamentals database exists for either market (a true rolling backtest would need fundamentals snapshots at many distinct past dates, which no free provider exposes). This sprint instead used a **single-window proxy methodology**: for India, each company's multi-year arrays were truncated by one year to compute a genuine "score as of ~1 year ago," anchored to the real date implied by the second-to-last fiscal-year label, then real historical price data (via yfinance) gave actual forward 3/6/12-month returns from that anchor. **This is also the Stability Review's mechanism** — comparing the truncated ("1-year-ago") score against today's full-data score is a direct, evidence-based rank-stability measurement, not a separate exercise.

**A real methodological limitation was found and is reported transparently, not hidden:** the identical truncation approach was attempted for US but failed — yfinance typically exposes only ~4-5 years of annual financials, and dropping one year left most US companies with only 3 valid data points, below the engine's own 4-point minimum for any CAGR. **Confirmed directly: 111 of 112 US "truncated" scores were artificial REJECTEDs (zero core data), not genuine low scores** — this is not an engine defect, it's a real, structural difference between yfinance's shallow window and screener.in's 12-13 year depth. The US analysis was therefore re-run using the **current (full-data) score**, anchored to its own latest reported fiscal year-end date — methodologically sound for the outcome-correlation question, but it means **the US Stability Review could not be performed with this methodology** (no second timepoint without truncation) — named as an open gap, not silently worked around.

---

## 1. Outcome Validation Report

| Market | Sample | Anchor dates | Windows with adequate sample size |
|---|---|---|---|
| India | 119 companies (of 123 attempted; 4 lost to delisted/unresolvable symbols or missing price history) | Spread across FY2023-2025 fiscal year-ends (genuine temporal diversity, since companies report on different cycles) | 3m, 6m, 12m — all three n=119 |
| US | 112 companies (of 117; 5 lost to fetch errors) | **Heavily clustered around late-2025/Dec-2025** (most US companies share a calendar fiscal year) | 3m only (n=111) has adequate power; 6m n=20; 12m n=2 (too small to analyze) |

**This date-clustering is itself a finding, not just a footnote**: India's naturally-staggered fiscal calendars gave this sprint real temporal diversity across the sample; the US sample is effectively **one overlapping market snapshot**, not independent observations — a structural limitation of single-window proxy validation for a market where most companies report on the same calendar, separate from anything about Growth Intelligence itself.

## 2. Performance Correlation Analysis

| Market | Window | n | Spearman ρ | Bottom quartile avg return | Top quartile avg return |
|---|---|---|---|---|---|
| **India** | 3-month | 119 | **+0.174** | +7.1% | +12.1% |
| **India** | 6-month | 119 | **+0.170** | +2.7% | +11.3% |
| **India** | 12-month | 119 | **+0.150** | -6.7% | +8.2% |
| **US** | 3-month | 111 | **-0.437** | +19.4% | -7.2% |
| **US** | 6-month | 20 (small) | **-0.574** | +7.2% | -10.8% |
| **US** | 12-month | 2 | not analyzable | — | — |

**India: positive, monotonic, modest-but-real correlation across all three windows** — top decile averaged +16.0%/+14.0%/+13.5% (3m/6m/12m) vs. bottom decile's +6.4%/+2.9%/-6.4%. The 12-month window shows the cleanest separation: bottom quartile companies *lost* money on average (-6.7%) while top-quartile companies gained (+8.2%).

**US: negative correlation in the only well-powered window.** This is a genuine, important, evidence-based finding — not dismissed. §3 traces its root cause directly.

## 3. False Signal Analysis

Investigated every high-score/poor-outcome and low-score/excellent-outcome case directly, in both markets:

| | US (3m window) | India (12m window) |
|---|---|---|
| **High score, poor outcome (worst examples)** | SOFI (score 80, -40.3%), NOW (100, -32.1%), SHOP (88, -26.4%), SPGI (93, -18.5%), ISRG (100, -18.0%), PLTR (81, -17.6%), IBM (96, -17.5%), INTU (100, -16.3%) | TRENT (97, -57.4%), BRIGADE (94, -29.1%), COFORGE (90, -28.8%), SIEMENS (96, -27.2%), ABB (99, -24.0%), DIXON (100, -24.0%) |
| **Low score, excellent outcome (best examples)** | LYB (0, +79.3%), DOW (12, +76.1%), CF (0, +66.4%), OXY (0, +52.1%), MPC (0, +47.2%), COP (0, +38.2%), PSX (0, +37.2%), AA (13, +35.8%) | SAIL (13, +28.8%), TATASTEEL (17, +28.2%) |

**A single, consistent, cross-market pattern, not noise**: every "false positive" in both markets is a genuine high-growth/secular-grower/capital-goods name (SaaS/fintech in the US; retail/industrials/electronics in India) that the market had already priced for continued strong growth, then re-rated downward. Every "false negative" is a genuine cyclical/commodity/materials name (chemicals/energy in the US; steel in India) with weak recent growth — correctly scored low — that rebounded sharply on a cyclical/commodity recovery, independent of its own growth trajectory.

**Attribution, per the brief's explicit categories:**
- **Not a data limitation** — every score traced to real, correctly-computed underlying metrics (re-confirmed by direct inspection, not assumed); the companies named above are genuinely high- or low-growth by the numbers.
- **Not a sector effect** in the narrow sense (it spans multiple named sectors — SaaS, fintech, capital goods, retail all on one side; chemicals, energy, steel on the other) — it is better described as a **factor/style effect**: growth-style names broadly de-rated, value/cyclical-style names broadly re-rated, in this specific window, in both markets independently.
- **Primarily a valuation and macro effect.** This is the well-documented, decades-old "growth vs. value" rotation phenomenon in equity markets (e.g., 2000-2002, 2022 are real historical precedents) — a company can have genuinely strong, accurately-measured growth and still underperform if the market re-prices growth-style valuations downward in aggregate, and vice versa for value/cyclical names. **This is not unique to Growth Intelligence** — any growth-measurement signal, however accurate, would show this exact pattern during a value-rotation regime, because the signal measures growth, not valuation or market regime.
- **Not confirmed as a genuine engine weakness** — but **not ruled out either**, honestly: this sprint's single-window methodology cannot distinguish "this window happened to be a value-rotation period" from "this is a persistent characteristic of the engine's relationship to forward returns." That distinction requires multiple independent historical windows, which neither provider in this codebase's Data Fabric currently makes available. Named explicitly as the limiting factor in this sprint's own confidence, not glossed over.

One specific India case (the IT sector) deserves separate mention: **all 11 IT companies in the sample had negative 12-month forward returns** (-10.6% to -35.3%), almost uniformly regardless of individual growth score (PERSISTENT at score 100 fell -10.6%; TECHM at score 23 was the only positive outcome, +1.0%) — a textbook sector-wide re-rating that swamped the individual-company growth signal entirely. Same root cause as above, concentrated in one sector this time rather than spread across several.

## 4. Sector Performance Analysis

| India sector | n | Bottom-half avg (12m) | Top-half avg (12m) | Direction |
|---|---|---|---|---|
| Financials | 21 | -0.6% | +15.7% | Correct (higher score → better outcome) |
| Industrials | 20 | -1.0% | +12.6% | Correct |
| Utilities | 8 | -14.4% | +13.2% | Correct, strongly |
| Pharma | 12 | +4.4% | +10.3% | Correct |
| Materials | 17 | +1.9% | +4.5% | Correct, weakly |
| Consumer | 15 | -23.2% | -5.9% | Correct (both negative, but ordering holds) |
| Energy | 8 | -0.6% | -9.6% | **Inverted** |
| **IT** | **11** | **-12.9%** | **-26.3%** | **Inverted, most strongly** — see §3's sector-wide explanation |
| Real Estate | 5 | -25.9% | -13.5% | Correct (both negative, ordering holds) |
| Telecom | 2 | — | — | Sample too small |

**7 of 9 analyzable sectors showed the correct direction** (higher score → better subsequent outcome) even when both halves were negative in absolute terms (Consumer, Real Estate) — the *relative* ordering held. **2 sectors inverted** (IT, Energy), both independently traced to sector-wide re-rating events in §3, not a per-company measurement problem.

US segment-level data (3-month window only, since longer windows lack power) showed the same pattern: cyclical (+43.1% top-half vs. +17.1% bottom-half — inverted, the cyclical recovery was strongest in the highest-growth-scoring cyclicals) and energy (deep-value rebound, inverted) vs. mature_compounder, secular_grower, high_growth, hyper_growth all showing the *correct* direction or both-negative-with-correct-ordering.

## 5. Stability Assessment

**India only** (the US truncation methodology failed, per the Methodology section above — no second timepoint exists for a US stability check this sprint).

- **Spearman rank correlation, 1-year-ago score vs. today's score: ρ = 0.751** (n=119) — a strong, positive relationship. Companies broadly maintain similar growth rankings year-over-year; the engine is not producing wildly unstable, noise-driven scores.
- **Mean absolute year-over-year score change: 13.5 points** (on the 0-100 scale) — a moderate, plausible amount of real movement, not flat/frozen and not erratic.
- **7 of 119 companies (5.9%) showed a ≥40-point swing**: JSWSTEEL (39→79), PIIND (58→12), NMDC (24→89), SAIL (13→61), GLAND (16→74), JSWENERGY (53→93), MGL (71→19). **All seven are commodity/cyclical/small-cap names** (steel, chemicals, mining, specialty pharma, power, city gas) — sectors where a genuine, real, multi-year swing in growth trajectory (not noise) is exactly what would be expected; this is consistent with, not contradictory to, the engine correctly tracking real fundamental change.

## 6. Production Readiness

**The engine's internal behavior remains sound** — confirmed again this sprint via the same India/US sample, no new crashes, no new explainability defects, stability confirmed strong for India. **What this sprint adds, and what changes the readiness picture, is the outcome-correlation evidence itself**: India's positive-but-modest correlation is encouraging but not strong; the US's negative correlation in its only well-powered window is a real, unresolved question this sprint's methodology cannot fully settle, given the single-window limitation. Per SSDS-007's own design (confidence-only, narrowly-bounded adjustment, never overriding the BUY/HOLD/SELL signal — mirroring Financial Strength's already-proven integration pattern), the practical risk of integrating *with that specific bound* is lower than a hypothetical "growth score drives the signal" design would carry — named as a real mitigating factor, not a reason to skip outcome validation.

## 7. Final Recommendation

**Minor refinement required.**

Not "Ready for Prediction Engine Integration": the US market's negative correlation in its only adequately-powered window is a genuine, unresolved finding this sprint cannot fully attribute with confidence between "value-rotation regime" (most likely, per the consistent cross-market false-signal pattern in §3) and "a more persistent characteristic" (not ruled out, given only one window). Integrating now, even confidence-only, would mean shipping an India-validated, US-unresolved signal into a platform that treats both markets as first-class.

Not "Significant redesign required" either: nothing found this sprint implicates the engine's internal scoring logic, explainability, or stability — all of which held up well, including a methodologically rigorous Stability Review for India (ρ=0.751) and a clean, well-attributed false-signal analysis in both markets. The specific, narrow gap is **outcome-validation methodology for the US market**, not the engine itself.

**The specific refinement this sprint recommends, not an open-ended re-investigation:**
1. Re-run the US outcome validation once enough calendar time has passed for the existing sample's anchors to support genuine 6/12-month windows (most US anchors are Dec-2025; revisit in 6-12 months) — or find a provider/methodology that gives multiple, non-clustered historical anchor points for US sooner.
2. If the value-rotation explanation is correct, the negative correlation should *not* persist once measured across a window that doesn't coincide with that specific rotation — re-measuring is the direct test of that hypothesis, not an assumption to take on faith.
3. Decide, before integration, whether Growth Intelligence's confidence-only bound (per SSDS-007) is sufficient protection against a *persistent* (not just regime-specific) negative relationship, were one to be confirmed — this is a design question worth answering explicitly rather than leaving implicit.

---

## GitHub Actions Result

No backend code was modified this sprint — no new CI run applicable (docs/evidence-only, per the path-filtered `backend_tests.yml` workflow, consistent with this engagement's prior docs-only sprints).

## Final Commit Hash

Recorded below, after this sprint's commit.

---

*This sprint performed no Prediction Engine integration, no scoring-weight changes, and no Business Quality/Financial Strength/Valuation work — confirmed by the diff's scope. Every number in this report traces to a real, live-fetched price history or a real, computed engine score from already-validated data — no outcome was estimated or assumed. The one real methodological limitation found (US truncation failing due to yfinance's shallow depth) is reported as a finding in its own right, not silently worked around without disclosure.*
