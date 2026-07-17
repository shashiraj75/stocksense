# StockSense360 Daily Picks Implementation Register

> Canonical source of truth for Daily Picks strategy defects, product decisions, implementation status, deployment evidence, production verification, and outcome validation.
>
> **Last reconciled:** 2026-07-18  
> **Repository state reviewed:** `40b0a42d53de81f8cfbad1b926996e23f4e5030c` and current Daily Picks implementation files  
> **Production learning:** Contained/disabled unless a later entry explicitly proves otherwise.

## Governance rule

No Daily Picks item may be called complete from a chat summary, test result, commit, merge, or deployment alone. The terminal implementation state is **PRODUCTION VERIFIED**. Statistical or effectiveness work may remain **MONITORING** after production verification until sufficient real outcomes accumulate.

Every future Daily Picks implementation must:

1. cite one or more permanent `DP-###` IDs;
2. update this register in the same implementation commit where practical;
3. record exact files, tests, commit SHA, deployment evidence, production evidence, and remaining work;
4. preserve a truthful distinction between production-active, shadow-only, validation-only, and documentation-only work;
5. never enable `LEARNING_ALPHA_PRODUCTION_ENABLED` as an incidental part of another item.

## Controlled status values

| Status | Meaning |
|---|---|
| IDENTIFIED | Reported but not independently verified against current code. |
| VERIFIED | Confirmed against current code. |
| APPROVED | Remediation or product direction approved by the owner. |
| IN IMPLEMENTATION | Code changes are actively being prepared. |
| IMPLEMENTED — LOCAL | Code changed locally but not merged. |
| MERGED | Present on the default branch. |
| DEPLOYED | Deployment completed but live behaviour not yet verified. |
| SHADOW ONLY | Running diagnostically and not influencing production output. |
| VALIDATION PENDING | Implemented/deployed, but required production or outcome validation remains. |
| PRODUCTION VERIFIED | Live production behaviour has been verified against the approved contract. |
| MONITORING | Production verified, but statistical effectiveness still requires accumulated outcomes. |
| DEFERRED | Intentionally postponed. |
| REJECTED | Deliberately not being implemented. |
| SUPERSEDED | Replaced by another registered solution. |

## Current dashboard

| Measure | Count |
|---|---:|
| Total registered findings | 30 |
| P0 | 12 |
| P1 | 13 |
| P2 / informational | 5 |
| Production verified | 0 |
| Monitoring | 1 |
| Verified, remediation pending | 26 |
| Verified, decision pending | 3 |

### Current P0 implementation queue

1. `DP-009` and `DP-010` — preserve genuine zero factor scores.
2. `DP-020` — make the position cap mathematically valid for one/two-pick days and represent cash explicitly.
3. `DP-001`–`DP-005` — reconcile horizon labels, target methodology, resolution windows, and final-confidence ordering as one governed design workstream, but implement one concern per commit.
4. `DP-017` — anchor semantic regime labels after KMeans retraining.
5. `DP-025` and `DP-026` — build a live-pipeline replay harness and remove point-in-time fundamentals leakage before treating current validation as proof of production accuracy.

### Important current-state notes

- The latest audit was read-only. No item below was fixed by that audit.
- Recent commits added confidence-range changes and historical track-record observability, but did **not** calibrate the live confidence percentage as a probability or replay the full production pipeline.
- `alpha_observations` and learned IC/meta-model outputs remain shadow/diagnostic while containment is active.
- Previously exposed credentials are remediated and are outside this register unless new evidence appears.

## Evidence standard

Each item should eventually carry:

- implementation commit;
- files changed;
- focused test command and result;
- full regression suite result;
- deployment identifier/status;
- live endpoint or database evidence;
- outcome-validation evidence where applicable;
- rollback condition;
- next safe action.

---

## Registered findings

### DP-001 — Medium target uses long-horizon analyst consensus

