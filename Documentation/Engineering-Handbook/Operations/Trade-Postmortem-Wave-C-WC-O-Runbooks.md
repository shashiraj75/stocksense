# Trade Postmortem — Wave C, WC-O Runbooks

**Status: dormant.** Neither `TRADE_POSTMORTEM_PRICE_PATH_ENABLED` nor
`NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED` is enabled in production
by this document. These runbooks describe the procedures an operator
follows once Owner Gate 1 (merge and dark deploy) and Owner Gate 2
(activate Postmortem beta) are separately approved — nothing here
authorizes performing any of these actions now.

Frozen references: WC-K executable SHA
`7432851ea7affa7a80e8db131337763c7cbb69eb`; WC-N executable SHA
`d9096a9b16c2267a5cd6c4fac866467acffe07df`. Metrics/log lines referenced
below are defined in `Trade-Postmortem-Wave-C-WC-O-Observability-And-Capacity-Review.md`.

## 1. Dark deployment

Merge and deploy with both flags left at their default (unset/disabled).
Verify:

1. `GET /api/paper-trading/{trade_id}/current-report` for any closed
   trade returns `availability: "FEATURE_DISABLED"` (confirms the
   backend flag is off in this environment — see runbook 2 below for
   the full verification procedure).
2. The frontend "View Postmortem" link does not render on any closed
   trade row (confirms `NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED`
   is unset in the deployed build).
3. `[outbox_worker] started` does NOT appear in application logs at
   startup (the worker is only started when the backend flag is
   enabled — its absence confirms no background work is running).
4. No new error-rate or latency change on `/api/paper-trading/*` routes
   generally (a dark deploy should be invisible to existing traffic).

## 2. Feature-disabled production verification

Before enabling anything, confirm the dormant state is genuinely
dormant:

1. Call the current-report GET endpoint for a real closed trade with a
   valid session token; confirm `availability: "FEATURE_DISABLED"` and
   that `structured_report`/`claims`/`evidence_items`/`source_manifest`
   are all `null` (never a stale value from a prior enabled window).
2. Confirm no outbox rows are being created: `close_service.py`'s
   `insert_current_outbox_record` call is itself gated by the same
   flag at the call site in `paper_trading.py` — a `SELECT count(*) FROM
   paper_trade_postmortem_outbox WHERE created_at > now() - interval '1
   hour'` should show zero growth attributable to this feature.
3. Confirm the outbox worker task is not running (no
   `[outbox_worker_queue_depth]` log lines appearing).

## 3. Enablement (backend)

1. Set `TRADE_POSTMORTEM_PRICE_PATH_ENABLED=1` in the target
   environment's variables (owner-approved change, not performed by
   this document).
2. Restart/redeploy so `api/main.py`'s lifespan startup re-evaluates the
   flag and calls `start_outbox_worker` (best-effort — failure to start
   is logged, never blocks app startup).
3. Confirm `[outbox_worker] started` appears in logs.
4. Confirm one real closed trade transitions from `NOT_AVAILABLE` (or
   `PROCESSING`, if a request was already in flight) to `READY` within
   a few worker poll cycles (~30-90s), by polling the GET endpoint
   directly (not via the frontend) and inspecting
   `[outbox_worker_queue_depth]` log lines for `pending` decreasing.

## 4. Enablement (frontend)

1. Set `NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED=true` at build
   time (this is a build-time env var — it requires a rebuild/redeploy
   of the frontend, not just a runtime toggle).
2. Confirm the "View Postmortem" link appears on closed trades and does
   not appear on open trades.
3. Confirm `/postmortem/[tradeId]` renders one of the seven governed
   availability states explicitly for a real trade ID (never a blank
   page or an unhandled error).

## 5. Controlled beta

1. Enable the backend flag first, frontend flag second (backend-first
   ordering means the API is ready before any user-facing entry point
   exists — the reverse order would surface a link that immediately
   404s or shows `FEATURE_DISABLED`, a confusing user experience even
   though it fails safely).
2. Restrict the beta population via whatever mechanism the deployment
   platform already supports for gradual rollout (this document does
   not introduce a new per-user flag — Wave C's flags are global,
   environment-level toggles only).
