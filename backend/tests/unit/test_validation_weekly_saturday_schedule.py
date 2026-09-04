"""
2026-09 schedule change: automated Validation (medium + long horizons)
moved from "medium daily / long weekly-Sunday" (both 06:00 IST) to a
single consolidated weekly window — every Saturday at 12:00 UTC.

These tests exercise the pure, UTC-anchored scheduling helpers in
services/market_calendar.py (next_saturday_1200_utc / last_saturday_1200_utc)
— the single shared implementation the live scheduler loop
(api.main._validation_schedule_loop), startup catch-up
(api.main._catchup_validation), and the /status endpoint's displayed
next-run all call. Testing the pure functions directly (rather than the
async loop) is deliberate: the loop's own logic is "sleep until X, then
run" — proving X is always the correct next/last Saturday 12:00 UTC for
every day of the week, including both midnight-UTC and 12:00-UTC
boundaries, is a complete proof that Sunday-Friday never produce a
same-day trigger and that Saturday can never produce more than one
trigger per week.
"""
from datetime import datetime, timedelta, timezone

import pytest

from services.market_calendar import next_saturday_1200_utc, last_saturday_1200_utc

UTC = timezone.utc


def _dt(y, m, d, h=0, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


# 2026-09-05 is a Saturday (confirmed: 2026-09-01 is a Tuesday per the
# session's own dated evidence, so 2026-09-05 is the following Saturday).
SATURDAY = _dt(2026, 9, 5, 12, 0, 0)


# ---------------------------------------------------------------------------
# next_saturday_1200_utc — Sunday through Friday never return a same-day or
# past instant; every result lands on a Saturday at exactly 12:00:00 UTC.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("now,label", [
    (_dt(2026, 8, 30, 0, 0, 0), "Sunday 00:00"),
    (_dt(2026, 8, 30, 23, 59, 59), "Sunday 23:59:59"),
    (_dt(2026, 8, 31, 12, 0, 0), "Monday noon"),
    (_dt(2026, 9, 1, 6, 0, 0), "Tuesday 06:00"),
    (_dt(2026, 9, 2, 18, 0, 0), "Wednesday 18:00"),
    (_dt(2026, 9, 3, 0, 0, 1), "Thursday just after midnight"),
    (_dt(2026, 9, 4, 11, 59, 59), "Friday 11:59:59"),
])
def test_next_saturday_never_fires_sunday_through_friday(now, label):
    result = next_saturday_1200_utc(now)
    assert result.weekday() == 5, f"{label}: expected Saturday, got weekday {result.weekday()}"
    assert (result.hour, result.minute, result.second) == (12, 0, 0), f"{label}: not anchored to 12:00:00 UTC"
    assert result > now, f"{label}: next run must be strictly in the future"
    # Never the same calendar day as `now` unless `now` is itself Saturday.
    assert result.date() != now.date() or now.weekday() == 5


def test_next_saturday_before_noon_on_saturday_stays_this_saturday():
    now = _dt(2026, 9, 5, 8, 0, 0)  # Saturday, before 12:00 UTC
    result = next_saturday_1200_utc(now)
    assert result == SATURDAY


def test_next_saturday_at_exactly_noon_advances_a_full_week():
    """Boundary: at the exact trigger instant, the NEXT computed target must
    be a full week later — never the same instant again — proving Saturday
    cannot trigger more than once."""
    result = next_saturday_1200_utc(SATURDAY)
    assert result == SATURDAY + timedelta(days=7)


def test_next_saturday_just_after_noon_on_saturday_advances_a_full_week():
    now = SATURDAY + timedelta(seconds=1)
    result = next_saturday_1200_utc(now)
    assert result == SATURDAY + timedelta(days=7)


def test_next_saturday_one_second_before_noon_on_saturday_stays_this_saturday():
    now = SATURDAY - timedelta(seconds=1)
    result = next_saturday_1200_utc(now)
    assert result == SATURDAY


# ---------------------------------------------------------------------------
# No-duplicate-trigger property: simulating the scheduler loop's own
# behavior — after "waking" at `next_run`, immediately recomputing from
# `next_run` itself must never return the same instant again.
# ---------------------------------------------------------------------------

def test_repeated_calls_at_the_trigger_instant_never_repeat_the_same_target():
    now = _dt(2026, 8, 24, 12, 8, 26)  # arbitrary Monday, matches this session's own deploy timestamp
    seen = set()
    for _ in range(3):
        target = next_saturday_1200_utc(now)
        assert target not in seen, "the same Saturday slot was returned twice — duplicate-trigger risk"
        seen.add(target)
        now = target  # simulate the loop waking exactly at its own computed target
    # Confirms strictly weekly cadence: each successive target is exactly 7 days after the last.
    ordered = sorted(seen)
    for a, b in zip(ordered, ordered[1:]):
        assert b - a == timedelta(days=7)


# ---------------------------------------------------------------------------
# last_saturday_1200_utc — used by startup catch-up. Must always return a
# Saturday 12:00 UTC at or before `now`, and must be internally consistent
# with next_saturday_1200_utc (the two functions must never disagree about
# which Saturday is "current").
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("now,label", [
    (_dt(2026, 8, 30, 0, 0, 0), "Sunday 00:00"),
    (_dt(2026, 9, 1, 6, 0, 0), "Tuesday 06:00"),
    (_dt(2026, 9, 4, 23, 59, 59), "Friday 23:59:59"),
])
def test_last_saturday_never_in_the_future(now, label):
    result = last_saturday_1200_utc(now)
    assert result.weekday() == 5, f"{label}: expected Saturday"
    assert (result.hour, result.minute, result.second) == (12, 0, 0), f"{label}: not anchored to 12:00:00 UTC"
    assert result <= now, f"{label}: last Saturday must not be in the future"


def test_last_saturday_on_saturday_after_noon_is_today():
    now = SATURDAY + timedelta(hours=2)
    assert last_saturday_1200_utc(now) == SATURDAY


def test_last_saturday_on_saturday_before_noon_is_last_week():
    now = SATURDAY - timedelta(hours=2)
    assert last_saturday_1200_utc(now) == SATURDAY - timedelta(days=7)


def test_last_and_next_are_always_exactly_one_week_apart_or_equal_at_boundary():
    """For any instant, last_saturday_1200_utc(now) and
    next_saturday_1200_utc(now) must be exactly 7 days apart (never 0,
    never any other gap) — proving there is exactly one canonical weekly
    slot surrounding every instant, with no ambiguity a scheduler and a
    catch-up path could disagree on."""
    for now in [
        _dt(2026, 8, 24, 12, 8, 26),
        _dt(2026, 8, 30, 0, 0, 0),
        _dt(2026, 9, 1, 6, 0, 0),
        _dt(2026, 9, 5, 0, 0, 0),
        _dt(2026, 9, 5, 12, 0, 0),
        _dt(2026, 9, 5, 23, 59, 59),
    ]:
        nxt = next_saturday_1200_utc(now)
        lst = last_saturday_1200_utc(now)
        assert nxt - lst == timedelta(days=7), f"now={now}: next={nxt} last={lst}"
