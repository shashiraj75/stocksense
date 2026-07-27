"""
Regression tests for services.memory_guard (2026-07-21 memory-exhaustion
postmortem — Railway process memory repeatedly hit its container ceiling,
peaking at ~7.97 GB, shortly before a US Daily Picks job was killed
mid-`ranking`). Deterministic — a fake memory reader stands in for real
cgroup/psutil telemetry so thresholds are exercised exactly, with no
dependency on actual host memory state.
"""
from unittest.mock import patch

import pytest


def _fake_reader(used, limit):
    return lambda: (used, limit)


# ─── _float_env: rejects non-finite values (2026-07-21 independent review) ─

def test_float_env_rejects_nan_and_falls_back_to_default():
    """float("nan") parses successfully, but `pct >= nan` is always False
    in Python — a malformed env value that happens to parse as NaN would
    otherwise silently and permanently disable whichever threshold it set,
    reintroducing the exact unbounded-OOM-kill failure this module exists
    to prevent, with no warning logged."""
    from services.memory_guard import _float_env
    with patch.dict("os.environ", {"DAILY_PICKS_MEM_ABORT_PCT": "nan"}):
        assert _float_env("DAILY_PICKS_MEM_ABORT_PCT", 80.0) == 80.0


def test_float_env_rejects_positive_and_negative_infinity():
    from services.memory_guard import _float_env
    with patch.dict("os.environ", {"DAILY_PICKS_MEM_ABORT_PCT": "inf"}):
        assert _float_env("DAILY_PICKS_MEM_ABORT_PCT", 80.0) == 80.0
    with patch.dict("os.environ", {"DAILY_PICKS_MEM_ABORT_PCT": "-inf"}):
        assert _float_env("DAILY_PICKS_MEM_ABORT_PCT", 80.0) == 80.0


def test_float_env_still_accepts_valid_finite_overrides():
    """The fix must not reject legitimate values — only non-finite ones."""
    from services.memory_guard import _float_env
    with patch.dict("os.environ", {"DAILY_PICKS_MEM_ABORT_PCT": "75.5"}):
        assert _float_env("DAILY_PICKS_MEM_ABORT_PCT", 80.0) == 75.5


def test_abort_threshold_still_fires_when_env_is_malformed_nan():
    """End-to-end: a NaN-valued abort threshold must not silently disable
    the circuit breaker — it must fall back to the safe numeric default
    and still raise when usage crosses it."""
    from services.memory_guard import MemoryCircuitBreaker, DailyPicksMemoryLimitError, _float_env

    with patch.dict("os.environ", {"DAILY_PICKS_MEM_ABORT_PCT": "nan"}):
        abort_pct = _float_env("DAILY_PICKS_MEM_ABORT_PCT", 80.0)
    assert abort_pct == 80.0  # sane default, not NaN

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(85, 100), abort_pct=abort_pct)
    with pytest.raises(DailyPicksMemoryLimitError):
        breaker.check("phase_1")


def test_no_limit_visible_is_a_safe_no_op():
    """When no container-wide limit can be determined (local dev, psutil-
    only fallback), the breaker must never raise or warn — there is nothing
    safe to compare against."""
    from services.memory_guard import MemoryCircuitBreaker

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(500_000_000, None))
    breaker.check("phase_1", 10, 100)  # must not raise


def test_below_warning_threshold_is_silent():
    from services.memory_guard import MemoryCircuitBreaker

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(50, 100))  # 50%
    with patch("services.memory_guard.log") as mock_log:
        breaker.check("phase_1", 10, 100)
    mock_log.warning.assert_not_called()
    mock_log.error.assert_not_called()


def test_warning_threshold_logs_once_per_run():
    from services.memory_guard import MemoryCircuitBreaker

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(66, 100))  # 66% >= 65%
    with patch("services.memory_guard.log") as mock_log:
        breaker.check("phase_1", 10, 100)
        breaker.check("phase_1", 20, 100)
    assert mock_log.warning.call_count == 1  # not repeated every call


def test_cleanup_threshold_collects_garbage():
    from services.memory_guard import MemoryCircuitBreaker

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(73, 100))  # 73% >= 72%
    with patch("services.memory_guard.gc") as mock_gc:
        breaker.check("ranking_entry")
    mock_gc.collect.assert_called_once()


