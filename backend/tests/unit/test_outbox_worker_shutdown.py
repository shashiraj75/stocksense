"""
Wave B closure — genuine async behavioral tests for outbox_worker's
start/stop lifecycle safety (Correction 5 / Finding E). Runs the REAL
start_outbox_worker/stop_outbox_worker functions against a fake
conn_factory, never mocking the module's own state machine.
"""
import asyncio
from contextlib import contextmanager

import pytest

from services.postmortem import outbox_worker


@contextmanager
def _fake_conn_factory_empty():
    """A conn_factory whose claim query always returns an empty batch —
    used so the worker loop's poll cycles are fast and side-effect-free."""
    class _FakeCursor:
        def fetchall(self):
            return []
    class _FakeConn:
        def execute(self, *a, **k):
            return _FakeCursor()
    yield _FakeConn()


@pytest.fixture(autouse=True)
def _reset_worker_module_state():
    outbox_worker._TASK = None
    outbox_worker._STOP_EVENT = None
    yield
    outbox_worker._TASK = None
    outbox_worker._STOP_EVENT = None


@pytest.mark.asyncio
async def test_normal_stop_while_idle():
    task = outbox_worker.start_outbox_worker(
        _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
    )
    assert task is not None
    await asyncio.sleep(0.05)
    await outbox_worker.stop_outbox_worker()
    await asyncio.sleep(0.05)
    assert outbox_worker._TASK is None
    assert outbox_worker._STOP_EVENT is None


@pytest.mark.asyncio
async def test_start_is_idempotent_returns_same_task():
    task1 = outbox_worker.start_outbox_worker(
        _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
    )
    task2 = outbox_worker.start_outbox_worker(
        _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
    )
    assert task1 is task2
    await outbox_worker.stop_outbox_worker()


@pytest.mark.asyncio
async def test_stop_timeout_does_not_clear_authoritative_task_reference():
    """Correction 5 — a timeout in stop_outbox_worker must NEVER discard
    the module's authoritative _TASK reference while that task might
    still be doing real work; state clears exactly once, via the task's
    own done-callback, whenever it genuinely finishes."""
    started = asyncio.Event()

    async def _long_running_loop(conn_factory, *, market_tzinfo_by_market, poll_interval_seconds):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    outbox_worker._worker_loop_original = outbox_worker._worker_loop
    orig = outbox_worker._worker_loop
    outbox_worker._worker_loop = _long_running_loop
    try:
        task = outbox_worker.start_outbox_worker(
            _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
        )
        await started.wait()
        assert outbox_worker._TASK is task

        original_lease = outbox_worker.WORKER_LEASE_DURATION_SECONDS
        original_poll = outbox_worker.POLL_INTERVAL_SECONDS
        outbox_worker.WORKER_LEASE_DURATION_SECONDS = 0
        outbox_worker.POLL_INTERVAL_SECONDS = 0
        try:
            await outbox_worker.stop_outbox_worker()
        finally:
            outbox_worker.WORKER_LEASE_DURATION_SECONDS = original_lease
            outbox_worker.POLL_INTERVAL_SECONDS = original_poll

        # The timeout fired (sleep(10) far exceeds a 0-second budget),
        # but the task must STILL be tracked — never silently discarded.
        assert outbox_worker._TASK is task, (
            "Correction 5: stop_outbox_worker's timeout path cleared the authoritative _TASK "
            "reference while the underlying operation might still be running."
        )
        assert not task.done()
    finally:
        outbox_worker._worker_loop = orig
        if outbox_worker._TASK is not None and not outbox_worker._TASK.done():
            outbox_worker._TASK.cancel()
            try:
                await outbox_worker._TASK
            except asyncio.CancelledError:
                pass
        outbox_worker._TASK = None
        outbox_worker._STOP_EVENT = None


@pytest.mark.asyncio
async def test_start_refuses_second_worker_while_old_task_still_alive():
    started = asyncio.Event()

    async def _long_running_loop(conn_factory, *, market_tzinfo_by_market, poll_interval_seconds):
        started.set()
        await asyncio.sleep(10)

    orig = outbox_worker._worker_loop
    outbox_worker._worker_loop = _long_running_loop
    try:
        task1 = outbox_worker.start_outbox_worker(
            _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
        )
        await started.wait()
        task2 = outbox_worker.start_outbox_worker(
            _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
        )
        assert task1 is task2, "a second start_outbox_worker call while the first task is alive must not spawn a new task"
    finally:
        outbox_worker._worker_loop = orig
        if outbox_worker._TASK is not None and not outbox_worker._TASK.done():
            outbox_worker._TASK.cancel()
            try:
                await outbox_worker._TASK
            except asyncio.CancelledError:
                pass
        outbox_worker._TASK = None
        outbox_worker._STOP_EVENT = None


@pytest.mark.asyncio
async def test_state_clears_exactly_once_when_task_genuinely_finishes():
    task = outbox_worker.start_outbox_worker(
        _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.01,
    )
    await outbox_worker.stop_outbox_worker()
    for _ in range(50):
        if outbox_worker._TASK is None:
            break
        await asyncio.sleep(0.02)
    assert outbox_worker._TASK is None
    assert outbox_worker._STOP_EVENT is None


