"""Release unreachable quote objects and freed native arenas in the web process.

Repeated yfinance fast-info refreshes create cyclic Python objects and native
allocations. Python GC alone does not return glibc's freed pages to the OS.
This maintenance never clears application caches or touches reachable objects.
"""
import asyncio
import gc
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from services.memory_guard import malloc_trim

log = logging.getLogger(__name__)


def configure_web_executor(loop):
    """Bound persistent quote/IO threads independently of host CPU count.

    The event loop owns and shuts down its default executor. Cron processes
    do not call this web-lifespan hook.
    """
    workers = int(os.getenv("WEB_IO_WORKERS", "8"))
    if not 1 <= workers <= 32:
        raise ValueError("WEB_IO_WORKERS must be between 1 and 32")
    loop.set_default_executor(ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="web-io"))
    log.info("[web_memory] io_workers=%s malloc_arena_max=%s",
             workers, os.getenv("MALLOC_ARENA_MAX", "default"))


def _rss_kib():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def reclaim_web_memory():
    """Collect unreachable cycles, then release freed allocator pages only."""
    before = _rss_kib()
    collected = gc.collect()
    available, invoked = malloc_trim()
    after = _rss_kib()
    log.info("[web_memory] rss_before_kib=%s rss_after_kib=%s collected=%s "
             "trim_available=%s trim_invoked=%s",
             before, after, collected, available, invoked)


async def web_memory_maintenance():
    """One non-overlapping maintenance pass per minute, off the event loop."""
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.get_running_loop().run_in_executor(None, reclaim_web_memory)
        except Exception:
            log.exception("[web_memory] maintenance failed; retrying next interval")
