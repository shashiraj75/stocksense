"""
V-SCHED1C2B — RED/GREEN tests for services/market_calendar.py, the
trusted (package-shipped, offline, fail-closed) exchange-calendar
resolver for automatic short-horizon scheduling.

All dates/durations used below were independently confirmed directly
against the installed pandas_market_calendars==5.4.0 package before
writing these assertions (not assumed) — see the V-SCHED1C2A/B
investigation notes. In particular:
  - XNSE regular session: 03:45-10:00 UTC (09:15-15:30 IST), 375 min.
  - NYSE regular session (EDT, e.g. 2026-08-13): 13:30-20:00 UTC.
  - NYSE regular session (EST, e.g. 2026-01-05): 14:30-21:00 UTC.
  - NYSE early close (2026-11-27, day after Thanksgiving): 14:30-18:00
    UTC — a real 210-minute session, confirmed via direct package query.
  - XNSE silently returns "open" weekdays with zero holiday exclusion
    for dates in 2028 (beyond its own lunar-holiday data) — confirmed
    directly, not assumed — which is exactly why
    NSE_CALENDAR_SUPPORTED_THROUGH must be enforced by this module's own
    code.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from services.market_calendar import (
    NSE_CALENDAR_SUPPORTED_THROUGH,
    NSE_FULL_SESSION_MIN_MINUTES,
    ShortSessionResolution,
    resolve_latest_completed_short_session,
)


# ─────────────────────────────────────────────────────────────────────────
# 1-2: universe -> exchange mapping
# ─────────────────────────────────────────────────────────────────────────

def test_nifty100_and_midcap_map_to_nse():
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)  # well after 2026-08-13's XNSE close
    for universe in ("nifty100", "midcap"):
        r = resolve_latest_completed_short_session(universe, now_utc=now)
        assert r.exchange == "XNSE"


def test_us_maps_to_nyse():
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("us", now_utc=now)
    assert r.exchange == "NYSE"


# ─────────────────────────────────────────────────────────────────────────
# 3-4: normal completed session resolution
# ─────────────────────────────────────────────────────────────────────────

def test_normal_completed_nse_session_resolves():
    # 2026-08-13's XNSE session closes 10:00 UTC; evaluate well after.
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("nifty100", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date == date(2026, 8, 13)
    assert r.close_utc == datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    assert r.open_utc == datetime(2026, 8, 13, 3, 45, tzinfo=timezone.utc)


def test_normal_completed_nyse_session_resolves():
    # 2026-08-13's NYSE session (EDT) closes 20:00 UTC; evaluate at the
    # production short-scheduler instant: 03:30 IST on 2026-08-14 = 22:00
    # UTC on 2026-08-13.
    now = datetime(2026, 8, 13, 22, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("us", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date == date(2026, 8, 13)
    assert r.close_utc == datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────
# 5-8: weekend / holiday / cross-market independence
# ─────────────────────────────────────────────────────────────────────────

def test_saturday_creates_no_new_nse_obligation_beyond_friday():
    # Evaluate as of Saturday 2026-08-15 03:00 UTC (still Friday night IST-
    # adjacent) — the latest completed session must be Friday 2026-08-14,
    # not a manufactured Saturday session.
    now = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("nifty100", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date == date(2026, 8, 14)
    assert r.session_date.weekday() < 5


def test_sunday_still_resolves_to_last_real_session_not_sunday():
    now = datetime(2026, 8, 16, 3, 0, tzinfo=timezone.utc)  # Sunday
    r = resolve_latest_completed_short_session("us", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date.weekday() < 5


def test_nse_holiday_is_excluded_independence_2026_08_15():
    # 2026-08-15 is Independence Day (NSE fixed holiday) — confirm the
    # resolver skips it and lands on the prior real session, not on the
    # holiday date itself.
    now = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("nifty100", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date != date(2026, 8, 15)


def test_one_market_open_other_closed_remains_independent():
    # 2026-08-15 is an NSE holiday (Independence Day) but an ordinary
    # trading day for NYSE (a Saturday actually — use a genuine
    # NSE-holiday-on-a-US-trading-weekday instead: 2026-01-26 Republic
    # Day, a Monday).
    now = datetime(2026, 1, 27, 3, 0, tzinfo=timezone.utc)
    nse_result = resolve_latest_completed_short_session("nifty100", now_utc=now)
    us_result = resolve_latest_completed_short_session("us", now_utc=now)
    assert nse_result.session_date != date(2026, 1, 26)  # NSE was closed
    # US result is entirely independent of NSE's holiday calendar
    assert us_result.exchange == "NYSE"
    assert us_result.status == "eligible"


# ─────────────────────────────────────────────────────────────────────────
# 9: EST/EDT correctness at the exact 03:30 IST production instant
# ─────────────────────────────────────────────────────────────────────────

def test_edt_resolves_correct_previous_session_at_0330_ist():
    # 03:30 IST on 2026-08-14 = 22:00 UTC on 2026-08-13 (EDT in effect).
    now = datetime(2026, 8, 13, 22, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("us", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date == date(2026, 8, 13)


def test_est_resolves_correct_previous_session_at_0330_ist():
    # 03:30 IST on 2026-01-06 = 22:00 UTC on 2026-01-05 (EST in effect).
    now = datetime(2026, 1, 5, 22, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("us", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date == date(2026, 1, 5)


# ─────────────────────────────────────────────────────────────────────────
# 10-11: NYSE early close eligible; NSE shortened session excluded
# ─────────────────────────────────────────────────────────────────────────

def test_nyse_early_close_session_is_eligible():
    # 2026-11-27 (day after Thanksgiving) is a real, confirmed 210-minute
    # NYSE early-close session (14:30-18:00 UTC) — must still be eligible.
    now = datetime(2026, 11, 27, 20, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("us", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date == date(2026, 11, 27)
    duration_min = (r.close_utc - r.open_utc).total_seconds() / 60
    assert duration_min < 390  # shorter than a regular session
    assert duration_min == 210


def test_shortened_nse_session_is_excluded_generically(monkeypatch):
    """No real shortened NSE session exists in the installed calendar's
    data (XNSE excludes Muhurat as a full holiday, not a short session),
    so this proves the GENERIC duration-threshold mechanism directly by
    injecting a synthetic short session via a monkeypatched calendar —
    not by hardcoding a Muhurat date, matching the settled requirement
    that this be implemented generically."""
    import pandas as pd
    import services.market_calendar as mc

    class _FakeCalendar:
        def schedule(self, start_date, end_date):
            idx = pd.to_datetime(["2026-08-12", "2026-08-13"])
            return pd.DataFrame({
                "market_open": pd.to_datetime([
                    "2026-08-12T03:45:00Z", "2026-08-13T13:00:00Z",
                ]),
                "market_close": pd.to_datetime([
                    "2026-08-12T10:00:00Z", "2026-08-13T14:00:00Z",  # 60-min shortened session
                ]),
            }, index=idx)

    monkeypatch.setattr(mc.mcal, "get_calendar", lambda name: _FakeCalendar())
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("nifty100", now_utc=now)
    assert r.status == "eligible"
    assert r.session_date == date(2026, 8, 12)  # the regular session, not the shortened one


# ─────────────────────────────────────────────────────────────────────────
# 12-14: fail-closed behavior
# ─────────────────────────────────────────────────────────────────────────

def test_date_after_nse_boundary_fails_closed():
    now = datetime(NSE_CALENDAR_SUPPORTED_THROUGH.year + 1, 6, 1, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("nifty100", now_utc=now)
    assert r.status == "unknown"
    assert r.reason == "nse_beyond_supported_boundary"


def test_us_date_far_in_future_does_not_get_nse_boundary_treatment():
    # US is not NSE-gated by the boundary constant — confirm no false
    # positive fail-closed for US at the same future instant.
    now = datetime(NSE_CALENDAR_SUPPORTED_THROUGH.year + 1, 6, 1, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("us", now_utc=now)
    assert r.status == "eligible"  # NYSE is rule-generated indefinitely


def test_calendar_exception_fails_closed(monkeypatch):
    import services.market_calendar as mc

    def _raise(name):
        raise RuntimeError("simulated calendar package failure")

    monkeypatch.setattr(mc.mcal, "get_calendar", _raise)
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("nifty100", now_utc=now)
    assert r.status == "unknown"
    assert r.reason == "calendar_unavailable"
    assert "RuntimeError" not in r.reason and "simulated" not in r.reason


def test_empty_calendar_schedule_fails_closed(monkeypatch):
    import pandas as pd
    import services.market_calendar as mc

    class _EmptyCalendar:
        def schedule(self, start_date, end_date):
            return pd.DataFrame({"market_open": [], "market_close": []})

    monkeypatch.setattr(mc.mcal, "get_calendar", lambda name: _EmptyCalendar())
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("nifty100", now_utc=now)
    assert r.status == "unknown"
    assert r.reason == "no_sessions_in_lookback_window"


def test_naive_now_utc_fails_closed():
    naive = datetime(2026, 8, 14, 3, 0)  # no tzinfo
    r = resolve_latest_completed_short_session("nifty100", now_utc=naive)
    assert r.status == "unknown"
    assert r.reason == "now_utc_not_timezone_aware"


def test_unsupported_universe_fails_closed():
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("not_a_real_universe", now_utc=now)
    assert r.status == "unknown"
    assert r.reason == "unsupported_universe"


# ─────────────────────────────────────────────────────────────────────────
# 15: all returned datetimes are timezone-aware
# ─────────────────────────────────────────────────────────────────────────

def test_all_returned_datetimes_are_timezone_aware():
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    for universe in ("nifty100", "midcap", "us"):
        r = resolve_latest_completed_short_session(universe, now_utc=now)
        assert r.status == "eligible"
        assert r.open_utc.tzinfo is not None
        assert r.close_utc.tzinfo is not None
        assert r.open_utc.tzinfo == timezone.utc or r.open_utc.utcoffset() == timedelta(0)
        assert r.close_utc.tzinfo == timezone.utc or r.close_utc.utcoffset() == timedelta(0)


# ─────────────────────────────────────────────────────────────────────────
# 16-17: canonical slot == calendar close; idempotent repeated resolution
# ─────────────────────────────────────────────────────────────────────────

def test_canonical_slot_is_exactly_the_calendar_reported_utc_close():
    now = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    r = resolve_latest_completed_short_session("nifty100", now_utc=now)
    assert r.close_utc == datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def test_repeated_resolution_of_same_session_is_identical():
    now1 = datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc)
    now2 = datetime(2026, 8, 14, 5, 0, tzinfo=timezone.utc)  # later, same completed session
    r1 = resolve_latest_completed_short_session("nifty100", now_utc=now1)
    r2 = resolve_latest_completed_short_session("nifty100", now_utc=now2)
    assert r1.session_date == r2.session_date
    assert r1.close_utc == r2.close_utc
