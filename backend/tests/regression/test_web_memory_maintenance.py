import asyncio
import weakref

import pytest

from services import web_memory


def test_collects_unreachable_cycles_without_discarding_live_data(monkeypatch):
    class Payload:
        pass

    live = Payload()
    live.self = live
    dead = Payload()
    dead.self = dead
    dead_ref = weakref.ref(dead)
    del dead
    seen = []
    monkeypatch.setattr(web_memory, "malloc_trim",
                        lambda: (seen.append(dead_ref() is None) or (True, True)))
    web_memory.reclaim_web_memory()
    assert dead_ref() is None
    assert live.self is live
    assert seen == [True]


def test_unsupported_allocator_and_missing_proc_are_nonfatal(monkeypatch):
    monkeypatch.setattr(web_memory, "malloc_trim", lambda: (False, False))
    monkeypatch.setattr(web_memory, "_rss_kib", lambda: None)
    web_memory.reclaim_web_memory()


@pytest.mark.asyncio
async def test_failed_pass_waits_before_retry_and_cancellation_stops_loop(monkeypatch):
    waits = []
    passes = []

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            raise asyncio.CancelledError

    def reclaim():
        passes.append(True)
        if len(passes) == 1:
            raise RuntimeError("allocator probe failed")

    monkeypatch.setattr(web_memory.asyncio, "sleep", sleep)
    monkeypatch.setattr(web_memory, "reclaim_web_memory", reclaim)
    monkeypatch.setattr(web_memory, "reclaim_due", lambda *a, **k: True)
    with pytest.raises(asyncio.CancelledError):
        await web_memory.web_memory_maintenance()
    assert len(passes) == 2
    assert waits == [60, 60, 60]


def test_reclaim_due_on_interval_or_growth_only():
    due = web_memory.reclaim_due
    # neither elapsed nor grown -> not due
    assert due(1000, 900, 10, 900, 256 * 1024) is False
    # interval elapsed -> due regardless of RSS
    assert due(1000, 1000, 900, 900, 256 * 1024) is True
    # RSS grew past the threshold -> due before the interval
    assert due(900 + 256 * 1024, 900, 10, 900, 256 * 1024) is True
    # RSS unreadable and interval not elapsed -> never due (fail quiet, not fail loud)
    assert due(None, None, 10, 900, 256 * 1024) is False


@pytest.mark.asyncio
async def test_loop_skips_reclaim_until_due_and_interval_env_restores_old_cadence(monkeypatch):
    async def run(interval_env):
        monkeypatch.setenv("WEB_MEMORY_RECLAIM_INTERVAL_S", interval_env)
        monkeypatch.setenv("WEB_MEMORY_RECLAIM_GROWTH_MIB", "100000")
        ticks = []
        passes = []
        clock = {"t": 0.0}

        async def sleep(seconds):
            ticks.append(seconds)
            clock["t"] += seconds
            if len(ticks) == 5:
                raise asyncio.CancelledError

        monkeypatch.setattr(web_memory.asyncio, "sleep", sleep)
        monkeypatch.setattr(web_memory.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(web_memory, "_rss_kib", lambda: 1000)
        monkeypatch.setattr(web_memory, "reclaim_web_memory",
                            lambda: (passes.append(True) or 1000))
        with pytest.raises(asyncio.CancelledError):
            await web_memory.web_memory_maintenance()
        return len(passes)

    assert await run("900") == 0   # 5 minutes elapsed < 15 min interval
    assert await run("60") == 4    # interval=60 restores one pass per tick
