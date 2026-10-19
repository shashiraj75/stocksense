"""Release unreachable quote objects and freed native arenas in the web process.

Repeated yfinance fast-info refreshes create cyclic Python objects and native
allocations. Python GC alone does not return glibc's freed pages to the OS.
This maintenance never clears application caches or touches reachable objects.

Cadence: the loop wakes every minute but only runs the (full-heap) reclaim when
it is due -- RSS has grown by WEB_MEMORY_RECLAIM_GROWTH_MIB since the last
reclaim, or WEB_MEMORY_RECLAIM_INTERVAL_S has elapsed.  Setting the interval to
60 restores the previous every-minute behaviour with no code change.
"""
import asyncio
import gc
import logging
import os
import time
from pathlib import Path

from services.memory_guard import malloc_trim

log = logging.getLogger(__name__)

_CHECK_INTERVAL_S = 60


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _rss_kib():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def reclaim_due(rss_kib, baseline_kib, elapsed_s, interval_s, growth_kib) -> bool:
    """Pure decision: reclaim when the interval elapsed or RSS grew enough."""
    if elapsed_s >= interval_s:
        return True
    if rss_kib is not None and baseline_kib is not None:
        return rss_kib - baseline_kib >= growth_kib
    return False


def reclaim_web_memory():
    """Collect unreachable cycles, then release freed allocator pages only.

    Returns the post-reclaim RSS in KiB (None when unavailable)."""
    before = _rss_kib()
    collected = gc.collect()
    available, invoked = malloc_trim()
    after = _rss_kib()
    log.info("[web_memory] rss_before_kib=%s rss_after_kib=%s collected=%s "
             "trim_available=%s trim_invoked=%s",
             before, after, collected, available, invoked)
    return after


async def web_memory_maintenance():
    """Non-overlapping, demand-driven maintenance, off the event loop."""
    interval_s = _int_env("WEB_MEMORY_RECLAIM_INTERVAL_S", 900)
    growth_kib = _int_env("WEB_MEMORY_RECLAIM_GROWTH_MIB", 256) * 1024
    baseline = _rss_kib()
    last = time.monotonic()
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_S)
        now = time.monotonic()
        if not reclaim_due(_rss_kib(), baseline, now - last, interval_s, growth_kib):
            continue
        try:
            after = await asyncio.get_running_loop().run_in_executor(None, reclaim_web_memory)
            baseline = after if after is not None else _rss_kib()
        except Exception:
            log.exception("[web_memory] maintenance failed; retrying next interval")
        last = time.monotonic()
