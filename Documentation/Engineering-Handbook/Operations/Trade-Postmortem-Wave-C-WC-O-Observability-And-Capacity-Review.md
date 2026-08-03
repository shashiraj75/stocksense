# Trade Postmortem — Wave C, WC-O Observability and Capacity Review

**Status: infrastructure added and dormant, corrected.** Neither
`TRADE_POSTMORTEM_PRICE_PATH_ENABLED` nor
`NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED` is activated by this
document. This is the corrected version of the initial WC-O pass
(commit `aedbf41e745c691284a4977c4aa1402f41f7bf0f`) after Package O
review found the initial observability foundation needed to be made
fail-open, age-aware, cost-bounded, and noise-bounded before WC-O could
close — see §0 for what changed.

## 0. What this correction changed

The initial pass (`aedbf41`) added counters and one log line per worker
poll cycle, but:

- a metrics-store failure could propagate into application logic (not
  actually fail-open, despite the intent stated in that commit);
- the queue-depth query conflated a read concern with a logging concern
  in one function, was untestable in isolation, and had no age fields;
- `DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT` was defined but never actually
  measured anywhere — a metric name implying a measurement the system
  did not produce;
- the queue-depth log line and its warnings fired every single
  30-second poll cycle indefinitely, with no bound;
- provider acquisition had a failure counter but no attempt/success
  counters, so no failure RATE could be computed;
- this document overstated confidence about query cost and beta
  restriction without evidence.

Each of these is corrected below.

## 1. Fail-open metrics — `current_report_metrics.py`

