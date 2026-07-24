"""
Memory circuit breaker for Daily Picks generation (2026-07-21).

Production evidence: Railway process memory repeatedly climbed to its
service ceiling (observed peak ~7.97 GB, ~2026-07-21 12:56 UTC) shortly
before a US Daily Picks job stopped responding mid-`ranking` — status
stuck `running`, heartbeat frozen, `last_error` null, its heavy-workload
lease still held. The signature (no traceback, no shutdown log, an
immediate fresh-process restart) is consistent with the OS/cgroup killing
the process outright once it exceeded its memory allowance — something no
in-process `except` or `finally` block can react to after the fact.

This module is a *complementary* control, not a replacement for bounding
retention in the pipeline itself (see the Phase 1 sequential-loop and
per-horizon release changes in services/daily_picks.py): it periodically
checks actual process/container memory usage against the real detected
limit and, at a hard threshold, raises before the OS ever needs to kill
anything — turning a silent, unrecoverable process kill into an ordinary,
already-handled Python exception that `generate_picks()`'s existing
top-level `except Exception` marks `failed`, persists a bounded
`last_error` for, and (via the caller's own `finally`) releases the lease
for, exactly like any other in-process failure.

Never logs prediction payloads, credentials, provider responses, or user
data — only phase name, task progress, and memory percentages/byte counts.
"""

import gc
import logging
import math
import os
import sys

log = logging.getLogger(__name__)

_CGROUP_V2_CURRENT = "/sys/fs/cgroup/memory.current"
_CGROUP_V2_MAX = "/sys/fs/cgroup/memory.max"
_CGROUP_V1_CURRENT = "/sys/fs/cgroup/memory/memory.usage_in_bytes"
_CGROUP_V1_MAX = "/sys/fs/cgroup/memory/memory.limit_in_bytes"

# Percentage-of-detected-limit thresholds. Deliberately expressed as
# percentages, not a hardcoded byte ceiling (e.g. "8 GB") — Railway's actual
# container memory limit is whatever plan/service configuration says it is,
# and hardcoding a specific number here would silently stop being correct
# the moment that configuration changes. Overridable via environment
# variables for tuning without a code change.
def _float_env(name: str, default: float) -> float:
    """
    Reads a float env var, falling back to `default` on absence OR on any
    unparseable/unexpected value (including a bare None — e.g. some test
    fixtures elsewhere in this codebase patch `os.getenv` globally to
    always return None regardless of the key/default requested, which a
    plain `float(os.getenv(name, default))` does not survive since the
    mock ignores the default argument entirely). Never raises.

    Also rejects non-finite results (NaN, +inf, -inf) — `float("nan")`
    parses successfully but `pct >= float("nan")` is always False in
    Python, so a malformed-but-float-parseable env value (typo, bad shell
    substitution, templating bug) would otherwise silently and permanently
    disable whichever threshold it set — reintroducing the exact
    unbounded-OOM-kill failure mode this module exists to prevent, with no
    log line indicating anything is wrong. Found in independent review
    (2026-07-21) before this was ever exercised in production.
    """
    val = os.getenv(name)
    if val is None:
        return default
    try:
        parsed = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


DEFAULT_WARNING_PCT = _float_env("DAILY_PICKS_MEM_WARNING_PCT", 65.0)
DEFAULT_CLEANUP_PCT = _float_env("DAILY_PICKS_MEM_CLEANUP_PCT", 72.0)
DEFAULT_ABORT_PCT = _float_env("DAILY_PICKS_MEM_ABORT_PCT", 80.0)


def _clear_sec_facts_cache() -> None:
    from services import sec_edgar_adapter as _sea
    with _sea._facts_lock:
        _sea._facts_cache.clear()


def _clear_us_fundamentals_cache() -> None:
    from services import us_fundamentals as _usf
    with _usf._cache_lock:
        _usf._cache.clear()


def _clear_prediction_cache() -> None:
    from services import prediction_engine as _pe
    _pe._pred_cache.clear()


def _clear_market_data_fundamentals_cache() -> None:
    from services import market_data as _md
    _md._fundamentals_cache.clear()


def _clear_news_cache() -> None:
    from services import news_sentiment as _ns
    with _ns._news_lock:
        _ns._news_cache.clear()


# Explicit allowlist of caches that are safe and deterministic to rebuild
# mid-generation: every entry is a TTL'd provider-response/derived-result
# cache whose only rebuild cost is a refetch/recompute of the same
# deterministic inputs. Nothing here holds job state, ranking accumulators,
# containment state, published payloads, database sessions, or anything a
# concurrent API request mutates non-rebuildably. The two largest (SEC
# companyfacts at up to ~25 MB/entry, and per-symbol yfinance fundamentals)
# clear under their modules' own locks (as does news_sentiment); the two
# lock-less ones (prediction results, market-data fundamentals) rely on
# dict.clear() being atomic under the GIL, with their evictors hardened
# against the empty-during-eviction race (see those modules' _cache_set).
# Deliberately NOT cleared: regime cache (computed once per run and reused),
# quote/OHLCV/name caches (short-TTL and small — clearing buys nothing),
# alpha-engine IC/meta caches (containment-controlled state, out of scope).
_SAFE_CACHE_CLEARERS: list[tuple[str, callable]] = [
    ("sec_edgar_facts", _clear_sec_facts_cache),
    ("us_fundamentals", _clear_us_fundamentals_cache),
    ("prediction_results", _clear_prediction_cache),
    ("market_data_fundamentals", _clear_market_data_fundamentals_cache),
    ("news_sentiment", _clear_news_cache),
]


