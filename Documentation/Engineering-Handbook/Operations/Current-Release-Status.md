# StockSense360 — Current Release Status

**Purpose:** This document is the authoritative operational-status register for live and pending releases. It records what is deployed, what remains disabled, what is pending validation, and which future actions require explicit approval.

**Use this document for current state.** Historical sprint reports, Epic closures, SSDS documents, and audit reports remain authoritative evidence for their own completed scope, but they do not automatically describe the current production operating state.

**As of:** 2026-08-07 — maintained as a live operational register

## Major Feature Lifecycle Summary (2026-08-07 reconciliation)

Code-grounded snapshot from the post-Jul-11 documentation reconciliation
(PR #37). Full evidence and per-commit mapping:
[Documentation-Current-State-Reconciliation-Ledger-2026-08.md](Documentation-Current-State-Reconciliation-Ledger-2026-08.md).
Production *runtime* toggle state (vs. code default) is asserted only where
a specific closure doc says so; otherwise "not independently verifiable
from this repository checkout" per that ledger's own caveat.

| Subsystem | Classification |
|---|---|
| Prediction Engine (confidence, target-price floors) | LIVE BACKEND / OPERATIONAL |
| Daily Picks (India + US, incl. premarket finalizer) | LIVE USER-FACING / LIVE BACKEND OPERATIONAL |
| Multibagger (weekly-refresh architecture) | LIVE USER-FACING |
| Paper Trading | LIVE USER-FACING |
| Trade Postmortem | LIVE USER-FACING — RELEASE COMPLETE (see below) |
| Portfolio | LIVE USER-FACING |
| Watchlist | LIVE USER-FACING |
| Alerts | LIVE USER-FACING |
| Validation Engine | LIVE BACKEND / OPERATIONAL |
| Learning Alpha Engine | FEATURE-FLAGGED OFF (contained, `LEARNING_ALPHA_PRODUCTION_ENABLED`) |
| RCI | FEATURE-FLAGGED OFF (`RCI_LIVE_STOCK_ANALYSIS_ENABLED`, unchanged since baseline) |
| SEC EDGAR (live provider, `sec_edgar_adapter.py`) | LIVE BACKEND / OPERATIONAL — feeds live US confidence scoring via `us_financial_strength_adapter.py` → `prediction_engine.py` |
| SEC PIT store (DP-033 persisted point-in-time facts, `sec_pit_store.py`) | LIVE BACKEND / OPERATIONAL for Validation Engine replay only — its sole production consumer is `validation_engine.py`'s acquisition-free replay path; does NOT feed live prediction/confidence scoring (not imported by `prediction_engine.py` or `daily_picks.py`) — corrected 2026-08-07 after this doc previously conflated it with the live SEC EDGAR row above |
| NSE Instrument Master | FOUNDATION / UNINTEGRATED |
| Market Leadership | FEATURE-FLAGGED OFF / DEPLOYED DORMANT |
| Intelligence Engine / Universe Builder | SHADOW / EXPERIMENTAL (`INTELLIGENCE_ENGINE_SHADOW_ENABLED`) |
| Postgres Schema Init | LIVE BACKEND / OPERATIONAL (fail-closed) |
| Caching / Egress Containment | LIVE BACKEND / OPERATIONAL |

---

## Trade Postmortem (Wave C + Explainability)

**Status:** RELEASE COMPLETE — merged, deployed, and activated in Production.

- PR #35 (Wave C base: current-report read API, per-trade frontend,
  observability) — merged and dark-deployed.
- PR #36 (Explainability/UX overhaul: three-layer report, factor-specific
  price-path assessment, `Company Name (SYMBOL)` identity, evidence-coverage
  matrix) — merged at `5170692f27b1742406a21d67fca8a74d62490c1f`.
