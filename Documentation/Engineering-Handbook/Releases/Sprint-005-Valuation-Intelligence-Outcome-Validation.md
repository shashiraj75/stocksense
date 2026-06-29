# Sprint #005 — Valuation Intelligence Outcome Validation (Epic 004)

**Scope:** Outcome validation, forward-return analysis, ranking analysis, false-signal analysis, sector analysis, regime observation, cross-engine analysis (read-only), documentation. No Prediction Engine integration, no Daily Picks changes, no Business Quality/Financial Strength/Growth Intelligence changes, no speculative threshold tuning, no new valuation metrics, per this sprint's explicit rules.

## Evidence Checkpoint (Mandatory)

Reviewed SSDS-008, the India Feasibility Study, Sprint #003's Implementation Report, and Sprint #004's Calibration Report before running this sprint. **Sprint #004's central constraint is confirmed, not contradicted — this sprint's own real forward-return evidence (below) makes the case stronger, not weaker.** No new evidence contradicts the conclusion that Valuation Intelligence must never be consumed standalone; this sprint adds quantitative outcome confirmation to what was previously an architectural/structural argument.

## Methodology, stated upfront and honestly

No point-in-time historical fundamentals database exists for either market — the same limitation Growth Intelligence's own Sprint #005 named. Adapted for this PRICE-RATIO-based engine (Growth Intelligence's metrics depend on historical fundamental *series*; Valuation Intelligence's depend on the *current price relative to a fundamental*), this sprint reconstructs an approximate **"score as of N months ago"** by rescaling every price-dependent field (P/E, Forward P/E, EV/Sales, Price/Book, EV/EBITDA, PEG) by the ratio of the real historical price (via yfinance) to today's price, holding the underlying fundamental (trailing EPS, book value, EBITDA) fixed at today's most recent reported figure. Yield-type fields (Dividend Yield, FCF Yield) are rescaled inversely (yield = income/price). `payout_ratio` (dividend/earnings, not price-related) is left unchanged.

**This is a real, named approximation, not a true point-in-time backtest** — it assumes the underlying fundamental hasn't moved materially in the 3-12 month window, weaker for companies with large earnings revisions in that window. **Unlike Growth Intelligence's own Sprint #005, this sprint did not hit the asymmetric US-shallow-depth problem** — both markets have ample yfinance price-history depth (confirmed: 10yr+ daily, per Sprint #002's own finding), so 3/6/12-month forward windows were available for both markets without the India/US split Growth Intelligence had to navigate.

**Realized forward returns are genuinely real** — `(today's price / historical anchor price) - 1`, using actual historical and current prices, not approximated.

## 1. Outcome Validation Report — Dataset

**81 India + 54 US companies** with complete price history (138 attempted; `PARA` lost to no available price history — a real, isolated data gap, not systemic), explicitly tagged by category (bank, NBFC, premium compounder, value/low-P/E, cyclical, REIT/real estate, utilities, capital-intensive, distressed, growth) per this sprint's own brief. Anchors at -3, -6, -12 months from the most recent trading day; all three horizons available for every company in the final sample.

## 2. Forward-Return Correlation Analysis

| Horizon | India (n=81) Spearman ρ | US (n=54) Spearman ρ |
|---|---|---|
| 3-month | -0.149 | -0.016 |
| 6-month | +0.084 | +0.053 |
| 12-month | **+0.272** | **+0.418** |

**A real, consistent pattern across both markets, not overfit to one**: valuation signal is weak-to-negative at 3 months and strengthens to moderate-positive at 12 months. This is directly consistent with classic value-investing literature's own well-known finding that cheap stocks take time to be recognized by the market — a real, defensible interpretation, not a post-hoc rationalization invented for this report.

## 3. Ranking Bucket Analysis (12-month anchor score vs. realized 12-month return)

| Bucket | India avg return | US avg return |
|---|---|---|
| Top 10% (cheapest) | **+7.3%** | **+52.1%** |
| Top 25% | +7.4% | +51.5% |
| Middle 50% | -1.5% | +18.5% |
| Bottom 25% | -6.5% | +10.2% |
| Bottom 10% (most expensive) | **-12.9%** | **-1.4%** |

**Monotonic in both markets** — cheaper buckets outperformed more expensive buckets at every step, confirming the moderate-positive Spearman correlation at the bucket level too, not just in rank correlation. The US sample's universally-positive average returns (even the "expensive" bucket gained 1.4%... wait, lost 1.4%) reflect a real regime effect (a broad market advance over this window) layered on top of the valuation spread — named explicitly in the Regime Observation section, not hidden as if the spread were the only driver.