- **Severity:** P0
- **Horizon:** Medium (2–4 weeks)
- **Status:** VERIFIED
- **Current behaviour:** The medium target can blend approximately 70% analyst consensus target with 30% current price, although analyst targets generally represent a materially longer horizon.
- **Risk:** The displayed 2–4 week upside can represent a much longer thesis and distort reward/risk and outcome interpretation.
- **Primary code:** `backend/services/prediction_engine.py::_estimate_target`
- **Dependencies:** `DP-004`, `DP-005`, `DP-025`
- **Approved behaviour:** Not yet decided. Choose a truly 2–4 week target methodology or relabel the product horizon honestly.
- **Next safe action:** Produce a design decision comparing reformulation versus relabeling; do not change formula and label in one uncontrolled commit.

### DP-002 — Medium stop/trailing logic is horizon-inconsistent

- **Severity:** P0
- **Horizon:** Medium
- **Status:** VERIFIED
- **Current behaviour:** Medium stop logic is documented as covering “3-month swings,” despite a 2–4 week product label.
- **Risk:** Stop distance, trailing stop, target, and evaluation window do not share one horizon contract.
- **Primary code:** `backend/services/prediction_engine.py::_trade_levels`
- **Dependencies:** `DP-001`, `DP-004`
- **Next safe action:** Reconcile target, volatility window, stop, trailing stop, and resolver window in one written horizon contract before implementation.

### DP-003 — Positional target uses multi-year valuation logic

- **Severity:** P0
- **Horizon:** Current “long” (3–6 months)
- **Status:** VERIFIED
- **Current behaviour:** The target can extrapolate an analyst target by another year or compound earnings for three years while the product describes a 3–6 month holding period.
- **Risk:** Users and outcome resolvers judge a multi-year valuation against a short positional window.
- **Primary code:** `backend/services/prediction_engine.py::_estimate_target`
- **Dependencies:** `DP-004`, `DP-016`, `DP-025`
- **Approved behaviour:** Decision pending between a 3–6 month positional formula and relabeling/adding a genuine 1–3 year horizon.
- **Next safe action:** Resolve `DPD-001` in the decision log.

### DP-004 — Outcome-resolution windows do not match target horizons

- **Severity:** P0
- **Horizon:** Medium and current “long”; review short contract too
- **Status:** VERIFIED
- **Current behaviour:** Outcome resolution uses approximately 3/30/90-day windows, while medium and long target formulas imply longer horizons.
- **Risk:** Track record can classify a target as failed before its own valuation horizon expires.
- **Primary code:** `backend/services/alpha_engine/outcome_logger.py::HORIZON_CONFIG`
- **Dependencies:** `DP-001`, `DP-002`, `DP-003`, `DP-016`
- **Next safe action:** Change resolver windows only after the product horizon/target contract is approved.

### DP-005 — Final confidence and target/trade levels can desynchronise

- **Severity:** P0
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** Target and trade levels are calculated from initial confidence; risk/reward, pledge, Financial Strength, Growth Intelligence, and Valuation Intelligence may then change displayed confidence without recomputing the levels.
- **Risk:** Displayed conviction and target aggressiveness can describe different internal states.
- **Primary code:** `backend/services/prediction_engine.py::predict`
- **Dependencies:** `DP-006`, `DP-007`, `DP-008`
- **Next safe action:** Decide whether final confidence is frozen before target calculation or target/levels are recomputed after all adjustments; add a regression test proving one shared value.

### DP-006 — Confidence percentage is not a calibrated probability

- **Severity:** P0
- **Horizon:** All
- **Status:** MONITORING
- **Current behaviour:** Confidence is a linear transformation of composite score. BUY and SELL ranges were recently rescaled; code comments acknowledge SELL and HOLD lack a useful hit-rate gradient. Historical track-record data is now exposed separately in the API/UI.
- **Implemented evidence:** `f068156b2393bcd7ce927b38888bb9ea2284d790`, `8dc61340f5067bbc26e548488a1ed4a3f06a4912`, `4922553fb21512b767c8feaff0853b01563c67ec`, `40b0a42d53de81f8cfbad1b926996e23f4e5030c`.
- **What those commits do not prove:** They do not turn the live percentage into a calibrated success probability and do not validate the full live pipeline.
- **Risk:** “AI confidence” may be interpreted as an empirical probability.
- **Approved behaviour:** Pending decision: rename to Signal Strength, introduce separate Data Confidence and Estimated Success Probability, or calibrate with production-equivalent out-of-sample outcomes.
- **Dependencies:** `DP-025`, `DP-026`, `DP-027`, `DP-028`, `DP-029`
- **Next safe action:** Resolve naming/product contract before changing which confidence field drives gates or target scaling.

