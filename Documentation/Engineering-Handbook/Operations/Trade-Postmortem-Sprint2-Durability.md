# Trade Postmortem Sprint 2 — Durability Layer

**Status: dormant.** Neither feature flag (`TRADE_POSTMORTEM_DAILY_ENABLED`,
`NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED`) is enabled by this sprint.
This sprint adds infrastructure underneath Sprint 1's evidence model; it
does not turn anything on for users.

**This document describes the corrected design**, after PR #33's initial
real-PostgreSQL CI run failed (see "PostgreSQL CI failure and correction"
below) and a subsequent correction phase addressed that defect plus
several design gaps found in independent review. Do not trust an earlier
copy of this document describing GET-triggered recovery, a process-local
GENERATING lock, or Boolean-only exit-evidence — all three were corrected
here.

## What this sprint adds

Sprint 1 built the evidence-governance rules (claim/evidence model,
non-circular attribution, anti-fabrication tests). Sprint 2 makes that
model *durable*: one authoritative close path, immutable exit evidence,
crash-safe report generation, and versioned persisted reports — so a
report survives a process restart and is never silently recomputed
differently on every page load.

### PostgreSQL CI failure and correction

PR #33's first real-PostgreSQL CI run (PostgreSQL 15 and 17, workflow run
30354562093) failed during the exit-snapshot INSERT: the column list in
`close_service._EXIT_SNAPSHOT_COLUMNS` had 24 entries but the hand-written
`VALUES (%s, %s, ...)` clause had only 23 placeholders — a manually
miscounted literal no mocked-connection test could ever catch, since a
mock never verifies bind-parameter count against a real server the way
PostgreSQL itself does. Fixed by deriving the placeholder string from
`len(values)` and asserting `len(columns) == len(values)` before the SQL
is ever built (`close_service._build_exit_snapshot_insert`) — a future
field addition that updates only one side now fails immediately, in
Python, before ever reaching a database.

### 1. Authoritative close path — `services/postmortem/close_service.py`

Before this sprint, `POST /sell/{trade_id}` was the only code path that
closed a trade, and it did so as several independently-committed
statements (no explicit transaction wrapping the trade UPDATE and the
cash credit together). `close_paper_trade()` is now the single
authoritative close function:

1. Row-locks the trade (`SELECT ... FOR UPDATE`) inside one transaction.
2. Validates ownership, open status, and finite/positive inputs —
   including a legacy/corrupt stored `entry_price` (`None`, NaN,
   Infinity): treated as a genuinely indeterminate P&L (`None`), never a
   crash and never silently coerced to a computed zero.
3. Computes P&L via the existing shared `paper_trade_math` functions
   (never recomputed independently).
4. Updates the trade, guarded by `WHERE status = 'OPEN'` as a
   defense-in-depth duplicate-close check beyond the row lock.
5. Writes one immutable exit snapshot.
6. Writes one idempotent report-generation outbox row.
7. Commits atomically.

`POST /sell/{trade_id}` (`paper_sell`) delegates to this function,
crediting cash to the correct market's portfolio column inside the SAME
transaction `close_paper_trade` opened (a documented nested-transaction/
savepoint call pattern — see `close_paper_trade`'s own docstring).
Portfolio **reset** is a deliberately separate path — see below.

**Indeterminate P&L, precisely:** `CloseResult.realized_pnl_pct`
preserves a genuine `None` when it cannot be reliably computed (e.g. a
legacy non-positive `entry_price`). The endpoint's JSON response applies
a "return 0" fallback ONLY at that HTTP boundary, to preserve `/sell`'s
own pre-existing response contract (locked in by
`test_paper_trading_postmortem_pnl_parity.py`) — the authoritative
`CloseResult` itself, and everything downstream of it (the persisted
report), never sees a fabricated zero standing in for "unknown."

**Authorization privacy:** the market-open preflight probe that runs
before `close_paper_trade` is user-scoped
(`WHERE id = %s AND user_id = %s`) — a trade owned by another user
returns the identical 404 a nonexistent trade_id returns, never the
market, existence, or open/closed state of a trade the caller doesn't
own. `close_paper_trade`'s own ownership check remains as defense in
depth for a same-request race, not as the primary authorization
boundary.

**Close idempotency (additive, backward-compatible):** `SellRequest`
accepts an optional `idempotency_key` field, mirroring `BuyRequest`'s own
established convention exactly and reusing the same
`services.postmortem.idempotency` engine with a DISTINCT operation type
(`OPERATION_TYPE_PAPER_SELL` — never collides with
`OPERATION_TYPE_PAPER_BUY` even for an identical key string and user). A
client that omits the key gets exactly today's behavior — no
response-loss dedup guarantee. **Documented limitation:** this guarantee
only applies when a caller actually supplies a key; the frontend's
existing auto-close callers do not yet supply one, so response-loss
idempotency for THOSE calls remains a future frontend change, not
something this backend addition alone provides end-to-end today.

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
best-effort immediate post-commit processing, now backed by a **genuine
database-backed lease** (correction phase), not merely an unbounded
GENERATING window. No new scheduler, worker process, or lease-renewal
daemon. No existing approved worker/lease framework exists in this
codebase to reuse without a much larger, riskier change than this
sprint's scope.

- Insertion (`close_service._insert_outbox_record`) is idempotent
  (`ON CONFLICT DO NOTHING` on `(paper_trade_id,
  requested_report_schema_version, requested_calculation_version,
  requested_rules_version)`), happens inside the same transaction as the
  close itself.
- `claimed_at` / `lease_expires_at` / `claimed_by` columns back a real
  lease. `claim_next_attempt` is one atomic `WITH claimable AS (...)
  UPDATE ... FROM claimable ...` statement that handles all three
  claimable cases at once — PENDING, an eligible FAILED_RETRYABLE row
  (backoff respected via `next_attempt_at`), or a GENERATING row whose
  lease has expired — and, in the SAME statement, settles the row
  FAILED_TERMINAL instead of claiming it if the attempt would exceed
  `MAX_ATTEMPTS_BEFORE_TERMINAL`. PostgreSQL's own row-level locking is
  the entire concurrency guarantee, never a process-local lock.
- Every terminal/retryable mark (`mark_terminal`, `mark_retryable_failure`,
  `mark_terminal_failure`) requires the caller's `claimed_by` token to
  still match the row's current lease holder, returning `False` (a safe
  no-op) rather than overwriting a fresher claimant's result if the
  lease was already reclaimed.