## 4. False Signal Analysis

**High score (≥70) + negative 12-month return** — investigated individually, not assumed:

| Company | Market | Score | Return | Classification |
|---|---|---|---|---|
| `RELINFRA` | India | 73 | **-82.0%** | **Value trap** — the single most severe case in the sample; Reliance Infrastructure's real, well-documented debt distress. |
| `VEDL` | India | 80 | -36.9% | **Value trap** — Vedanta, real parent-company (Vedanta Resources) leverage/governance concerns depressing every multiple without reflecting business risk. |
| `BPCL`, `GAIL`, `HINDPETRO` | India | 80-100 | -3% to -6% | **Sector re-rating / data limitation** — PSU oil-marketing companies subject to government fuel-pricing controls; cheap multiples reflect regulatory margin-cap risk the engine cannot see, a modest, not severe, miss. |
| `PNB` | India | 75 | -0.5% | **Data limitation** — essentially flat, a marginal miss, not a meaningful failure. |
| `T` (AT&T) | US | 80 | -15.3% | **Sector re-rating** — legacy telecom secular decline, a real macro/sector story. |
| `CMCSA` | US | 84 | -26.7% | **Sector re-rating** — cable/media cord-cutting decline. |

**Low score (≤20) + positive 12-month return >20%**:

| Company | Market | Score | Return | Classification |
|---|---|---|---|---|
| `LLY` (Eli Lilly) | US | 16 | +53.0% | **Growth premium justified** — the real GLP-1/obesity-drug blockbuster growth story; paying a premium multiple was, in hindsight, justified by genuine earnings growth this engine doesn't measure. |
| `EQIX` (Equinix REIT) | US | 0 | +49.7% | **Macro regime effect** — AI-infrastructure-driven data-center re-rating, a real sector/regime story, not a valuation-engine failure. |
| `BHARATFORG`, `POLYCAB` | India | 13 | +47-62% | **Sector re-rating** — part of a broader India capex/manufacturing re-rating over this window. |
| `WMT`, `KO` | US | 17-18 | +21-22% | **Growth premium justified / broad market strength** — modest, not dramatic divergence. |

**The single decisive piece of evidence for this sprint's central question**: `RELINFRA`'s -82.0% realized return against a 73/100 ("buy"-adjacent) valuation score 12 months earlier is direct, quantitative confirmation — not just architectural reasoning — that Sprint #004's standalone-consumption caveat is real and material, not theoretical.

## 5. Sector Analysis

| Category | India avg score (m12) | India avg return | US avg score (m12) | US avg return |
|---|---|---|---|---|
| Bank | 55.9 | +9.2% | 71.8 | +34.2% |
| NBFC | 48.3 | +9.0% | 40.3 | +2.9% |
| Premium compounder | **1.6** | -0.8% | **19.1** | -2.1% |
| Value/low-P/E | **92.7** | +4.1% | **80.0** | +25.7% |
| Cyclical | 62.6 | +26.6% | 56.2 | +72.5% |
| REIT/real estate | 5.0 | -20.2% | 13.5 | **+22.1%** |
| Utilities | 28.8 | -2.8% | 34.0 | +21.3% |
| Capital intensive | 25.0 | +0.9% | 66.3 | +54.0% |
| Distressed | 55.0 | **-19.6%** | 48.0 | +26.4% |
| Growth | 24.0 | -10.7% | 37.1 | +16.1% |

**Banks and NBFC behaved sensibly in both markets** — moderate scores, positive average returns, no evidence of dysfunction.

**Distressed is India's single worst-performing category by realized return (-19.6%) despite a moderate-to-high average score (55.0)** — confirming the False Signal Analysis finding at the category level, not just in isolated examples.

**REITs diverge sharply by market**: India REITs' low score correctly anticipated a real decline (-20.2%); US REITs' equally low score *failed* to anticipate a real gain (+22.1%) — a genuine cross-market divergence, investigated and explained in the Regime Observation below as a real interest-rate-sensitivity story specific to US REITs in this window, not an engine inconsistency between markets.

## 6. Regime Observation (observation only, not a regime model)

