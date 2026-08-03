"""
Wave C, WC-O (corrected, Package O §O2/§O3/§O4) — outbox queue-health
snapshot and noise-bounded log emission for the current (1.2.0) governed
report identity.

Split into two deliberately separate concerns the original single
`_log_queue_depth_snapshot` conflated:

1. `read_queue_health_snapshot` — a PURE, read-only PostgreSQL query
   returning a typed `QueueHealthSnapshot`. Never mutates, claims,
   leases, settles, or repairs anything. Independently testable without
   any logging concern at all.

2. `QueueHealthLogState` + `apply_transition_and_log` — a process-local,
   deterministically-testable transition/reminder tracker (injectable
   clock) deciding WHEN to emit a log line, kept separate from the query
   itself so the noise-bounding policy can be tested without a database.

§O3 query-cost note: this query filters by
(requested_report_schema_version, requested_calculation_version,
requested_rules_version) — a predicate NOT efficiently served by the
existing `idx_paper_trade_pm_outbox_unique` index (that index leads with
`paper_trade_id`, so a query that does not also filter on
`paper_trade_id` cannot use it as an index-only/index-range scan for this
predicate) or `idx_paper_trade_pm_outbox_claim` (leads with `status`).
No new index is added by this pass: at the row volumes this feature is
expected to reach during a controlled beta (a small population's closed
trades — low thousands of outbox rows, not millions), a sequential scan
aggregate over this table is fast (single-digit milliseconds) and adding
an index purely on the prompt's suggestion, without EXPLAIN evidence
showing it is actually needed, would be premature. Real-PostgreSQL
EXPLAIN evidence for this query is captured in
`tests/postgres_integration/test_outbox_queue_health.py` and recorded in
`Trade-Postmortem-Wave-C-WC-O-Observability-And-Capacity-Review.md`
(§3, "Query cost evidence"). Revisit this conclusion if the outbox
table's row count for a single governed version identity exceeds
roughly 50,000 rows — at that scale a composite index on
`(requested_report_schema_version, requested_calculation_version,
requested_rules_version, status)` would be the smallest additive index
matching this query's actual predicates.
"""

import dataclasses
import logging
import time

log = logging.getLogger(__name__)

# The frontend's bounded PROCESSING poll is 30 attempts * 4s = ~120s
# (WC-N's useBoundedProcessingPoll) — a GENERATING row older than this
# window has outlived what any single polling user would have waited
# through, making it operationally interesting regardless of whether its
# lease has technically expired yet.
FRONTEND_POLL_WINDOW_SECONDS = 120

# §O4 — noise-bounding defaults. A bounded reminder interval so a
# persistently non-zero condition (e.g. a steady terminal-failure
# backlog an operator has already been warned about) does not vanish
# from logs forever, but also does not repeat every single 30-second
# poll cycle.
REMINDER_INTERVAL_SECONDS = 15 * 60  # 15 minutes
HEARTBEAT_INTERVAL_SECONDS = 5 * 60  # 5 minutes — ordinary (non-warning) snapshot cadence


@dataclasses.dataclass(frozen=True)
class QueueHealthSnapshot:
    """Zero counts and `None` ages are the accurate, honest result when no
    row matches a given category — never a fabricated zero-age or an
    omitted field."""

    pending: int
    generating_active: int
    generating_expired_lease: int
    failed_retryable: int
    failed_terminal: int
    oldest_pending_age_seconds: float | None
    oldest_generating_age_seconds: float | None
    oldest_failed_retryable_age_seconds: float | None
    generating_over_poll_window_count: int