- After `paper_sell` commits, `_attempt_best_effort_generation` (via
  `_claim_and_run_generation`) tries to claim and generate immediately,
  strictly outside the close transaction — a generation failure can
  never undo or affect the response of an already-successful close. The
  claim UPDATE is an autocommitted statement, never wrapped in the same
  transaction as generation/persistence, so it is durably visible to
  other connections the instant it commits, independent of whether
  generation afterward ever completes.
- **`POST /postmortem/{trade_id}/generate` is THE explicit recovery
  mechanism** for a PENDING/eligible-FAILED_RETRYABLE/stale-GENERATING
  row. `GET /postmortem/{trade_id}` (Phase 1) is, and remains,
  completely read-only — it never claims, generates, or writes anything.
  (An earlier draft of this document incorrectly described GET as a
  recovery path; it never has been.)

**Documented operational limitation:** there is still no background
sweep. A row that reaches `FAILED_RETRYABLE` and whose owner never
revisits it (via another close attempt or `POST /generate`) sits there
until its own next natural trigger. A future sprint may add a bounded
periodic sweep once a real worker framework exists — this is accepted
for Sprint 2, not an oversight.

**Crash-safety, precisely:** a hard process kill between claim and mark
now leaves the row reclaimable once `LEASE_DURATION_SECONDS` (120s)
elapses — bounded, not permanent. This is a genuine improvement over the
original design (an unbounded GENERATING window), verified by both
mocked unit tests (in-memory lease-expiry simulation) and a real-
PostgreSQL test matrix.

### 4. Versioned persisted reports — `paper_trade_postmortem_report`

"Current version identity" for a trade is the quadruple `(paper_trade_id,
report_schema_version, calculation_version, attribution_rules_version)`,
enforced by a UNIQUE index. `persist_report` uses `INSERT ... ON CONFLICT
DO NOTHING`, so two concurrent generation attempts for the identical
trade and identical versions can never both insert a row — the loser
reads back the winner's row. **A completed report row is never UPDATEd**
by application code, and (correction phase) this is now also a
**database-level guarantee**: `trg_paper_trade_postmortem_report_
immutable` rejects any UPDATE, the same `BEFORE UPDATE` trigger pattern
the entry/exit snapshot tables already use. INSERT and DELETE remain
permitted (reset still works). A genuinely new rules/calculation version
inserts a new row under its own key, never overwrites the old one.

**Report/outbox consistency invariant:** a completed/limited-evidence
outbox row must never exist without a corresponding persisted report,
and vice versa. `generation_service.generate_and_persist` persists the
report and marks the outbox row terminal in the SAME transaction; if the
mark-terminal call fails because the lease was lost to a reclaimer
(`StaleLeaseError`), the whole transaction — including the report
INSERT — rolls back, so an abandoned attempt never leaves a dangling
report. `tests/postgres_integration/test_postmortem_durability.py`
includes the exact SQL a monitoring job would run to detect either
contradiction, exercised against real close+generate flows.

`structured_report`/`evidence_items`/`claims` are the authoritative JSON
representation — the same claim/evidence objects Sprint 1's attribution
engine produced, PLUS (correction phase) real exit-evidence claims (see
below), serialized without alteration (`build_evidence_attribution`'s
own referential-integrity check, plus a merged-set check covering the
added exit-evidence claims, both run before persistence — a broken
report is never persisted).

### 5. Actual exit-evidence integration (correction phase)

