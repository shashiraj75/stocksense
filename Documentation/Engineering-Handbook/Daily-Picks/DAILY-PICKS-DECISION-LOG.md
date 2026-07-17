# StockSense360 Daily Picks Decision Log

> Permanent record of product and methodology decisions that must not be silently reversed by later implementation sessions.
>
> **Created:** 2026-07-18  
> **Status:** Initial decisions pending owner approval unless explicitly marked decided.

## Decision rules

1. A code comment, Claude response, ChatGPT response, or existing implementation is not automatically an approved product decision.
2. Every decision below must record the chosen option, rationale, consequences, and reconsideration trigger.
3. Implementations must cite both the relevant `DP-###` finding and any `DPD-###` decision.
4. Absence of a decision means preserve current production behaviour unless the change is an unambiguous correctness defect with no product trade-off, such as preserving a genuine numeric zero.
5. Production learning remains contained unless a separate, explicit graduation decision is approved after validated outcome evidence.

---

## DPD-001 — Horizon architecture and naming

- **Related findings:** `DP-001`, `DP-002`, `DP-003`, `DP-004`, `DP-016`
- **Status:** DECISION PENDING
- **Question:** What horizons should StockSense360 explicitly support?
- **Options:**
  1. Keep Short 1–5 days, Medium 2–4 weeks, Long 3–6 months and reformulate all targets/stops/resolvers to those periods.
  2. Rename 3–6 months to Positional and add a separate Long-Term 1–3 years horizon.
  3. Relabel current medium/long products to the longer periods implied by the existing formulas.
- **Recommended direction:** Option 2. Preserve Short, rename Medium to Swing if desired, rename 3–6 months to Positional, and design a separate 1–3 year investment engine.
- **Implementation block:** Do not change target formulas or resolver windows until this decision is approved.
- **Reconsideration trigger:** Product research or validated outcome evidence supporting a different horizon structure.

## DPD-002 — Meaning and naming of confidence

- **Related finding:** `DP-006`
- **Status:** DECISION PENDING
- **Question:** What should the current percentage communicate?
- **Options:**
  1. Rename current live field to **Signal Strength** and expose Data Confidence and Historical Reliability separately.
  2. Keep “confidence” but add an explicit statement that it is not a probability.
  3. Replace it with an out-of-sample calibrated success probability after full-pipeline validation exists.
- **Recommended direction:** Use Option 1 immediately as the honest product contract; pursue Option 3 as a future calibrated field rather than overloading the current score transformation.
- **Implementation block:** Do not swap the diagnostic `confidence_score` into production gates or target scaling without a separate design and validation phase.
- **Reconsideration trigger:** A production-equivalent calibration dataset with stable reliability curves by market, horizon, regime, and confidence band.

## DPD-003 — Minimum reward/risk rule

- **Related findings:** `DP-007`, `DP-008`
- **Status:** DECISION PENDING
- **Question:** Is 1.5:1 a hard eligibility gate or a target objective?
- **Options:**
  1. Require `R:R >= 1.5` for every Daily Pick.
  2. Require `R:R >= 1.0`, call 1.5 an objective, and display the actual ratio prominently.
  3. Use horizon-/setup-specific minimums validated from favourable/adverse excursion data.
- **Recommended direction:** Short-term safety default: Option 1 until Option 3 is validated. Medium/positional may later use empirically calibrated setup-specific rules.
- **Implementation block:** Correct any “guaranteed 1.5” wording before or with the gate change.
- **Reconsideration trigger:** Full-pipeline MFE/MAE validation demonstrating a better threshold.

## DPD-004 — Top-6 ranking versus cap-tier diversification

- **Related finding:** `DP-014`
- **Status:** DECISION PENDING
- **Question:** Should “Top 6” mean highest six alphas or a cap-diversified portfolio slate?
- **Options:**
  1. Pure global ranking.
  2. Hard 2/2/2 cap-tier quota.
  3. Soft diversification penalty with a maximum allowed alpha sacrifice.
  4. Publish two outputs: Best Opportunities and Diversified Basket.
- **Recommended direction:** Option 4 provides the clearest product truth. If only one list is retained, prefer Option 3 with a disclosed diversification constraint.
- **Implementation block:** Do not tune quotas until the owner approves the intended product meaning.
- **Reconsideration trigger:** User testing or outcome evidence comparing pure versus constrained lists.

## DPD-005 — Portfolio cash and maximum-position contract

