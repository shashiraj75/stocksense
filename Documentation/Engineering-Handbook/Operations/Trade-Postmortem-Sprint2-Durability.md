# Trade Postmortem Sprint 2 — Durability Layer

**Status: dormant.** Neither feature flag (`TRADE_POSTMORTEM_DAILY_ENABLED`,
`NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED`) is enabled by this sprint.
This sprint adds infrastructure underneath Sprint 1's evidence model; it
does not turn anything on for users.

## What this sprint adds

Sprint 1 built the evidence-governance rules (claim/evidence model,
non-circular attribution, anti-fabrication tests). Sprint 2 makes that
model *durable*: one authoritative close path, immutable exit evidence,
crash-safe report generation, and versioned persisted reports — so a
report survives a process restart and is never silently recomputed
differently on every page load.

### 1. Authoritative close path — `services/postmortem/close_service.py`

Before this sprint, `POST /sell/{trade_id}` was the only code path that
closed a trade, and it did so as several independently-committed
statements (no explicit transaction wrapping the trade UPDATE and the
cash credit together). `close_paper_trade()` is now the single
authoritative close function:

1. Row-locks the trade (`SELECT ... FOR UPDATE`) inside one transaction.
2. Validates ownership, open status, and finite/positive inputs.
3. Computes P&L via the existing shared `paper_trade_math` functions
   (never recomputed independently).
4. Updates the trade, guarded by `WHERE status = 'OPEN'` as a
   defense-in-depth duplicate-close check beyond the row lock.
5. Writes one immutable exit snapshot.
6. Writes one idempotent report-generation outbox row.
7. Commits atomically.