### DP-007 — 1.5:1 reward/risk is attempted, not guaranteed

- **Severity:** P0
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** Stop tightening attempts to reach 1.5:1 but respects a volatility noise floor and can return a lower ratio.
- **Risk:** Any copy implying a guarantee is inaccurate.
- **Primary code:** `backend/services/prediction_engine.py::_trade_levels`
- **Dependencies:** `DP-008`
- **Next safe action:** Audit UI/API wording and decide whether 1.5 becomes a hard eligibility rule or a stated objective.

### DP-008 — Daily Picks can publish 1.0–1.49 reward/risk

- **Severity:** P1
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** A red flag is created only below 1.0, and Daily Picks filters the red-flag indicator. Ratios from 1.0 through 1.49 remain eligible.
- **Risk:** Product promise and selection gate can differ.
- **Primary code:** `prediction_engine.py::_apply_risk_reward_adjustment`; `daily_picks.py::_passes_quality_gate`
- **Dependencies:** `DP-007`
- **Next safe action:** Record the approved minimum in the decision log before modifying the gate.

### DP-009 — Genuine quality score zero becomes neutral 50

- **Severity:** P0
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** `qf.get("score") or 50` converts both missing quality and a genuine zero into 50 on the ranking path.
- **Risk:** A worst-quality observation can be ranked as average.
- **Primary code:** `backend/services/daily_picks.py::_predict_stock`
- **Next safe action:** First implementation commit: replace truthiness fallback with an explicit `is None` check and add zero-preservation tests.
- **Rollback:** Simple code revert; no schema dependency.

### DP-010 — Genuine zero factor score becomes 50 during z-scoring

- **Severity:** P0
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** After a prior `None` guard, `float(raw_val or 50)` still converts a genuine zero technical/fundamental/quality value into 50.
- **Risk:** The stock can both depress the universe mean with zero and then be ranked using 50, benefiting twice from the defect.
- **Primary code:** `backend/services/daily_picks.py::_zscore_and_rank`
- **Next safe action:** Implement with `DP-009` in the same focused zero-preservation commit; do not combine with other scoring redesign.

### DP-011 — No hard trading-liquidity or execution gate

- **Severity:** P1
- **Horizon:** All; highest impact on short
- **Status:** VERIFIED
- **Current behaviour:** No mandatory average traded value, spread, price floor, market-impact, delivery-volume, or circuit-history eligibility gate exists on the final selection path.
- **Risk:** A solvent but practically untradeable micro/small-cap can be published.
- **Primary code:** `backend/services/daily_picks.py` selection path; absence confirmed
- **Dependencies:** Data-provider/market microstructure design
- **Next safe action:** Define market-specific tradability contracts before selecting thresholds.

### DP-012 — US common-equity eligibility is heuristic

- **Severity:** P1
- **Horizon:** All US
- **Status:** VERIFIED
- **Current behaviour:** Static name/symbol keyword exclusions do not constitute a verified common-equity master; the code itself documents residual ETF/trust/commodity pass-through risk.
- **Risk:** Non-common-equity instruments can enter an equity recommendation list.
- **Primary code:** `backend/services/daily_picks.py::_build_us_daily_picks_heuristic_filtered`
- **Next safe action:** Select a verified instrument master/issuer identifier source and retain the heuristic only as a fallback with degraded-state disclosure.

### DP-013 — No earnings or material-event proximity gate

