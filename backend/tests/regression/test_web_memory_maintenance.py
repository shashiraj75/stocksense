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
    with pytest.raises(asyncio.CancelledError):
        await web_memory.web_memory_maintenance()
    assert len(passes) == 2
    assert waits == [60, 60, 60]