def clear_safe_caches() -> list[str]:
    """Clear every allowlisted cache, best-effort. Returns the labels
    actually cleared; a failure to clear any one cache is logged and
    skipped — it must never fail generation."""
    cleared: list[str] = []
    for label, clearer in _SAFE_CACHE_CLEARERS:
        try:
            clearer()
            cleared.append(label)
        except Exception as e:
            log.warning(f"[memory_guard] cache clear failed for {label} (non-fatal): {e}")
    return cleared


def malloc_trim() -> tuple[bool, bool]:
    """
    Best-effort glibc malloc_trim(0) to return freed-but-retained allocator
    arenas to the OS — the fragmentation-driven RSS ratchet observed in the
    2026-07-23/24 US Daily Picks incident is exactly the memory gc.collect()
    cannot give back. Returns (available, invoked): (False, False) on
    non-Linux or when libc/malloc_trim can't be resolved; (True, True) after
    a successful call. No guarantee of RSS reduction is implied — glibc only
    releases what its arenas can actually trim. Any failure at import,
    lookup, or invocation is logged and swallowed; this must never fail
    generation.
    """
    if not sys.platform.startswith("linux"):
        return (False, False)
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        trim = getattr(libc, "malloc_trim", None)
        if trim is None:
            return (False, False)
    except Exception as e:
        log.warning(f"[memory_guard] malloc_trim unavailable (non-fatal): {e}")
        return (False, False)
    try:
        trim(0)
        return (True, True)
    except Exception as e:
        log.warning(f"[memory_guard] malloc_trim invocation failed (non-fatal): {e}")
        return (True, False)


class DailyPicksMemoryLimitError(Exception):
    """Raised when memory usage crosses the controlled-abort threshold.

    The message is intentionally bounded to phase/progress/percentage —
    never a payload, credential, or raw provider response — since it
    becomes part of the job's durable `last_error` column.
    """


def read_memory_usage():
    """
    Return (used_bytes, limit_bytes) for the current process/container, or
    (None, None) if no reliable source is available.

    Preference order: cgroup v2 -> cgroup v1 -> psutil -> resource. The
    first two give the actual container limit Railway enforces; the latter
    two are best-effort local fallbacks (no container-wide limit is visible
    from them, so a limit of None must be handled by callers as "cannot
    evaluate thresholds, do not abort").
    """
    try:
        if os.path.exists(_CGROUP_V2_CURRENT) and os.path.exists(_CGROUP_V2_MAX):
            with open(_CGROUP_V2_CURRENT) as f:
                used = int(f.read().strip())
            with open(_CGROUP_V2_MAX) as f:
                raw_max = f.read().strip()
            if raw_max != "max":
                return used, int(raw_max)
    except Exception:
        pass

    try:
        if os.path.exists(_CGROUP_V1_CURRENT) and os.path.exists(_CGROUP_V1_MAX):
            with open(_CGROUP_V1_CURRENT) as f:
                used = int(f.read().strip())
            with open(_CGROUP_V1_MAX) as f:
                limit = int(f.read().strip())
            # cgroup v1 reports an effectively-unlimited value (commonly
            # 2**63-1 rounded to a page boundary) when no limit is set —
            # treat that the same as cgroup v2's "max" sentinel.
            if limit < (1 << 62):
                return used, limit
    except Exception:
        pass

    try:
        import psutil
        proc = psutil.Process(os.getpid())
        used = proc.memory_info().rss
        return used, None
    except Exception:
        pass

    try:
        import resource
        # ru_maxrss is KB on Linux, bytes on macOS — normalize to bytes
        # assuming Linux (production target); harmless overestimate on a
        # local macOS dev run, which has no limit to compare against anyway.
        used = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        return used, None
    except Exception:
        return None, None


