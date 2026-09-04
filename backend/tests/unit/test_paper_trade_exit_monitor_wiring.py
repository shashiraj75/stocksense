"""
Structural proof that _paper_trade_exit_monitor_loop is actually wired
into the FastAPI lifespan — started once at startup and cancelled/awaited
on shutdown, matching the existing convention for every other background
loop in this file (validation scheduler, trade notifier, etc.). Source-
inspection style, matching this codebase's existing convention for
lifespan wiring checks (see tests/unit/test_scheduler_ledger_integration.py's
TestAutoShortSchedulerStructure).
"""
import inspect

import api.main as main_module


def test_exit_monitor_loop_function_exists_and_is_distinct_from_notify_loop():
    assert hasattr(main_module, "_paper_trade_exit_monitor_loop")
    exit_src = inspect.getsource(main_module._paper_trade_exit_monitor_loop)
    notify_src = inspect.getsource(main_module._paper_trade_notify_loop)
    assert exit_src != notify_src


def test_exit_monitor_loop_calls_run_exit_monitor_cycle():
    src = inspect.getsource(main_module._paper_trade_exit_monitor_loop)
    assert "run_exit_monitor_cycle" in src
    assert "services.paper_trade_exit_monitor" in src


def test_exit_monitor_loop_runs_more_frequently_than_notify_loop():
    """Closing a triggered position is more time-sensitive than a
    proximity email — the exit monitor must poll at least as often as the
    notify loop, and this repo's implementation uses a shorter interval."""
    exit_src = inspect.getsource(main_module._paper_trade_exit_monitor_loop)
    notify_src = inspect.getsource(main_module._paper_trade_notify_loop)
    assert "asyncio.sleep(5 * 60)" in exit_src
    assert "asyncio.sleep(15 * 60)" in notify_src


def test_exit_monitor_task_is_created_in_lifespan():
    src = inspect.getsource(main_module.lifespan)
    assert "trade_exit_monitor_task = asyncio.create_task(_paper_trade_exit_monitor_loop())" in src


def test_exit_monitor_task_is_cancelled_and_awaited_on_shutdown():
    src = inspect.getsource(main_module.lifespan)
    assert "trade_exit_monitor_task.cancel()" in src
    # Must appear in the awaited-tasks tuple alongside the other loops, not
    # just cancelled and forgotten (which would leak a dangling task on
    # every reload/shutdown).
    await_block_idx = src.index("for t in (")
    await_block = src[await_block_idx:await_block_idx + 500]
    assert "trade_exit_monitor_task" in await_block
