"""
In-process counters and durations for Buy idempotency and entry-snapshot
integrity — Real PostgreSQL Verification, Idempotency Retention, and
Operational Observability phase, Stage 9.

No existing metrics framework (Prometheus/StatsD/etc.) exists anywhere in
this codebase — confirmed by inspection before writing this module. This
follows the simplest repository-consistent pattern instead: a thread-safe
in-process counter/duration store, plus a structured `log.info` line at
every increment (this repo's actual existing observability mechanism —
see market_data.py/paper_trading.py's own `log.info`/`log.warning` calls).

Exposing these counters via a new API/admin/diagnostic endpoint is
explicitly NOT done in this phase — that's a product-surface decision
outside this phase's approval boundary. `get_snapshot()` is the read path
a future, separately-approved endpoint could use; it is otherwise only
exercised by this module's own tests.

Never logs: idempotency keys in full (only `hash_key_prefix`'s short,
irreversible hash prefix), response bodies, request fingerprints,
recommendation reasoning, or any other request/response payload.
"""

import hashlib
import logging
import threading
import time
from collections import defaultdict
from contextlib import contextmanager

log = logging.getLogger(__name__)

_lock = threading.Lock()
_counters: dict = defaultdict(int)
_durations: dict = defaultdict(list)
_MAX_DURATION_SAMPLES = 1000  # bounded so this in-process store can never grow unboundedly over a long-lived process

# Canonical counter names — one shared set, not ad hoc strings invented at
# each call site (which would make cross-referencing counters in logs
# error-prone).
COUNTER_FRESH_RESERVATION = "idempotency.reservation.fresh"
COUNTER_COMPLETED = "idempotency.buy.completed"
COUNTER_REPLAYED = "idempotency.buy.replayed"
COUNTER_FINGERPRINT_CONFLICT = "idempotency.conflict.fingerprint_mismatch"
COUNTER_ALREADY_IN_PROGRESS = "idempotency.conflict.already_in_progress"
COUNTER_STALE_RECLAIMED = "idempotency.reclaim.stale_pending"
COUNTER_FAILED_RECLAIMED = "idempotency.reclaim.failed"
COUNTER_BUY_FAILED = "idempotency.buy.failed"
COUNTER_DB_ERROR = "idempotency.db_error"
COUNTER_CLEANUP_ROWS_DELETED = "idempotency.cleanup.rows_deleted"
COUNTER_CLEANUP_FAILURE = "idempotency.cleanup.failure"
COUNTER_SNAPSHOT_INSERT_FAILURE = "entry_snapshot.insert_failure"
COUNTER_SNAPSHOT_IMMUTABILITY_VIOLATION_OBSERVED = "entry_snapshot.immutability_violation_observed"
COUNTER_DUPLICATE_TRADE_PREVENTED = "idempotency.duplicate_trade_prevented"

DURATION_BUY_TRANSACTION = "idempotency.buy_transaction_duration_seconds"
DURATION_RESERVATION = "idempotency.reservation_duration_seconds"
DURATION_REPLAY = "idempotency.replay_duration_seconds"
DURATION_CONCURRENT_WAIT = "idempotency.concurrent_wait_duration_seconds"
DURATION_CLEANUP = "idempotency.cleanup_duration_seconds"
DURATION_PENDING_ROW_AGE = "idempotency.pending_row_age_seconds"


def increment(counter_name: str, amount: int = 1) -> None:
    with _lock:
        _counters[counter_name] += amount
    log.info("[metrics] %s +%d", counter_name, amount)


def record_duration(metric_name: str, seconds: float) -> None:
    with _lock:
        samples = _durations[metric_name]
        samples.append(seconds)
        if len(samples) > _MAX_DURATION_SAMPLES:
            del samples[: len(samples) - _MAX_DURATION_SAMPLES]
    log.info("[metrics] %s=%.4fs", metric_name, seconds)


@contextmanager
def timed(metric_name: str):
    """Usage: `with timed(DURATION_BUY_TRANSACTION): ...`"""
    start = time.monotonic()
    try:
        yield
    finally:
        record_duration(metric_name, time.monotonic() - start)


def hash_key_prefix(idempotency_key: str, length: int = 8) -> str:
    """Short, irreversible reference safe to include in a log line — never
    the raw idempotency key itself."""
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:length]


def get_snapshot() -> dict:
    """Read-only view of every counter and a summary (count/avg/max) of
    every duration metric's recent samples. For a future diagnostic
    endpoint (not exposed by this phase) and for this module's own tests."""
    with _lock:
        counters = dict(_counters)
        duration_summary = {
            name: {
                "count": len(samples),
                "avg": (sum(samples) / len(samples)) if samples else None,
                "max": max(samples) if samples else None,
            }
            for name, samples in _durations.items()
        }
    return {"counters": counters, "durations": duration_summary}


def reset_for_tests() -> None:
    """Test-only helper — clears all in-process state so tests don't leak
    counts into one another."""
    with _lock:
        _counters.clear()
        _durations.clear()
