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

FAIL-OPEN CONTRACT (WC-O correction, Package O §O1): observability must
never alter governed application behavior. `increment`/`record_duration`
are the raw, throwing primitives (kept for this module's own tests and
for callers that have already decided a failure here is acceptable to
propagate — none currently exist). Every application call site outside
this module MUST use the `safe_*` wrappers below instead, which catch
every exception internally, log a single bounded warning (never the raw
exception text — only its type name, which is not user data), and return
without raising. A metrics failure must never replace, mask, or delay the
caller's own original exception or control flow.
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
COUNTER_INTEGRITY_CONTRADICTION_DETECTED = "current_report.generation.integrity_contradiction_detected"
COUNTER_WORKER_CLAIM_BATCH_FAILURE = "current_report.worker.claim_batch_failure"
COUNTER_WORKER_ROW_PROCESSING_FAILURE = "current_report.worker.row_processing_failure"
COUNTER_WORKER_POLL_CYCLE_FAILURE = "current_report.worker.poll_cycle_failure"
COUNTER_WORKER_LOOP_CRASHED = "current_report.worker.loop_crashed"

# Provider acquisition signals (§O5) — a failure RATE needs a denominator,
# so every real acquisition attempt is counted, not just its failures.
# COUNTER_PROVIDER_ACQUISITION_REPLAY is deliberately separate and never
# added to attempts/successes/failures: reusing already-persisted
# compatible evidence (gen_ctx.compatible_evidence is not None) makes no
# network call at all and is not a provider "attempt" in any sense a
# failure-rate denominator should include.
COUNTER_PROVIDER_ACQUISITION_ATTEMPT = "current_report.provider.acquisition_attempt"
COUNTER_PROVIDER_ACQUISITION_SUCCESS = "current_report.provider.acquisition_success"
COUNTER_PROVIDER_ACQUISITION_FAILURE = "current_report.provider.acquisition_failure"
COUNTER_PROVIDER_ACQUISITION_REPLAY = "current_report.provider.acquisition_replay_no_attempt"

# Metrics-subsystem self-failure counter — incremented (best-effort, from
# inside the safe_* wrapper's own except block) whenever the metrics
# store itself raises. Never allowed to raise again itself.
COUNTER_METRICS_INTERNAL_FAILURE = "current_report.metrics.internal_failure"

DURATION_PROVIDER_ACQUISITION = "current_report.provider.acquisition_duration_seconds"

_AVAILABILITY_COUNTER_BY_STATE = {
    "READY": COUNTER_AVAILABILITY_READY,
    "PROCESSING": COUNTER_AVAILABILITY_PROCESSING,
    "NOT_ELIGIBLE": COUNTER_AVAILABILITY_NOT_ELIGIBLE,
    "NOT_AVAILABLE": COUNTER_AVAILABILITY_NOT_AVAILABLE,
    "TERMINAL_FAILURE": COUNTER_AVAILABILITY_TERMINAL_FAILURE,
    "INTEGRITY_CONTRADICTION": COUNTER_AVAILABILITY_INTEGRITY_CONTRADICTION,
    "FEATURE_DISABLED": COUNTER_AVAILABILITY_FEATURE_DISABLED,
}


# ============================= raw (throwing) primitives ============================= #
# Kept for this module's own tests and internal use only. Application
# call sites outside this module must use the safe_* wrappers below.

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


def record_availability(availability: str) -> None:
    """Raises KeyError on an unrecognized availability value — this is
    the raw primitive; safe_record_availability below is what every
    application call site actually uses."""
    counter_name = _AVAILABILITY_COUNTER_BY_STATE[availability]
    increment(counter_name)


# ============================= fail-open wrappers ============================= #
# WC-O §O1 — the ONLY entry points api/routers/paper_trading.py,
# current_report_generation.py, and outbox_worker.py are permitted to
# call. Every one of these functions is guaranteed to never raise,
# regardless of what happens inside the in-process store — a metrics
# subsystem defect must never become a user-visible 500, a changed
# generation outcome, or a corrupted worker state.

def safe_increment(counter_name: str, amount: int = 1) -> None:
    try:
        increment(counter_name, amount)
    except Exception:
        _log_internal_failure_bounded("safe_increment")


def safe_record_duration(metric_name: str, seconds: float) -> None:
    try:
        record_duration(metric_name, seconds)
    except Exception:
        _log_internal_failure_bounded("safe_record_duration")


def safe_record_availability(availability: str) -> None:
    """Recognized values are recorded normally. An unrecognized value
    (e.g. a future availability state added to the Literal without a
    matching table entry) produces one bounded, privacy-safe warning
    line — never a KeyError propagating into the request path, and
    never the raw exception object logged (only a fixed message plus
    the bounded `availability` string itself, which is a governed enum
    value, not user content)."""
    try:
        record_availability(availability)
    except KeyError:
        log.warning("[metrics] unrecognized availability value for recording: %s", availability)
    except Exception:
        _log_internal_failure_bounded("safe_record_availability")


@contextmanager
def safe_timed(metric_name: str):
    """Usage: `with safe_timed(DURATION_...): ...` — times the wrapped
    block and records the duration via safe_record_duration (never
    raises from the timing/recording itself). Does NOT suppress an
    exception raised by the wrapped block — only the metrics recording
    around it is fail-open; the caller's own exception still propagates
    normally."""
    start = time.monotonic()
    try:
        yield
    finally:
        safe_record_duration(metric_name, time.monotonic() - start)


def _log_internal_failure_bounded(call_site: str) -> None:
    # Deliberately never logs the caught exception itself (str(exc) could
    # in principle include repr() of arbitrary call-site data depending on
    # how a future bug manifests) — only a fixed message and the calling
    # function's own name, which is source code, not user data. Also
    # never re-raises and never calls back into increment()/safe_increment
    # for its own counter (that would risk unbounded recursion if the
    # store itself is what's broken) — uses a direct, best-effort,
    # separately-guarded increment instead.
    log.warning("[metrics] internal failure recording a metric from %s — application behavior unaffected", call_site)
    try:
        with _lock:
            _counters[COUNTER_METRICS_INTERNAL_FAILURE] += 1
    except Exception:
        pass


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
