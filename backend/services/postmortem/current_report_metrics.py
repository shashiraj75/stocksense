"""
In-process counters and durations for the Wave C, WC-K/WC-O current-report
governed read/generation path — Trade Postmortem, Gate 4B (WC-O)
observability.

Same pattern as services.postmortem.idempotency_metrics (the one
established precedent in this codebase — no Prometheus/StatsD/OpenTelemetry
framework exists anywhere here, confirmed by inspection before writing this
module): a thread-safe in-process counter/duration store, plus a
structured `log.info("[metrics] ...")` line at every increment, the
existing repo-wide observability mechanism. Not wired to any new API/admin
endpoint in this phase — `get_snapshot()` is the read path a future,
separately-approved diagnostic surface could use.

Privacy discipline (never violated by any call site using this module):
only counter names, trade_id, outbox_id, market, and bounded error_code
strings are ever passed to these functions — never prices, P&L amounts,
claim text, evidence values, or any other report content.
"""

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

# Availability-state counters — incremented once per GET
# /api/paper-trading/{trade_id}/current-report response, keyed by the
# exact CurrentReportAvailability value returned. Privacy-safe: a plain
# count, never trade-identifying content.
COUNTER_AVAILABILITY_READY = "current_report.availability.ready"
COUNTER_AVAILABILITY_PROCESSING = "current_report.availability.processing"
COUNTER_AVAILABILITY_NOT_ELIGIBLE = "current_report.availability.not_eligible"
COUNTER_AVAILABILITY_NOT_AVAILABLE = "current_report.availability.not_available"
COUNTER_AVAILABILITY_TERMINAL_FAILURE = "current_report.availability.terminal_failure"
COUNTER_AVAILABILITY_INTEGRITY_CONTRADICTION = "current_report.availability.integrity_contradiction"
COUNTER_AVAILABILITY_FEATURE_DISABLED = "current_report.availability.feature_disabled"

# Generation/worker counters.
COUNTER_PROVIDER_ACQUISITION_FAILURE = "current_report.generation.provider_acquisition_failure"
COUNTER_INTEGRITY_CONTRADICTION_DETECTED = "current_report.generation.integrity_contradiction_detected"
COUNTER_WORKER_CLAIM_BATCH_FAILURE = "current_report.worker.claim_batch_failure"
COUNTER_WORKER_ROW_PROCESSING_FAILURE = "current_report.worker.row_processing_failure"
COUNTER_WORKER_POLL_CYCLE_FAILURE = "current_report.worker.poll_cycle_failure"
COUNTER_WORKER_LOOP_CRASHED = "current_report.worker.loop_crashed"

DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT = "current_report.outbox_row_age_at_settlement_seconds"

_AVAILABILITY_COUNTER_BY_STATE = {
    "READY": COUNTER_AVAILABILITY_READY,
    "PROCESSING": COUNTER_AVAILABILITY_PROCESSING,
    "NOT_ELIGIBLE": COUNTER_AVAILABILITY_NOT_ELIGIBLE,
    "NOT_AVAILABLE": COUNTER_AVAILABILITY_NOT_AVAILABLE,
    "TERMINAL_FAILURE": COUNTER_AVAILABILITY_TERMINAL_FAILURE,
    "INTEGRITY_CONTRADICTION": COUNTER_AVAILABILITY_INTEGRITY_CONTRADICTION,
    "FEATURE_DISABLED": COUNTER_AVAILABILITY_FEATURE_DISABLED,
}


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
    """Usage: `with timed(DURATION_...): ...`"""
    start = time.monotonic()
    try:
        yield
    finally:
        record_duration(metric_name, time.monotonic() - start)


def record_availability(availability: str) -> None:
    """The ONE call site every GET /current-report response path should
    use — a single lookup table (never a per-branch hardcoded string) so
    a typo in a new availability value fails loudly (KeyError) rather
    than silently going uncounted."""
    counter_name = _AVAILABILITY_COUNTER_BY_STATE[availability]
    increment(counter_name)


def get_snapshot() -> dict:
    """Read-only view of every counter and a summary (count/avg/max) of
    every duration metric's recent samples. For a future diagnostic
    endpoint (not exposed by this phase) and for this module's own
    tests."""
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
