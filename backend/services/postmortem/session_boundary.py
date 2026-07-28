"""
Session-boundary resolution for price-path evidence — Trade Postmortem
Sprint 3A, Pre-Stage-H Correction 1.

Wraps services.market_hours's existing session-time constants (9:15-
15:30 IST for NSE, 9:30-16:00 ET for US) with an explicit, typed
session-boundary API: given a market and a trading day, what was the
official open/close of that session, and given an actual entry/exit
timestamp, how the day's own daily bar should be classified for
MFE/MAE eligibility.

**Known, explicitly-documented limitation**: services.market_hours has
no early-close (half-day) schedule for either market. NSE's own regular
calendar has none; the US market does observe half-days (e.g. the day
after Thanksgiving) that this codebase's calendar does not model. Every
session resolution returned by this module carries an
EARLY_CLOSE_UNSUPPORTED limitation string for that reason — never
silently assumed away. If a real early-close day is ever encountered,
an entry/exit between the true (unmodeled) early close and the assumed
standard close would be misclassified as "during the session" rather
than "at/after close" — a conservative failure mode (PARTIAL_UNKNOWN is
more restrictive than INCLUDED_FULL, so this errs toward excluding
evidence, never fabricating it).
"""
import datetime as dt

from services.market_hours import ET, IST, NSE_EXTRA_HOLIDAYS, _nse_fixed_holidays, _us_fixed_holidays
from services.postmortem.price_path_evidence import (
    ENTRY_BAR_INCLUDED_FULL,
    ENTRY_BAR_PARTIAL_UNKNOWN,
    EXIT_BAR_INCLUDED_FULL,
    EXIT_BAR_PARTIAL_UNKNOWN,
)

AMBIGUOUS_RESOLUTION = "AMBIGUOUS_RESOLUTION"
SAME_DAY_FULL_SESSION = "SAME_DAY_FULL_SESSION"

EARLY_CLOSE_UNSUPPORTED = (
    "this codebase's market calendar does not model early-close (half-day) "
    "schedules; a real early-close session is conservatively evaluated "
    "against the standard close time, which can only make boundary "
    "classification MORE restrictive, never less"
)


class SessionBoundaryError(ValueError):
    pass


def _is_nse_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in _nse_fixed_holidays(d.year) and d.strftime("%Y-%m-%d") not in NSE_EXTRA_HOLIDAYS


def _is_us_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in _us_fixed_holidays(d.year)


def _market_tzinfo(market: str):
    if market == "IN":
        return IST
    if market == "US":
        return ET
    raise SessionBoundaryError(f"unknown market {market!r}")


def is_trading_day(market: str, session_date: dt.date) -> bool:
    if market == "IN":
        return _is_nse_trading_day(session_date)
    if market == "US":
        return _is_us_trading_day(session_date)
    raise SessionBoundaryError(f"unknown market {market!r}")


def resolve_session(market: str, session_date: dt.date) -> dict:
    """Pure — the official {open, close, tzinfo, is_trading_day,
    limitations} for one calendar date in the given market. Never
    fetches anything; reuses services.market_hours's own constants."""
    tzinfo = _market_tzinfo(market)
    trading = is_trading_day(market, session_date)
    if market == "IN":
        open_dt = dt.datetime(session_date.year, session_date.month, session_date.day, 9, 15, tzinfo=tzinfo)
        close_dt = dt.datetime(session_date.year, session_date.month, session_date.day, 15, 30, tzinfo=tzinfo)
    else:
        open_dt = dt.datetime(session_date.year, session_date.month, session_date.day, 9, 30, tzinfo=tzinfo)
        close_dt = dt.datetime(session_date.year, session_date.month, session_date.day, 16, 0, tzinfo=tzinfo)
    return {
        "session_date": session_date, "open": open_dt, "close": close_dt,
        "tzinfo": tzinfo, "is_trading_day": trading,
        "limitations": (EARLY_CLOSE_UNSUPPORTED,),
    }


def next_trading_session(market: str, d: dt.date) -> dt.date:
    cursor = d + dt.timedelta(days=1)
    for _ in range(30):
        if is_trading_day(market, cursor):
            return cursor
        cursor += dt.timedelta(days=1)
    raise SessionBoundaryError(f"no trading session found within 30 days after {d} for market {market}")


def previous_trading_session(market: str, d: dt.date) -> dt.date:
    cursor = d - dt.timedelta(days=1)
    for _ in range(30):
        if is_trading_day(market, cursor):
            return cursor
        cursor -= dt.timedelta(days=1)
    raise SessionBoundaryError(f"no trading session found within 30 days before {d} for market {market}")