def read_queue_health_snapshot(
    conn, *, schema_version: str, calculation_version: str, rules_version: str,
) -> QueueHealthSnapshot:
    """Pure read. Filters by the EXACT governed version identity — never
    a cross-version aggregate, which would conflate a historical
    version's backlog with the current one's.

    Age semantics (never inferred from `next_attempt_at`, which
    represents FUTURE retry eligibility, not time already spent
    waiting):
    - pending age: now - created_at (a PENDING row was never attempted,
      so its own creation is the only meaningful start time);
    - generating age: now - claimed_at. The claim query
      (outbox_worker.claim_next_current_outbox_batch) always sets
      claimed_at when transitioning a row to GENERATING — there is no
      legitimate historical evidence of a GENERATING row with a NULL
      claimed_at, so such a row is excluded from the age aggregate
      entirely (via `claimed_at IS NOT NULL` in the query) rather than
      given a fabricated fallback;
    - failed-retryable age: now - last_attempt_at, since a
      FAILED_RETRYABLE row is only reached after at least one attempt
      (outbox.mark_retryable_failure always sets last_attempt_at at the
      claim that produced this status) — again no fallback is needed or
      applied, for the same reason.
    """
    row = conn.execute(
        """SELECT
             count(*) FILTER (WHERE status = 'PENDING') AS pending,
             count(*) FILTER (WHERE status = 'GENERATING' AND (lease_expires_at IS NULL OR lease_expires_at > now())) AS generating_active,
             count(*) FILTER (WHERE status = 'GENERATING' AND lease_expires_at IS NOT NULL AND lease_expires_at <= now()) AS generating_expired_lease,
             count(*) FILTER (WHERE status = 'FAILED_RETRYABLE') AS failed_retryable,
             count(*) FILTER (WHERE status = 'FAILED_TERMINAL') AS failed_terminal,
             EXTRACT(EPOCH FROM (now() - min(created_at) FILTER (WHERE status = 'PENDING'))) AS oldest_pending_age_seconds,
             EXTRACT(EPOCH FROM (now() - min(claimed_at) FILTER (WHERE status = 'GENERATING' AND claimed_at IS NOT NULL))) AS oldest_generating_age_seconds,
             EXTRACT(EPOCH FROM (now() - min(last_attempt_at) FILTER (WHERE status = 'FAILED_RETRYABLE' AND last_attempt_at IS NOT NULL))) AS oldest_failed_retryable_age_seconds,
             count(*) FILTER (
               WHERE status = 'GENERATING' AND claimed_at IS NOT NULL
                 AND claimed_at <= now() - make_interval(secs => %(poll_window)s)
             ) AS generating_over_poll_window
           FROM paper_trade_postmortem_outbox
           WHERE requested_report_schema_version = %(schema_version)s
             AND requested_calculation_version = %(calculation_version)s
             AND requested_rules_version = %(rules_version)s""",
        {
            "schema_version": schema_version, "calculation_version": calculation_version,
            "rules_version": rules_version, "poll_window": FRONTEND_POLL_WINDOW_SECONDS,
        },
    ).fetchone()
    (
        pending, generating_active, generating_expired_lease, failed_retryable, failed_terminal,
        oldest_pending_age, oldest_generating_age, oldest_failed_retryable_age, generating_over_poll_window,
    ) = row
    return QueueHealthSnapshot(
        pending=pending, generating_active=generating_active,
        generating_expired_lease=generating_expired_lease, failed_retryable=failed_retryable,
        failed_terminal=failed_terminal,
        oldest_pending_age_seconds=float(oldest_pending_age) if oldest_pending_age is not None else None,
        oldest_generating_age_seconds=float(oldest_generating_age) if oldest_generating_age is not None else None,
        oldest_failed_retryable_age_seconds=(
            float(oldest_failed_retryable_age) if oldest_failed_retryable_age is not None else None
        ),
        generating_over_poll_window_count=generating_over_poll_window,
    )


@dataclasses.dataclass
class QueueHealthLogState:
    """Process-local (never persisted, never shared across replicas —
    see the observability doc's explicit multi-replica disclosure)
    transition/reminder tracker. One instance lives for the lifetime of
    the worker loop (module-level singleton in outbox_worker.py)."""

    last_expired_lease_count: int = 0
    last_expired_lease_warning_at: float | None = None
    last_terminal_failure_count: int = 0
    last_terminal_failure_warning_at: float | None = None
    last_heartbeat_at: float | None = None