3. Monitor the thresholds in runbook 6 for the beta's duration before
   considering a wider rollout — that is itself an owner decision, not
   automated by anything in this pass.

## 6. Monitoring thresholds

Watch these `[outbox_worker_queue_depth]` fields (logged once per
30-second poll cycle) and the corresponding metrics counters:

| Signal | Where | Investigate when |
|---|---|---|
| `pending` count | queue-depth log line | trending upward across consecutive cycles (backlog growing faster than the ~20 rows/minute processing ceiling — see capacity review) |
| `generating_expired_lease` count | queue-depth log line / `[outbox_worker_expired_lease]` warning | non-zero and not shrinking on the next cycle (a worker that claimed a row is not completing it within the lease window — possible stuck/crashed processing) |
| `failed_terminal` count | queue-depth log line / `[outbox_worker_terminal_failure_backlog]` warning | any sustained non-zero count (see runbook 11, "Report-generation failure") |
| `COUNTER_INTEGRITY_CONTRADICTION_DETECTED` | `[metrics]` log line | any occurrence at all (see runbook 9) |
| `COUNTER_WORKER_LOOP_CRASHED` | `[metrics]` log line | any occurrence (see runbook 8) |
| `COUNTER_PROVIDER_ACQUISITION_FAILURE` rate | `[metrics]` log line | a spike relative to request volume (see runbook 10, "Provider storm") |
| `COUNTER_AVAILABILITY_TERMINAL_FAILURE` rate | `[metrics]` log line | a spike in user-facing terminal failures |

No dashboard is created by this pass — these are log-line signals an
operator (or a future, separately-approved log-alerting rule) watches
directly.

## 7. Immediate disablement

Set `TRADE_POSTMORTEM_PRICE_PATH_ENABLED` back to unset/`0` and
redeploy. This is safe at any time:

- The GET endpoint immediately starts returning `FEATURE_DISABLED` for
  every request, regardless of any in-progress generation (ownership/404
  checks still run first, so this never leaks existence information).
- `stop_outbox_worker` is called from the lifespan shutdown path;
  restarting the process with the flag off means `start_outbox_worker`
  is never called on the next startup.
- No data is deleted or rolled back — any reports already generated
  remain persisted (immutable, per the DB trigger) and become visible
  again immediately if the flag is re-enabled later.
- The frontend flag should also be reverted (requires a rebuild) so the
  "View Postmortem" link disappears; leaving it on with the backend
  flag off is safe but confusing (the link would lead to a page showing
  `FEATURE_DISABLED`), not incorrect.

## 8. Rollback

If a genuine defect is found post-enablement:

1. Perform immediate disablement (runbook 7) first — this stops new
   impact instantly without waiting for a code rollback.
2. Roll back the deployed code to the last known-good commit on
   `feature/trade-postmortem-sprint3a-price-path` (or `main`, once
   merged) using the normal deployment platform rollback mechanism.
3. Because `paper_trade_postmortem_report` rows are immutable
   (INSERT-only, DB-trigger-enforced) and the outbox table only ever
   gains new rows or transitions existing ones through its own status
   machine, a code rollback does not require a data rollback — the
   worst case is a partially-processed outbox batch, which the next
   worker start (after roll-forward) picks back up via the same
   `FOR UPDATE SKIP LOCKED` claim query, never double-processing a
   row already marked `GENERATING` with a live lease.
4. If the worker loop itself crashed (`COUNTER_WORKER_LOOP_CRASHED`),
   confirm the crash cause before re-enabling — restarting the process
   with the same defect present will only crash again.

## 9. Integrity contradiction

Triggered by: `COUNTER_INTEGRITY_CONTRADICTION_DETECTED` incrementing,
or a user-facing `INTEGRITY_CONTRADICTION` availability response.

1. This state means a terminal-success outbox row exists with no
   matching persisted report — by design, the system refuses to
   silently self-heal by regenerating a second "success" (that would
   fabricate a result). It requires manual operator judgment.
2. Identify the affected `outbox_id`/`trade_id` from the
   `current_report_generation: integrity contradiction — outbox_id=%s`
   log line.