- **Severity:** P1
- **Horizon:** Short and medium primarily
- **Status:** VERIFIED
- **Current behaviour:** Corporate-action scoring exists, but no pre-publication proximity exclusion/warning for earnings, board meetings, results, regulatory decisions, or other gap-risk events is enforced.
- **Risk:** A technically valid setup can be overwhelmed by a known event.
- **Dependencies:** Reliable event-calendar provider
- **Next safe action:** Define exclude/warn/event-driven modes before implementation.

### DP-014 — Mandatory 2/2/2 cap-tier selection can override alpha rank

- **Severity:** P1 — product decision, not an implementation bug
- **Horizon:** Medium and current “long”
- **Status:** VERIFIED
- **Current behaviour:** Two large-, two mid-, and two small-cap names are selected when available, even when a non-selected tier candidate has higher alpha.
- **Risk:** “Top 6” can be interpreted as the highest six ranking alphas when it is actually a diversification-constrained list.
- **Primary code:** `backend/services/daily_picks.py::_select_with_tier_quota`
- **Decision dependency:** `DPD-004`
- **Next safe action:** Decide between pure-alpha, soft quota, hard quota, or two separately labelled lists.

### DP-015 — US issuer deduplication is incomplete

- **Severity:** P1
- **Horizon:** All US
- **Status:** VERIFIED
- **Current behaviour:** Static mapping covers only a small set of share-class pairs and does not provide general ADR/ordinary-share or issuer resolution.
- **Risk:** Duplicate economic exposure can occupy multiple pick slots.
- **Primary code:** `backend/services/daily_picks.py::_US_ISSUER_GROUP`, `_deduplicate_by_issuer`
- **Dependency:** `DP-012`
- **Next safe action:** Use canonical issuer/CIK or equivalent identifiers from the selected security master.

### DP-016 — No separate genuine long-term horizon

- **Severity:** P1 — product architecture decision
- **Horizon:** Current “long” and proposed 1–3 years
- **Status:** VERIFIED
- **Current behaviour:** Product exposes 3–6 months as long-term while target math partly operates over multi-year assumptions.
- **Risk:** Positional trading and investment theses are conflated.
- **Decision dependency:** `DPD-001`
- **Next safe action:** Decide whether to rename 3–6 months to Positional and add a separate 1–3 year engine.

### DP-017 — KMeans cluster IDs can detach from semantic regime labels

- **Severity:** P0
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** Bootstrap centroids are semantically ordered, but retraining can arbitrarily permute KMeans IDs while fixed labels remain mapped to IDs 0–3.
- **Risk:** A panic cluster could be interpreted as bull/calm and invert production factor multipliers.
- **Primary code:** `backend/services/alpha_engine/regime_cluster.py::retrain_on_history`
- **Next safe action:** Add deterministic centroid-to-semantic-anchor matching before saving any retrained model; validate against intentionally permuted test clusters.

### DP-018 — Four regime features are not clipped/winsorised

- **Severity:** P2
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** VIX is capped; S&P change, DXY, US 10Y, and Nifty change can leave the training range on extreme days.
- **Risk:** Cluster assignment becomes least stable when regime detection matters most.
- **Primary code:** `backend/services/alpha_engine/regime_cluster.py::extract_features`
- **Dependency:** `DP-017`
- **Next safe action:** Validate clipping ranges in shadow mode after semantic label safety is fixed.

### DP-019 — Regime multipliers lack production-equivalent validation

- **Severity:** P1
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** Multipliers are hand-set and affect production prior-weight ranking while no full-pipeline backtest proves improvement versus no multiplier.
- **Risk:** Regime adaptation may reduce rather than improve alpha.
- **Primary code:** `backend/services/alpha_engine/regime_cluster.py::REGIME_WEIGHT_MULTIPLIERS`
- **Dependencies:** `DP-017`, `DP-025`, `DP-026`
- **Next safe action:** Add multiplier/no-multiplier ablation to the production-equivalent validation harness.

### DP-020 — Position cap is infeasible/violated on one- and two-pick days

