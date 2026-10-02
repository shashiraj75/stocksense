"""Real executor admission and shutdown, without provider/network calls."""
import asyncio
import threading

import pytest

from services.web_memory import configure_web_executor


def test_executor_bounds_concurrency_and_drains_queued_work(monkeypatch):
    monkeypatch.setenv("WEB_IO_WORKERS", "2")
    release = threading.Event()
    started = []
    lock = threading.Lock()

    def work(i):
        with lock:
            started.append(i)
        assert release.wait(5)
        return i

    async def run():
        loop = asyncio.get_running_loop()
        configure_web_executor(loop)
        futures = [loop.run_in_executor(None, work, i) for i in range(6)]
        try:
            for _ in range(100):
                with lock:
                    count = len(started)
                if count == 2:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            with lock:
                assert len(started) == 2
        finally:
            release.set()
        assert await asyncio.gather(*futures) == list(range(6))

    asyncio.run(run())  # also joins the owned executor


@pytest.mark.parametrize("value", ["0", "33", "invalid"])
def test_invalid_worker_limit_does_not_install_executor(monkeypatch, value):
    monkeypatch.setenv("WEB_IO_WORKERS", value)
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(ValueError):
            configure_web_executor(loop)
        assert loop._default_executor is None
    finally:
        loop.close()