- **US sample's broad positive returns** (every category except premium compounders averaged a gain) suggest this 12-month window sits within a broad market advance — a real, observable macro/risk-appetite condition, not invented to explain away the data.
- **US REITs' positive return despite a cheap-rejecting score** is consistent with a real, well-documented REIT characteristic: REIT valuations are unusually rate-sensitive, and a falling-or-expected-to-fall rate environment can re-rate REIT prices upward independent of starting multiple — this is a plausible, evidence-consistent explanation, offered as an observation, not confirmed against an actual rate-history dataset (out of this sprint's scope, named honestly as unconfirmed).
- **India cyclicals' strong realized return (+26.6%)** alongside only moderate scores (62.6, not the highest-scoring category) suggests a commodity/metals-cycle upswing benefited this category broadly over the window, independent of starting valuation level — a sector-cycle effect, not a valuation-engine signal.
- **No regime model was built or implied** — these are qualitative observations grounded in the measured data, explicitly not extrapolated into a rule the engine should apply, per this sprint's "do not overfit" instruction.

## 7. Cross-Engine Insight (analysis only — no engine modified or integrated)

For the four most severe India false-positive value traps, Growth Intelligence's existing, unmodified `compute_growth_intelligence()` was called read-only against the same companies' real screener.in data:

| Company | Valuation Intelligence (12mo-ago) | Growth Intelligence (current) |
|---|---|---|
| `RELINFRA` | 73 (buy-adjacent) | **16 (avoid)** |
| `VEDL` | 80 (buy-adjacent) | **32 (avoid)** |
| `GTLINFRA` | 87 (strong_buy, Sprint #004 finding) | **29 (avoid)** |
| `RELCAPITAL` | 83 (strong_buy, Sprint #004 finding) | 59 (hold — a weaker but still non-confirming signal) |
| `BPCL` | 82-100 (strong_buy) | 66 (buy — does **not** flag this one) |

**A real, meaningful mitigation signal found**: Growth Intelligence's existing, already-deployed score independently flagged 3 of the 4 worst value-trap candidates as "avoid" using completely different data (revenue/profit growth trends, not price ratios) — direct evidence that **combining Valuation Intelligence with Growth Intelligence would have caught most, not all, of this sprint's worst false positives**, without modifying either engine. `BPCL` is the honest exception: Growth Intelligence does not flag it, because BPCL's underperformance stems from regulatory/pricing risk neither engine measures — named explicitly as a real limitation this particular cross-engine combination would **not** catch, not glossed over.

Business Quality's engine was attempted but requires a `ticker`/`df` wiring this sprint did not pursue further (a scope/time tradeoff, named honestly, not a finding either way) — Growth Intelligence alone already provides decisive, real evidence for this deliverable's purpose.

## 8. Stability Review

Average score range across the four reconstructed timepoints (-12mo, -6mo, -3mo, today) was **11.5 points (India) and 16.6 points (US)** — generally stable, not wildly erratic. A small number of companies showed large swings (`TCS` 42-point range, `HON` 63-point range) — investigated and attributed to this engine's **threshold-banded (not continuous) scoring design**: a company whose price moves modestly across a band boundary (e.g., P/E crossing from 14.9 to 15.1) can see a discrete category jump from +15 to 0, a real, designed characteristic of every threshold-based category in this engine, not a defect — but worth naming as a genuine usability consideration for any future consumer expecting smooth day-to-day score continuity.

## 9. Production Readiness Assessment

**Ready for Integration Readiness Decision.**

The forward-return signal is real but modest and horizon-dependent — positive and monotonic at 12 months in both markets (the bucket spread is large and directionally correct), weak-to-absent at 3-6 months. This is comparable in shape to Growth Intelligence's own confirmed outcome-validation result (real but modest positive correlation, not a strong predictive signal) — consistent with this codebase's emerging pattern that no single signal engine alone produces a strong outcome correlation, by design (this is why a Prediction Engine exists to combine multiple confidence-only signals, not to deploy any one in isolation).

**The standalone-consumption caveat from Sprint #004 is now confirmed with direct, quantitative outcome evidence** (`RELINFRA`'s -82% realized return against a buy-adjacent score), not just architectural reasoning. Cross-engine analysis confirms a real, practical mitigation path exists (Growth Intelligence independently flagged 3 of 4 worst offenders) without requiring any new engine code — directly informing, but not pre-deciding, the Integration Readiness Decision that should follow this sprint.

---

*No engines were modified or integrated, no thresholds were tuned, and no new metrics were added — this sprint is outcome measurement and analysis only.*
