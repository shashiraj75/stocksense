# StockSense360 — Current Release Status

**Purpose:** This document is the authoritative operational-status register for live and pending releases. It records what is deployed, what remains disabled, what is pending validation, and which future actions require explicit approval.

**Use this document for current state.** Historical sprint reports, Epic closures, SSDS documents, and audit reports remain authoritative evidence for their own completed scope, but they do not automatically describe the current production operating state.

**As of:** 2026-07-08 — maintained as a live operational register

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

**Status:** Deployed — controlled production validation pending.

- India validation requires a genuine fresh post-release generation window.
- US validation requires a normal US market session and a separate controlled validation.
- No Daily Picks scheduler enablement is approved until India and US validations both pass.
- No validation result may be described as passed until its release-specific evidence record is complete.

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

- No scheduler enablement before both India and US Release 12B validations pass.
- No RCI feature-flag change without explicit approval.
- No production-status documentation may claim a validation passed without recorded evidence.
- Historical test totals remain historical snapshots. Current test status must be taken from the latest validated release or CI evidence.
- Intelligence Engine V1's runtime-validation criteria (IN and US shadow telemetry at `source_commit = bb5d3cf` or later, with `tradability`/`liquidity`/`data_confidence` all `available = true`) were met on 2026-07-08 — evidence recorded in the Runtime Validation Closure record. Formal closure of the initiative remains a separate, explicit decision.
