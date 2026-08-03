# Trade Postmortem — Wave C, WC-O Observability and Capacity Review

**Status: infrastructure added and dormant.** Neither
`TRADE_POSTMORTEM_PRICE_PATH_ENABLED` nor
`NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED` is activated by this
document. This pass adds in-process metrics, structured log visibility,
and a capacity/timeout/shutdown review for the already-frozen WC-K
executable path (commit `7432851ea7affa7a80e8db131337763c7cbb69eb`) and
the WC-N frontend (commit `d9096a9b16c2267a5cd6c4fac866467acffe07df`) —
it activates nothing.

## 1. Metrics — `backend/services/postmortem/current_report_metrics.py`

No third-party metrics framework (Prometheus/StatsD/OpenTelemetry/Datadog)
exists anywhere in this codebase — confirmed by inspection before writing
this module, matching the same finding `idempotency_metrics.py` already
documented for the Buy-idempotency phase. This module follows that exact
established pattern: a thread-safe in-process counter/duration store, plus
a structured `log.info("[metrics] ...")` line at every increment — this
repository's actual existing observability mechanism, not a new one.
`get_snapshot()` exposes read-only state; wiring it to a new diagnostic
endpoint is explicitly out of scope for this pass (a product-surface
decision, not an engineering default).

### Availability-state counts

`GET /api/paper-trading/{trade_id}/current-report`
(`get_current_governed_report`, `backend/api/routers/paper_trading.py`)
calls `current_report_metrics.record_availability(...)` at every return
point, keyed by the exact `CurrentReportAvailability` value returned:
`COUNTER_AVAILABILITY_READY`, `_PROCESSING`, `_NOT_ELIGIBLE`,
`_NOT_AVAILABLE`, `_TERMINAL_FAILURE`, `_INTEGRITY_CONTRADICTION`,
`_FEATURE_DISABLED`. `record_availability` looks the state up in a single
shared table (`_AVAILABILITY_COUNTER_BY_STATE`) and raises `KeyError` on
an unrecognized value rather than silently leaving it uncounted — a
future new availability value added to the Literal without a matching
table entry fails loudly in tests, not silently in production.

Privacy-safe by construction: every call site passes only the counter
name (a fixed string from the module's own constants) — never `trade_id`,
`user_id`, prices, P&L, claim text, or evidence content.

### Generation/worker visibility

- `COUNTER_INTEGRITY_CONTRADICTION_DETECTED` — incremented in
  `current_report_generation.process_current_report` at the exact point
  (Gate J4F) where a terminal-success outbox row has no matching report.
- `COUNTER_PROVIDER_ACQUISITION_FAILURE` — incremented at the existing
  `except Exception` around `acquire_evidence_outside_transaction`.
- `COUNTER_WORKER_CLAIM_BATCH_FAILURE` — incremented when
  `claim_next_current_outbox_batch` raises (database outage/transient
  error).
- `COUNTER_WORKER_ROW_PROCESSING_FAILURE` — incremented when a single
  claimed row's `process_current_report` call raises (per-row isolation
  is preserved; this does not abort the rest of the batch).
- `COUNTER_WORKER_POLL_CYCLE_FAILURE` — incremented when an entire poll
  cycle raises inside `_worker_loop`.
- `COUNTER_WORKER_LOOP_CRASHED` — incremented in the task's own
  done-callback when the worker loop task itself ends with an unhandled
  exception (distinct from a single poll-cycle failure, which the loop
  already survives — this counts the loop dying outright).

### Outbox backlog, expired-lease, and terminal-failure visibility

`outbox_worker._log_queue_depth_snapshot`, called once per poll cycle
(every `POLL_INTERVAL_SECONDS` = 30s) from `_poll_once`, is a single
read-only `SELECT ... FILTER (WHERE ...)` query — never part of the
claim/settlement transaction, so it can never add lock contention or
affect claim correctness — that reports, for the current governed
version identity:

- `pending` — rows never yet attempted;
- `generating_active` — rows currently leased with a non-expired lease;
- `generating_expired_lease` — rows whose lease has expired and are
  reclaimable on the next batch (logged as a distinct `[outbox_worker_expired_lease]`
  warning line when non-zero);
- `failed_retryable` — rows awaiting their next retry attempt;
- `failed_terminal` — rows that exhausted `MAX_ATTEMPTS_BEFORE_TERMINAL`
  (5, `outbox.py`) and will never be retried automatically (logged as a
  distinct `[outbox_worker_terminal_failure_backlog]` warning line when
  non-zero).

This gives backlog growth, expired-lease accumulation, and terminal-
failure accumulation full log-line visibility with no dashboard required
to notice a trend — every number is in one structured
`[outbox_worker_queue_depth]` line per poll cycle.

### What was deliberately NOT added this pass

