# StockSense360 Daily Picks Implementation Register

> Canonical source of truth for Daily Picks strategy defects, product decisions, implementation status, deployment evidence, production verification, and outcome validation.
>
> **Last reconciled:** 2026-07-18  
> **Repository state reviewed:** `40b0a42d53de81f8cfbad1b926996e23f4e5030c` and current Daily Picks implementation files  
> **Production learning:** Contained/disabled unless a later entry explicitly proves otherwise.  
> **Register baseline:** Claude's five-agent read-only audit at HEAD `40b0a42` was reconciled into the permanent `DP-###` inventory.

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
| DEPLOYED | Deployed, but live behaviour not yet proved. |
| SHADOW ONLY | Runs diagnostically and does not influence published picks. |
| VALIDATION PENDING | Implemented/deployed, but required production or outcome validation is incomplete. |
| PRODUCTION VERIFIED | Live production behaviour and evidence confirmed. |
| MONITORING | Production-verified, but effectiveness needs continuing outcome evidence. |
| DEFERRED | Intentionally postponed. |
| REJECTED | Explicit decision not to implement. |
| SUPERSEDED | Replaced by another controlled item. |

## Current dashboard

| Category | Count |
|---|---:|
| Total controlled findings | 30 |
| VERIFIED — remediation pending | 27 |
| MONITORING / existing work acknowledged | 1 |
| DECISION REQUIRED before implementation | 6 |
| Documentation-only baseline created | 1 |
| Production learning active | 0 |

**Immediate recommended implementation order:** `DP-009` + `DP-010` → `DP-020` → horizon decision `DPD-001` covering `DP-001`–`DP-004` → `DP-005` → `DP-017` → validation harness `DP-025`/`DP-026`.

## Evidence and status rules

- A finding remains `VERIFIED` until an implementation commit is inspected.
- A commit alone changes status only to `MERGED`.
- Railway/Vercel success changes status only to `DEPLOYED`.
- Live API/database/UI evidence is required for `PRODUCTION VERIFIED`.
- Backtest/statistical claims require methodology evidence and may remain `MONITORING`.
- Product choices marked **Decision required** must reference `DAILY-PICKS-DECISION-LOG.md` before code changes.

## Controlled findings

