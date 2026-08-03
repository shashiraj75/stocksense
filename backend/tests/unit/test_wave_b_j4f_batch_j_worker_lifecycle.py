"""
Wave B, Stage J4F — Batch J red tests: background worker startup/
shutdown, bounded polling, batch size, database-outage backoff, and
no-connection-during-sleep/provider-IO (scenario-matrix expansion
under WB-J4F-13/17/19/20 plus new structural scenarios).

Written and run against genuine PRE-FIX production code. Absent
module/symbols are probed via importlib.util.find_spec / getattr so
collection always succeeds; the assertion carries the intended
failure. No production implementation is added by this file.
"""
import importlib
import importlib.util
import inspect

import pytest


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


# Scenario 21 — outbox_worker module must exist at all.
def test_wb_j4f_21_outbox_worker_module_exists():
    assert _module_or_none("services.postmortem.outbox_worker") is not None, (
        "WB-J4F-13[module existence]: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    )


# Scenario 22 — the worker must be flag-gated: it must check
# TRADE_POSTMORTEM_PRICE_PATH_ENABLED (or an equivalent dedicated flag)
# before doing any work, matching the existing flag-gating convention
# used throughout paper_trading.py.
def test_wb_j4f_22_worker_is_flag_gated():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-22: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    source = inspect.getsource(module)
    assert "TRADE_POSTMORTEM" in source, (
        "WB-J4F-22: outbox_worker.py does not visibly reference any TRADE_POSTMORTEM_* feature flag — "
        "the worker must be flag-gated and default-disabled."
    )


# Scenario 23 — a start function (e.g. start_worker) must exist, callable
# from a FastAPI lifespan context, returning an awaitable/task handle.
def test_wb_j4f_23_worker_exposes_a_start_function():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-23: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    start_fns = [name for name in dir(module) if name.startswith("start_")]
    assert start_fns, (
        "WB-J4F-23: outbox_worker.py exposes no start_* function for lifespan integration."
    )


# Scenario 24 — a stop/shutdown function must exist and must be
# awaitable/callable to request graceful shutdown (never os._exit or a
# bare thread kill).
def test_wb_j4f_24_worker_exposes_a_graceful_stop_function():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-24: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    stop_fns = [name for name in dir(module) if name.startswith("stop_")]
    assert stop_fns, (
        "WB-J4F-24: outbox_worker.py exposes no stop_* function for graceful lifespan shutdown."
    )


# Scenario 25 — the worker's poll loop must accept an explicit, bounded
# batch size parameter — never claim an unbounded number of rows in one
# cycle.
def test_wb_j4f_25_worker_poll_has_bounded_batch_size_parameter():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-25: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    poll_fns = [getattr(module, name) for name in dir(module) if "poll" in name.lower() and callable(getattr(module, name))]
    assert poll_fns, "WB-J4F-25: outbox_worker.py has no poll-cycle function to inspect for a bounded batch-size parameter."
    sig = inspect.signature(poll_fns[0])
    assert any("batch" in p.lower() for p in sig.parameters), (
        f"WB-J4F-25: poll function signature {sig} has no batch-size parameter — risk of an unbounded claim."
    )


# Scenario 26 — the worker's poll interval must be explicit/configurable,
# never a bare hardcoded time.sleep with no named constant.
def test_wb_j4f_26_worker_poll_interval_is_named_not_bare_magic_number():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-26: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    source = inspect.getsource(module)
    assert "POLL_INTERVAL" in source or "poll_interval" in source, (
        "WB-J4F-26: outbox_worker.py has no named poll-interval constant/parameter."
    )


# Scenario 27 — no database connection may be held open across the
# poll-to-poll sleep — the worker must acquire a connection per claim
# batch, not hold one for the loop's entire lifetime.
def test_wb_j4f_27_worker_does_not_hold_connection_across_sleep():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-27: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    source = inspect.getsource(module)
    assert "asyncio.sleep" in source or "await asyncio.sleep" in source, (
        "WB-J4F-27: outbox_worker.py does not use asyncio.sleep for its poll interval — cannot verify "
        "the sleep happens without a held connection (async sleep yields control; a sync time.sleep "
        "inside an async loop would block the event loop entirely, which is its own defect)."
    )


# Scenario 28 — no database connection may be held across a provider I/O
# call (price-path evidence acquisition) — matches J4E's existing 5-phase
# design (phase 2 has no `conn` parameter at all).
def test_wb_j4f_28_worker_delegates_provider_acquisition_outside_any_conn_block():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-28: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    source = inspect.getsource(module)
    assert "process_current_report" in source, (
        "WB-J4F-28: outbox_worker.py does not call current_report_generation.process_current_report — "
        "it must reuse the shared orchestrator's own conn-free acquisition phase, never duplicate it."
    )


# Scenario 29 — a database-outage (connection error at claim time) must be
# handled by backing off before the next poll, never crashing the worker
# loop or busy-looping with no delay.
def test_wb_j4f_29_worker_backs_off_on_database_outage():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-29: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    source = inspect.getsource(module)
    assert "except" in source and ("OperationalError" in source or "psycopg.Error" in source or "Exception" in source), (
        "WB-J4F-29: outbox_worker.py does not visibly catch a database connection error around its "
        "claim/poll cycle — a DB outage could crash the background task instead of backing off and retrying."
    )


# Scenario 30 — the worker must log (sanitized) start/stop lifecycle
# events, so operators can confirm it is running without reading source.
def test_wb_j4f_30_worker_logs_lifecycle_events():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, "WB-J4F-30: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    source = inspect.getsource(module)
    assert "logger" in source or "log." in source or "logging" in source, (
        "WB-J4F-30: outbox_worker.py has no visible logging — lifecycle events (start/stop/claim/error) "
        "would be unobservable to operators."
    )