- Two independent flag pairs — this is not one backend-only flag paired with
  one frontend-only flag; each side has both a backend and a frontend twin
  (`backend/api/routers/paper_trading.py`,
  `frontend/src/utils/featureFlags.ts`):
  - `TRADE_POSTMORTEM_PRICE_PATH_ENABLED` (backend) /
    `NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED` (frontend) gates the
    per-trade `/postmortem/[tradeId]` route this release (PR #35/#36) is
    about — **reported enabled in Production** per the
    [Explainability Production Closure](../Releases/Trade-Postmortem-Explainability-Production-Closure.md)'s
    own validation evidence.
  - `TRADE_POSTMORTEM_DAILY_ENABLED` (backend) /
    `NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED` (frontend) gates a
    separate, older Sprint 1 daily-batch surface — **its Production runtime
    state was not independently verified during this reconciliation**; do
    not assume it matches the price-path pair's state.
- Production validation: 48-hour backend-only stability observation passed;
  natural production lifecycle verified on a real trade; frontend
  activation, authenticated Production smoke test, and the
  Company Name (SYMBOL) identity gate all passed; temporary Preview-only
  frontend flag and temporary Preview CORS authorization removed after QA;
  final 60-minute Production observation passed with zero rollback
  threshold crossed.
- Full closure evidence:
  [Trade Postmortem Explainability — Production Closure](../Releases/Trade-Postmortem-Explainability-Production-Closure.md).

**FUTURE POSTMORTEM EVIDENCE COMPLETION WORK** (not a release-stability
issue — the release above is complete and stable): the evidence *coverage*
matrix identifies factors where underlying evidence capture is still
partial. This is tracked separately in the
[Evidence Completion Roadmap](./Trade-Postmortem-Evidence-Completion-Roadmap.md)
and does not block or qualify the RELEASE COMPLETE status above.

---

## Validation Benchmark Evidence Integrity

**Status:** two PRs, both merged and automatically deployed:

- **PR [#26](https://github.com/shashiraj75/stocksense/pull/26)** (`fix/validation-benchmark-evidence-integrity`)
  — **major benchmark-evidence integrity remediation**, merge commit `0c3926c`, 2026-07-26.
- **PR [#28](https://github.com/shashiraj75/stocksense/pull/28)** (`fix/validation-benchmark-evidence-final-hardening`)
  — **follow-up hardening**: malformed-type, positive-exit, coverage, retry and observability
  closure, merge commit `2247384`, 2026-07-26.

**Automatic deployment successful** for both — confirmed via GitHub's commit-status/deployment API
(Railway `success`, Vercel `success` on both merge commits) and a direct, read-only production smoke
check after each merge: `GET /health` returns `{"status":"ok",...}` and `GET /api/validation/status`
returns the clean, expected shape. **No `/api/validation/run` was triggered** to force this evidence
for either PR — **natural production failure-path verification remains pending**; the failure path
is fully test-backed instead (PR #26: 50 tests; PR #28: 36 additional tests using deterministic
fault injection — string/mixed/malformed `Close` columns, negative/zero exit values, hand-computed
exact coverage boundaries, timezone/intraday-timestamp mismatches, non-`DatetimeIndex` input) rather
than production-observed. **No win-rate or accuracy claim** is made or implied by either PR.

**PR #26 — major remediation.** Closed a class of defect where unavailable or invalid benchmark
evidence was silently treated as genuine flat/neutral evidence: a benchmark alignment step that
could backward-fill a *future* observation into an earlier stock date (a real look-ahead
violation); a missing benchmark making the market-regime adjustment default to a genuine-looking
neutral `0.0` for every signal; a missing benchmark forward return defaulting to `0.0` and
fabricating `alpha_pct`/`actual_direction`/`correct`; `avg_return_benchmark_pct` using a truthy
check that collapsed a genuine `0.0%` to `None`; a missing-BUY-signals case fabricating a `0%`
model return instead of `None`; and a degraded-but-completed run persisting a contaminated result.
Introduced the centralized, versioned `BenchmarkEvidence` contract (`validation_benchmark_evidence_v1`)
and the run-level fail-closed gate (`BENCHMARK_EVIDENCE_UNAVAILABLE`). 70 new/updated tests. Test
evidence at merge: backend 3830/3830, frontend 454/454.

**PR #28 — follow-up hardening**, closing edge cases found on fresh adversarial review of PR #26's
own implementation (none of PR #26's protections repeated or weakened — its full test suite remains
green):
- **Total, exception-safe validator** — `_validate_benchmark_acquisition` now never raises for any
  DataFrame shape/dtype (an escaping exception previously risked stranding the active job slot,
  since this function runs deliberately outside the main try/except). New `non_numeric`,
  `invalid_index_type`, `validation_error` states; deterministic numeric coercion with disclosed
  invalid counts; original frame never mutated.
- **Positive entry AND exit** — the prior condition only checked `entry > 0`; a zero/negative
  `exit` could silently reach the forward-return division. Fixed at all three layers (acquisition,
  aggregate return, per-signal alignment), which now provably share one coerced series.
- **Coverage-based acquisition adequacy** — a single valid forward-return window among hundreds of
  invalid rows is no longer accepted; requires >= 95% window coverage
  (`BENCHMARK_MIN_ACQUISITION_WINDOW_COVERAGE_PCT`), with real, disclosed coverage counts. New
  `insufficient_window_coverage` status.
- **Post-alignment, whole-run signal coverage gate** — new `signal_windows_considered`/
  `benchmark_valid_signal_windows`/`benchmark_signal_coverage_pct`, gated at >= 95%
  (`BENCHMARK_MIN_SIGNAL_COVERAGE_PCT`) after the stock backtests run but before
  `_compute_metrics()`/persistence — zero benchmark-valid signals, or below-threshold coverage,
  fails closed (`BENCHMARK_ALIGNMENT_COVERAGE_INSUFFICIENT`) and persists nothing; the latest
  previously completed valid result remains unchanged.
- **Retry extended** to non-exception acquisition failures (empty/malformed frame returned without
  raising) for plausibly-transient states — still capped at 2 attempts, same ticker, no new
  provider; structural states are explicitly not retried.
- **Failed-job evidence** — a failed job's status snapshot now carries the full, safe
  `BenchmarkEvidence` contract (never raw provider/exception text).
- **Timezone/index robustness** — `_align_benchmark_close` normalizes tz-aware/naive and intraday
  timestamps safely (never via a UTC-shifting conversion that could move a date across midnight);
  a non-`DatetimeIndex` fails safe rather than raising.

**Healthy-path parity preserved** for both PRs — every pre-existing signal-computation test's
expected numeric values are unchanged; dedicated India/US parity tests confirm both markets
complete unaffected. No scoring, benchmark methodology, alpha/hit-rate calculation, universe/
horizon definition, or scheduler behavior changed. No production data rewritten at any point.
36 new tests plus 5 existing test-file corrections in PR #28. Final combined test evidence:
backend 3866/3866, frontend 454/454, clean typecheck, clean production build.

## Validation Public Diagnostic Sanitization

**Status:** PR [#24](https://github.com/shashiraj75/stocksense/pull/24)
(`fix/validation-public-error-sanitization`) **MERGED** to `main` (merge commit `d83488f`,
2026-07-25) and **automatically deployed** — confirmed via GitHub's commit-status/deployment API
(Railway `success`, Vercel `success` on the merge commit) and a direct, read-only production smoke
check: `GET /health` returns `{"status":"ok",...}` and `GET /api/validation/status` returns the
clean, expected shape with no error content. No `/api/validation/run` was triggered to force this
evidence — **failure-path production evidence remains naturally pending**, consistent with this
release's own operational safety boundaries; the failure path is test-backed (20 new regression
tests using hostile fixture exception text) rather than production-observed.

Closes the residual risk named in the Market Leadership entry below: four write sites in
`validation_engine.py` previously embedded a live exception object directly into a field reachable
from a public Validation API response — `benchmark_unavailable_reason` (persisted + returned by
`/api/validation/results`), `job.failure_message` and the per-symbol/run-level progress log (both
returned by `/api/validation/status`). All four now return a stable failure code / fixed message
from a centralized `VALIDATION_PUBLIC_FAILURE_MESSAGES` contract; the real exception is always
logged server-side (`log.exception`/`log.warning`, with symbol/market/universe/horizon context
where applicable) and never crosses into a public field. Older `val_runs.summary` rows persisted
before this fix (which may already contain raw exception text) are sanitized on read by a pure,
deterministic helper — **the stored row itself is never rewritten or backfilled**. No scoring,
benchmark methodology, alpha/hit-rate calculation, universe/horizon definition, or scheduler
behavior changed; `benchmark_avg_fwd_return_pct`/`nifty_avg_fwd_return_pct` remain `None` (never a
fabricated `0.0`) when unavailable, unchanged from the pre-existing behavior. 20 new regression
tests plus 2 existing tests corrected (they previously asserted the *old, unsafe* behavior). Final
test evidence: backend 3780/3780, frontend 454/454, clean typecheck.

## Validation Job-Identity Fix

**Status:** PR [#21](https://github.com/shashiraj75/stocksense/pull/21)
(`fix/validation-job-universe-identity`) **MERGED** to `main` (merge commit `37bfe39`, 2026-07-25) and
**automatically deployed** — confirmed via GitHub's commit-status API (Railway and Vercel both
`success` on the merge commit) and a direct, natural, unmanufactured observation: the production
`/api/validation/status` endpoint transitioned from its pre-merge shape (no `job` field) through a
502 during the Railway restart to its fresh post-merge default (`{"running": false, "progress": 0,
"total": 0, "started_at": null, "log": []}`), proving the new code is live. **Natural behavioural
verification of the cross-universe fix itself is PENDING** — no scheduled or manually-triggered
validation run has occurred since deploy, so the original defect (a US run rendering under the Nifty
100 tab) has not yet been observed corrected under real, naturally-occurring traffic. No validation
run was manually triggered to force this evidence, consistent with this repository's operational
safety boundaries.

Fixed via a fresh independent pre-merge review (not just the original implementation): jobs are now
bound to an immutable identity (market/universe/horizon/timestamps) at claim time; a caller-supplied
mismatched job identity now fails closed instead of silently misattributing a run; a benchmark fetch
failure is now explicitly disclosed (`benchmark_data_available`/`benchmark_unavailable_reason`)
rather than silently reported as a fabricated 0.0% return; `data_cutoff` is honestly `None` (with
`data_cutoff_basis: "not_captured"`) rather than presenting a claim/run-start timestamp as a verified
market-data cutoff. 29 regression tests. Final test evidence: backend 3582/3582, frontend 420/420,
clean typecheck, clean production build.

## Market Leadership and Trend Context Layer

**Status:** PR [#22](https://github.com/shashiraj75/stocksense/pull/22)
(`feat/shadow-market-leadership-context`) **MERGED** to `main` (merge commit `67a1f13`, 2026-07-25)
and **automatically deployed DORMANT** — confirmed via GitHub's commit-status API (Railway and
Vercel both `success`) and a direct, natural observation of the live production endpoint:
`GET /api/leadership/context?symbol=AAPL&market=US` returns exactly `{"status":"disabled"}`, proven
by watching the endpoint transition from a pre-deploy 404 (route didn't exist yet) through a 502
during the Railway restart to this live disabled response — genuine evidence, not a manufactured
test. See
[Market Leadership and Trend Context Layer — Local Implementation and Release Evidence](../Releases/Market-Leadership-Trend-Context-Layer-Local-Implementation.md)
and its companion
[Architecture](../Architecture/Market-Leadership-Trend-Context-Layer.md).

**ALL SIX FEATURE FLAGS REMAIN OFF** — five backend capability flags
(`MARKET_LEADERSHIP_ENGINE_ENABLED`/`_SHADOW_ENABLED`/`_UI_ENABLED`/`_VALIDATION_ENABLED`/`_SCORING_ENABLED`)
plus one fail-closed public frontend presentation gate (`NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED`,
only the exact string `"1"` enables it). None was set in any Railway/Vercel environment during this
release. **UI NOT EXPOSED. SHADOW PERSISTENCE NOT ENABLED. SCORING INFLUENCE ZERO** —
`MARKET_LEADERSHIP_SCORING_ENABLED` is statically proven unconsumed by any scoring/ranking code
path. Daily Picks, Multibagger, Portfolio, Paper Trading, Alerts, Validation, Heatmap, and Screener
are unmodified by this work.

- New, isolated `backend/services/market_leadership/` package: Stock Relative Strength Rank,
  Sector/Industry Group Leadership, Trend Lifecycle classification, Market Breadth, "Why Now?"
  explanation contract, plus an additive `GET /api/leadership/context` endpoint and an experimental
  Stock Detail page component.
- Six genuine defects were found and fixed across this release's review passes — three via live
  manual verification against real yfinance data (a provider-incomplete-bar JSON crash; a misleading
  "insufficient history" explanation for a case where data was actually fine; an unbounded
  per-request recomputation cost), one cache-mutation hazard (a caller could corrupt the shared TTL
  cache via in-place mutation of its own response), one incomplete "flags-off" claim (the frontend
  issued a browser request regardless of backend flag state — fixed with the new frontend
  presentation gate), and two found via a final fresh adversarial pre-merge review using more
  extreme inputs than the original test suite: a group cap-weight redistribution algorithm that
  could silently violate its own 20% ceiling under extreme concentration, and a NaN composite value
  that could be sorted as if it were a genuine top-percentile relative-strength extreme. All six are
  fixed, tested, and — where practical — sanity-checked by deliberately reverting the fix, confirming
  the regression test fails, and restoring.
- Final test evidence (this repository's own venv, real exit codes checked): backend **3762/3762**
  passed, frontend **454/454** passed, clean typecheck, clean production build (built with the
  frontend gate absent, matching real deployment state).
- **Quantitative shadow validation (Section 15) status: VALIDATION PENDING.** The walk-forward
  harness is built, functional, and smoke-tested against real data (225 real observations, correctly
  self-suppressed below its own 300-observation floor), but **no statistically adequate evidence
  base exists yet** — a genuine evidence base (India + US separately, multiple horizons, multiple
  regimes, walk-forward splits, bootstrap confidence intervals) requires a data-collection period
  well beyond what any single implementation session can produce. The 300-observation floor is a
  presentation minimum, not proof of statistical adequacy — no accuracy, win-rate, or profitability
  claim is made or implied anywhere in this release.
- **Residual risk closed**: the raw-exception-string disclosure flagged during PR #22's review
  (outside both PRs' merged scope at the time) was fixed and merged separately as PR #24 — see the
  "Validation Public Diagnostic Sanitization" entry above. Public Validation responses no longer
  carry any raw exception text; full diagnostic detail remains in server-side logs only.
- **Next gates required before this layer can affect a user or a score**: separate, explicit
  approval for (1) production shadow enablement (`MARKET_LEADERSHIP_SHADOW_ENABLED`), (2)
  user-visible UI enablement (both `MARKET_LEADERSHIP_UI_ENABLED` and
  `NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED`), and (3) any recommendation-scoring influence
  (`MARKET_LEADERSHIP_SCORING_ENABLED`) — none of which is requested or implied by this entry.

---

## Intelligence Engine V1 — Universe Builder (Instrument Type / Tradability / Liquidity / Data Confidence Gates)

**Naming note (read first):** this work was referred to informally as "Epic 007" throughout its own task history — that number is **already allocated** in `MASTER-ROADMAP.md` Section 11 to *Portfolio and Watchlist Intelligence* (a separate, unrelated, still-"Planned / Not Started" initiative). This entry does not claim the Epic 007 number; it is recorded here under its own name to avoid the collision. Whoever formally assigns this work an Epic number later should reconcile the two.

**Status:** Deployed to production — shadow-only, zero effect on published Daily Picks, Heatmap, or Portfolio. **Runtime validation complete (2026-07-08)** — see [Intelligence Engine V1 — Runtime Validation Closure](../Releases/Intelligence-Engine-V1-Runtime-Validation-Closure.md); formal closure remains a separate, explicit decision.

- **Architecture**: `backend/services/intelligence_engine/` — a standalone package (Instrument Type Gate, Tradability Gate, Liquidity Gate, Data Confidence scoring, shadow-run orchestration, telemetry persistence). Universe Builder is one component inside this engine, not the whole system. Gated end-to-end by `INTELLIGENCE_ENGINE_SHADOW_ENABLED` (default off); with the flag off, `daily_picks.py` never even imports the package — confirmed directly via `sys.modules` inspection, not just by code review.
- **Phase 3A (Instrument Type Gate)**: implemented and **production runtime-validated**. First real India shadow telemetry captured 2026-07-06: `raw_count: 2402`, `passed_count: 2400` (plain equities), `excluded_count: 2` (`GOLDBEES`, `SILVERBEES` — correctly classified as ETFs).
- **Phase 3B-A (Liquidity / Tradability / Data Confidence gate framework)**: implemented and deployed. All three gates are pure, unit-tested functions with configurable (env-driven, not hardcoded) thresholds.
- **Phase 3B-B (selected-pick market data wiring)**: implemented and deployed (commit `bb5d3cf8247ce829ca9d0e06d4048fc7aa7b740e`). Derives real price/freshness/completeness data for the gates from the Daily Picks payload already returned by `generate_picks()` — no new provider/API calls, no production function's return contract changed. Coverage is currently limited to the symbols selected into `payload["picks"]` (~18 per run), not the full Phase-0/Phase-1 candidate pool scanned before narrowing to those picks — a disclosed limitation, not an oversight.
- **Runtime validation complete (2026-07-08)**: both closure criteria verified via direct, read-only production API calls — IN telemetry (run 2026-07-07T22:25 UTC) shows `source_commit = d81363e` (verified descendant of `bb5d3cf` by git ancestry check) and US telemetry (run 2026-07-07T06:04 UTC) shows `source_commit = bb5d3cf` exactly; both markets report `tradability.available = true`, `liquidity.available = true`, `data_confidence.available = true`. Full evidence, sanity notes, and named residual risks (picks-only gate coverage, single-run evidence, zero-failure gates as weak positive evidence): [Intelligence Engine V1 — Runtime Validation Closure](../Releases/Intelligence-Engine-V1-Runtime-Validation-Closure.md).
- **Formal closure not yet declared** — runtime validation met the register's two named criteria, but declaring the initiative closed (and reconciling the Epic-number collision above) remains an explicit decision, not an automatic consequence of this evidence.

## Release 12B — Daily Picks Universe and Reliability Validation

**Status:** Deployed — India and US natural-generation evidence now recorded (2026-07-14); scheduler-timing reliability remains a separate open item.

Original gate criteria, reviewed individually rather than declared passed as a block:

| Criterion (as originally stated) | Classification | Evidence |
|---|---|---|
| "India validation requires a genuine fresh post-release generation window." | **Passed by the 2026-07-14 India evidence** | India's scheduled Daily Picks run completed on the current deployed code (commit `36d4b33`, includes both Sprint #014's stratified universe and Phase 1A.6): `has_today: true`, `last_error: null`, `universe_degraded: false`. This is a genuine natural (scheduler-fired, not manually triggered) generation window post-release. |
| "US validation requires a normal US market session and a separate controlled validation." | **Passed by the 2026-07-14 US evidence** | A separate scheduled US job (`942231a1`) completed on the same commit on a live US market day: `has_today: true`, `last_error: null`, `universe_used: "fundamentals_cache"`, `universe_degraded: false`, `universe_candidate_count: 400` (matches Sprint #014's `_TARGET_UNIVERSE_SIZE`). This is also the first live confirmation Sprint #014's stratified universe completes end-to-end for US. |
| "No Daily Picks scheduler enablement is approved until India and US validations both pass." | **Superseded by later implementation** | There is no separate application-level "scheduler enabled" flag to approve — GitHub Actions cron for both markets already exists, is active, and both markets' triggers are what produced the passing evidence above. What remains genuinely open is scheduler-*timing* reliability (see below), not a gate on whether the pipeline itself may run. |
| "No validation result may be described as passed until its release-specific evidence record is complete." | **Still governs — satisfied for this update** | Evidence for both markets is recorded in [Product Integrity #003](../Releases/Product-Integrity-003-Phase-1A6-Production-Migration-and-Natural-Run-Verification.md). This rule itself remains standing policy. |

- **Superseded scope note (2026-07-10):** this release's original universe-construction logic (`yf.screen()`-based, market-cap-descending with a hard cutoff) has been replaced entirely — see the Daily Picks Large/Mid/Small-Cap Stratification entry in `STOCKSENSE_DOCUMENTATION.md` §27 Session 10. The 2026-07-14 evidence above is scoped against this replacement (`stock_fundamentals_cache`-sourced, tier-stratified universe), not the old Yahoo-screener logic — resolving the concern this note originally flagged.
- **Hardening preflight recorded (2026-07-12, markets closed):** a planning-only preflight for runtime/provider hardening (timeouts, retry/backoff, per-symbol failure isolation, stale-cache detection, and related areas) was recorded at [Preflight — Daily Picks Runtime/Provider Hardening](../Releases/Preflight-Daily-Picks-Runtime-Provider-Hardening.md). No code from that preflight has been implemented; it remains a planning record only, unaffected by the 2026-07-14 evidence above.
- **What "natural generation works" does NOT mean:** it does not mean all Daily Picks runtime/provider hardening work is complete (the 2026-07-12 preflight above remains unimplemented), and it does not mean granular output quality (real tier diversity, confidence distribution, total runtime) has been inspected — see the still-open items in `Releases/Sprint-014-Daily-Picks-Cap-Stratification-and-Confidence-Priority.md`'s own Recommendations section.
- **Scheduler reliability — separately gated, open work.** GitHub Actions' scheduled cron for both markets has repeatedly fired hours later than its nominal time (US observations below were recorded against the then-nominal 04:00 UTC base cron; the base schedule has since moved to **06:00 UTC** — see Product Integrity #007 — so these specific dispatch-delay timestamps are historical evidence of the delay pattern, not a statement of the current nominal time): observed firing at 06:04 UTC on 2026-07-14, 06:45 UTC on 2026-07-13, 07:27 UTC on 2026-07-10, 15:33 UTC on 2026-07-09 (see [Product Integrity #003](../Releases/Product-Integrity-003-Phase-1A6-Production-Migration-and-Natural-Run-Verification.md)). A GitHub Actions run reporting "success" only certifies that the asynchronous trigger `POST` was accepted (202) — it does **not** certify that the downstream generation job actually completed; those are two different, decoupled facts. Scheduler timing and end-to-end completion monitoring require a separate forensic design review — not solved by, and not blocking, the evidence recorded above.
- **Product Integrity #007 / #008 — US schedule and workload-overlap correction (2026-07-15).** US Daily Picks base generation moved to 06:00 UTC and the Premarket Finalizer to a 6:00 AM ET target (6:00-7:30 AM ET acceptance window); the US Multibagger refresh moved from 02:00 UTC to 08:00 UTC to stop colliding with the new base schedule. **The 6:00 AM ET finalizer schedule change (#007) is deployed and awaiting natural-run verification. #008's daily-weekday US Multibagger schedule and its "cooperative stop" concurrency mechanism have both since been superseded by #009 below — do not treat this entry as describing the current Multibagger schedule.** See [Product Integrity #007](../Releases/Product-Integrity-007-US-Premarket-Finalizer-6am-ET-Schedule-Change.md) and [Product Integrity #008](../Releases/Product-Integrity-008-US-Scheduler-Workload-Overlap-Compatibility.md).
- **Product Integrity #009 — weekly Multibagger refresh and durable lifecycle (2026-07-16).** Converted the full-universe fundamentals refresh from nightly (India) / daily-weekday (US) to weekly for both markets and made both markets durable. **A production forensic audit for #010 below found this release's own schema migration silently failed to apply for two objects (the market constraint stayed US-only, the active-job index kept its old predicate) — India Multibagger was non-functional in production for the full day #009 was live. See #010 for the fix; this entry's schedule/design details (the two-DST-candidate US cron in particular) are also superseded below.** See [Product Integrity #009](../Releases/Product-Integrity-009-Weekly-Multibagger-Refresh-and-Durable-Lifecycle.md).
- **Product Integrity #010 — Multibagger production hardening and legacy-schema repair (2026-07-16).** Repaired the #009 migration gap above via explicit `DROP`-then-`ADD`/`CREATE` statements (verified against production: market constraint now `IN ('IN','US')`, active-job index now `WHERE status IN ('queued','running')`, both confirmed via direct read-only introspection post-deploy). Simplified US Multibagger to a single fixed `0 8 * * 0` cron (3:00 AM EST / 4:00 AM EDT, truthfully disclosed in user-facing copy) instead of two DST candidates. Job reservation and heavy-workload lease acquisition are now atomic (one transaction). Added a resumable worker claim (`SELECT ... FOR UPDATE SKIP LOCKED`) and per-symbol staging with atomic cache promotion — a partial/failed run never touches the active `stock_fundamentals_cache`. India/US Daily Picks and the Premarket Finalizer schedules were explicitly frozen and regression-tested (unchanged: `56 21 * * 0-4`, `0 6 * * 1-5`, `0 10 * * 1-5` + `0 11 * * 1-5`). **Deployed, awaiting the first natural India Daily Picks run, US Daily Picks/Finalizer sequence, India Saturday Multibagger run, and US Sunday Multibagger run — none yet observed.** See [Product Integrity #010](../Releases/Product-Integrity-010-Multibagger-Production-Hardening-and-Legacy-Schema-Repair.md).

## Product Integrity #011 — India Session-Freshness Backend Gate

**Status:** Deployed to production (2026-07-16, commit `ec3cef0`). See [Product Integrity #011](../Releases/Product-Integrity-011-India-Session-Freshness-Backend-Gate.md).

- Root cause: per-symbol Yahoo/yfinance price-data lag for India Daily Picks (already forensically identified by Product Integrity #004, which built a frontend-only disclosure banner but explicitly deferred a backend gate). Triggered by a 2026-07-16 production screenshot review showing every India Daily Pick flagged stale.
- Adds `get_expected_latest_completed_nse_session()` (backend port of the frontend's existing session-freshness algorithm), a retry-on-stale branch inside `_fetch_history()`'s existing 3-attempt budget, honest `is_stale`/`expected_session` labeling on `price_reference` at generation time, and a fix so error-shaped `predict()` results are excluded from the candidate pool instead of silently polluting it with `None` fields.
- India (`market == "IN"`) only — does not change scoring, ranking, or universe selection.
- Does not eliminate staleness entirely on its own — see Product Integrity #012 immediately below, which adds a real second data source for the cases retry alone can't resolve. Natural-run verification (comparing the live stale-rate against the 2026-07-16 baseline) is pending the next scheduled India generation.

## Product Integrity #012 — NSE Bhavcopy Last-Resort Price Correction

**Status:** Deployed to production (2026-07-16, commit `b83b12a`). See [Product Integrity #012](../Releases/Product-Integrity-012-NSE-Bhavcopy-Price-Correction-Fallback.md).

- Builds on #011: when yfinance's 3-attempt retry budget is exhausted and a bar is still stale, adds one last-resort lookup against NSE's own official daily bhavcopy archive (`https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv`) before accepting the stale price.
- Reachability from Railway's production network was verified directly (not assumed) via a read-only probe run from inside the actual production container: `HTTP_CODE: 200`, real ~369KB daily file returned.
- **Meaningfully changes #011's stated scope**: when a bhavcopy correction fires, `current_price` — and therefore entry/target/stop-loss trade levels, not just the displayed reference price — is computed from NSE's corrected close rather than the stale Yahoo one. This is deliberate and disclosed, not incidental.
- Does not touch OHLC history or technical indicators (bhavcopy has no history, only a single day's close) — a corrected pick's price is accurate; its technical indicators may still be computed from history that includes a stale last bar. Does not add a US equivalent.
- Natural-run verification (checking for `generation_reference_source: "nse_bhavcopy"` on any pick) is pending the next scheduled India generation.

## Product Integrity #013 — India Daily Picks Schedule Move (2:07 AM IST) and Stale-Copy Repair

**Status:** Deployed to production. See [Product Integrity #013](../Releases/Product-Integrity-013-India-Daily-Picks-Schedule-Move-and-Stale-Copy-Repair.md).

- India Daily Picks cron moved from `56 21 * * 0-4` UTC (3:26 AM IST) to `37 20 * * 0-4` UTC (**2:07 AM IST**), by explicit user request.
- In the process, found and fixed a pre-existing, silent staleness: the frontend's `"generated daily at 2 AM IST"` label had been wrong for about a week (since the cron moved to 3:26 AM IST on 2026-07-09 without the label following). Now correctly reads `"2:07 AM IST"`, matching the new live schedule.
- Confirmed (did not need to fix) that `"screened from N eligible NSE stocks"` is not a hardcoded/stale number — it's a live template driven by the backend's actual `screened_from` field each run.
- Corrected a related functional drift in `main.py`'s startup catch-up threshold, which had assumed generation starts near 2 AM IST while the real cron had drifted to 3:26 AM IST — the schedule move itself resolves this (2:07 AM IST is close enough to the existing hour=2 threshold), the code comment was updated to state the current reality rather than the stale one.
- Two "frozen schedule" regression tests (Product Integrity #010) were deliberately updated to the new cron — that freeze exists to prevent *accidental* drift from unrelated work, not to block an explicit, user-directed scheduling decision like this one.
- Scope was explicitly limited to functional code (workflow YAML, frontend label, backend strings/comments, tests) — the ~12 `Documentation/*.md`/`README.md` prose references to "2 AM IST" were left as-is per user direction, and remain a known stale-reference risk for a future doc pass.

## Product Integrity #014 — Stock Detail Page Forensic Audit, HIGH-Severity Fixes

**Status:** Deployed to production (commit `645a7c8`). See [Product Integrity #014](../Releases/Product-Integrity-014-Stock-Detail-Page-Forensic-Audit-HIGH-Severity-Fixes.md).

- Full forensic audit of `stocksense360.com/stock/{symbol}` (~2,100-line page, live quote + per-horizon AI predictions + fundamentals + backtest + score history + news/sentiment) triggered by a user-reported ambiguity: the AI Signal card silently showed Medium Term data on the Fundamentals tab with no disclosure. Five parallel read-only audits found **23 distinct findings** (7 HIGH, 10 MEDIUM, 6 LOW) plus a documented list of verified-clean areas.
- This release fixed the 7 HIGH-severity findings: (1) AI Signal card now discloses "· Medium Term" on Fundamentals/History tabs; (2) 52W High/Low pills null-guarded (previously could render the literal string "₹undefined"); (3) AI Prediction card's signal strip now uses `getSignalTone` so a low-confidence BUY mutes consistently instead of showing a muted badge next to a bright-green strip; (4) Trade Levels card discloses when it's computed off a stale `prediction.current_price` vs. the live quote price; (5) Paper Trade button disables during a background horizon refetch instead of risking a trade against the wrong horizon's data; (6) Backtest results now clear on symbol/market navigation instead of potentially showing a previous stock's results under a new stock's header.
- 12 new regression tests, full frontend suite 291/291 passing, clean typecheck. No backend changes.
- The remaining 16 MEDIUM/LOW findings are addressed in [Product Integrity #015](../Releases/Product-Integrity-015-Stock-Detail-Page-Forensic-Audit-MEDIUM-LOW-Severity-Fixes.md) immediately below.

## Product Integrity #015 — Stock Detail Page Forensic Audit, MEDIUM/LOW-Severity Fixes

**Status:** Deployed to production (commit `1e3c627`). See [Product Integrity #015](../Releases/Product-Integrity-015-Stock-Detail-Page-Forensic-Audit-MEDIUM-LOW-Severity-Fixes.md).

- Follows PI-014 directly — fixed 15 of the 16 remaining findings from the same audit (10 MEDIUM, 5 of 6 LOW).
- Highlights: Market Regime/Evidence/Research panels gated to horizon tabs only (previously leaked onto Fundamentals/History/Backtest unlabeled); Take Profit color now reflects win/loss instead of raw price-direction (was showing red for a SELL's successful target, indistinguishable from its stop-loss); Mkt Cap uses ₹ Cr for India instead of a contradicting T/B/M convention elsewhere on the same page; History chart dates pinned to UTC (could previously show the wrong calendar day for non-IST viewers); Debt/Equity convention unified with the Multibagger page (× ratio instead of raw-scale %); several backend-computed US fundamentals fields (ROCE, EV/EBITDA, OPM%, interest coverage, P/S) surfaced for the first time.
- Finding #15 was excluded (backend DB schema change, different risk category) — see [Product Integrity #016](../Releases/Product-Integrity-016-Score-Snapshots-Market-Scoping.md) immediately below.
- 21 new regression tests, full frontend suite 312/312 passing, clean typecheck. No backend changes.

## Product Integrity #016 — Score Snapshots Market Scoping

**Status:** Deployed to production (commit `883b304`) — migration confirmed live via direct DB query (market column present, new constraint/index in place, old ones gone). See [Product Integrity #016](../Releases/Product-Integrity-016-Score-Snapshots-Market-Scoping.md).

- Closes finding #15 from the Stock Detail page audit — `score_snapshots` had no `market` column.
- **A direct production audit found this wasn't hypothetical**: 225 symbols genuinely exist in both IN and US markets (AAPL, ADBE, ABT, and others), and since the old unique constraint was `(symbol, horizon, snapshot_date)` with `ON CONFLICT DO UPDATE`, this means one market's score-history data may have been silently overwritten by the other's for any of those symbols scored by both markets on the same day — not just a display bug.
- Migration: nullable `market` column added (no backfill guess for existing rows — genuinely unrecoverable which market a legacy colliding row belongs to), unique constraint and index rebuilt to include `market`, using the DROP-then-ADD idempotent pattern established in Product Integrity #010 (not relying on `IF NOT EXISTS` alone). Self-applied via `init_db()` on backend startup, same mechanism PI-010 used.
- Write path (`daily_picks.py`) and both read paths (History tab chart, Portfolio's Signal-column fallback — a second caller with the identical pre-existing gap, found in the same pass) now require `market`, matching `market IS NULL` too so the ~95% of symbols that never collided keep their pre-migration history intact.
- 16 new regression tests (12 backend, 4 frontend), full suites passing (2169/2169 backend, 316/316 frontend), clean typecheck.

## Product Integrity #017 — AI Prediction Confidence Display Fixes

**Status:** Deployed to production (commit `58632de`). See [Product Integrity #017](../Releases/Product-Integrity-017-AI-Prediction-Confidence-Display-Fixes.md).

- User feedback on a live low-confidence pick (DIXON, 12% confidence BUY): the "AI Prediction" card showed Confidence twice in a row (Signal Strip + a redundant standalone `ConfidenceMeter` using a *different* muting threshold than the strip); Trade Levels rendered a fully precise, confident-looking setup with no acknowledgment the underlying signal was only 12% confident.
- Fixes: removed the duplicate `ConfidenceMeter`; added a low-confidence warning banner to Trade Levels (`confidence < 45`, the same threshold `getSignalTone` already uses to mute a weak BUY) — discloses rather than hides the numbers, since they're still mathematically valid ATR-based levels.
- 5 new regression tests, full frontend suite 321/321 passing, clean typecheck. No backend changes.
- Follow-up: the user's own sharper question ("how is 12% confidence + 15.8% upside possible?") led directly to Product Integrity #018 below, which fixes the actual root cause rather than just disclosing it.

## Product Integrity #018 — Confidence-Scaled Target Price Floors

**Status:** Deployed to production (commit `970e0d6`). See [Product Integrity #018](../Releases/Product-Integrity-018-Confidence-Scaled-Target-Price-Floors.md).

- **Root cause of PI-017's reported mismatch**: `_estimate_target()`'s BUY/SELL minimum target floors (e.g. "a long-term BUY always shows at least +15% upside") were flat constants, completely decoupled from `confidence` — a 12%-confidence BUY and a 90%-confidence BUY got the identical guaranteed floor.
- Fix: `conf_factor`'s floor lowered from 0.5 to 0.2 and applied consistently to every BUY/SELL floor across short/medium/long horizons (medium and long previously had no confidence scaling on their floors at all). At full confidence, floors match the original flat constants exactly — zero behavior change for high-confidence picks. HOLD's target band is intentionally untouched (no directional-conviction claim to scale).
- This is core `predict()` logic, not just Stock Detail page display — the fix propagates automatically into Daily Picks, Multibagger, and backtest target-price consumers, since they all read the same `target_price`/`trade_levels` fields. Verified (not assumed): `_trade_levels()` takes `target` directly as its `take_profit` value, and its own stop-loss-tightening logic already has a "surface the honest sub-1.5 risk/reward rather than fake the take-profit" fallback, so Risk/Reward now also honestly reflects weak signals as a side effect, no extra code needed.
- 8 new behavioral tests (real numeric assertions on `_estimate_target()`, not source-text checks), full backend suite 2177/2177 passing. No frontend changes.

## Product Integrity #019 — News & Sentiment Pipeline Freshness Fixes

**Status:** Deployed to production (commit `fa0afe3`) — live-verified against the real production news endpoint. See [Product Integrity #019](../Releases/Product-Integrity-019-News-Sentiment-Pipeline-Freshness-Fixes.md).

- User reported DIXON's News & Sentiment section showed only 4-8 month old articles and "Insufficient fresh company-specific news evidence" despite genuine same-day coverage existing. Also asked whether this affects the AI signal — **verified via code trace: it does not**, missing news evidence is excluded and its weight redistributed to technicals/fundamentals rather than defaulting to neutral or degrading confidence.
- Four independent, confirmed bugs found (not thin coverage): (A) Google News RSS query had no recency operator — ranked by relevance not date; (B) the company-relevance classifier required the full run-on company-name phrase including the trailing country word, so ordinary title-case headlines like "Dixon Technologies shares rally 5%" were excluded; (C) the Economic Times per-symbol RSS feed is dead (verified live — returns generic homepage HTML); (D) the Yahoo Finance RSS feed is deprecated (verified live — returns generic homepage HTML). C and D were silently contributing zero articles for every symbol, not just DIXON.
- Fixes: added `when:14d` to Google News queries (matching the existing freshness window); accepted the already-computed 2-word company-name prefix as an additional relevance-match path (ticker case-sensitivity deliberately untouched — an existing test protects it against a specific false-positive); removed both dead feeds.
- **Live end-to-end verified**, not just unit-tested: 3 DIXON articles now correctly classify as fresh + company-specific, all from the prior week.
- 10 new regression tests, all 22 pre-existing news-relevance tests still passing, full backend suite 2187/2187. No frontend changes.

## Product Integrity #020 — SEC EDGAR Facts Cache Memory Cap

**Status:** Deployed to production (commit `0af3dbd`). See [Product Integrity #020](../Releases/Product-Integrity-020-SEC-EDGAR-Facts-Cache-Memory-Cap.md).

- User noticed US Daily Picks hadn't refreshed in 54+ hours. Direct production DB read found a US job stuck `running` for ~5 hours (finalized as a separate direct data correction, not part of this release), and a prior US job that failed the day before with a confirmed Railway OOM.
- Root cause found: `sec_edgar_adapter.py`'s `_facts_cache` was the one cross-run cache in the prediction pipeline with no size cap or eviction, unlike `prediction_engine.py`'s `_pred_cache`/`_regime_cache` (already capped at 300 specifically to prevent OOM on Render's free 512MB tier). With the US universe at 400 symbols, a single run could grow this cache unboundedly, each entry holding a full multi-year SEC EDGAR companyfacts payload.
- Fix: added `_FACTS_CACHE_MAX = 300` (matching the already-proven-safe cap elsewhere) and a `_facts_cache_set()` helper that evicts the oldest entry when the cap is reached; `fetch_company_facts()` now writes through it instead of a direct dict assignment.
- 6 new tests (cap value, eviction order, 400-symbol simulated run, re-insert behavior, write-through regression guard), all 5 pre-existing SEC EDGAR test files re-verified passing (39/39), full backend suite 2193/2193. No frontend changes.
- Natural-run verification: a manually-triggered US Daily Picks run was used as the first real-world test of this fix (same day as deploy, since the automatic GitHub Actions cron had already fired once for the day before the fix landed).

## Product Integrity #021 — High Conviction Picks Filter

**Status:** Deployed to production (commit `37cf159`) — verified live on stocksense360.com. See [Product Integrity #021](../Releases/Product-Integrity-021-High-Conviction-Picks-Filter.md).

- User asked how to find Daily Picks with >85% AI confidence — there was no way to do this; picks could only be scanned manually per horizon tab.
- Added a "High Conviction Only (≥85%)" toggle to the Picks page, alongside the existing horizon tabs. Filters the current horizon's picks to `confidence >= 85` and sorts highest-first; composes with (doesn't replace) the horizon tabs. Distinct empty state when a horizon has picks but none clear the bar.
- Pure client-side view filter — no backend/ranking/confidence-computation change.
- 13 new tests (wiring + real behavioral assertions on the exact filter/sort logic, including the 84%/85% boundary), full frontend suite 334/334 passing, clean typecheck, clean production build. No backend changes.
- **Live-verified in browser**: toggle renders, activates with correct styling, filters/sorts correctly (confirmed against real India Long Term data — LUPIN 91%, NATIONALUM 88%), and the freshness-notice count correctly follows the filtered set.

## Product Integrity #022 — Risk-Based Position Sizing in Paper Trade

**Status:** Deployed to production (commit `0ef83a7`) — confirmed live via the deployed JS bundle. See [Product Integrity #022](../Releases/Product-Integrity-022-Risk-Based-Position-Sizing.md).

- Follow-up to a user question about why US paper trading had a 75.6% win rate but a net loss while India (61.4% win rate) was profitable. Pulling the user's actual closed trades showed the cause: a flat share count (e.g. "10 shares") carries wildly different dollar risk by stock price — the 3 largest US losses ($1,439/$604/$944) were all large-notional positions (10 shares of $1,189/$824/$751 stocks), dwarfing many small wins on cheap stocks.
- Added a risk-based quantity suggestion to the Paper Trade modal: sizes the position so a stop-loss hit costs ~1% of available virtual capital, capped at what the account can afford. Auto-fills Quantity (editable, same pattern as the existing AI stop-loss/target pre-fill) with a visible "risks ~$X (1% of $Y available)" hint.
- Pure client-side suggestion — no backend or trade-execution logic change; purely additive to the existing manual Buy flow.
- 13 new tests on the extracted pure sizing function, including a reproduction of the user's real MU incident confirming the fix would have suggested far fewer than the 10 flat shares that caused the actual loss. Full frontend suite 347/347 passing, clean typecheck, clean production build. No backend changes.

## Product Integrity #023 — Post-Job Memory Retention Fix

**Status:** Deployed to production (commit `637ef34`). See [Product Integrity #023](../Releases/Product-Integrity-023-Post-Job-Memory-Retention-Fix.md).

- User spotted a "⚠ 2" Out of Memory badge on the Railway dashboard. Investigated via Railway's own memory metrics API: one genuine OOM occurred at 15:48 UTC 2026-07-16 — memory climbed to 98% of the 8GB container limit during a US Daily Picks run and **stayed pinned there for 90 minutes after the job had already completed**, then crashed and auto-restarted. (The other two detected "cliffs" were confirmed to be ordinary deploy-triggered restarts, not OOMs.)
- Root cause: `generate_picks()`'s `raw` accumulator holds all ~1,200 candidate×horizon full result entries (reasoning, quality breakdowns, bull/bear cases) simultaneously for the entire function, even though the per-horizon ranking loop only needs one horizon at a time and the one consumer needing the full set already finished beforehand.
- Fix: release each horizon's `raw[horizon]` reference immediately after it's captured for that horizon's processing, so at most one horizon's full candidate pool is held at a time instead of all three. Also added start/completion timing logs to the un-joined background thread (`weight_adapter.run_adaptation`) that continues running after the job is marked complete — a plausible but not-yet-conclusively-sized contributor to the 90-minute stall, now directly measurable on the next run.
- 7 new tests (4 structural, verifying the release happens at the right point and `raw` is never read again; 3 behavioral, verifying the new timing logs), full backend suite 2200/2200 passing. No frontend changes.
- Does not increase the Railway memory limit (deferred as a fallback) and does not conclusively prove the background thread is the sole cause of the plateau — natural-run verification against the next heavy US run is the real test.

## Product Integrity #024 — Paper Trading Horizon Summary and Overdue Flag

**Status:** Deployed to production (commit `b6d812e`, layout follow-up `07cad13`) — verified live via the deployed JS bundle. See [Product Integrity #024](../Releases/Product-Integrity-024-Paper-Trading-Horizon-Summary-and-Overdue-Flag.md).

- User asked whether their open paper-trade positions actually conform to their horizon's stated holding window. Checked the code: `horizon` is a static label frozen at trade-open time, never checked against actual holding duration — confirmed concretely via the user's own real NNY position (opened 29/6, still bucketed "Short Term (1-5 trading days)" 17 days later, no warning anywhere).
- Added an "⏱ Overdue for {horizon}" flag on any open position that has exceeded its horizon's expected calendar-day window (short: 7 days, medium: 28, long: 183 — documented calendar-day approximations, not a precise trading-day calendar).
- Added a per-horizon summary strip (Invested, Unr. P&L, % of Total Investment, Avg Days Held), folded into the existing group header row after user feedback that a separate row wasted vertical space.
- 10 new tests on the extracted pure `horizonHolding.ts` utility, including a direct reproduction of the real NNY overdue case. Full frontend suite 357/357 passing, clean typecheck, clean production build. No backend changes.

## Product Integrity #025 — Alpha Observations Write Path Bug

**Status:** Implemented, tested, locally committed — pending production safety gate and push confirmation. See [Product Integrity #025](../Releases/Product-Integrity-025-Alpha-Observations-Write-Path-Bug.md).

- Follow-up to a conceptual question about how the platform's self-learning system works: investigated why `alpha_observations` (the canonical clean dataset meant to eventually let production learning safely re-enable) had zero rows in production despite the write code appearing correctly wired.
- Root cause, confirmed by direct reproduction against the real driver: `save_alpha_observations()` called `conn.executemany(...)` on a psycopg3 `Connection` — which has no `executemany` method (only `Cursor` does). Every call raised `AttributeError`, silently caught (by design — shadow telemetry must never break the real Daily Picks job) and logged as a non-fatal warning, invisible anywhere else. This has failed on every single Daily Picks run, both markets, since the feature was written.
- Fix: route the insert through `conn.cursor()` instead of calling it on the connection directly. One call site.
- Confirmed isolated, not a wider pattern: `git grep`'d every `executemany` call site in the backend — the only other matches are legitimate (a real Postgres cursor, a real `sqlite3.Connection`, which genuinely has that method).
- Blast-radius verified, not assumed: nothing in production reads from `alpha_observations` yet, so this fix can only start populating a previously-idle table — it cannot change any ranking, confidence, or target-price output. Full backend suite 2203/2203 passing (zero regressions elsewhere), including a new test proven both ways (fails against the reverted bug with the exact real error message, passes against the fix).
- Also found (separately, out of scope for this fix): `factor_ic_history` is also empty because its writer `log_factor_ic()` has zero callers anywhere — dead code, not the same bug class.

## Phase 1A.6 — Market Integrity Hardening and Database-Default Closure

**Status:** Deployed and verified live (2026-07-14). Write-boundary and schema-default closure is **COMPLETE**. Historical contamination repair is **NOT STARTED**. Canonical clean learning-dataset construction is **NOT STARTED**. Learning re-enablement is **NOT AUTHORIZED**.

- Commit `36d4b338377026ad7aa69014cf3efe4edc45a572` deployed to Railway (deployment `07ff8c6d`, `SUCCESS`/Online); live logs confirm the new market-integrity containment logic is active against real production traffic.
- `predictions.market` and `outcomes.market`'s database-level `DEFAULT 'IN'` was dropped on 2026-07-14T05:54:02Z under a separately authorized, evidenced production migration. Both columns remain `NOT NULL`. Pre/post row counts are identical for both tables — zero historical rows were changed, relabeled, or repaired by this action.
- Production learning remains quarantined: `production_learning_enabled: false`, `production_alpha_source: "fixed_academic_prior"`, `learning_dataset_version: "legacy-quarantined-2026-07-12"` — unaffected by the above.
- Full detail: [Phase 1A.6 architecture document](../Architecture/Phase-1A6-Market-Integrity-Hardening-and-Repair-Planning.md) (design/implementation/closure) and [Product Integrity #003](../Releases/Product-Integrity-003-Phase-1A6-Production-Migration-and-Natural-Run-Verification.md) (production evidence).
- Historical contamination repair, the unreconciled Phase 1A.5-vs-planner duplicate-count discrepancy, and canonical clean learning-dataset construction all remain separately gated, future, explicitly-authorized work — none of it began or was implied by the above.

## Release 13C — Recommendation Consolidation Observability

**Status:** Deployed — RCI remains disabled.

- Aggregate RCI composition-success and fail-open counters are deployed for controlled operational observation.
- Counters reset on service restart and are not per-symbol, per-user, or persistent telemetry.
- Counter availability does not itself prove RCI correctness or authorize activation.

## Release 13D — Recommendation Consolidation Activation Readiness

**Status:** Runbook complete — activation not approved.

- `RCI_LIVE_STOCK_ANALYSIS_ENABLED` remains disabled.
- The existing Evidence Summary frontend consumer is already deployed.
- Any future RCI activation is user-visible on the Stock Detail page; it is not a backend-only dark launch.
- RCI activation, Daily Picks validation, and scheduler enablement remain separate approval decisions.

## Release 14B — Debug Endpoint Security Hardening

**Status:** Deployed — protected-endpoint runtime verification passed.

- `/api/predictions/debug/state` requires a configured non-empty `PICKS_SECRET` and a matching non-empty `X-Secret` header.
- Read-only negative-path verification confirmed that missing, blank, and deliberately incorrect secret values fail closed with the generic `401` response `{"detail":"Invalid secret"}`.
- Read-only authenticated verification confirmed HTTP `200` and the approved aggregate-only response shape: operational counts, cache-age summary, thread count, and RCI observability counters only.
- Verification confirmed no raw cache identifiers, in-flight identifiers, symbol/market/horizon identifiers, background-log content, or exception text are exposed.
- Verification did not change Release 12B validation, RCI activation, Daily Picks scheduler state, configuration, or deployment state.

## Daily Picks Scheduler & End-to-End Completion Reliability Hardening (2026-08-10)

**Status:** Implemented on feature branch `feature/daily-picks-scheduler-completion-reliability` — pending owner review before PR/merge/deployment. Completes the GO recommendation from a prior read-only forensic review of the Daily Picks trigger/completion path. GitHub Actions remains the sole scheduler — no new external scheduler was introduced, and no schema/migration change was made.

- **Defect closed:** the India and US-base GitHub Actions workflows previously reported success purely from an HTTP 2xx on the trigger POST to `/api/picks/generate`. A 202 `accepted` (or a 200 `already_fresh`) response only means the request was received — it never proved generation actually finished or that fresh picks were durably published. A `daily_picks_jobs` row can still fail, stall, or be interrupted by a Railway restart after the POST succeeds.
- **India + US base workflows** (`daily_picks_in.yml`, `daily_picks_us.yml`) now poll `/api/picks/status?market=<M>` after the trigger, bound to the exact `job_id` the trigger response returned, using a new shared helper (`scripts/ci/poll_daily_picks_completion.sh`) so the completion-verification logic exists in exactly one place. Workflow success now requires BOTH the bound job reaching `completed` status AND `has_today`/`last_successful_generated_at` evidence of durable publication — a job that merely reports `completed` with no publication evidence is reported as a failure, not a success.
- **New India recovery watchdog** (`daily_picks_in_watchdog.yml`, cron `37 22 * * 0-4`, two hours after the primary India cron) is the India-side analog of the US Premarket Finalizer's existing accidental recovery path. It checks today's India freshness first (no-ops if already fresh), and if not fresh, calls a new `POST /api/picks/recover` endpoint — a thin, generic HTTP wrapper over the existing, unmodified `services.daily_picks.attempt_governed_recovery()` (the same governed, bounded, atomically-reserved recovery function the US premarket finalizer has called internally since the 2026-07-22 incident). The watchdog then polls `/api/picks/status` itself to verify the recovery's actual outcome — it never treats "recovery POST accepted" as "recovery succeeded."
- **API contract addition:** `POST /api/picks/recover?market=<IN|US>&reason=<text>`, secret-protected identically to `/generate` and `/premarket-finalize`. Added only because a GitHub Actions job has no way to call the in-process `attempt_governed_recovery()` function directly; it introduces no new recovery semantics of its own.
- **No schema/migration change.** All new/reused fields (`job_id`, `job_status`, `phase`, `processed`, `total`, `last_runner_heartbeat_at`, `last_progress_at`, `has_today`, `last_successful_generated_at`, `derived_job_health`) already existed on `/api/picks/status` before this change.
- **Startup catch-up** (`DAILY_PICKS_STARTUP_CATCHUP_ENABLED`) remains unchanged and still defaults OFF in code; this work did not enable it anywhere and did not change its timing. **Correction (follow-up, 2026-08-10):** a prior version of this note incorrectly implied the India catch-up threshold (`_catchup_picks("IN", _IST, 2, 60)` in `backend/api/main.py` — trigger_hour=2, i.e. 2:00 AM IST local) sits safely *after* the primary India cron (2:07 AM IST). It does not — 2:00 AM IST is actually **~7 minutes *before*** the primary cron's 2:07 AM IST trigger, a narrow overlap window. This is existing, already-safe behavior, not a new gap introduced by this change: if catch-up were ever enabled and its check ran inside that ~7-minute window, it would race the primary cron only at the request level — the atomic `try_reserve_daily_picks_job_with_lease()` reservation (the same one `/generate`, `/recover`, and every other trigger path use, under the `idx_daily_picks_jobs_one_active_per_market` partial unique index) still guarantees only one of the two can win the job row; the loser gets a clean `already_running` no-op, never a competing job. No catch-up timing change was made and none is needed to preserve this guarantee — flagged here only so this document accurately describes the real timing relationship instead of a safely-after one that doesn't exist. US catch-up (trigger_hour=3, i.e. 3:00 AM ET) remains safely after the US base cron (06:00 UTC = 1-2 AM ET depending on DST) with no such overlap. All five possible triggers of a Daily Picks job (primary GitHub cron, the India watchdog, US finalizer recovery, startup catch-up, and the manual `/generate` endpoint) reserve through the same `daily_picks_jobs` table under the same `idx_daily_picks_jobs_one_active_per_market` partial unique index — they cannot create competing jobs for the same market, regardless of how closely their trigger windows overlap.
- Provider/runtime hardening (timeouts, per-symbol isolation, retries, fallback) is explicitly out of scope for this change and was not touched.

## Conviction-Gated Daily Picks Publication Policy (2026-08-16)

**Status:** Implemented on feature branch `feature/daily-picks-conviction-gated-publication`, based on verified `origin/main` @ `b30b56baefb21739786c2c01dbfb6581e1d58a96` — committed to a draft PR, **NOT merged, NOT deployed**. Full evidence trail in [DP-034, Daily-Picks Implementation Register](../Daily-Picks/DAILY-PICKS-IMPLEMENTATION-REGISTER.md#dp-034--conviction-gated-daily-picks-publication-policy-implementation-status-not-yet-deployed).

- **What changed:** /picks now publishes at most 3 candidates per (market, horizon) pair (0-3 is a legitimate outcome), gated on the existing `confidence` field ("Model Conviction") being valid and >= 85.0 on its native 0-100 scale, capped in the existing ranking order. No change to eligibility/BUY-signal logic, ranking formulas, hard gates, target/stop-loss logic, or generation schedules — a publication-boundary filter only, added via `services/thresholds.py`'s new `DailyPicksPublicationThresholds` registry and `services/daily_picks.py`'s new `_apply_conviction_publication_gate()`.
- **Confidence-semantics evidence:** `confidence` was already load-bearing pre-existing production logic (the >=25 noise floor in `_passes_quality_gate`, the >80 short-horizon priority bucket in `_select_short_term_top_six`, and Product Integrity #021's existing >85% "High Conviction" client-side filter above) — proven suitable for reuse as a publication gate, not a newly-invented score.
- **No schema/migration change.** Publication metadata (`n_conviction_qualified`, `n_published`, `conviction_threshold`, `max_published_per_horizon`) is payload-only, additive, backward-compatible.
- **Explicitly not done:** merge, deployment, or natural-run production verification — all intentionally withheld pending independent review per this branch's governing implementation prompt.

## Operational Safety Rules

- Daily Picks generation itself is live for both markets, with recorded India and US natural-run evidence (2026-07-14) — see Release 12B above. This does **not** authorize skipping evidence-based validation for future generation-logic changes, and does **not** resolve the separately-tracked GitHub Actions scheduler-timing reliability issue (see Release 12B).
- No RCI feature-flag change without explicit approval.
- No production-status documentation may claim a validation passed without recorded evidence.
- Historical test totals remain historical snapshots. Current test status must be taken from the latest validated release or CI evidence.
- Intelligence Engine V1's runtime-validation criteria (IN and US shadow telemetry at `source_commit = bb5d3cf` or later, with `tradability`/`liquidity`/`data_confidence` all `available = true`) were met on 2026-07-08 — evidence recorded in the Runtime Validation Closure record. Formal closure of the initiative remains a separate, explicit decision.
- No historical market-integrity contamination repair, backfill, or relabeling may be executed without a separate, explicit authorization — see Phase 1A.6 above. Production learning remains quarantined (`production_learning_enabled: false`) until a canonical clean learning dataset is separately constructed and validated.
