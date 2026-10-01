"""Release unreachable quote objects and freed native arenas in the web process.

Repeated yfinance fast-info refreshes create cyclic Python objects and native
allocations. Python GC alone does not return glibc's freed pages to the OS.
This maintenance never clears application caches or touches reachable objects.
"""
import asyncio
import gc
import logging
from pathlib import Path

from services.memory_guard import malloc_trim

log = logging.getLogger(__name__)


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
