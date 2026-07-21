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
- **Status:** DECIDED
- **Decision owner:** Raviraj Shetty
- **Decision date:** 2026-07-18
- **Question:** Must Daily Picks portfolio weights always sum to 100% invested?
- **Options considered:**
  1. Fully invested, with dynamic cap relaxation when too few names qualify.
  2. Preserve hard per-name caps and represent remaining weight as cash/unallocated.
  3. Suppress weights entirely when too few names qualify.
- **Decision:** Option 2. Hard per-position caps must not be relaxed merely because too few stocks qualify. Any allocation that cannot be assigned without exceeding the cap must remain as explicit cash/unallocated.
- **Rationale:** A weak or narrow opportunity set should produce cash rather than false concentration.
- **Consequences:** `DP-020`'s optimizer fix implements this directly — position weights are capped at `max_weight` unconditionally; any shortfall versus full investment is surfaced as cash/unallocated rather than silently redistributed back onto the capped names. `DP-021`/`DP-022`/`DP-024` remain governed by this same contract for their own future implementation but are not implemented by this decision.
- **Implementation block:** None — `DP-020` may now proceed under this decision.
- **Reconsideration trigger:** Validated portfolio-level evidence and a future Portfolio Copilot suitability framework.

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
- **2026-07-21 cross-reference:** `DP-026` investigated; no point-in-time fundamentals source exists among StockSense360's currently integrated data providers or internally retained data, for either market (a repository-scoped finding — no market-wide vendor survey was performed, and this must not be read as "no such data exists anywhere"). Disclosure-contained under this decision (see `DAILY-PICKS-IMPLEMENTATION-REGISTER.md`'s `DP-026` evidence) — this did not require a new DPD, since this decision's "truthful scope and limitations" language already covers adding an honest limitations disclosure. **This is containment, not remediation: the underlying non-point-in-time `fund_score` calculation is unchanged, and DP-026 remains open.** The disclosure was initially API-only (`GET /api/validation/results`); **as of `DPD-010` (2026-07-21, product-owner authorized), it is now also UI-visible** across every identified user-facing consumer — see `DPD-010` for the full decision record. Two new related findings proposed the same session, `DP-031` (fund/sentiment factor-IC instrumentation gap, now also covering the fund_score neutral-vs-unavailable ambiguity) and `DP-032` (production weight-rationale/evidence gap), are **not** covered by this decision and each need their own future DPD before implementation — they concern measurement/weight-setting, not disclosure.

---

## DPD-010 — User-facing surfacing of the DP-026 fundamentals look-ahead limitation

- **Related findings:** `DP-026` (primary), `DP-025`/`DPD-009` (surrounding disclosure posture — extended, not superseded)
- **Status:** DECIDED
- **Date:** 2026-07-21
- **Decision owner:** Product owner (repository owner, `shashiraj75`), via explicit direct authorization in a chat prompt during this session — recorded here per this register's rule that a product-owner decision must be captured before the code change it authorizes.
- **Question:** `DPD-009` authorizes disclosure of validation methodology limitations in general, and DP-026's containment (prior commits `27c4972`/`99c424d`/`122a94c`) added a `data_limitations` field to the API response. That field reaches `GET /api/validation/results` only — it does not reach the "Historical Accuracy" UI, and the existing amber caveat text there does not mention fundamentals reuse/look-ahead bias. Should this specific limitation be made user-visible, beyond the existing API-only disclosure?
- **Options considered:**
  1. Leave disclosure API-only, unchanged (status quo pending further review).
  2. Add a narrowly scoped, non-tooltip-only warning wherever users can view or interpret DP-026-affected historical results.
  3. Withhold the affected UI panels entirely (rejected — disproportionate; DP-026 is a real but bounded methodology limitation, not a correctness defect in live Daily Picks, and `DPD-009` already establishes that directional evidence with disclosed limitations may remain visible).
- **Decision:** Option 2. A narrowly scoped warning is added wherever users can view or interpret historical validation results affected by DP-026 — the "Historical accuracy" card (`picks/page.tsx`), the `BacktestPanel` component (prepared for when `INTEGRITY_HOLD_ACTIVE` lifts, hold itself untouched), the standalone `/validation` page, the nav-bar accuracy badge, and the per-stock accuracy chip on stock pages. Required wording (verbatim facts preserved, phrasing lightly adapted for UI space): *"Historical validation limitation: Fundamentals were not reconstructed as they were known on each past date. A current fundamental snapshot was reused across historical signals, so these results are not a fully point-in-time replay."* plus *"Use these results as directional evidence, not as a precise reconstruction of past Daily Picks."*
- **Rationale:** `DPD-009`'s "truthful scope and limitations" language already permits this; this decision exists to record that the product owner has now explicitly resolved the specific open question DPD-009 left unaddressed (which limitations, and where) rather than leaving it as an unresolved gap, and to fix the exact required wording and its guardrails (must not imply live Daily Picks are defective, must not claim all fundamental retrievals failed, must not claim historical results are entirely invalid, must not claim no external provider could ever supply point-in-time data, must not claim the methodology was repaired, must not promise accuracy/returns/profitability).
- **Consequences:** The warning must remain visible for legacy persisted validation results that predate the `data_limitations` API field (enforced by rendering it unconditionally, never gated on that field's presence — verified by automated tests). `INTEGRITY_HOLD_ACTIVE` is not bypassed or weakened by this decision. This is disclosure, not remediation — DP-026 itself remains open; see its own status.
- **Implementation:** `frontend/src/components/DataLimitationsNotice.tsx` (new — `DataLimitationsNotice` full banner, `DataLimitationsMark` compact non-tooltip-only marker) wired into `picks/page.tsx` (`HistoricalTrackRecordSummary`, `BacktestPanel`), `validation/page.tsx`, `NavLinks.tsx`, `stock/[symbol]/page.tsx`. Commit `257cc26`.
- **Reconsideration trigger:** Same as `DPD-009` — a production-equivalent point-in-time validation harness with costs and bias controls would allow this warning (and DP-026 itself) to be retired, not merely reworded.
- **2026-07-21 deployment confirmation:** Merged (PR #10, merge commit `191f1518`), deployed (Railway + Vercel both `success`), and confirmed live in production — the warning renders correctly on real legacy India results across short/medium/long horizons, the integrity hold remains intact, and contrast measures 9.15:1 (exceeds WCAG AAA). See `DAILY-PICKS-IMPLEMENTATION-REGISTER.md`'s DP-026 production-verification addendum for full evidence.

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

`DPD-005` (explicit cash policy) was decided 2026-07-18 — see above. `DP-009`, `DP-010`, and `DP-017` (unambiguous, trade-off-free correctness defects under Decision rule 4) and `DP-020` (implemented under the newly decided `DPD-005`) have since been implemented; see `DAILY-PICKS-IMPLEMENTATION-REGISTER.md` for current status. Until `DPD-001`–`DPD-004` are decided, no further code change should touch the horizon, confidence, reward/risk, or Top-6 cap-tier findings they govern.