A per-row `DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT` sample (time from
outbox-row creation to terminal settlement) was scoped but not wired into
the write path itself: doing so would require an extra read inside
`persist_current_report`'s existing short atomic transaction (Gate 4's
explicit instruction is not to change frozen WC-K behavior without a
genuine integration blocker, and this is observability-only, not a
blocker). The constant `DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT` is defined
and unit-tested so a future pass can wire it in without renaming anything
already referenced by a dashboard or alert.

## 2. Log cardinality review

Every existing and newly-added structured log line in the postmortem
feature uses a bounded label set: `trade_id`/`outbox_id` (necessary,
high-cardinality but already the accepted convention throughout this
codebase — e.g. `close_service.py`'s `[trade_close_completed]`), fixed
counter/metric names, and bounded enum-like values (`status`,
`error_code`, market). No log line in this feature ever includes: raw
claim text, evidence values, prices, P&L amounts, or full exception
messages/stack traces (every `except Exception` block logs a fixed,
descriptive string plus the bounded identifiers above — never
`str(exc)`). This matches the pre-existing discipline documented in
`idempotency_metrics.py`'s own module docstring and was not weakened by
this pass.

## 3. Capacity assessment

Real configured values (not invented for this document):

- **Database connection pool**: `psycopg_pool.ConnectionPool(min_size=1,
  max_size=10, autocommit=True, prepare_threshold=None)`
  (`paper_trading.py`). The outbox worker's `conn_factory` draws from the
  same pool as the read/write API routes — a worker batch of
  `CLAIM_BATCH_SIZE=10` rows processed sequentially (never all 10
  connections held concurrently; `_process_claimed_row` opens and closes
  its own connection per row via `process_current_report`'s own phase
  boundaries) does not itself exhaust the pool, but a burst of
  concurrent user-facing GET/POST requests during a worker batch could
  approach the 10-connection ceiling. No change is made here; this is
  recorded as a capacity fact for the owner to weigh before enabling at
  scale, not an engineering default to raise.
- **API rate limit**: `USER_DATA_RATE_LIMIT = "60/minute"`
  (`services/rate_limit.py`), applied to paper-trading routes including
  the current-report GET — the frontend's bounded polling (30 attempts *
  4s = ~120s, at most one request per 4s) stays well under this limit
  for a single user; it does not protect against many users polling
  many trades simultaneously, which is a capacity question for the
  connection pool above, not the rate limiter.
- **Worker throughput**: `CLAIM_BATCH_SIZE=10` rows per
  `POLL_INTERVAL_SECONDS=30` cycle = a sustained ceiling of ~20
  rows/minute per worker replica under this configuration. A queue
  backlog growing faster than that (visible directly in the
  `[outbox_worker_queue_depth]` `pending` count trending upward across
  consecutive log lines) is the signal to either raise `CLAIM_BATCH_SIZE`,
  shorten `POLL_INTERVAL_SECONDS`, or run more than one worker replica
  (the `FOR UPDATE SKIP LOCKED` claim query is already multi-replica-safe
  by design).

## 4. Timeout and graceful-shutdown review

- **In-flight generation cannot be forcibly cancelled**:
  `process_current_report` runs inside `asyncio.to_thread`, which cannot
  be interrupted once started (the OS thread keeps running regardless of
  any `asyncio.Task.cancel()`). `stop_outbox_worker` already accounts for
  this correctly: it uses `asyncio.shield` so its own timeout
  (`WORKER_LEASE_DURATION_SECONDS + POLL_INTERVAL_SECONDS` = 150s) never
  cancels the underlying task — a timeout here only means "we stopped
  waiting," never "the worker actually stopped." State is cleared exactly
  once, by the task's own done-callback, whenever the task genuinely
  finishes. This review confirms that design is sound and adds no change
  to it (changing it would touch WC-K-adjacent frozen behavior without a
  genuine blocker).
- **In-flight HTTP request draining**: no explicit drain logic exists
  beyond FastAPI/uvicorn's own default shutdown handling. This is a
  pre-existing, feature-agnostic property of the whole API process, not
  specific to the postmortem feature — recorded here as an observed fact,
  not a gap this pass introduces or is scoped to fix.
- **Worker crash recovery**: a worker loop task that crashes outright
  (rather than surviving via its own per-cycle `except Exception`) is now
  visible via `COUNTER_WORKER_LOOP_CRASHED` and the
  `[outbox_worker] worker loop task ended with an unhandled exception`
  log line (pre-existing log line; metric is new). Recovery itself is
  operational (see the Runbooks document, "Queue backlog" and "Worker
  crash" sections) — restarting the API process re-enters
  `api/main.py`'s lifespan startup, which re-calls `start_outbox_worker`
  when the flag is enabled.

## 5. Related documents

- Runbooks: `Trade-Postmortem-Wave-C-WC-O-Runbooks.md` (this same
  directory).
- WC-K freeze record: `Documentation/Engineering-Handbook/ADR/Trade-Postmortem-Wave-C-API-Frontend-Operations-Contract.md`, §3a.
