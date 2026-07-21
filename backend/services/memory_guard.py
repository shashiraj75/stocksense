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
import os

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
    """
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


DEFAULT_WARNING_PCT = _float_env("DAILY_PICKS_MEM_WARNING_PCT", 65.0)
DEFAULT_CLEANUP_PCT = _float_env("DAILY_PICKS_MEM_CLEANUP_PCT", 72.0)
DEFAULT_ABORT_PCT = _float_env("DAILY_PICKS_MEM_ABORT_PCT", 80.0)


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

    def check(self, phase: str, processed=None, total=None) -> None:
        used, limit = self._reader()
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
                f"— releasing caches and collecting."
            )
            gc.collect()
            self._cleaned = True
            return

        if pct >= self.warning_pct and not self._warned:
            log.warning(
                f"[memory_guard] [{self.market}] warning threshold reached: "
                f"{pct:.1f}% at phase={phase} processed={processed} total={total}"
            )
            self._warned = True
