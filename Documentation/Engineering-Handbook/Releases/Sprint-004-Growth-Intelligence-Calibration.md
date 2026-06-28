# Epic 003, Sprint #004 — Growth Intelligence Engine Calibration & Production Validation

**Status:** Complete. **No Prediction Engine integration performed, per this sprint's explicit rule.** Business Quality and Financial Strength confirmed untouched. One genuine explainability defect found and fixed (a presentation-layer filter, not a scoring-weight change) — no other threshold changes made, since the expanded validation did not produce evidence requiring one.

**Methodology:** Expanded the Sprint #003 validation sample from 85+61 to **123 India + 117 US real companies** (246 total, both exceeding the 100-company minimum) — reusing Sprint #002/#003's already-fetched data where identically shaped, fetching ~38 new India and ~56 new US companies live to fill out every named segment (compounders, hyper-growth, mature, cyclicals, turnarounds, secular decliners, banks, NBFCs, IT, Pharma, FMCG, Industrials, Utilities, Energy).

---

## 1. Calibration Report

Reviewed all 7 scoring categories against the expanded 246-company sample:

| Category | Cap reviewed | Verdict |
|---|---|---|
| Revenue Growth | ±15/±18 | **Appropriate, no change.** Directional separation held across both markets at this larger sample (high-growth/hyper-growth averaged 84/84 in US, 76 in India; cyclical/declining averaged 6/15 in US, 65/30 in India). |
| Profit Growth | ±15/±18 | **Appropriate, no change.** Same evidence as Revenue Growth — the two categories' independent scoring correctly diverged for companies with growing revenue but shrinking profit (margin compression), e.g. several India specialty-chemical names. |
| EPS Trend | ±8 | **Appropriate magnitude, presentation defect found and fixed (see §2).** The ±8/±3 cap itself wasn't wrong; how marginal +3/-3 contributions were *surfaced* in `strengths`/`weaknesses` was. |
| Growth Durability | ±12 | **Appropriate, no change.** Correctly flagged high-CV (>1.0) erratic trends for distressed/declining names (COALINDIA CV 1.26, SRF CV 1.16, DRREDDY CV 0.92) — all independently confirmed real via direct metric inspection, not coincidental. |
| Operating Profit Growth | ±12 | **Appropriate, no change.** Correctly absent (not fabricated) for all confirmed bank/NBFC cases across the larger sample. |
| Reinvestment Efficiency | ±8 | **Cap appropriate; a real, evidence-based limitation found (see §2, Edge Cases) — not fixed this sprint, named for a future sprint.** Corporate-action distortion (bonus issues/splits) can enter this metric's invested-capital calculation; confirmed concretely for RELIANCE, not hypothetically. |
| Margin Trend | ±8 | **Appropriate, no change** — validated narrowly (spot-check only, per Sprint #003's own named limitation; this sprint did not expand that spot-check, since doing so required a fresh full-sample re-fetch out of this sprint's scope). |

**No category cap was changed.** The one fix made (§2) is a presentation-layer filter on which categories qualify as a headline "strength"/"weakness," not a change to any category's score contribution, per this sprint's explicit "do not change weights unless evidence requires it" rule — and the evidence here specifically pointed at *display*, not *scoring*.

## 2. False Positive Analysis

**Genuine false-positive-shaped finding:** a marginal +3 "EPS Trend: mixed_positive" contribution was qualifying as the *headline* (and in two cases, *only*) listed "strength" for companies the engine itself scored 6/100 ("avoid") — **COALINDIA and SRF**, confirmed directly from real validation output, not constructed. This is not a *scoring* false positive (the overall grade was correctly "avoid" in both cases) — it's an *explainability* false positive: a misleadingly positive-sounding statement attached to an overwhelmingly negative verdict. **Fixed**: added `MIN_NOTABLE_CONTRIBUTION = 5.0` (a new threshold, evidence-driven) so only categories scoring at least ±5 qualify for `strengths`/`weaknesses` — excludes the ±3 EPS Trend "mixed" tiers specifically, leaves every other category (±8 and up) unaffected. Confirmed via direct re-run: both companies now correctly show `strengths: []`.

**No scoring-level false positive found** in the expanded sample — every "strong_buy"/"buy" grade independently spot-checked (GE, DIS, ETN, IBM, INTU, several India IT/pharma names) traced to genuinely strong, explainable underlying metrics (consistent, accelerating, or recently-turned-around growth), not an artifact of the formula.

## 3. False Negative Analysis

**No confirmed false negative found.** Every extreme-low score spot-checked (TSLA, DRREDDY, SRF, PIIND, DEEPAKNTR, the cyclical/energy clusters) traced to genuine, real, currently-weak underlying metrics — confirmed by direct inspection of the actual fetched financial data, not assumed. The one *candidate* false-negative-shaped pattern — India's "secular_grower" tag averaging *lower* (50.8) than "cyclical" (65.2) — was investigated and found to be a real, current phenomenon (several large Indian consumer-staples names are in a genuine, well-documented multi-year growth slowdown), not a defect; reported honestly in Sprint #003 and reconfirmed here at the larger sample size, not contradicted.

**One confirmed instance of a transient, provider-level (not Growth Intelligence) false negative**: GPS and DFS (both real, actively-traded US companies) returned empty `.financials` from yfinance at the moment of this sprint's live fetch, producing a `REJECTED` (0 confidence) result that doesn't reflect either company's true growth profile — confirmed via direct inspection this is an upstream yfinance data gap (`fin.shape == (0, 0)`), not an adapter defect; the engine's fail-soft behavior (reject rather than fabricate or crash) is exactly correct given the input it received.

## 4. Sector Analysis

| Finding | Evidence |
|---|---|
| **No sector bias found beyond the already-known, structural bank/NBFC gap.** | Every other sector (IT, Pharma, Consumer/FMCG, Industrials, Utilities, Energy, Real Estate, Telecom, Materials) showed full data availability and sensible score distributions in both markets at the expanded sample size — confirmed, not assumed, by the same field-presence check methodology Sprint #002 established. |
| **Cyclical bias: present, but it is the *correct* behavior, not a defect.** | US cyclicals/energy averaged 5.8/5.2 (out of 100) during this sample's underlying multi-year window, which spans a real, confirmed down-cycle for commodities/chemicals/energy (steel, chemicals, oil) — the engine is *supposed* to score a company low when its actual revenue/profit growth was negative for multiple years; this is the intended signal, not an artifact to correct. Named explicitly so a future reader doesn't mistake "cyclicals score low in this sample" for a bug. |
| **Growth traps**: high revenue growth with deteriorating/negative profit growth | Checked directly — no India or US company in the sample showed this exact shape scoring as a false "strong_buy"; the engine's independent Revenue Growth and Profit Growth categories correctly diverged whenever this pattern existed (e.g. India specialty chemicals: flat-to-negative revenue *and* negative profit, not a growth-trap shape but a genuine broad decline — confirmed by direct metadata inspection). |
| **Acquisition-driven growth** | Confirmed unchanged from SSDS-007/the Feasibility Study: no data source exists to distinguish this from organic growth; not newly discoverable from this sprint's validation evidence either, since no metric isolates M&A contribution. Named as still out of scope, not newly solved. |
| **Temporary earnings spikes** | The Growth Durability category (coefficient of variation) is specifically the mechanism that should catch this — confirmed working as intended: every company with an erratic single-year spike in the sample showed correspondingly high CV and a negative Growth Durability contribution, not inflated by the spike. |

## 5. Confidence Review

- **Missing metrics**: confirmed correctly excluded (not fabricated) across the larger sample — every bank/NBFC in both markets landed at a reduced confidence (42.9% for the 18 confirmed India banks/NBFCs; 57.1%/71.4% tiers for various US financial-services names depending on exactly which fields resolved).
- **Sector exemptions**: working as designed — confirmed the REJECTED gate (core-field count) correctly distinguishes "structurally different population" (banks: not rejected, confidence-penalized) from "insufficient data" (X/PARA/WBA/GPS/DFS: correctly rejected, 0 confidence).
- **Partial data**: confirmed graceful at every observed completeness level (42.9% through 100%), never crashing, never silently treating a partial result as complete.
- **Conflicting signals**: spot-checked directly — companies with revenue growing but profit shrinking (margin compression) correctly show both categories contributing in opposite directions in `category_contributions`, and the `explanation` string states each category's exact, separate contribution rather than blending them into one ambiguous statement.

**One new, real finding from this review**: a previously-unconfirmed India bank (**BANDHANBNK**) returned `available: True` from the scraper but **zero** of the core growth fields (no `profit_growth_3y_pct`, no `quarterly_pat_cr`) — landing in `REJECTED` (0% confidence) rather than the other banks' graceful 42.9%. Confirmed this is a genuine root-cause-unconfirmed data gap (not an adapter defect — the engine correctly rejected given zero usable input) — named as an open item for a future, separate scraper-investigation task, not fixed speculatively in this calibration sprint.

## 6. Performance Review

| Measurement | Result |
|---|---|
| India adapter (`build_india_growth_fields`) | **0.008ms/call** (already-fetched data, pure computation) |
| US adapter (`build_us_growth_fields`) | **1.29ms/call** (warm/cached yfinance Ticker, repeated calls) — ~160x India's cost, attributable to re-running `_get_financial_row`'s DataFrame sort/lookup on every call with no intermediate caching |
| Engine (`compute_growth_intelligence`) | **0.011-0.013ms/call**, both markets — negligible |
| yfinance cold fetch (`.financials` + `.balance_sheet`) | **~1.5 seconds** — the dominant real-world cost, but this is the *existing*, already-shared fetch `prediction_engine.py`'s `_SharedTickerCache` already amortizes across Business Quality/Deep Fundamentals/Financial Strength (per Sprint #012's prior optimization) — Growth Intelligence would add **zero new network cost** if integrated to reuse that same shared ticker, not measured as a new burden |
| Memory | **~3.75 bytes/call average net growth** over 1,000 full adapter+engine calls (`tracemalloc`) — confirms no meaningful memory accumulation; transients are GC-reclaimable |
| Cache behavior | **No Growth-Intelligence-specific cache exists.** Relies entirely on the existing provider-layer caches (screener.in's 4-hour TTL, yfinance's session-level behavior) — a real, named architectural fact, not a defect, since the engine/adapter computation itself is already sub-millisecond and doesn't need its own cache. |

