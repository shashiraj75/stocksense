# StockSense360 — Current Release Status

**Purpose:** This document is the authoritative operational-status register for live and pending releases. It records what is deployed, what remains disabled, what is pending validation, and which future actions require explicit approval.

**Use this document for current state.** Historical sprint reports, Epic closures, SSDS documents, and audit reports remain authoritative evidence for their own completed scope, but they do not automatically describe the current production operating state.

**As of:** 2026-07-14 — maintained as a live operational register

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
- **Scheduler reliability — separately gated, open work.** GitHub Actions' scheduled cron for both markets has repeatedly fired hours later than its nominal time (US: nominal 04:00 UTC, observed firing at 06:04 UTC on 2026-07-14, 06:45 UTC on 2026-07-13, 07:27 UTC on 2026-07-10, 15:33 UTC on 2026-07-09 — see [Product Integrity #003](../Releases/Product-Integrity-003-Phase-1A6-Production-Migration-and-Natural-Run-Verification.md)). A GitHub Actions run reporting "success" only certifies that the asynchronous trigger `POST` was accepted (202) — it does **not** certify that the downstream generation job actually completed; those are two different, decoupled facts. Scheduler timing and end-to-end completion monitoring require a separate forensic design review — not solved by, and not blocking, the evidence recorded above.

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

## Operational Safety Rules

- Daily Picks generation itself is live for both markets, with recorded India and US natural-run evidence (2026-07-14) — see Release 12B above. This does **not** authorize skipping evidence-based validation for future generation-logic changes, and does **not** resolve the separately-tracked GitHub Actions scheduler-timing reliability issue (see Release 12B).
- No RCI feature-flag change without explicit approval.
- No production-status documentation may claim a validation passed without recorded evidence.
- Historical test totals remain historical snapshots. Current test status must be taken from the latest validated release or CI evidence.
- Intelligence Engine V1's runtime-validation criteria (IN and US shadow telemetry at `source_commit = bb5d3cf` or later, with `tradability`/`liquidity`/`data_confidence` all `available = true`) were met on 2026-07-08 — evidence recorded in the Runtime Validation Closure record. Formal closure of the initiative remains a separate, explicit decision.
- No historical market-integrity contamination repair, backfill, or relabeling may be executed without a separate, explicit authorization — see Phase 1A.6 above. Production learning remains quarantined (`production_learning_enabled: false`) until a canonical clean learning dataset is separately constructed and validated.