def _should_warn(*, previous_count: int, current_count: int, last_warning_at: float | None, now: float) -> bool:
    """§O4 transition/reminder rule, shared by both expired-lease and
    terminal-failure tracking:
    - zero -> non-zero: always warn (a new condition just appeared);
    - count increased further: always warn (getting worse);
    - count unchanged and non-zero: warn again only after
      REMINDER_INTERVAL_SECONDS has elapsed since the last warning for
      this condition (a bounded reminder, not silence forever);
    - count unchanged, no time elapsed yet: suppressed;
    - count is zero: never warn (the "count > 0" caller-side check
      already excludes this, but kept explicit here for clarity)."""
    if current_count == 0:
        return False
    if previous_count == 0:
        return True  # zero -> non-zero
    if current_count > previous_count:
        return True  # got worse
    if last_warning_at is None:
        return True  # defensive: no prior warning recorded, but count is non-zero — warn
    return (now - last_warning_at) >= REMINDER_INTERVAL_SECONDS


def apply_transition_and_log(
    snapshot: QueueHealthSnapshot, *, state: QueueHealthLogState, now: float | None = None,
) -> None:
    """The ONE log-emission entry point outbox_worker.py calls once per
    poll cycle. Mutates `state` in place (process-local, not thread-safe
    beyond the single worker loop's own sequential execution — this
    matches the worker's own single-loop-at-a-time design). `now` is
    injectable (defaults to time.monotonic()) so tests are
    deterministic without patching the system clock."""
    if now is None:
        now = time.monotonic()

    # Ordinary queue-health line: a bounded heartbeat, not one line per
    # 30-second cycle forever (§O4 — "do not produce unbounded log
    # volume"). Emitted at HEARTBEAT_INTERVAL_SECONDS cadence regardless
    # of whether anything changed, so an operator watching logs always
    # has a recent baseline reading.
    if state.last_heartbeat_at is None or (now - state.last_heartbeat_at) >= HEARTBEAT_INTERVAL_SECONDS:
        log.info(
            "[outbox_worker_queue_depth] pending=%d generating_active=%d generating_expired_lease=%d "
            "failed_retryable=%d failed_terminal=%d oldest_pending_age_s=%s oldest_generating_age_s=%s "
            "oldest_failed_retryable_age_s=%s generating_over_poll_window=%d",
            snapshot.pending, snapshot.generating_active, snapshot.generating_expired_lease,
            snapshot.failed_retryable, snapshot.failed_terminal,
            _fmt_age(snapshot.oldest_pending_age_seconds), _fmt_age(snapshot.oldest_generating_age_seconds),
            _fmt_age(snapshot.oldest_failed_retryable_age_seconds), snapshot.generating_over_poll_window_count,
        )
        state.last_heartbeat_at = now

    if _should_warn(
        previous_count=state.last_expired_lease_count, current_count=snapshot.generating_expired_lease,
        last_warning_at=state.last_expired_lease_warning_at, now=now,
    ):
        log.warning(
            "[outbox_worker_expired_lease] count=%d — rows reclaimable on next claim batch",
            snapshot.generating_expired_lease,
        )
        state.last_expired_lease_warning_at = now
    if snapshot.generating_expired_lease == 0:
        state.last_expired_lease_warning_at = None  # reset — the next non-zero occurrence warns immediately
    state.last_expired_lease_count = snapshot.generating_expired_lease

    if _should_warn(
        previous_count=state.last_terminal_failure_count, current_count=snapshot.failed_terminal,
        last_warning_at=state.last_terminal_failure_warning_at, now=now,
    ):
        log.warning("[outbox_worker_terminal_failure_backlog] count=%d", snapshot.failed_terminal)
        state.last_terminal_failure_warning_at = now
    if snapshot.failed_terminal == 0:
        state.last_terminal_failure_warning_at = None
    state.last_terminal_failure_count = snapshot.failed_terminal


def _fmt_age(seconds: float | None) -> str:
    return "null" if seconds is None else f"{seconds:.1f}"