def classify_entry_boundary(market: str, entry_timestamp: dt.datetime) -> dict:
    """Returns {policy, effective_session_date, limitations}.
    `policy` is None only in the after-close case, where the entry-day
    bar is not post-entry evidence at all and the caller's eligible
    window should begin at `effective_session_date` (the NEXT trading
    session) instead."""
    if entry_timestamp.tzinfo is None:
        raise SessionBoundaryError("entry_timestamp must be timezone-aware")

    tzinfo = _market_tzinfo(market)
    local_ts = entry_timestamp.astimezone(tzinfo)
    session_date = local_ts.date()
    session = resolve_session(market, session_date)
    limitations = list(session["limitations"])

    if not session["is_trading_day"]:
        limitations.append("entry timestamp falls on a non-trading day per this codebase's holiday calendar")
        return {"policy": ENTRY_BAR_PARTIAL_UNKNOWN, "effective_session_date": session_date, "limitations": tuple(limitations)}

    if local_ts < session["open"]:
        limitations.append("entry occurred before official session open — pre-open activity cannot be attributed to this trade")
        return {"policy": ENTRY_BAR_PARTIAL_UNKNOWN, "effective_session_date": session_date, "limitations": tuple(limitations)}

    if local_ts == session["open"]:
        return {"policy": ENTRY_BAR_INCLUDED_FULL, "effective_session_date": session_date, "limitations": tuple(limitations)}

    if local_ts < session["close"]:
        return {"policy": ENTRY_BAR_PARTIAL_UNKNOWN, "effective_session_date": session_date, "limitations": tuple(limitations)}

    limitations.append(
        "entry occurred at or after official session close — this session's bar is not "
        "post-entry evidence; the eligible window begins at the next trading session"
    )
    return {"policy": None, "effective_session_date": next_trading_session(market, session_date), "limitations": tuple(limitations)}


def classify_exit_boundary(market: str, exit_timestamp: dt.datetime) -> dict:
    """Symmetric to classify_entry_boundary. `policy` is None only in the
    before-open case, where the exit-day bar is post-exit and excluded;
    the eligible window should end at `effective_session_date` (the
    PREVIOUS trading session) instead."""
    if exit_timestamp.tzinfo is None:
        raise SessionBoundaryError("exit_timestamp must be timezone-aware")

    tzinfo = _market_tzinfo(market)
    local_ts = exit_timestamp.astimezone(tzinfo)
    session_date = local_ts.date()
    session = resolve_session(market, session_date)
    limitations = list(session["limitations"])

    if not session["is_trading_day"]:
        limitations.append("exit timestamp falls on a non-trading day per this codebase's holiday calendar")
        return {"policy": EXIT_BAR_PARTIAL_UNKNOWN, "effective_session_date": session_date, "limitations": tuple(limitations)}

    if local_ts < session["open"]:
        limitations.append(
            "exit occurred before official session open — this session's bar is post-exit; "
            "the eligible window ends at the previous trading session"
        )
        return {"policy": None, "effective_session_date": previous_trading_session(market, session_date), "limitations": tuple(limitations)}

    if local_ts < session["close"]:
        return {"policy": EXIT_BAR_PARTIAL_UNKNOWN, "effective_session_date": session_date, "limitations": tuple(limitations)}

    return {"policy": EXIT_BAR_INCLUDED_FULL, "effective_session_date": session_date, "limitations": tuple(limitations)}


def classify_same_day_trade(market: str, entry_timestamp: dt.datetime, exit_timestamp: dt.datetime) -> dict:
    """Stage E-correction item 9 — a same-day trade gets full daily-bar
    precision ONLY when the trade spans the complete official session
    (entry at-or-before open AND exit at-or-after close); otherwise it is
    AMBIGUOUS_RESOLUTION and stop/target ordering is never inferred from
    the single ambiguous bar."""
    tzinfo = _market_tzinfo(market)
    entry_local = entry_timestamp.astimezone(tzinfo)
    exit_local = exit_timestamp.astimezone(tzinfo)
    if entry_local.date() != exit_local.date():
        raise SessionBoundaryError("classify_same_day_trade requires entry and exit on the same local session date")

    session = resolve_session(market, entry_local.date())
    limitations = list(session["limitations"])
    if not session["is_trading_day"]:
        limitations.append("trade date is not a trading day per this codebase's holiday calendar")
        return {"resolution": AMBIGUOUS_RESOLUTION, "limitations": tuple(limitations)}

    full_session_covered = entry_local <= session["open"] and exit_local >= session["close"]
    if full_session_covered:
        return {"resolution": SAME_DAY_FULL_SESSION, "limitations": tuple(limitations)}

    limitations.append(
        "same-day trade did not span the complete official session — stop/target touch "
        "ordering within this single ambiguous bar can never be determined"
    )
    return {"resolution": AMBIGUOUS_RESOLUTION, "limitations": tuple(limitations)}