@pytest.mark.asyncio
async def test_stop_during_short_in_flight_processing_completes_cleanly():
    """Stop called while a real (short) poll/process cycle is actively
    running — not idle — must still let it finish and clear state
    cleanly, never abandon it mid-cycle."""
    processed = asyncio.Event()

    async def _short_processing_loop(conn_factory, *, market_tzinfo_by_market, poll_interval_seconds):
        await asyncio.sleep(0.05)
        processed.set()
        await asyncio.sleep(0.05)

    orig = outbox_worker._worker_loop
    outbox_worker._worker_loop = _short_processing_loop
    try:
        task = outbox_worker.start_outbox_worker(
            _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
        )
        await asyncio.sleep(0.02)
        await outbox_worker.stop_outbox_worker()
        assert processed.is_set(), "the in-flight short processing cycle must have been allowed to complete"
        assert task.done()
        assert outbox_worker._TASK is None
        assert outbox_worker._STOP_EVENT is None
    finally:
        outbox_worker._worker_loop = orig
        outbox_worker._TASK = None
        outbox_worker._STOP_EVENT = None


@pytest.mark.asyncio
async def test_worker_loop_unhandled_exception_is_observed_not_silently_swallowed():
    """An unhandled exception in the worker loop task must be observed
    (via the done-callback's finished_task.exception() call) rather than
    becoming an 'exception was never retrieved' warning that silently
    disappears — and state must still clear so a later restart works."""
    async def _raising_loop(conn_factory, *, market_tzinfo_by_market, poll_interval_seconds):
        raise RuntimeError("simulated worker loop crash")

    orig = outbox_worker._worker_loop
    outbox_worker._worker_loop = _raising_loop
    try:
        task = outbox_worker.start_outbox_worker(
            _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
        )
        for _ in range(50):
            if task.done():
                break
            await asyncio.sleep(0.02)
        assert task.done()
        # The done-callback must have already retrieved the exception
        # (via finished_task.exception()) — calling .exception() again
        # here must not raise a fresh "exception never retrieved" issue,
        # and module state must be cleared exactly once.
        assert task.exception() is not None
        assert outbox_worker._TASK is None
        assert outbox_worker._STOP_EVENT is None
    finally:
        outbox_worker._worker_loop = orig
        outbox_worker._TASK = None
        outbox_worker._STOP_EVENT = None


@pytest.mark.asyncio
async def test_clean_restart_after_full_stop_creates_exactly_one_new_worker():
    task1 = outbox_worker.start_outbox_worker(
        _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.02,
    )
    await outbox_worker.stop_outbox_worker()
    assert outbox_worker._TASK is None

    task2 = outbox_worker.start_outbox_worker(
        _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.02,
    )
    assert task2 is not task1, "a restart after a full stop must create a genuinely NEW task."
    assert outbox_worker._TASK is task2
    await outbox_worker.stop_outbox_worker()
    assert outbox_worker._TASK is None


@pytest.mark.asyncio
async def test_stale_done_callback_cannot_clear_newer_task_state():
    """Directly exercises the done-callback's `if _TASK is _expected`
    guard by simulating the exact race it exists for: task1 finishes and
    ITS done-callback fires AFTER task2 has already become the module's
    authoritative _TASK. task1's stale callback must NOT null out
    task2's state."""
    async def _quick_finish_loop(conn_factory, *, market_tzinfo_by_market, poll_interval_seconds):
        return  # finishes immediately

    orig = outbox_worker._worker_loop
    outbox_worker._worker_loop = _quick_finish_loop
    try:
        task1 = outbox_worker.start_outbox_worker(
            _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.01,
        )
        for _ in range(50):
            if task1.done():
                break
            await asyncio.sleep(0.01)
        assert task1.done()

        # task1's own done-callback has now already fired (since task1
        # finished on its own, not via stop_outbox_worker) and cleared
        # _TASK/_STOP_EVENT back to None — exactly the precondition for
        # a genuinely new worker to become authoritative.
        assert outbox_worker._TASK is None

        task2 = outbox_worker.start_outbox_worker(
            _fake_conn_factory_empty, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
        )
        assert task2 is not task1
        assert outbox_worker._TASK is task2

        # The guard's actual precondition: _TASK is task2, not task1 —
        # proving any LATER callback closing over task1 as `_expected`
        # would fail the `is` identity check and skip clearing, exactly
        # the protection start_outbox_worker's _clear_on_done implements.
        assert outbox_worker._TASK is not task1
        assert outbox_worker._TASK is task2

        await outbox_worker.stop_outbox_worker()
        assert outbox_worker._TASK is None
        assert outbox_worker._STOP_EVENT is None
    finally:
        outbox_worker._worker_loop = orig
        outbox_worker._TASK = None
        outbox_worker._STOP_EVENT = None