`increment`/`record_duration`/`record_availability` remain the raw,
throwing primitives (used only by this module's own tests). Every
application call site (the current-report GET handler,
`current_report_generation.py`, `outbox_worker.py`) now uses the
`safe_*` wrappers instead: `safe_increment`, `safe_record_duration`,
`safe_record_availability`, `safe_timed`. Each:

- records normally when the underlying store and the value are both
  valid;
- for `safe_record_availability`, logs one bounded warning
  (`[metrics] unrecognized availability value for recording: %s`) and
  returns without raising when given a value with no matching counter —
  never a silently-uncounted value, never a propagated `KeyError`;
- for any other internal failure (the in-process store itself raising),
  logs one bounded warning
  (`[metrics] internal failure recording a metric from %s — application
  behavior unaffected`) naming only the calling function — NEVER the
  caught exception's own text, since a future defect's exception could
  in principle embed data this module has no way to pre-vet;
- never raises, under any internal failure mode, back into its caller.

`safe_timed` is a partial exception: it does NOT suppress an exception
raised by the code it wraps — only the timing/recording itself is
fail-open. A real application error inside `with safe_timed(...):` still
propagates normally (proven by
`test_current_report_metrics.py::TestSafeWrappersAreFailOpen::test_safe_timed_still_propagates_the_wrapped_block_s_own_exception`).

Proof (unit-level, deterministic, no PostgreSQL needed):
`tests/unit/test_current_report_metrics.py::TestSafeWrappersAreFailOpen`
forces the underlying store to raise via monkeypatching and confirms
every `safe_*` function still returns normally;
`tests/unit/test_outbox_worker_metrics_fail_open.py` forces BOTH
`process_current_report` and the metrics store to fail simultaneously
and confirms per-row batch isolation is unaffected (a second row in the
same batch is still attempted after the first row's processing AND its
metrics recording both fail).

## 2. Queue-health snapshot — `outbox_queue_health.py`

Split into two deliberately separate, independently-testable concerns
(previously conflated in one `_log_queue_depth_snapshot` function):

1. `read_queue_health_snapshot(conn, ...)` — a PURE read-only query
   returning a typed `QueueHealthSnapshot` dataclass. Never mutates,
   claims, leases, settles, or repairs anything. Filters by the exact
   governed version identity. Read BEFORE the claim in each
   `outbox_worker._poll_once` cycle, so the numbers reflect the backlog
   state actually observed at cycle start, not state already mutated by
   that same cycle's own claim.
2. `QueueHealthLogState` + `apply_transition_and_log(...)` — the
   noise-bounding policy (§4), kept separate so it is testable with an
   injectable clock, with no database involved at all.

Fields, with exact timestamp semantics (never inferred from
`next_attempt_at`, which represents FUTURE retry eligibility, not time
already spent waiting):

| Field | Meaning | Age source |
|---|---|---|
| `pending` | count, `status='PENDING'` | — |
| `generating_active` | count, `GENERATING` with a non-expired (or NULL) lease | — |
| `generating_expired_lease` | count, `GENERATING` with `lease_expires_at <= now()` | — |
| `failed_retryable` | count, `status='FAILED_RETRYABLE'` | — |
| `failed_terminal` | count, `status='FAILED_TERMINAL'` | — |
| `oldest_pending_age_seconds` | oldest PENDING row's age | `now() - created_at` |
| `oldest_generating_age_seconds` | oldest GENERATING row's age | `now() - claimed_at` (rows with a NULL `claimed_at` are excluded, not given a fabricated fallback — the claim query always sets `claimed_at` on every GENERATING transition, so no legitimate historical case produces this) |
| `oldest_failed_retryable_age_seconds` | oldest FAILED_RETRYABLE row's age | `now() - last_attempt_at` (same no-fallback rationale — `mark_retryable_failure` always sets it) |
| `generating_over_poll_window_count` | count of GENERATING rows claimed more than `FRONTEND_POLL_WINDOW_SECONDS` (120s, matching WC-N's bounded polling window) ago | `claimed_at` |

Every count is exactly zero and every age is `None` (never a fabricated
zero) when no row matches — proven by
`tests/postgres_integration/test_outbox_queue_health.py::test_null_ages_and_zero_counts_when_a_category_is_empty`.

`DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT` (the unmeasured metric name from
the initial pass) has been REMOVED, not renamed-and-kept-unused. If
settlement-age measurement is added in a future pass, it needs a new
name and an actual wiring into `persist_current_report`'s transaction —
not a name kept alive without a measurement behind it.

## 3. Query cost evidence

`read_queue_health_snapshot`'s query filters by
`(requested_report_schema_version, requested_calculation_version,
requested_rules_version)`. Existing indexes:
`idx_paper_trade_pm_outbox_unique (paper_trade_id,
requested_report_schema_version, requested_calculation_version,
requested_rules_version)` and `idx_paper_trade_pm_outbox_claim (status,
next_attempt_at)`. Neither serves this query's predicate as a
leading-column index range scan (the unique index leads with
`paper_trade_id`, which this query does not filter on; the claim index
leads with `status`, which this query aggregates over rather than
filters a single value of).

**No new index is added by this pass.** At the row volumes the §5
(§O9) evidence-based beta boundary implies — a global, low-traffic
activation, meaning at most a few thousand outbox rows for the current
governed version identity during the beta observation window — a
sequential-scan aggregate over this table is expected to complete in
single-digit milliseconds. This conclusion should be revisited if the
outbox table's row count for a single governed version identity exceeds
roughly 50,000 rows; at that scale, the smallest additive index matching
this query's actual predicates would be `CREATE INDEX IF NOT EXISTS
idx_paper_trade_pm_outbox_version_status ON
paper_trade_postmortem_outbox (requested_report_schema_version,
requested_calculation_version, requested_rules_version, status)`.

EXPLAIN evidence: captured via the real-PostgreSQL CI dispatch for this
correction (see the WC-O correction SHA's PostgreSQL run, recorded in
this document's own commit history / PR description once opened) rather
than asserted as a brittle exact-plan unit test — the test suite
(`test_outbox_queue_health.py`) proves query CORRECTNESS (exact counts,
isolation, ages, no mutation) on real PostgreSQL, not a specific plan
shape, which is appropriately left to be re-evaluated as the table
grows rather than pinned now.

## 4. Noise-bounded logging

`QueueHealthLogState` (process-local, never shared across replicas,
reset on every genuinely NEW worker task start — including a
stop_outbox_worker/start_outbox_worker cycle within the SAME process,
per Package O §5's correction — as well as on a full process restart)
implements:

- **Heartbeat**: the `[outbox_worker_queue_depth]` line is emitted at
  most once per `HEARTBEAT_INTERVAL_SECONDS` (5 minutes), not once per
  30-second poll cycle — a ~10x reduction in steady-state log volume for
  this line alone.
- **Expired-lease and terminal-failure warnings** each follow the same
  transition/reminder rule, independently: warn on a zero→non-zero
  transition; warn again if the count increases further; while the
  count stays the same and non-zero, suppress further warnings until
  `REMINDER_INTERVAL_SECONDS` (15 minutes) has elapsed since the last
  warning for that specific condition; reset to "no prior warning" the
  moment the count returns to zero, so the NEXT non-zero occurrence
  warns immediately rather than waiting out a stale reminder timer.
- The clock is injectable (`now: float | None` parameter, defaulting to
  `time.monotonic()`), making all of the above deterministically
  testable without real time passing —
  `tests/unit/test_outbox_queue_health.py` covers first-warning,
  suppression, increase-warns-again, reminder-after-interval,
  no-early-reminder, reset-on-zero, and warn-after-reset-transition.

**Explicit disclosures** (§O7):
- Suppression/heartbeat state is **process-local** — it lives in a
  module-level `QueueHealthLogState()` instance and is reset every time
  `start_outbox_worker` creates a genuinely new worker task (not the
  idempotent no-op path when a task is already running), as well as on
  a full process restart.
- **Each worker replica maintains independent state** — running more
  than one replica means the same condition can produce one warning per
  replica, not one deduplicated warning total. This is NOT global alert
  deduplication.
- The GET-handler's `safe_record_availability` call is NOT subject to
  this same suppression — it increments an in-process counter on every
  request (cheap, no log line beyond the counter's own `[metrics]`
  line from `increment`'s existing behavior), so its log-volume
  characteristics are analyzed separately in §6 below, not conflated
  with the worker's heartbeat cadence.

## 5. Provider acquisition signals (§O5)

`COUNTER_PROVIDER_ACQUISITION_ATTEMPT` / `_SUCCESS` / `_FAILURE` are
incremented in `current_report_generation.py`'s Phase 2, exactly around
the real `acquire_evidence_outside_transaction` call — never around the
`gen_ctx.compatible_evidence is not None` branch, which reuses
already-persisted evidence and makes no provider network call at all
(that branch increments the separate
`COUNTER_PROVIDER_ACQUISITION_REPLAY` counter instead, which is
deliberately never included in a failure-rate calculation).
`DURATION_PROVIDER_ACQUISITION` times the real acquisition call via
`safe_timed`, entirely outside any database connection (Phase 2 already
runs with no open connection, per this module's own phase-boundary
design).

**Provider failure rate** = `COUNTER_PROVIDER_ACQUISITION_FAILURE` /
`COUNTER_PROVIDER_ACQUISITION_ATTEMPT`. Both counters (like all counters
in this module) are **process-local** — a single replica's rate is
directly readable from that replica's own logs; a true fleet-wide rate
requires aggregating the `[metrics] current_report.provider.acquisition_*
+1` structured log lines across every replica in a log platform, which
this pass does not build.

## 6. Log cardinality and volume review

Every log line in this feature uses a bounded label set (counter/metric
names, `trade_id`/`outbox_id`, bounded enum-like values) — never raw
claim text, evidence values, prices, P&L, or full exception
text/messages (confirmed again for the new `_log_internal_failure_bounded`
path, which logs only the calling function's own name, not the caught
exception).

**Log-volume estimate** (§O7 — an honest estimate, not a claim of
negligible volume):

- GET request volume: unspecified without real traffic data — each GET
  produces exactly one `[metrics] current_report.availability.<state>
  +1` line via `safe_record_availability`, so GET-driven log volume
  scales linearly 1:1 with request volume; there is no batching or
  suppression on this path (unlike the worker's heartbeat).
- Frontend bounded polling: up to 30 PROCESSING polls per viewed report
  (WC-N's `MAX_POLL_ATTEMPTS`), each producing one GET request and
  therefore one metrics log line — a single user viewing one still-
  generating report over the full ~120s window contributes up to 30
  such lines, not 1.
- Worker replica count: assume N replicas (operator-controlled, not
  fixed by this code). Each replica emits its own heartbeat
  (`HEARTBEAT_INTERVAL_SECONDS` = 5 min) and its own independent
  warning stream (§4's disclosure) — total worker-originated log volume
  scales linearly with N, with no cross-replica deduplication.
- At the §9/§O9 evidence-based beta boundary (a global, low-traffic
  activation with no cohort restriction, meaning the entire
  authenticated user population of whatever deployment enables the
  flags), the dominant log-volume driver is expected to be GET-request-
  driven `[metrics]` lines (1:1 with traffic, no cap), not the worker's
  bounded heartbeat/warning lines. If GET volume during the beta proves
  high enough that this 1:1 log line becomes a real cost/noise concern,
  that would need its own follow-up correction (e.g. sampling or
  aggregating availability counts before logging) — not solved by this
  pass, and explicitly not claimed to be solved here.

**Removed claim**: the prior version of this document stated the
queue-health query was "cheap" without EXPLAIN evidence — that
unsupported claim is removed; see §3 for the evidence-based conclusion
and its explicit revisit threshold instead.

## 7. Capacity assessment

Unchanged real configured values from the initial pass (still accurate):
DB pool `max_size=10` (`paper_trading.py`), `USER_DATA_RATE_LIMIT =
"60/minute"` (`services/rate_limit.py`), `CLAIM_BATCH_SIZE=10` /
`POLL_INTERVAL_SECONDS=30` → ~20 rows/minute/replica worker throughput
ceiling. A queue backlog growing faster than that (visible in the
heartbeat's `pending` count trending upward across consecutive
heartbeats) is the signal to raise `CLAIM_BATCH_SIZE`, lower
`POLL_INTERVAL_SECONDS`, or run additional replicas (the claim query is
already multi-replica-safe).

## 8. Timeout and graceful-shutdown review

Unchanged from the initial pass — `stop_outbox_worker`'s existing
`asyncio.shield` design (timeout never cancels in-flight
`asyncio.to_thread` work; state clears exactly once via the task's own
done-callback) was reviewed and found sound; no change was made to it
in this correction, since none of the fail-open/queue-health corrections
touch shutdown behavior.

## 9. Related documents

- Runbooks: `Trade-Postmortem-Wave-C-WC-O-Runbooks.md` (this same
  directory) — see its §5 "Controlled beta" for the full §O9 beta-
  boundary definition and its §11 for the corrected (no ad hoc SQL)
  remediation procedure.
- WC-K freeze record: `Documentation/Engineering-Handbook/ADR/Trade-Postmortem-Wave-C-API-Frontend-Operations-Contract.md`, §3a.
