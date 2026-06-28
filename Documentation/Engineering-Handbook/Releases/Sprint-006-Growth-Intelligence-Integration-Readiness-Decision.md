# Epic 003, Sprint #006 — Growth Intelligence Integration Readiness Decision

**Status:** Complete. **No implementation performed.** This is a decision sprint: it synthesizes Sprints #001-#005's already-gathered evidence into an integration decision — no code touched, no scoring changed, no Prediction Engine/Daily Picks/Business Quality/Financial Strength modification, per this sprint's explicit rules.

**Evidence base** (all already established, not re-gathered this sprint): [SSDS-007](../SSDS/SSDS-007-StockSense360-Growth-Intelligence-Engine.md), the [India Growth Feasibility Study](../Architecture/Growth-Intelligence-India-Data-Feasibility-Study.md), [Sprint #003's Engine v1](Sprint-003-Growth-Intelligence-Engine-v1.md), [Sprint #004's Calibration Report](Sprint-004-Growth-Intelligence-Calibration.md), and [Sprint #005's Outcome Validation](Sprint-005-Growth-Intelligence-Outcome-Validation.md).

---

## Answers to the Eight Questions

### 1. Should Growth Intelligence be integrated now, deferred, or integrated only under limited conditions?

**Integrated only under limited conditions.** Neither a clean "yes" nor a clean "no" is supported by the evidence — the two markets point in different directions, and treating them identically would either (a) needlessly withhold a validated India signal, or (b) ship a US signal that the only available outcome evidence argues against. A market-gated, narrowly-bounded integration is the evidence-consistent middle path, not a compromise made for its own sake.

### 2. Is India evidence strong enough for confidence-only integration?

**Yes.** India cleared every bar Financial Strength cleared before its own integration: zero scoring-level false positives/negatives across 123 live companies (Sprint #004), a positive and *monotonic* correlation between score and real forward returns across all three measured windows (ρ +0.150 to +0.174, n=119, Sprint #005), and a strong year-over-year rank-stability result (ρ=0.751) that Financial Strength's own integration sprint never even had to produce (no prior StockSense360 engine has had its rank-stability independently measured this rigorously). The correlation magnitude is *modest*, not strong — this argues for a *small* confidence cap (see §Confidence-Adjustment Cap below), not for withholding integration.

### 3. Is US evidence insufficient enough to keep Growth disabled for US recommendations?

**Yes — and the case is stronger than "insufficient."** This is not a data-absence problem (which would call for caution); it's a **directionally negative result** (ρ=-0.437, the only well-powered US window) that the false-signal investigation could plausibly but not conclusively attribute to a growth-to-value rotation rather than a persistent flaw. "Insufficient evidence" would already justify withholding US integration under this engagement's standing "validate before integrate" discipline; an *adverse* result is a categorically stronger reason, not a weaker one.

### 4. Should Growth be enabled for India only, globally at low weight, explainability-only, or deferred entirely?

**A hybrid, not a single uniform choice across both markets — because the evidence itself is not uniform across both markets:**
- **India:** enabled as a confidence-only numeric signal, at low weight (see cap recommendation).
- **US:** **explainability-only** — Growth Intelligence's score, category breakdown, and narrative explanation are surfaced to the user for transparency and context, but contribute **zero** numeric influence to confidence, ranking, or any filtering decision, until Sprint #005's own recommended re-measurement resolves the correlation question. This is not "deferred entirely" for US (the engine's explainable output has independent value even without a validated forward-return relationship — it tells a user *what already happened* to a company's growth, which is true and useful regardless of whether that history predicts the future) — but it is **not** the numeric integration India receives.

### 5. What risk controls are required if integration proceeds?

1. **A hard, explicit market gate** inside `_apply_growth_intelligence_adjustment` (the integration function this decision authorizes scoping, not building): `if market != "IN": return confidence unchanged` — mirroring Financial Strength's own `V1_EXCLUDED_SECTOR_BUCKETS` gate pattern exactly, not a new architectural idea.
2. **A small, named confidence-adjustment cap** (see below) — deliberately smaller than Financial Strength's own ±6, proportionate to Growth Intelligence's more modest (though real) correlation evidence.
3. **A kill switch** — a config flag or environment variable that disables Growth Intelligence's confidence contribution without a code deploy, so a live-monitoring concern post-integration can be acted on immediately, not queued behind a release cycle.
4. **Continuous outcome monitoring as a standing practice, not a one-time gate** — Sprint #005's methodology (score vs. real subsequent return) should be re-run periodically post-integration, not treated as a single pass/fail checkpoint never revisited. This is the direct, practical answer to the regime-risk question (§8).
5. **No hard-reject path from Growth Intelligence into Prediction Engine** — already guaranteed by SSDS-007's own design (confidence-only, never overrides the BUY/HOLD/SELL signal) and not something this decision needs to add; named here only to confirm it's still the binding design, unchanged.

### 6. Should Growth affect confidence only, ranking, Daily Picks filtering, explainability only, or none yet?

| Market | Confidence | Ranking | Daily Picks filtering | Explainability |
|---|---|---|---|---|
| **India** | **Yes** (small, capped) | **No** | **No** | Yes (already free, not gated) |
| **US** | **No** | **No** | **No** | **Yes** |

Ranking and Daily Picks filtering are withheld for **both** markets, not just US — even India's evidence, while positive, is a single calibration cycle's worth of validation (one outcome-validation sprint, one methodology, one set of anchor windows). Filtering is a harder, less reversible commitment than a soft confidence nudge (a filtered-out stock never reaches a user; a confidence-adjusted one still does, just with a modestly different number attached) — the asymmetry in how wrong-and-costly each failure mode would be justifies a higher evidence bar for filtering/ranking than this sprint's evidence yet clears, even for India.

### 7. What evidence threshold should be required before US Growth is enabled (for confidence)?

1. **A re-measurement using non-clustered anchor windows** — Sprint #005's own named limitation (most US anchors shared a Dec-2025 fiscal year-end, making the sample effectively one overlapping observation, not many independent ones). This is the single most important unmet bar.
2. **A non-negative correlation across at least the 3-month and 6-month windows**, at adequate sample size (n≥80, matching this engagement's own established minimums for this kind of validation).
3. **Repetition across at least two materially different time windows** — specifically to distinguish "this was a value-rotation period" (Sprint #005's working hypothesis) from "this is structural," which a single window can never settle by construction.
4. **A completed Stability Review for US** — Sprint #005 could not produce one (the truncation methodology that worked for India failed for US's shallower yfinance depth); US needs its own valid rank-stability evidence before integration, not an assumption that India's strong result (ρ=0.751) transfers.

### 8. How should valuation/macro rotation risk be handled?

**Named explicitly as a permanent characteristic of growth-factor signals, not a defect to engineer away.** Sprint #005's false-signal analysis found the same pattern in *both* markets independently (high-growth names caught in an apparent rotation; low-growth cyclicals rebounding) — this is consistent with the well-documented, decades-old growth-vs-value dynamic in equity markets generally, not something unique to this engine's measurement of growth. Two concrete, evidence-grounded handling decisions follow from that:
- **Keep Growth Intelligence's influence small** (the capped, confidence-only design) specifically *because* this risk exists — a modest, bounded nudge stays safe even if the growth/value relationship inverts again in some future window; a larger influence would not.
- **Note, but do not act on yet**: this risk is the natural argument for eventually pairing Growth Intelligence with a Valuation Intelligence engine (already on the Master Roadmap as a proposed future Epic) before Growth's signal is trusted for anything stronger than a small confidence nudge — premature to build now (no Valuation engine exists), but worth recording as the long-term mitigation path rather than leaving it implicit.

---

## Confidence-Adjustment Cap Recommendation

**±3 points** (vs. Financial Strength's own ±6) — proportionate to the evidence: Financial Strength's pre-integration validation showed a clearer, more directly-relevant signal (live-data-validated category scores against known company states); Growth Intelligence's outcome-correlation evidence, while genuinely positive for India, is *modest in magnitude* (ρ~0.15-0.17, not a strong correlation) and is the *first* time this kind of forward-outcome check has been run for any engine in this codebase. A smaller cap is the evidence-proportionate choice, not an arbitrary halving — it reflects that this is weaker, earlier-stage evidence than Financial Strength had, while still being real, positive evidence worth acting on at a small scale.

## Explainability Recommendation

**Always surface Growth Intelligence's full explanation, score, and category breakdown — in both markets — regardless of whether confidence is affected.** Sprint #004 already confirmed the explanation output is deterministic, evidence-based, and (after one found-and-fixed defect) free of misleading statements. There is no reason to withhold a well-validated explanation layer from US users merely because the *numeric* confidence-adjustment isn't yet authorized for that market — explainability and numeric influence are separable, and this decision deliberately separates them (see §6's table).

## Future US Outcome-Validation Plan

1. **Re-run Sprint #005's methodology** once enough calendar time has passed that US companies' fiscal-year-end anchors are no longer clustered around a single recent date (most current anchors are Dec-2025; revisit once a meaningfully later/staggered set of anchors exists — practically, several months to a year from this decision).
2. **Resolve the Stability Review gap** — either by finding a US data source with deeper multi-year history than yfinance's ~4-5 years (enabling the same truncation methodology India used), or by designing an alternative stability check that doesn't require truncation (e.g., comparing scores computed from two independently-fetched snapshots taken months apart, once that much time has passed).
3. **Treat a second negative result as a stronger signal than the first** — if re-measurement across a genuinely different window *also* shows a negative or unstable relationship, that meaningfully changes the regime-effect explanation's credibility and should prompt a deeper, dedicated investigation (possibly involving Valuation Intelligence once it exists) rather than a third attempt at the same methodology.
4. **This plan itself is the deliverable** — no further evidence-gathering for US is authorized by this decision sprint; the next sprint that actually re-runs it should be scoped separately, when enough time has elapsed to do so meaningfully.

---

## Final Recommendation

**A. Integrate India-only confidence signal.**

With the explicit, evidence-grounded addenda this report establishes as part of that choice, not as separate, optional extras: a small (±3) cap, a hard market gate excluding US from any numeric influence, mandatory continuous outcome monitoring rather than a one-time check, and — independently of the confidence decision — explainability surfaced for **both** markets regardless of the numeric gate, since that part of the evidence (Sprint #004's explainability review) was never in question for either market.

**This decision does not authorize any of the following, which require their own future, separately-scoped sprints**: the actual code change to `prediction_engine.py` (a future Sprint #007-or-later implementation sprint, gated on this decision); Daily Picks filtering or ranking influence in either market (withheld pending more than one calibration cycle's evidence); or US confidence integration (withheld pending the re-measurement plan above).

---

## GitHub Actions Result

No backend code was modified this sprint — no new CI run applicable (decision/evidence-synthesis only, consistent with this engagement's prior docs-only sprints' path-filtered CI behavior).

## Final Commit Hash

Recorded below, after this sprint's commit.

---

*This sprint made an integration-readiness decision from already-established evidence. No code, scoring, threshold, or consumer-integration change was made — confirmed by the diff being limited to this document.*