- **Severity:** P0
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** A fully invested portfolio with two positions cannot satisfy a 40% cap; normalisation can return 50/50. The optimizer’s one-name path returns 100%, while the Daily Picks caller separately hard-codes 50% for a single pick.
- **Risk:** Published portfolio weights can contradict the stated cap and omit unallocated cash.
- **Primary code:** `backend/services/alpha_engine/optimizer.py::optimize`; `backend/services/daily_picks.py` Phase 6
- **Decision dependency:** `DPD-005`
- **Next safe action:** Introduce explicit cash/unallocated weight and test `N=1..6`; do not silently renormalise capped names above the cap.

### DP-021 — Optimizer one-name branch ignores max weight

- **Severity:** P2
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** `optimize()` returns 100% for one name, though the current caller bypasses this and writes 50%.
- **Risk:** Latent contract defect if the call path changes.
- **Primary code:** `backend/services/alpha_engine/optimizer.py::optimize`
- **Dependency:** `DP-020`
- **Next safe action:** Resolve as part of the explicit-cash contract, not as an isolated cosmetic patch.

### DP-022 — Optimizer mixes incompatible alpha and covariance units

- **Severity:** P1
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** Dimensionless ranking alpha is combined with daily-return covariance; fallback covariance also uses a materially different scale.
- **Risk:** Risk penalty can be negligible or inconsistent, making optimisation largely an alpha allocation despite mean-variance framing.
- **Primary code:** `backend/services/alpha_engine/optimizer.py::neg_utility`
- **Dependencies:** `DP-020`, `DP-025`
- **Next safe action:** Convert alpha to horizon expected-return units or calibrate objective scaling using out-of-sample data.

### DP-023 — Covariance lookback is fixed at six months

- **Severity:** P1
- **Horizon:** All; strongest mismatch for short
- **Status:** VERIFIED
- **Current behaviour:** The `days` argument is unused and all horizons use six months of daily returns.
- **Risk:** Position sizing for a 1–5 day trade uses the same risk window as a positional pick.
- **Primary code:** `backend/services/daily_picks.py::_fetch_returns_matrix`
- **Dependencies:** `DP-022`
- **Next safe action:** Establish horizon-specific risk-estimation windows and test stability before changing production weights.

### DP-024 — Portfolio construction lacks cash and exposure constraints

- **Severity:** P1
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** Fully invested, long-only, per-name cap only; no sector, theme, liquidity, volatility-target, contribution-to-risk, or tail-risk constraints.
- **Risk:** Six individually valid picks can form an unsuitable concentrated portfolio.
- **Primary code:** `backend/services/alpha_engine/optimizer.py`
- **Dependencies:** `DP-011`, `DP-015`, `DP-020`, `DP-022`
- **Next safe action:** Implement explicit cash first, then sector/liquidity constraints after reliable metadata is available.

### DP-025 — Validation does not replay the full production pipeline

- **Severity:** P0
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** Existing backtests use simplified formulas and do not replay the complete production composite, adjustments, ranking, tier selection, regime multipliers, and optimiser contract.
- **Risk:** Historical hit rates cited in comments may describe a different model than the live Daily Picks pipeline.
- **Primary code:** `backend/services/validation_engine.py`; `backend/services/backtester.py`
- **Related delivered work:** Historical track-record observability exists, but remains evidence about the existing validation model rather than proof of full production behaviour.
- **Next safe action:** Add an additive, non-production pipeline-replay harness using point-in-time inputs and exact production functions or versioned equivalents.

### DP-026 — Backtests use non-point-in-time fundamentals

- **Severity:** P0
- **Horizon:** Medium and longer primarily
- **Status:** VERIFIED
- **Current behaviour:** Current fundamentals can be applied across historical dates.
- **Risk:** Look-ahead leakage can materially inflate apparent predictive performance.
- **Primary code:** `backend/services/validation_engine.py`; `backend/services/backtester.py`
- **Dependencies:** Historical fundamentals source/schema
- **Next safe action:** Do not use current validation as a production-learning graduation gate until point-in-time data or an explicit technical-only scope is established.