class MemoryCircuitBreaker:
    """
    Stateful checker: call `check(phase, processed, total)` periodically
    (Phase 1 loop, phase boundaries). Tracks which thresholds have already
    been reported this run so repeated checks don't spam warnings, and
    raises DailyPicksMemoryLimitError exactly once, the first time usage
    crosses the abort threshold.
    """

    def __init__(
        self,
        market: str,
        job_id: str | None = None,
        warning_pct: float = DEFAULT_WARNING_PCT,
        cleanup_pct: float = DEFAULT_CLEANUP_PCT,
        abort_pct: float = DEFAULT_ABORT_PCT,
        reader=read_memory_usage,
    ):
        self.market = market
        self.job_id = job_id
        self.warning_pct = warning_pct
        self.cleanup_pct = cleanup_pct
        self.abort_pct = abort_pct
        self._reader = reader
        self._warned = False
        self._cleaned = False
        # Observability (2026-07-23/24 incident): peak/ending memory and
        # whether cleanup / malloc_trim actually ran are recorded on the
        # instance and logged via log_summary() — log-only, deliberately no
        # new persistence (a durable field would need a migration, out of
        # scope for this remediation).
        self.peak_used: int | None = None
        self.last_used: int | None = None
        self.limit: int | None = None
        self.cleanup_invoked = False
        self.malloc_trim_available = False
        self.malloc_trim_invoked = False

    def _read(self) -> tuple[int | None, int | None]:
        used, limit = self._reader()
        if used is not None:
            self.last_used = used
            if self.peak_used is None or used > self.peak_used:
                self.peak_used = used
        if limit is not None:
            self.limit = limit
        return used, limit

    def observe(self, stage: str, job_id_override=None, candidates=None, task_total=None) -> None:
        """Log a point-in-time memory reading with run context — no
        thresholds evaluated, never raises. Used at generation start and at
        milestones so baseline drift (the dominant driver of the 2026-07-23
        failure, which began at ~75% before any Phase 1 work) is visible in
        every run's logs, not just failing ones."""
        try:
            used, limit = self._read()
        except Exception:
            return
        pct = f"{100.0 * used / limit:.1f}%" if used is not None and limit else "n/a"
        extras = ""
        if candidates is not None:
            extras += f" candidates={candidates}"
        if task_total is not None:
            extras += f" task_total={task_total}"
        log.info(
            f"[memory_guard] [{self.market}] {stage}: used={used} limit={limit} "
            f"pct={pct} job_id={job_id_override or self.job_id}{extras}"
        )

    def release_memory(self, stage: str, processed=None, total=None) -> dict:
        """
        Genuine, best-effort memory release: clear the allowlisted safe
        caches, gc.collect(), then glibc malloc_trim(0) where available.
        Records memory before, after clear+gc, and after trim. Never raises.
        """
        summary: dict = {"stage": stage, "cleared": [], "trim_available": False, "trim_invoked": False}
        try:
            used_before, limit = self._read()
            summary["used_before"] = used_before
            summary["cleared"] = clear_safe_caches()
            gc.collect()
            used_after_gc, _ = self._read()
            summary["used_after_gc"] = used_after_gc
            available, invoked = malloc_trim()
            self.malloc_trim_available = available or self.malloc_trim_available
            self.malloc_trim_invoked = invoked or self.malloc_trim_invoked
            summary["trim_available"] = available
            summary["trim_invoked"] = invoked
            used_after_trim, _ = self._read()
            summary["used_after_trim"] = used_after_trim
            self.cleanup_invoked = True
            log.info(
                f"[memory_guard] [{self.market}] release_memory at stage={stage} "
                f"processed={processed} total={total}: before={used_before} "
                f"after_gc={used_after_gc} after_trim={used_after_trim} "
                f"cleared={summary['cleared']} malloc_trim_available={available} "
                f"malloc_trim_invoked={invoked}"
            )
        except Exception as e:
            log.warning(f"[memory_guard] release_memory failed (non-fatal): {e}")
        return summary

    def log_summary(self, stage: str = "run_end") -> None:
        """Log peak/ending memory and cleanup/trim provenance for the run.
        Log-only observability — never raises, never persists."""
        try:
            self._read()
        except Exception:
            pass
        log.info(
            f"[memory_guard] [{self.market}] {stage} summary: peak_used={self.peak_used} "
            f"ending_used={self.last_used} limit={self.limit} "
            f"cleanup_invoked={self.cleanup_invoked} "
            f"malloc_trim_available={self.malloc_trim_available} "
            f"malloc_trim_invoked={self.malloc_trim_invoked} job_id={self.job_id}"
        )

    def check(self, phase: str, processed=None, total=None) -> None:
        used, limit = self._read()
        if used is None or limit is None or limit <= 0:
            # No reliable container-wide limit visible (e.g. local dev,
            # psutil/resource-only fallback) — nothing safe to compare
            # against, so this check is a no-op rather than a false abort.
            return

        pct = 100.0 * used / limit

        if pct >= self.abort_pct:
            log.error(
                f"[memory_guard] [{self.market}] ABORT threshold reached: "
                f"{pct:.1f}% ({used}/{limit} bytes) at phase={phase} "
                f"processed={processed} total={total}"
            )
            raise DailyPicksMemoryLimitError(
                f"memory usage {pct:.1f}% >= abort threshold {self.abort_pct:.0f}% "
                f"at phase={phase} processed={processed} total={total}"
            )

        if pct >= self.cleanup_pct:
            log.warning(
                f"[memory_guard] [{self.market}] cleanup threshold reached: "
                f"{pct:.1f}% at phase={phase} processed={processed} total={total} "
                f"— clearing safe caches, collecting, and trimming."
            )
            self.release_memory(phase, processed=processed, total=total)
            return

        if pct >= self.warning_pct and not self._warned:
            log.warning(
                f"[memory_guard] [{self.market}] warning threshold reached: "
                f"{pct:.1f}% at phase={phase} processed={processed} total={total}"
            )
            self._warned = True