`POST /sell/{trade_id}` (`paper_sell`) now delegates to this function,
crediting cash to the correct market's portfolio column inside the SAME
transaction `close_paper_trade` opened (a documented nested-transaction/
savepoint call pattern — see `close_paper_trade`'s own docstring).
Portfolio **reset** is a deliberately separate path — see below.

### 2. Immutable exit snapshots — `paper_trade_exit_snapshot`

The exit-time mirror of the existing `paper_trade_entry_snapshot`: one
row per close event, written once inside the close transaction, guarded
by the same `BEFORE UPDATE` rejection trigger pattern (`reject_paper_
trade_exit_snapshot_update` / `trg_paper_trade_exit_snapshot_immutable`).

Two classification axes are kept deliberately separate:

- `financial_outcome` (WIN/LOSS/BREAKEVEN/INDETERMINATE) — reuses
  `deterministic.Outcome` exactly.
- `closure_classification` (TRADING_EXIT/SYSTEM_EXIT/ADMINISTRATIVE_
  CLOSE/RESET_CLOSE/UNKNOWN_CLOSE) — *what kind of event* closed the
  trade, so a profitable reset is never presented as an ordinary trading
  win without also exposing that it was a reset.

**Immutability wording, precisely:** immutable-by-UPDATE during normal
lifecycle. Not "absolutely immutable" — an authorized portfolio reset
may still DELETE the row (see below), exactly like the entry snapshot
already can.

**Trust boundary, unchanged from the entry snapshot:** a browser-detected
stop-loss/target close is always `CLIENT_REPORTED_UNVERIFIED`. No
server-side trigger-detection evidence exists anywhere in this codebase
today, so this module has no path to promote it to `SERVER_VERIFIED`.

### 3. Report-generation outbox — `paper_trade_postmortem_outbox`

**Architecture decision: Option B** — safe on-request recovery plus
best-effort immediate post-commit processing. No new scheduler, worker
process, or lease-renewal daemon. No existing approved worker/lease
framework exists in this codebase to reuse without a much larger,
riskier change than this sprint's scope.

- Insertion is idempotent (`ON CONFLICT DO NOTHING` on `(paper_trade_id,
  requested_report_schema_version, requested_calculation_version,
  requested_rules_version)`), happens inside the same transaction as the
  close itself.
- Claiming (`claim_next_attempt`) is one atomic `UPDATE ... WHERE status
  = ANY(...) RETURNING ...` — PostgreSQL's own row-level locking is the
  entire concurrency guarantee, never a process-local lock.
- After `paper_sell` commits, `_attempt_best_effort_generation` tries to
  claim and generate immediately, strictly outside the close transaction
  — a generation failure can never undo or affect the response of an
  already-successful close.
- `POST /postmortem/{trade_id}/generate` and the existing `GET
  /postmortem/{trade_id}` are the on-request recovery paths for a row
  that's still `PENDING`/`FAILED_RETRYABLE`.

**Documented operational limitation:** there is no background sweep. A
trade whose report generation failed and whose owner never revisits it
sits in `FAILED_RETRYABLE`/`PENDING` indefinitely. A future sprint may
add a bounded periodic sweep once a real worker framework exists — this
is accepted for Sprint 2, not an oversight.

**Crash-safety window:** once a row is claimed (flipped to `GENERATING`),
`_attempt_best_effort_generation` guarantees it reaches a terminal or
`FAILED_RETRYABLE` mark before returning, using a fresh connection for
the recovery mark in case the original connection is unusable. Only a
hard process kill (not a Python exception) between the claim and that
recovery mark can leave a row stuck in `GENERATING` — a narrower,
honestly disclosed residual gap of the Option B design.

### 4. Versioned persisted reports — `paper_trade_postmortem_report`

"Current version identity" for a trade is the quadruple `(paper_trade_id,
report_schema_version, calculation_version, attribution_rules_version)`,
enforced by a UNIQUE index. `persist_report` uses `INSERT ... ON CONFLICT
DO NOTHING`, so two concurrent generation attempts for the identical
trade and identical versions can never both insert a row — the loser
reads back the winner's row. **A completed report row is never UPDATEd**
— a genuinely new rules/calculation version inserts a new row under its
own key, never overwrites the old one.

`structured_report`/`evidence_items`/`claims` are the authoritative JSON
representation — the same claim/evidence objects Sprint 1's attribution
engine produced, serialized without alteration (`build_evidence_
attribution`'s own referential-integrity check, which raises
`ReportIntegrityError` on any dangling reference, runs before
persistence — a broken report is never persisted).

### 5. Deterministic generation service — `services/postmortem/generation_service.py`

Split into a pure function (`build_report_payload` — no I/O, easily
unit-tested) and a persistence wrapper (`generate_and_persist` — the
only function that touches `conn`). Status determination:

- `LIMITED_EVIDENCE` when the entry snapshot is missing, the exit
  snapshot is missing (a historical trade closed before this sprint
  existed), or Sprint 1's own `evidence_completeness` is `LIMITED`.
- `COMPLETE` otherwise — this does NOT mean every individual claim is
  `EVIDENCE_SUPPORTED`; Sprint 1's per-claim evidence classes remain the
  fine-grained truth. `COMPLETE` is only the coarse top-level status.

### 6. `POST /postmortem/{trade_id}/generate`

Additive, single-trade generation trigger. User-scoped, idempotent (via
`persist_report`'s own `ON CONFLICT DO NOTHING`), never alters the trade,
never accepts or injects evidence from the request body. **Not** gated by
`TRADE_POSTMORTEM_DAILY_ENABLED` — that flag only governs the daily,
multi-trade aggregate surface. The existing `GET /postmortem/{trade_id}`
(Phase 1) is **completely unchanged** by this sprint — it still computes
fresh on every call and never reads or writes the persisted-report table,
per the "retain Phase 1 deterministic fallback" design choice (minimizes
regression risk to an already-shipped, already-tested endpoint).

## Reset semantics (unchanged contract, orphan-safe)

Portfolio reset's existing, already-shipped contract is **deletion** of a
user's trades and evidence — not administrative closure. This sprint
does not change that contract; it extends it so a reset never leaves an
orphaned exit-snapshot/outbox/report row behind:

- `market=ALL`: `paper_trade_postmortem_report`, `paper_trade_postmortem_
  outbox`, and `paper_trade_exit_snapshot` are deleted by `user_id`
  alone, before `paper_trades`, mirroring the existing entry-snapshot
  deletion.
- Market-specific reset: `paper_trade_postmortem_report` and
  `paper_trade_exit_snapshot` both carry their own `market` column and
  are filtered directly. `paper_trade_postmortem_outbox` has **no**
  `market` column (it's scoped only by `paper_trade_id`/`user_id`), so
  its rows are identified via a subquery against `paper_trades` — which
  must run *before* the `paper_trades` DELETE removes the rows that
  subquery depends on.

## Historical-trade compatibility

A trade closed before this sprint shipped has no exit snapshot. Calling
`POST /postmortem/{trade_id}/generate` (or letting on-request recovery
run) for such a trade produces a `LIMITED_EVIDENCE` report with an
explicit `evidence_gaps` entry — never a fabricated exit snapshot, never
a crash.

## Structured observability events

Privacy-safe (event tag + `trade_id`/`outbox_id` only — never a price,
P&L figure, symbol, evidence value, or raw exception message):

`trade_close_started`, `trade_close_completed`, `trade_close_duplicate`,
`exit_snapshot_created`, `postmortem_outbox_created`,
`postmortem_generation_started`, `postmortem_generation_completed`,
`postmortem_generation_limited`, `postmortem_generation_retryable_failure`,
`postmortem_generation_terminal_failure`.

## Known pre-existing limitation (not introduced or fixed by this sprint)

`paper_trade_entry_snapshot` and `paper_trade_idempotency_key` are not in
the existing `ENABLE ROW LEVEL SECURITY` list — a genuine pre-existing
gap, out of this sprint's scope (an unrelated change). All three NEW
Sprint 2 tables (`paper_trade_exit_snapshot`, `paper_trade_postmortem_
outbox`, `paper_trade_postmortem_report`) DO have RLS enabled.

## Migration safety

All three new tables use `CREATE TABLE IF NOT EXISTS` / `CREATE OR
REPLACE FUNCTION` / `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER` — the
same idempotent, guarded pattern every existing migration in
`services/postgres_store.py` uses. No backfill, no data migration, no
change to any existing table's columns.

## What Sprint 3 would still need (out of scope here)

Point-in-time price-path evidence (MFE/MAE), market/sector/volatility/
liquidity/news evidence acquisition, PDF/CSV export, a background outbox
sweep once a real worker framework exists, and enabling either feature
flag for real users.