| ID | Priority | Horizon | Finding | Status | Decision required | Current evidence / reconciliation | Next safe action |
|---|---|---|---|---|---|---|---|
| DP-001 | P0 | Medium | The 2–4 week target blends 70% of a materially longer-horizon analyst consensus target. | VERIFIED | Yes — DPD-001 | Confirmed in `_estimate_target()` medium branch. | Decide horizon architecture, then design horizon-matched target methodology. |
| DP-002 | P0 | Medium | Medium stop/trailing-stop assumptions are described as approximately three-month swings despite a 2–4 week label. | VERIFIED | Yes — DPD-001 | Confirmed in `_trade_levels()` comments and parameters. | Resolve with DP-001 under one horizon contract. |
| DP-003 | P0 | Positional/Long | Current 3–6 month target extrapolates analyst targets or compounds fundamentals over roughly multi-year periods. | VERIFIED | Yes — DPD-001 | Confirmed in `_estimate_target()` long branch. | Decide whether to reformulate 3–6 month targets or introduce a genuine 1–3 year horizon. |
| DP-004 | P0 | All | Outcome-resolution windows do not match the economic horizon implied by medium/long target formulas. | VERIFIED | Yes — DPD-001 | Resolver uses approximately 3/30/90-day windows. | Align target, label, stop, trailing stop and outcome contract as one design. |
| DP-005 | P0 | All | Target and trade levels are computed from initial confidence before later confidence adjustments alter the displayed value. | VERIFIED | Limited — DPD-003 | Confirmed ordering in `PredictionEngine.predict()`. | Choose freeze-first or recompute-after-adjustments contract; implement with behavioural tests. |
| DP-006 | P0 | All | User-facing confidence is largely a linear score rescale, not a calibrated success probability; SELL/HOLD lack a useful observed gradient. | VERIFIED | Yes — DPD-002 | Recent commits added historical-track-record visibility but did not calibrate or rename the active confidence field. | Decide naming/calibration contract; retain track-record disclosure. |
| DP-007 | P0 | All | 1.5:1 R:R is attempted but not guaranteed because volatility floors can prevent it. | VERIFIED | Yes — DPD-003 | Confirmed in `_trade_levels()` and comments. | Decide enforce-vs-disclose policy. |
| DP-008 | P1 | All | Daily Picks only rejects explicit R:R red flags below 1.0; candidates from 1.0 to 1.49 remain publishable. | VERIFIED | Yes — DPD-003 | Confirmed in `_apply_risk_reward_adjustment()` and final quality gate. | Align actual gate and UI wording with approved policy. |
| DP-009 | P0 | All | `qf.get("score") or 50` turns a genuine quality score of zero into neutral 50 for ranking. | VERIFIED | No | Confirmed live ranking-path defect; raw value is separately preserved only for shadow observations. | First implementation: explicit `None` handling plus zero-preservation tests. |
| DP-010 | P0 | All | `_zscore_and_rank()` uses `raw_val or 50`, converting genuine zero technical/fundamental values to 50 after the `None` check. | VERIFIED | No | Newly confirmed by Claude audit. | Fix in same focused zero-preservation commit as DP-009. |
| DP-011 | P1 | Short/Medium | No hard traded-value, volume, spread, circuit, price or market-impact gate exists before publication. | VERIFIED | Yes — DPD-004 | Existing liquidity-distress gate concerns financial health, not market tradability. | Define market-specific tradability thresholds and data-source availability. |
| DP-012 | P1 | US | US eligibility is heuristic name filtering rather than a verified common-equity master; some non-equities can pass. | VERIFIED | Yes — DPD-004 | Code documents QQQ/GLD-style residual pass-through risk. | Select authoritative instrument-security master or provider contract. |
| DP-013 | P1 | Short/Medium | No earnings-date, board-meeting, results or material-event proximity gate exists. | VERIFIED | Yes — DPD-004 | Corporate-actions scoring is not an event-timing exclusion. | Define exclude, warn or event-driven policy and provider. |
| DP-014 | P1 | Medium/Positional | Mandatory 2/2/2 cap-tier selection can replace a stronger global-alpha candidate with a weaker tier candidate. | VERIFIED | Yes — DPD-005 | Current behaviour is intentional design, not an implementation defect. | Decide pure-alpha, soft-diversification or mandatory-quota policy. |
| DP-015 | P1 | US | Issuer deduplication covers only a small static mapping and lacks general dual-class/ADR/underlying resolution. | VERIFIED | Yes — DPD-004 | Current mapping handles only four issuer groups. | Use verified issuer identity/CIK/security master. |
| DP-016 | P1 | All | No sector, industry, theme or factor-concentration control exists in final selection/portfolio construction. | VERIFIED | Yes — DPD-005 | Position cap alone does not manage common-factor concentration. | Define selection and portfolio exposure caps. |
| DP-017 | P0 | All | Retrained KMeans cluster IDs can permute while semantic regime labels remain pinned to fixed numeric IDs. | VERIFIED | No | Confirmed in `retrain_on_history()` versus fixed `REGIME_LABELS`. | Add centroid-to-semantic-anchor matching and permutation regression test. |
| DP-018 | P2 | All | Four of five regime features are not clipped/winsorised, allowing out-of-distribution extreme values. | VERIFIED | No | VIX is capped; market changes, DXY, yields and Nifty feature are not. | Add bounded transformation after DP-017 correctness safeguard. |
| DP-019 | P1 | All | Regime factor multipliers are hand-set and lack direct production-pipeline validation against a no-multiplier baseline. | VERIFIED | No | Existing backtests do not validate this exact ranking path. | Include multiplier A/B in pipeline replay harness. |
| DP-020 | P0 | Portfolio | A two-pick portfolio cannot satisfy full-investment plus 40% caps; normalisation can output 50/50. | VERIFIED | Yes — DPD-006 | Confirmed in optimiser constraints/fallback. | Approve explicit cash allocation and test N=1..6 feasibility. |
| DP-021 | P2 | Portfolio | Optimiser's one-pick branch returns 100%, although current Daily Picks call site overrides singles to 50%. | VERIFIED | Yes — DPD-006 | Latent defect if the call path changes. | Resolve together with cash-aware optimiser contract. |
| DP-022 | P1 | Portfolio | Dimensionless ranking alpha and covariance risk are in incompatible/inconsistently scaled units. | VERIFIED | Yes — DPD-006 | Real covariance and fallback covariance differ materially in scale. | Define expected-return units or calibrate objective scaling. |
| DP-023 | P1 | Portfolio | Covariance lookback is fixed at six months for every horizon; the function's `days` argument is unused. | VERIFIED | Yes — DPD-006 | Newly confirmed by Claude audit. | Define horizon-specific estimation windows. |
| DP-024 | P1 | Portfolio | Optimiser has no explicit cash, sector, liquidity-capacity, volatility-target or tail-risk control. | VERIFIED | Yes — DPD-006 | Equality constraint forces full investment when N>1. | Design minimum viable cash-aware portfolio contract first. |
| DP-025 | P0 | Validation | Current validation scripts do not replay the complete live composite→confidence→ranking→selection→optimisation pipeline. | VERIFIED | No | Historical-track-record UI commit correctly discloses simplified walk-forward evidence but does not close this gap. | Build additive production-pipeline replay harness before using results for model graduation. |
| DP-026 | P0 | Validation | Historical fundamentals in existing backtests are not point-in-time and can introduce look-ahead bias. | VERIFIED | No | Current-day fundamentals are reused across historical windows. | Source/version point-in-time fundamentals or prominently quarantine affected metrics. |
| DP-027 | P1 | Validation | Static current universes produce survivorship bias and omit delisted/failed constituents. | VERIFIED | No | Confirmed in fixed Nifty/mid-cap/US basket constants. | Add point-in-time membership datasets. |
| DP-028 | P1 | Validation | Overlapping medium/long outcome windows reduce effective independent sample size. | VERIFIED | No | Existing step/window combinations overlap substantially. | Use non-overlapping samples or dependence-aware inference. |
| DP-029 | P2 | Validation | Existing backtests omit transaction costs, spread and slippage. | VERIFIED | No | No cost model found in the validation paths. | Add conservative market-specific cost assumptions after tradability gates. |
| DP-030 | P2 | Learning | Graduation observability exists, but `alpha_observations` is not yet a sufficient outcome-linked production-learning readiness gate. | MONITORING | No | Commit `203250c` is correctly read-only; production learning remains contained. | Continue outcome/provenance work; never infer graduation from coverage metrics alone. |