- **Related findings:** `DP-020`, `DP-021`, `DP-022`, `DP-024`
- **Status:** DECISION PENDING
- **Question:** Must Daily Picks portfolio weights always sum to 100% invested?
- **Options:**
  1. Fully invested, with dynamic cap relaxation when too few names qualify.
  2. Preserve hard per-name caps and represent remaining weight as cash/unallocated.
  3. Suppress weights entirely when too few names qualify.
- **Recommended direction:** Option 2. A weak opportunity set should naturally produce cash rather than false concentration.
- **Implementation block:** `DP-020` fix must not silently relax the stated cap without recording this decision.
- **Reconsideration trigger:** A broader Portfolio Copilot mandate with portfolio-level suitability and user risk profiles.

## DPD-006 — Liquidity and event-risk policy

- **Related findings:** `DP-011`, `DP-013`, `DP-029`
- **Status:** DECISION PENDING
- **Question:** Should untradeable or imminent-event stocks be excluded, warned, or published in a separate event-driven category?
- **Options:**
  1. Hard exclude below tradability thresholds and within defined event windows.
  2. Publish with strong warnings and reduced position sizing.
  3. Separate ordinary Daily Picks from Event-Driven Picks.
- **Recommended direction:** Hard liquidity exclusion plus a separate event-driven category. Known earnings/event risk should not be blended silently into ordinary technical picks.
- **Implementation block:** Thresholds require market-specific provider fields and validation; do not guess them from market cap alone.
- **Reconsideration trigger:** Reliable market microstructure and earnings-calendar provider coverage.

## DPD-007 — Regime model retraining policy

- **Related findings:** `DP-017`, `DP-018`, `DP-019`
- **Status:** DECIDED — SAFETY CONSTRAINT
- **Decision:** No retrained regime model may influence production unless semantic cluster labels are deterministically anchored, input features are range-validated, and multiplier/no-multiplier ablation evidence is available.
- **Rationale:** KMeans integer IDs have no stable semantic meaning after retraining.
- **Consequences:** Retraining may continue in shadow mode; production may use the existing contained deterministic behaviour until the safety contract is met.
- **Reconsideration trigger:** Passing automated permutation tests, staged semantic verification, and production-equivalent ablation results.

## DPD-008 — Production-learning containment

- **Related finding:** `DP-030`
- **Status:** DECIDED — HOLD
- **Decision:** Keep IC/meta-model production learning disabled. Shadow calculations and observability may continue, but they must not determine published ranking.
- **Rationale:** Canonical, market-correct, point-in-time, outcome-resolved, production-equivalent validation is not yet sufficient.
- **Graduation prerequisites:**
  - repaired and reconciled outcome lifecycle;
  - clean market-separated dataset;
  - point-in-time feature integrity;
  - full-pipeline walk-forward validation;
  - stable out-of-sample improvement after costs;
  - documented rollback and kill switch;
  - explicit owner approval.
- **Reconsideration trigger:** Every prerequisite above is evidenced in the implementation register.

## DPD-009 — Validation claims and user-facing historical accuracy

- **Related findings:** `DP-025`, `DP-026`, `DP-027`, `DP-028`, `DP-029`
- **Status:** DECIDED — DISCLOSURE HOLD
- **Decision:** Existing walk-forward results may be displayed only with truthful scope and limitations. They must not be described as full live-pipeline accuracy or used alone to graduate production learning.
- **Rationale:** The current validation is useful directional evidence but differs from the production pipeline and contains methodology limitations.
- **Consequences:** Historical track-record UI can remain, with warnings; integrity-gated panels remain held until their separate price-basis condition is resolved.
- **Reconsideration trigger:** A production-equivalent point-in-time validation harness with costs and bias controls.

---

## Decision template

```text
## DPD-### — Title

- Related findings:
- Status: DECISION PENDING / DECIDED / SUPERSEDED
- Date:
- Decision owner:
- Question:
- Options considered:
- Decision:
- Rationale:
- Consequences:
- Implementation block or authorization:
- Rollback/reconsideration trigger:
```

## Immediate owner decisions requested

1. Approve or revise `DPD-001` horizon architecture.
2. Approve or revise `DPD-002` confidence naming.
3. Approve or revise `DPD-003` reward/risk gate.
4. Approve or revise `DPD-004` meaning of Top 6.
5. Approve or revise `DPD-005` explicit cash policy.

Until those decisions are made, the next safe code change remains the unambiguous zero-preservation fix `DP-009` + `DP-010`.