def test_abort_threshold_raises_bounded_error():
    """The abort error message must be bounded — phase/progress/percentage
    only, never a payload, credential, or provider response — since it
    becomes the job's durable last_error."""
    from services.memory_guard import MemoryCircuitBreaker, DailyPicksMemoryLimitError

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(81, 100))  # 81% >= 80%
    with pytest.raises(DailyPicksMemoryLimitError) as exc_info:
        breaker.check("persisting", 1188, 1188)

    msg = str(exc_info.value)
    assert "81.0%" in msg
    assert "persisting" in msg
    assert "1188" in msg
    # bounded — nothing resembling a path, token, or connection string
    for forbidden in ("DATABASE_URL", "PICKS_SECRET", "://", "postgres"):
        assert forbidden not in msg


def test_abort_takes_priority_over_cleanup_and_warning():
    from services.memory_guard import MemoryCircuitBreaker, DailyPicksMemoryLimitError

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(95, 100))
    with pytest.raises(DailyPicksMemoryLimitError):
        breaker.check("phase_1")


def test_thresholds_are_configurable_and_not_hardcoded_to_8gb():
    """Thresholds are percentages of whatever limit is actually detected —
    never a hardcoded absolute byte ceiling like '8 GB'."""
    from services.memory_guard import MemoryCircuitBreaker, DailyPicksMemoryLimitError

    # A tiny 1 KB "container" — proves the check works off percentages of
    # the DETECTED limit, not a hardcoded large constant.
    breaker = MemoryCircuitBreaker(
        "US", reader=_fake_reader(900, 1000), abort_pct=80,
    )
    with pytest.raises(DailyPicksMemoryLimitError):
        breaker.check("phase_1")


def test_cgroup_v2_reader_parses_current_and_max(tmp_path):
    from services import memory_guard

    current = tmp_path / "memory.current"
    maxfile = tmp_path / "memory.max"
    current.write_text("104857600\n")   # 100 MiB
    maxfile.write_text("209715200\n")   # 200 MiB

    with patch.object(memory_guard, "_CGROUP_V2_CURRENT", str(current)), \
         patch.object(memory_guard, "_CGROUP_V2_MAX", str(maxfile)):
        used, limit = memory_guard.read_memory_usage()

    assert used == 104857600
    assert limit == 209715200


def test_cgroup_v2_unlimited_sentinel_is_treated_as_no_limit(tmp_path):
    from services import memory_guard

    current = tmp_path / "memory.current"
    maxfile = tmp_path / "memory.max"
    current.write_text("104857600\n")
    maxfile.write_text("max\n")

    with patch.object(memory_guard, "_CGROUP_V2_CURRENT", str(current)), \
         patch.object(memory_guard, "_CGROUP_V2_MAX", str(maxfile)):
        used, limit = memory_guard.read_memory_usage()

    assert limit is None


def test_cgroup_v1_unlimited_sentinel_is_treated_as_no_limit(tmp_path):
    """cgroup v1 reports an effectively-unlimited value (commonly
    2**63-1 rounded to a page boundary, not the literal string "max" v2
    uses) when no limit is configured — this must be treated the same as
    v2's sentinel, never misread as a real multi-exabyte container limit."""
    from services import memory_guard

    current = tmp_path / "memory.usage_in_bytes"
    maxfile = tmp_path / "memory.limit_in_bytes"
    current.write_text("104857600\n")
    maxfile.write_text("9223372036854771712\n")  # 2**63-1 rounded to a page

    with patch.object(memory_guard, "_CGROUP_V1_CURRENT", str(current)), \
         patch.object(memory_guard, "_CGROUP_V1_MAX", str(maxfile)), \
         patch.object(memory_guard, "_CGROUP_V2_CURRENT", "/nonexistent/memory.current"), \
         patch.object(memory_guard, "_CGROUP_V2_MAX", "/nonexistent/memory.max"):
        used, limit = memory_guard.read_memory_usage()

    assert limit is None


def test_cgroup_v1_reader_parses_real_limit_when_actually_set(tmp_path):
    from services import memory_guard

    current = tmp_path / "memory.usage_in_bytes"
    maxfile = tmp_path / "memory.limit_in_bytes"
    current.write_text("104857600\n")   # 100 MiB
    maxfile.write_text("209715200\n")   # 200 MiB — a real, small limit

    with patch.object(memory_guard, "_CGROUP_V1_CURRENT", str(current)), \
         patch.object(memory_guard, "_CGROUP_V1_MAX", str(maxfile)), \
         patch.object(memory_guard, "_CGROUP_V2_CURRENT", "/nonexistent/memory.current"), \
         patch.object(memory_guard, "_CGROUP_V2_MAX", "/nonexistent/memory.max"):
        used, limit = memory_guard.read_memory_usage()

    assert used == 104857600
    assert limit == 209715200