### DP-027 — Backtest universes are survivorship biased

- **Severity:** P1
- **Horizon:** All
- **Status:** VERIFIED
- **Current behaviour:** Static currently listed baskets omit historical members and delisted/failed companies.
- **Risk:** Historical results are biased upward.
- **Primary code:** Universe constants in `backend/services/validation_engine.py`
- **Dependencies:** Point-in-time constituent/security-master data
- **Next safe action:** Add historical membership and delisted securities to validation-only data before statistical claims.

### DP-028 — Outcome windows overlap and reduce effective sample size

- **Severity:** P1
- **Horizon:** All; strongest for medium/long
- **Status:** VERIFIED
- **Current behaviour:** Backtest windows overlap substantially, so observations are not independent.
- **Risk:** Sample sizes and significance appear stronger than effective evidence.
- **Primary code:** Horizon step/window definitions in validation code
- **Next safe action:** Use non-overlapping windows or block/bootstrap/Newey-West style inference and report effective sample size.

### DP-029 — Validation excludes transaction costs and slippage

- **Severity:** P2
- **Horizon:** All; strongest for short and illiquid names
- **Status:** VERIFIED
- **Current behaviour:** Gross returns are reported without conservative trading costs.
- **Risk:** Realisable performance is overstated.
- **Dependencies:** `DP-011`
- **Next safe action:** Add market- and liquidity-sensitive cost assumptions after tradability fields are available.

### DP-030 — Alpha graduation observability is not a readiness gate

- **Severity:** P2 — informational safeguard
- **Horizon:** All
- **Status:** SHADOW ONLY
- **Current behaviour:** Coverage/graduation observability exists, while canonical alpha observations do not yet form a complete, validated forward-return dataset for production learning.
- **Implemented evidence:** `203250ca7237420e01da2a37ba6115f81bd49989`; alpha-observation persistence repair `02920908e09c001aefacdea701e3d7117492208e`.
- **Risk:** None while containment remains active; risk would arise only if observability were misread as permission to enable learning.
- **Next safe action:** Continue outcome-lifecycle reconciliation in shadow mode. Do not enable production learning.

---

## Cross-cutting delivered work that must not be mistaken for closure

| Work | Evidence | What it accomplished | What remains |
|---|---|---|---|
| BUY confidence range rescale | `f068156b...` | Corrected score-range compression | Does not calibrate probability or full pipeline accuracy |
| SELL range rescale/HOLD investigation | `8dc61340...` | Corrected SELL range mismatch and documented lack of gradient | SELL/HOLD still not probability-calibrated |
| Historical track record API | `4922553f...` | Exposed validation summaries separately | Validation model is not full production replay |
| Historical track record UI | `40b0a42d...` | Shows benchmark/hit-rate evidence and warnings | Does not alter ranking, confidence, targets, or hold gate |
| Confidence-scaled target floors | `970e0d64...` | Reduced equal floor for weak and strong confidence | Does not fix horizon mismatch or final-confidence desync |
| Liquidity-distress Top-6 exclusion | `d082d526...` | Prevents a Financial Strength hard red flag from entering Top 6 | Does not implement trading-liquidity/execution gates |
| Market-separated learning engine | `8f6176ef...` | Separated IN/US learning and outcome routing | Learning remains contained; historical integrity still requires reconciliation |

## Mandatory close-out template for future implementation reports

```text
DP IDs:
Approved scope:
Files changed:
Tests added/changed:
Focused test result:
Full regression result:
Commit SHA:
Merge status:
Deployment status:
Production verification evidence:
Outcome-validation evidence:
Register status after this commit:
Remaining work:
Rollback condition:
Next safe action:
```

## Next safe implementation

Implement `DP-009` + `DP-010` only: preserve genuine zero quality/technical/fundamental values through `_predict_stock` and `_zscore_and_rank`, add focused regression tests, run the full backend suite, and update this register in the same commit. Do not alter thresholds, ranking weights, confidence formulas, targets, portfolio logic, model containment, or production data in that step.
