"""
Market-calendar helpers for the conviction-gate audit's EXECUTABLE return
measure.

Why this module exists
----------------------
`backend/scripts/conviction_gate_backtest.py` historically computed a single
return measure that entered at the first close on/after
`reference_session_date`. Daily Picks runs are generated PRE-MARKET (India
cron ~20:37 UTC, i.e. ~02:07 IST the following day; US cron ~06:00 UTC), so
that entry close is a PRIOR-SESSION price that no investor could have
transacted at by the time the pick existed.

That is NOT look-ahead bias in the classic sense — the price is strictly in
the past relative to the pick's own publication — but it is also NOT an
executable investor entry. It is a research reference price. The audit
therefore reports two explicitly-labelled measures side by side (see
`conviction_gate_backtest.RETURN_MEASURES`), and this module supplies the
calendar logic the executable one needs:

    the first REGULAR-SESSION OPEN strictly after `run_generated_at`.

Correctness requirements this module exists to satisfy:
  * weekends and exchange holidays are skipped (not approximated by +1 day),
  * DST is handled for both NYSE (13:30 UTC in EDT, 14:30 UTC in EST) and
    NSE (03:45 UTC year-round, India has no DST) — by delegating to
    `pandas_market_calendars`, never by hardcoding an offset,
  * a run generated *during* a session does not get that session's open
    (already un-transactable at the stated open price) — hence "strictly
    after the open", evaluated against the actual session open timestamp.

No network and no database access happens here; everything is derived from
the exchange calendars already vendored via the `pandas-market-calendars`
dependency (already in backend/requirements.txt — this module adds no new
third-party dependency).
"""

from __future__ import annotations

import datetime as _dt
import functools

# Exchange calendar identifiers. XNYS/XNSE are the ISO 10383 MIC codes;
# pandas_market_calendars also accepts the "NYSE"/"NSE" aliases, but the MIC
# is used here because it is unambiguous.
_MARKET_CALENDAR = {"US": "XNYS", "IN": "XNSE"}

# How far past a run timestamp we are ever willing to search for the next
# regular session. Comfortably longer than any real exchange closure while
# still bounding the search — a miss returns None rather than looping.
_MAX_SEARCH_DAYS = 30


class UnknownMarketError(ValueError):
    """Raised for a market with no configured exchange calendar."""


@functools.lru_cache(maxsize=8)
def _calendar(market: str):
    try:
        name = _MARKET_CALENDAR[market]
    except KeyError as exc:
        raise UnknownMarketError(
            f"no exchange calendar configured for market={market!r}; "
            f"known markets: {sorted(_MARKET_CALENDAR)}"
        ) from exc
    import pandas_market_calendars as mcal

    return mcal.get_calendar(name)


def _schedule(market: str, start: _dt.date, end: _dt.date):
    """UTC-indexed regular-session schedule between two dates, inclusive."""
    return _calendar(market).schedule(
        start_date=start.isoformat(), end_date=end.isoformat(), tz="UTC"
    )


def is_session(market: str, day: _dt.date) -> bool:
    """True when `day` is a regular trading session on `market`'s calendar."""
    return not _schedule(market, day, day).empty


def next_tradable_open(market: str, after) -> tuple[_dt.date, _dt.datetime] | None:
    """
    Return `(session_date, session_open_utc)` for the first regular session
    whose OPEN is strictly after the timezone-aware instant `after`.

    This is the executable entry point for a pick generated at `after`:
    the earliest moment a market order placed on the back of that pick could
    actually have been filled at a published price.

    Returns None if no session opens within `_MAX_SEARCH_DAYS` (the caller
    must then EXCLUDE the row rather than substitute an approximate entry —
    the audit never fabricates an entry it cannot place on a real session).
    """
    if after is None:
        return None
    after_utc = _as_utc(after)
    start = after_utc.date()
    sched = _schedule(market, start, start + _dt.timedelta(days=_MAX_SEARCH_DAYS))
    if sched.empty:
        return None
    for ts, row in sched.iterrows():
        open_utc = row["market_open"].to_pydatetime()
        if open_utc > after_utc:
            return (ts.date(), open_utc)
    return None


def session_offset(market: str, session_date: _dt.date, trading_days: int) -> _dt.date | None:
    """
    Return the session that is exactly `trading_days` regular sessions after
    the first session at/after `session_date` on `market`'s calendar.

    Returns None when that many sessions have not yet occurred — which is how
    the audit enforces "never resolve a partial horizon window". A partial
    window is an EXCLUSION, never a truncated or extrapolated return.
    """
    if trading_days < 0:
        return None
    # Search a generous calendar span: `trading_days` sessions can never span
    # more than ~1.75x that many calendar days plus a holiday allowance.
    span = int(trading_days * 1.75) + _MAX_SEARCH_DAYS
    sched = _schedule(market, session_date, session_date + _dt.timedelta(days=span))
    if sched.empty:
        return None
    days = [ts.date() for ts in sched.index]
    target = trading_days
    if target >= len(days):
        return None
    return days[target]


def _as_utc(value) -> _dt.datetime:
    """Coerce a date/datetime to a timezone-aware UTC datetime.

    A naive datetime is interpreted as UTC (every timestamp the audit reads
    comes from a `timestamptz` column, which psycopg returns tz-aware; the
    naive branch exists only for synthetic test inputs). A bare date is
    interpreted as that date's UTC midnight.
    """
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
    raise TypeError(f"cannot interpret {value!r} as a timestamp")