The original design only ever passed `exit_snapshot_present: bool` into
report generation — a report could say WHETHER an exit snapshot existed,
never WHAT it recorded. `services/postmortem/exit_evidence.py` now turns
an actual, typed `ExitSnapshot` into claim-level provenance using Sprint
1's governed vocabulary (`services.postmortem.evidence` —
`EvidenceItem`/`PostmortemClaim`/deterministic evidence IDs), covering:
financial outcome, closure classification, exit mechanism (both the
classified and raw values), exit price/quantity/timestamp, final
stop-loss/target/management-mode/levels-modified flag, and trigger
timing verification — each with an explicit source type, verification
level, and (where applicable) a limitation string. Fields the close
transaction itself computed and stored are `SERVER_STORED`/
`MECHANICALLY_VERIFIED`; browser-reported stop/target trigger timing
stays `CLIENT_REPORTED`/`UNVERIFIED` — never upgraded. A historical trade
with no exit snapshot produces a single `INSUFFICIENT_EVIDENCE` claim
using the exact fallback sentence, never a fabricated exit fact.
`source_manifest` now also records the actual `exit_snapshot_schema_
version` and `exit_trigger_timing_verification`, not only a `has_exit_
snapshot` Boolean — two different exit prices for the same trade produce
different claims/evidence_items, proven by dedicated tests.

### 6. Deterministic generation service — `services/postmortem/generation_service.py`

Split into a pure function (`build_report_payload` — no I/O, easily
unit-tested) and a persistence wrapper (`generate_and_persist` — the
only function that touches `conn`). Status determination:

- `LIMITED_EVIDENCE` when the entry snapshot is missing, the exit
  snapshot is missing (a historical trade closed before this sprint
  existed), or Sprint 1's own `evidence_completeness` is `LIMITED`.
- `COMPLETE` otherwise — this does NOT mean every individual claim is
  `EVIDENCE_SUPPORTED`; Sprint 1's per-claim evidence classes remain the
  fine-grained truth. `COMPLETE` is only the coarse top-level status.

### 7. `POST /postmortem/{trade_id}/generate` (rewritten, correction phase)

The full outbox-aligned lifecycle:

1. Finds or idempotently creates an outbox row for the CURRENT version
   triple (`close_service._insert_outbox_record`) — covers the
   historical-trade case (no outbox row existed yet).
2. If a persisted report already exists for that version triple, returns
   it verbatim (`generation_status: "ALREADY_COMPLETE"`, `generated:
   false`) — never re-generates.
3. Otherwise claims via the lease-safe `claim_next_attempt` and
   generates. Returns `generation_status: "GENERATED"` on success.
4. If another valid, non-expired lease currently owns the row, returns a
   stable `generation_status: "GENERATION_IN_PROGRESS"` response —
   **never** runs concurrent duplicate generation.
5. If the claim itself settled the row `FAILED_TERMINAL` (attempt limit
   exceeded), returns `500 GENERATION_ATTEMPTS_EXHAUSTED` — an honest
   terminal failure, never an infinite retry loop.

User-scoped, never alters the trade, never accepts or injects evidence
from the request body. **Not** gated by `TRADE_POSTMORTEM_DAILY_ENABLED`
— that flag only governs the daily, multi-trade aggregate surface. The
existing `GET /postmortem/{trade_id}` (Phase 1) remains **completely
unchanged and read-only** — see the outbox section above for why an
earlier draft's "GET-triggered recovery" description was wrong and has
been corrected.

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
- The new immutability triggers on `paper_trade_exit_snapshot` and
  `paper_trade_postmortem_report` reject UPDATE only — DELETE remains
  permitted for exactly this reset path, verified by a dedicated
  real-PostgreSQL test.

## Historical-trade compatibility

A trade closed before this sprint shipped has no exit snapshot. Calling
`POST /postmortem/{trade_id}/generate` for such a trade produces a
`LIMITED_EVIDENCE` report with explicit `evidence_gaps` and an
`INSUFFICIENT_EVIDENCE` exit-evidence claim — never a fabricated exit
snapshot, never a crash.

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

All three new tables (and the new immutability trigger on
`paper_trade_postmortem_report`, and the new lease columns on
`paper_trade_postmortem_outbox`) use `CREATE TABLE IF NOT EXISTS` /
`CREATE OR REPLACE FUNCTION` / `DROP TRIGGER IF EXISTS` + `CREATE
TRIGGER` — the same idempotent, guarded pattern every existing migration
in `services/postgres_store.py` uses. Because none of this schema has
ever been deployed (PR #33 is unmerged), the lease columns and the
report-immutability trigger were added directly to the `CREATE TABLE`/
migration block rather than as a separate guarded `ALTER` — there is no
production data to migrate around.

## What Sprint 3 would still need (out of scope here)

Point-in-time price-path evidence (MFE/MAE), market/sector/volatility/
liquidity/news evidence acquisition, PDF/CSV export, a background outbox
sweep once a real worker framework exists, frontend wiring of the new
`idempotency_key` field for auto-close callers, and enabling either
feature flag for real users.