3. Inspect the exact outbox row (`status`, `completed_at`,
   `attempt_count`) and confirm no report row exists at that identity
   (`paper_trade_postmortem_report` filtered by `paper_trade_id`,
   `report_schema_version`, `calculation_version`,
   `attribution_rules_version`).
4. Do not manually flip the outbox row's status back to `PENDING` to
   force a retry without understanding how it reached this state first
   — that could mask a real defect (e.g. a crash between the report
   INSERT and the outbox settle UPDATE, which the same-transaction
   design in `persist_current_report` is meant to prevent — this state
   occurring at all is itself evidence worth investigating, not merely
   working around).

## 10. Queue backlog / provider storm

**Queue backlog** (`pending` count trending upward): first confirm the
worker is actually running (`[outbox_worker] started` in logs, no
`COUNTER_WORKER_LOOP_CRASHED`). If running but falling behind demand,
either raise `CLAIM_BATCH_SIZE`/lower `POLL_INTERVAL_SECONDS` (requires
a code change and redeploy — these are named constants in
`outbox_worker.py`, not runtime-configurable) or run additional worker
replicas — the `FOR UPDATE SKIP LOCKED` claim query is already
multi-replica-safe.

**Provider storm** (a spike in `COUNTER_PROVIDER_ACQUISITION_FAILURE`):
this indicates the upstream market-data provider is failing or
rate-limiting acquisition calls. Each failure already routes the row to
`FAILED_RETRYABLE` (not `FAILED_TERMINAL`) via
`outbox_ops.mark_retryable_failure`, so the system self-throttles
retries naturally through the existing backoff (`next_attempt_at`).
Immediate disablement (runbook 7) is the fastest way to stop generating
more provider load if the storm is severe; otherwise, monitor
`failed_retryable` count until the provider recovers.

## 11. Report-generation failure

Triggered by: `COUNTER_AVAILABILITY_TERMINAL_FAILURE` or
`failed_terminal` backlog growing (`[outbox_worker_terminal_failure_backlog]`).

1. A `FAILED_TERMINAL` row means `MAX_ATTEMPTS_BEFORE_TERMINAL` (5) was
   exceeded — the system will never retry it automatically.
2. Identify the affected trades and the `last_error_code`/
   `last_error_summary` columns on the outbox row for the actual failure
   reason (never logged in full elsewhere, to keep log lines bounded —
   see the observability review's log-cardinality section).
3. If the root cause is fixed (e.g. a provider outage that has since
   resolved, or a code defect that has since been corrected and
   deployed), a `FAILED_TERMINAL` row can be manually reset to `PENDING`
   with `attempt_count` reset to 0 by an operator with direct database
   access — this is a deliberate manual action, not something any code
   path in this feature performs automatically, since automatically
   retrying a terminal failure indefinitely was the exact failure mode
   `MAX_ATTEMPTS_BEFORE_TERMINAL` exists to prevent.

## 12. Frontend polling failure

Triggered by: user reports of a postmortem page stuck on "Generating…"
indefinitely, or the bounded-timeout message ("taking longer than
expected") appearing frequently.

1. Confirm via the backend GET endpoint directly (bypassing the
   frontend) what availability state the trade is actually in — the
   frontend's bounded polling (30 attempts, ~120s) stopping and showing
   the timeout message is expected, correct behavior if generation is
   genuinely still `PROCESSING` after two minutes; it is not itself a
   frontend defect.
2. If the backend shows `TERMINAL_FAILURE` or `INTEGRITY_CONTRADICTION`
   but the frontend appeared stuck on "Generating…", confirm the
   deployed frontend build is the WC-N-closed version (`d9096a9` or
   later) — bounded polling's fix for the "stale PROCESSING data
   masking a request error" defect (WC-N closure pass) is required for
   correct behavior; an older build could exhibit exactly this symptom.
3. If a 401/403/404 is involved, confirm the user's session is valid
   and the trade ID belongs to them — the frontend's `retry: false`
   policy means these show the generic "Could not load this report"
   message immediately rather than retrying, by design.
