# Trade Postmortem — Wave C API, Frontend and Operations Contract

- **Status:** ratified, in force for Wave C
- **Branch:** feature/trade-postmortem-sprint3a-price-path
- **Wave C starting SHA:** 8734d1a65dea578942142f0d588e8ba716b12a37
- **SHA at time of ratification:** 53b5ac41c2868892c2fccc09408623f932dd5d86

## 1. Chronology disclosure

The WC-K current-report read endpoint
(`GET /api/paper-trading/{trade_id}/current-report`) and its 13
real-PostgreSQL tests in
`backend/tests/postgres_integration/test_current_report_read_api.py`
were implemented and merged to this branch **before** this contract
document existed. This document ratifies that implementation
against the full Wave C requirements after the fact — it does not
represent contract-first chronology. Any mismatch found during this
ratification pass is tracked below and must be corrected as a
separate, clearly-labeled commit before WC-K is considered closed;
none of the already-green PostgreSQL 15/17 evidence for the 13
existing tests is being reclassified as contract-first evidence.

## 2. Canonical route decision

Two read routes coexist under `/api/paper-trading`:

| Route | Origin | Response shape | Status |
|---|---|---|---|
| `GET /postmortem/{trade_id}` | Sprint 1 | `PostmortemResponse` — basic deterministic analytics (`schema_version`, `outcome`, `realized_pnl_abs`, ...) | **Legacy, supported.** Not deprecated in Wave C. Existing clients may depend on this shape. |
| `GET /{trade_id}/current-report` | Wave C | `CurrentReportReadResponse` — persisted governed 1.2.0 report identity, claims, evidence, availability state | **Canonical for report schema 1.2.0.** |

No third competing read route is introduced. No deprecation of the
legacy route occurs in Wave C without explicit owner approval.

## 3. Frozen contract

- **Auth:** required on the canonical route; reuses the existing
  `get_current_user_id` dependency.
- **Ownership:** a trade belonging to another user is byte-for-byte
  indistinguishable from a nonexistent `trade_id` (404, same body) —
  identical convention to the legacy route.
- **Feature-disabled (final, WC-K-15 closed):** the endpoint reuses the
  single existing authoritative helper,
  `_trade_postmortem_price_path_enabled()` (reads
  `TRADE_POSTMORTEM_PRICE_PATH_ENABLED`, accepted values `1`/`true`/
  `yes`/`on`) — no second parser or independent flag source was
  introduced. Evaluation order is fixed: (1) authenticate, (2) look up
  the trade, (3) enforce ownership — a nonexistent trade and another
  user's trade return the byte-for-byte identical 404 regardless of
  capability state — and only *after* ownership is established, (4)
  evaluate the capability. When disabled, the endpoint returns a
  stable `FEATURE_DISABLED` availability state for an owned trade,
  exposing no report identity, structured report, claims, evidence,
  or version fields (all null), and performs no generation, recovery,
  provider acquisition, claim, settlement, or write of any kind.
  `FEATURE_DISABLED` is returned even when a 1.2.0 report already
  exists in the database for that trade (e.g. generated while the
  flag was previously on) — an existing row is never leaked once the
  capability is off. `FEATURE_DISABLED` is distinguishable from
  `NOT_ELIGIBLE` (open trade), `NOT_AVAILABLE` (closed trade, never
  requested), `PROCESSING`, and `TERMINAL_FAILURE` — it is checked
  before, and short-circuits, that entire state machine.
- **Current-version identity:** derived via
  `current_report_generation.current_target_identity()` — the same
  Wave B primitive used by the write path — never a fuzzy/latest
  lookup.
- **Side-effect prohibition:** GET never calls
  `process_current_report`, never acquires provider evidence, never
  claims/settles/inserts/updates an outbox row, never mutates a
  trade. Proven by 3 of the 13 existing tests
  (`test_get_never_calls_provider_acquisition`,
  `test_get_inserts_no_outbox_row_and_no_report_row`,
  `test_get_does_not_mutate_trade_row`).
