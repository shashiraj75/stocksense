"""
Buy idempotency retention & cleanup — Real PostgreSQL Verification,
Idempotency Retention, and Operational Observability phase, Stage 7-8.

Problem this solves: `paper_trade_idempotency_key` has no automatic
cleanup — Stage 2's own hardening report named this as a residual risk. An
unbounded table is a real, if slow-moving, operational problem (storage
growth, index bloat, eventually slower key lookups). This module is a
bounded, idempotent, configuration-driven cleanup — a callable service
function, not a scheduled job. Per this phase's explicit approval
boundary, wiring this into any periodic loop or new scheduler is
deliberately NOT done here; that activation is its own separately
approved, production-impacting change (see the phase report's residual
risks / deferred items).

Retention policy (configurable via environment variables — never a
hardcoded, irreversible constant):
  - COMPLETED rows: retained `PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS`
    (default 30) — long enough that a legitimate slow client retry (a
    mobile client reconnecting after being offline for days, say) can still
    replay successfully.
  - FAILED rows: retained `PAPER_TRADE_IDEMPOTENCY_FAILED_RETENTION_HOURS`
    (default 24) — shorter, since a FAILED row is immediately reclaimable
    by the very next retry attempt regardless of cleanup; the only reason
    to keep it around at all is short-term operator diagnosis.
  - PENDING rows: NEVER deleted merely for being older than the
    reservation logic's own stale-reclaim threshold
    (`idempotency.STALE_PENDING_THRESHOLD`, 30s) — that threshold exists so
    a genuine retry can reclaim an abandoned reservation, not so cleanup
    can delete it out from under a request that might still complete. A
    PENDING row is only cleanup-eligible after
    `STALE_PENDING_THRESHOLD` PLUS an additional
    `PAPER_TRADE_IDEMPOTENCY_STALE_PENDING_SAFETY_HOURS` (default 1 hour)
    — i.e. a row nobody has reclaimed for a very long time, genuinely
    abandoned, not merely "stale enough for the next request to reclaim."

Batching: every DELETE is bounded by
`PAPER_TRADE_IDEMPOTENCY_CLEANUP_BATCH_SIZE` (default 500) via a
`WHERE id IN (SELECT id ... ORDER BY id LIMIT %s)` subquery — Postgres has
no direct `DELETE ... LIMIT`. Each of the three category deletes is its
own bounded statement, so a single cleanup invocation can never lock or
scan the whole table, and can safely be called repeatedly (e.g. once per
category per invocation, or looped by an external caller until a category
reports fewer than a full batch deleted) without special-casing "already
cleaned."
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import timedelta

from services.postmortem.idempotency import STALE_PENDING_THRESHOLD
from services.postmortem import idempotency_metrics as _metrics

log = logging.getLogger(__name__)

_DEFAULT_COMPLETED_RETENTION_DAYS = 30
_DEFAULT_FAILED_RETENTION_HOURS = 24
_DEFAULT_STALE_PENDING_SAFETY_HOURS = 1
_DEFAULT_BATCH_SIZE = 500


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("[idempotency_cleanup] invalid integer for %s=%r — using default %d", name, raw, default)
        return default
    if value <= 0:
        log.warning("[idempotency_cleanup] non-positive value for %s=%r — using default %d", name, raw, default)
        return default
    return value


def get_completed_retention() -> timedelta:
    return timedelta(days=_env_int("PAPER_TRADE_IDEMPOTENCY_COMPLETED_RETENTION_DAYS", _DEFAULT_COMPLETED_RETENTION_DAYS))


def get_failed_retention() -> timedelta:
    return timedelta(hours=_env_int("PAPER_TRADE_IDEMPOTENCY_FAILED_RETENTION_HOURS", _DEFAULT_FAILED_RETENTION_HOURS))


def get_stale_pending_retention() -> timedelta:
    """The full age a PENDING row must reach before cleanup will remove
    it — the reservation logic's own reclaim threshold PLUS a separate
    safety margin, so cleanup never races a legitimate reclaim attempt."""
    safety_hours = _env_int("PAPER_TRADE_IDEMPOTENCY_STALE_PENDING_SAFETY_HOURS", _DEFAULT_STALE_PENDING_SAFETY_HOURS)
    return STALE_PENDING_THRESHOLD + timedelta(hours=safety_hours)


def get_cleanup_batch_size() -> int:
    return _env_int("PAPER_TRADE_IDEMPOTENCY_CLEANUP_BATCH_SIZE", _DEFAULT_BATCH_SIZE)


@dataclass
class CleanupResult:
    completed_deleted: int = 0
    failed_deleted: int = 0
    stale_pending_deleted: int = 0
    batch_size: int = 0
    errors: list = field(default_factory=list)

    @property
    def total_deleted(self) -> int:
        return self.completed_deleted + self.failed_deleted + self.stale_pending_deleted


def _delete_batch(conn, status: str, older_than: timedelta, batch_size: int) -> int:
    """One bounded DELETE for a single status category. Uses the
    `created_at` index (COMPLETED/FAILED rows' relevant age is time since
    creation — `completed_at` would be more precise for COMPLETED rows,
    but `created_at` is what's indexed and reservation-to-completion
    latency is always small, so the difference is immaterial for a
    retention window measured in days). Returns the number of rows
    actually deleted (bounded by `batch_size`)."""
    rows = conn.execute(
        """DELETE FROM paper_trade_idempotency_key
           WHERE id IN (
               SELECT id FROM paper_trade_idempotency_key
               WHERE status = %s AND created_at < now() - %s::interval
               ORDER BY id
               LIMIT %s
           )
           RETURNING id""",
        (status, f"{older_than.total_seconds()} seconds", batch_size)
    ).fetchall()
    return len(rows)


def run_cleanup(conn, *, batch_size: int | None = None) -> CleanupResult:
    """Runs one bounded cleanup pass — up to `batch_size` rows per status
    category (COMPLETED, FAILED, stale-abandoned PENDING), never more.
    Safe to call repeatedly: a category with fewer than `batch_size`
    eligible rows simply deletes all of them and reports that count; a
    category that's already fully clean deletes zero. Logs only counts,
    never any row's `response_body`/`request_fingerprint`/`idempotency_key`
    (Part 10 / Stage 9's "never log payloads" rule).

    Each category is its own try/except so a failure in one (e.g. a
    transient connection issue) doesn't prevent the others from being
    attempted — errors are collected in the result rather than raised, so
    a caller can decide whether a partial cleanup pass is acceptable.
    """
    size = batch_size if batch_size is not None else get_cleanup_batch_size()
    result = CleanupResult(batch_size=size)
    started_at = time.monotonic()

    for status, get_retention, attr in (
        ("COMPLETED", get_completed_retention, "completed_deleted"),
        ("FAILED", get_failed_retention, "failed_deleted"),
        ("PENDING", get_stale_pending_retention, "stale_pending_deleted"),
    ):
        try:
            # get_retention() must be called INSIDE this try block, not in
            # the for-loop's target tuple above — a tuple literal evaluates
            # every element eagerly before the loop body (and its
            # try/except) ever runs, so a broken retention getter for one
            # category would previously raise before this except clause
            # existed to catch it, crashing the whole pass instead of being
            # isolated to that one category (see the docstring above).
            retention = get_retention()
            count = _delete_batch(conn, status, retention, size)
            setattr(result, attr, count)
            log.info("[idempotency_cleanup] deleted %d %s row(s) (batch_size=%d)", count, status, size)
            if count:
                _metrics.increment(_metrics.COUNTER_CLEANUP_ROWS_DELETED, amount=count)
        except Exception as exc:
            log.error("[idempotency_cleanup] cleanup failed for status=%s: %s", status, type(exc).__name__)
            result.errors.append(f"{status}: {type(exc).__name__}")
            _metrics.increment(_metrics.COUNTER_CLEANUP_FAILURE)

    _metrics.record_duration(_metrics.DURATION_CLEANUP, time.monotonic() - started_at)
    return result