**No performance concern found.** The only cost worth future attention is the US adapter's repeated DataFrame-lookup overhead (1.29ms) — small in absolute terms, but worth a cheap memoization if Growth Intelligence is ever called many times per ticker in one request cycle; not blocking integration.

## 7. Explainability Review

Reviewed explanation/strengths/weaknesses/risks output across dozens of real companies spanning every score band. **One genuine defect found and fixed** (§2's `MIN_NOTABLE_CONTRIBUTION` filter and the redundant "EPS Trend: EPS trend: mixed_positive" phrasing, now reworded to "EPS Trend: Mixed positive EPS momentum"). Beyond that:
- **Deterministic**: confirmed — identical input always produces identical output (no randomness anywhere in the engine).
- **Evidence-based**: every reason string traces to a real, named number (e.g. "Revenue growing 29.0%/yr (3Y)"), never a vague qualitative claim.
- **No duplicated reasoning** found beyond the one fixed redundancy — each category's reason is distinct and non-overlapping with the others.
- **No other misleading statements found** in the reviewed sample.

## 8. Recommended Threshold Changes

**None for the 7 scoring categories' caps or strong/weak cutoffs** — the expanded 246-company validation did not surface evidence that any of them are miscalibrated in direction or magnitude. **One new threshold added** (`MIN_NOTABLE_CONTRIBUTION = 5.0`), a presentation filter, not a scoring-weight change, justified by §2's concrete finding.

**Recommended for a future sprint, not made now** (per "no speculative tuning"):
1. Corporate-action detection for the Reinvestment Efficiency category's invested-capital series — confirmed concretely necessary for RELIANCE (the 3Y lookback window directly contains its bonus-issue jump), not hypothetical.
2. Investigate the BANDHANBNK-style zero-core-field scraper gap (§5) — likely a scraper edge case, not an engine defect, but unconfirmed without a dedicated look.
3. A full-sample (not spot-check) re-validation of Margin Trend now that more time has passed since the `opm_annual_pct` scraper addition — Sprint #003's own named limitation, still open.

## 9. Production Readiness

The **engine and adapters** are production-ready as computational components: 589/589 tests passing, zero crashes across 246 live real companies in two markets, sub-millisecond computation, negligible memory footprint, and one real explainability defect found-and-fixed rather than missed. **The calibration itself is not outcome-validated** — every check in this report and Sprint #003's confirms *directional sanity* (growers score higher than decliners, banks degrade gracefully, explanations are accurate) against *known, already-public facts* about these companies' recent performance, not against *forward* market outcomes, since no backtesting infrastructure or labeled outcome dataset exists yet for this brand-new engine. This is the same honest distinction Sprint #003 already named and this sprint did not resolve, because resolving it (a true backtest) is out of this sprint's "no speculative tuning, evidence over assumption" scope without a dedicated outcome dataset — fabricating a backtest result would violate this engagement's own standing discipline more than honestly naming the gap does.

## 10. Recommendation

**Needs another calibration sprint.**

Specifically: not because anything found in this sprint disqualifies the engine — quite the opposite, 246 real companies across both markets produced zero scoring-level false positives, zero scoring-level false negatives, and only one (now-fixed) presentation defect. The reason for this recommendation is the same one Sprint #003 already named and this sprint confirms remains open: **no threshold has been validated against a forward outcome**, which is the bar both Business Quality and Financial Strength cleared before their own Prediction Engine integration. A focused next sprint — comparing Growth Intelligence's scores against subsequent realized performance for a sample of already-validated companies — is the specific, bounded next step, not an open-ended further-research mandate. The three items in §8's "future sprint" list (corporate-action handling, the BANDHANBNK gap, Margin Trend's full-sample validation) are lower-priority and can run alongside or after that calibration, not block it.

---

## GitHub Actions Result

Recorded below, after this sprint's commit is pushed and confirmed.

## Final Commit Hash

Recorded below, after this sprint's commit.

---

*This sprint did not integrate Growth Intelligence into the Prediction Engine and did not modify Business Quality or Financial Strength — confirmed by the diff's scope. One genuine explainability defect was found and fixed (a presentation filter, not a scoring-weight change); no category cap or strong/weak threshold was altered, since the expanded 246-company validation did not produce evidence requiring one.*