## Existing work reconciled — not to be reimplemented

| Work | Commit | Register interpretation |
|---|---|---|
| BUY confidence range rescaling | `f068156b2393bcd7ce927b38888bb9ea2284d790` | Implemented before this register; does **not** close DP-006 because the value remains a score rescale rather than a calibrated probability. |
| SELL rescaling and HOLD investigation | `8dc61340f5067bbc26e548488a1ed4a3f06a4912` | Investigation/rescaling acknowledged; does not close DP-006. |
| Target floors scaled by confidence | `970e0d64c524d069261b5e3edccdb02e0f9be68c` | Closes the old flat-floor issue only; does not close DP-001/003/005. |
| Historical track record added to API | `4922553fb21512b767c8feaff0853b01563c67ec` | Adds truthful evidence disclosure; does not convert confidence into calibrated probability. |
| Historical track record displayed in UI | `40b0a42d53de81f8cfbad1b926996e23f4e5030c` | Production UI evidence exists; DP-006 remains open. |
| Learning graduation observability | `203250ca7237420e01da2a37ba6115f81bd49989` | Read-only observability; DP-030 remains monitoring and learning remains contained. |
| Financial Strength liquidity-distress Top-6 exclusion | `d082d526da5b3019cdba065d95c543c0cc25ada0` | Existing investor-safety gate acknowledged; not a tradability/liquidity gate and therefore does not close DP-011. |

## Implementation evidence template

Use this section under the relevant item after each approved implementation:

```markdown
### DP-### implementation evidence
- Approved decision: DPD-### or Not required
- Status:
- Files changed:
- Tests and exact result:
- Commit SHA:
- Deployment platform/status:
- Production verification evidence:
- Outcome-validation evidence:
- Remaining limitations:
- Rollback trigger:
- Next safe action:
- Last verified date:
```

## Session protocol

At the beginning of every Daily Picks engineering session:

1. Read this register and the decision log.
2. Reconcile the requested task with current `main` and recent commits.
3. State the exact `DP-###` item and current status.
4. Do not combine unrelated P0/P1 findings in one implementation commit.
5. End with the register update and exact next safe action.