- **Availability states:** `READY`, `PROCESSING`, `NOT_ELIGIBLE`,
  `NOT_AVAILABLE`, `TERMINAL_FAILURE`, `INTEGRITY_CONTRADICTION` —
  derived purely from persisted `paper_trades` /
  `paper_trade_postmortem_report` /
  `paper_trade_postmortem_outbox.status` state.
- **Caching:** not yet enforced with an explicit
  `Cache-Control: private, no-store` header — recorded as an open
  item (WC-K-11) for the completion pass.
- **Response typing:** current response model uses broad `dict`/
  `list`/`str` fields for structured report, claims, evidence, and
  source manifest — recorded as an open item (WC-K-01/03) for the
  completion pass; narrowing must not fabricate structure the
  persisted JSON doesn't guarantee.
- **Frontend route:** `/postmortem/[tradeId]`, not yet implemented —
  open (WC-N-03).
- **Paper Trading integration:** not yet implemented — open (WC-N-04).
- **Polling:** bounded, stops on READY/terminal/unmount — not yet
  implemented — open (WC-N-07).
- **Operations:** structured observability, runbook, capacity review —
  not yet implemented — open (WC-O-01..09).
- **Exclusions:** no PR, no merge, no Railway/Vercel production
  action, no flag activation, no production PostgreSQL access.

## 3a. WC-K executable freeze (Gate 3)

- **WC-K is FROZEN as of commit `7432851ea7affa7a80e8db131337763c7cbb69eb`**
  on `feature/trade-postmortem-sprint3a-price-path`. Only
  documentation-only commits may follow against this SHA for WC-K;
  any further behavioral change to the current-report read path
  requires a new, explicitly-labeled correction, not a silent edit
  under this freeze.
- **Closing real-PostgreSQL evidence:** GitHub Actions run
  [30804393885](https://github.com/shashiraj75/stocksense/actions/runs/30804393885)
  (jobs `postgres-integration (15)` id `91656249430`,
  `postgres-integration (17)` id `91656249507`), both `conclusion:
  success`. JUnit-verified (not terminal-summary-only) on both:
  `tests="314"`, `failures="0"`, `errors="0"`, `skipped="0"`.
  Artifacts `postgres-integration-results-pg15` (id `8852181028`) and
  `postgres-integration-results-pg17` (id `8852173779`), expiring
  2026-08-17.
- **Scope frozen:** Gate 1 (A4 typed
  `structured_report.price_path.version_and_provenance`, A5
  supersession proof, public `validate_merged_evidence_integrity`) and
  Gate 2 (availability-state matrix including active/expired-lease
  GENERATING and FAILED_RETRYABLE → PROCESSING, exact before/after
  DB-state snapshot proof across every governed state, deterministic-
  serialization proof, and proof GET never invokes any generation/
  acquisition/claim/recovery entry point) are both complete and
  green. WC-K-14 (provenance inventory) is CLOSED — see the dedicated
  ADR. This supersedes the "13 existing tests" figure in §1 above;
  WC-K now has 55 real-PostgreSQL tests in
  `test_current_report_read_api.py` (the original 13 plus subsequent
  sprints' additions, +6 from Gate 1, +10 from Gate 2), all green in
  the run cited above.
- **Not frozen:** WC-N (frontend) and WC-O (operations) remain fully
  open, tracked in §3 above, and are the subject of the next work
  package under this same governing prompt.

## 4. Acceptance criteria

Wave C may be classified COMPLETE only when every WC-K/WC-N/WC-O
requirement ID in the traceability manifest (to be created) is GREEN
with real evidence (PostgreSQL 15+17 for DB-dependent IDs, frontend
test/typecheck/build for frontend IDs), zero BLOCKING audit findings
remain open, and no production-facing action beyond the authorized
feature branch has occurred.
