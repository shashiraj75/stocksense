"""
V-SCHED1C2B — trusted, package-shipped exchange-calendar resolution for
automatic short-horizon scheduling.

Uses pandas_market_calendars (pinned pandas-market-calendars==5.4.0):
  - "XNSE" for nifty100 and midcap (NSE, India);
  - "NYSE" for us.

All calendar data is package-shipped at install time — no network calls,
no database access, no live fetch, at runtime. This module is a pure,
stateless, side-effect-free resolver: it never creates ledger state
itself (see api/main.py's short scheduler/catch-up for that).

FAIL-CLOSED CONTRACT — the single most important property of this
module: any exception, unsupported universe, non-timezone-aware input,
malformed schedule row, or NSE session date beyond
NSE_CALENDAR_SUPPORTED_THROUGH resolves to status="unknown", never
"eligible". Calendar uncertainty must never be silently treated as an
eligible trading session. Confirmed directly (not assumed) that
pandas_market_calendars' XNSE calendar does NOT raise or return an empty
schedule for dates beyond its own lunar/festival holiday data — it
silently continues generating "open" weekday rows indefinitely — so this
boundary is enforced by this module's own code, not delegated to the
library.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas_market_calendars as mcal

log = logging.getLogger(__name__)

# NSE lunar/festival holiday data in the underlying calendar package is
# only reliably populated through this date (directly verified: querying
# XNSE for 2028 returns every weekday as "open" with zero holidays
# excluded — no exception, no empty result). Any NSE-exchange
# determination for a session date after this boundary MUST fail closed
# as unknown/ineligible. Bump this constant only as part of a reviewed
# pandas-market-calendars package upgrade that re-verifies NSE holiday
# coverage through the new boundary — never bump it standalone.
NSE_CALENDAR_SUPPORTED_THROUGH = date(2027, 12, 31)

# A regular NSE session is ~6h15m (09:15-15:30 IST = 375 minutes).
# Muhurat and any other shortened/special session run roughly one hour.
# This is a generic duration threshold, not a hardcoded Muhurat date, so
# any future shortened session is excluded without a per-date update.
# 300 minutes (5 hours) sits comfortably below every observed regular
# NSE session length and comfortably above a ~1-hour special session.
NSE_FULL_SESSION_MIN_MINUTES = 300

_NSE_EXCHANGES = ("XNSE", "BSE")

_UNIVERSE_EXCHANGE = {
    "nifty100": "XNSE",
    "midcap": "XNSE",
    "us": "NYSE",
}

# Bounded lookback — comfortably longer than any credible NSE/NYSE
# holiday run (the longest realistic closure streak, e.g. a long weekend
# plus an adjacent holiday, is well under a week) while still bounded —
# this module must never walk an unbounded historical backlog.
_LOOKBACK_DAYS = 10


@dataclass(frozen=True)
class ShortSessionResolution:
    """Immutable result of resolve_latest_completed_short_session()."""
    status: str  # "eligible" | "ineligible" | "unknown"
    universe: str
    exchange: str | None
    session_date: date | None
    open_utc: datetime | None
    close_utc: datetime | None
    reason: str


def _unknown(universe: str, reason: str, exchange: str | None = None) -> ShortSessionResolution:
    return ShortSessionResolution(
        status="unknown", universe=universe, exchange=exchange,
        session_date=None, open_utc=None, close_utc=None, reason=reason,
    )


def resolve_latest_completed_short_session(universe: str, *, now_utc: datetime) -> ShortSessionResolution:
    """The latest COMPLETED, applicable exchange session for `universe`
    as of `now_utc` — or an "unknown"/"ineligible" result if none can be
    safely determined. Never manufactures a session from weekday
    arithmetic alone; every result is derived from the package-shipped
    calendar's own schedule.

    NYSE early-close sessions ARE eligible (a shorter session is still a
    real, completed session for the US market). NSE shortened/special
    sessions (e.g. Muhurat) are EXCLUDED in this version — see
    NSE_FULL_SESSION_MIN_MINUTES — by skipping them and continuing to
    walk backward for the last regular-length session, since a regular
    session usually did complete nearby, just not on that date.
    """
    if universe not in _UNIVERSE_EXCHANGE:
        return _unknown(universe, "unsupported_universe")
    if now_utc.tzinfo is None:
        return _unknown(universe, "now_utc_not_timezone_aware")

    exchange = _UNIVERSE_EXCHANGE[universe]
    is_nse = exchange in _NSE_EXCHANGES

    if is_nse and now_utc.date() > NSE_CALENDAR_SUPPORTED_THROUGH:
        return _unknown(universe, "nse_beyond_supported_boundary", exchange)

    try:
        calendar = mcal.get_calendar(exchange)
    except Exception:
        return _unknown(universe, "calendar_unavailable", exchange)

    start = (now_utc - timedelta(days=_LOOKBACK_DAYS)).date()
    end = now_utc.date()
    try:
        schedule = calendar.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    except Exception:
        return _unknown(universe, "calendar_schedule_error", exchange)

    if schedule is None or schedule.empty:
        return _unknown(universe, "no_sessions_in_lookback_window", exchange)

    try:
        closes = schedule["market_close"]
        opens = schedule["market_open"]
    except Exception:
        return _unknown(universe, "malformed_schedule", exchange)

    for session_ts in reversed(schedule.index):
        try:
            raw_close = closes.loc[session_ts]
            raw_open = opens.loc[session_ts]
            if raw_close.tzinfo is None or raw_open.tzinfo is None:
                return _unknown(universe, "naive_session_timestamp", exchange)
            close_utc = raw_close.to_pydatetime().astimezone(timezone.utc)
            open_utc = raw_open.to_pydatetime().astimezone(timezone.utc)
        except Exception:
            return _unknown(universe, "malformed_session_row", exchange)

        if close_utc > now_utc:
            continue  # not yet completed as of now_utc

        session_date = session_ts.date() if hasattr(session_ts, "date") else session_ts

        if is_nse and session_date > NSE_CALENDAR_SUPPORTED_THROUGH:
            return _unknown(universe, "nse_beyond_supported_boundary", exchange)

        if is_nse:
            duration_minutes = (close_utc - open_utc).total_seconds() / 60
            if duration_minutes < NSE_FULL_SESSION_MIN_MINUTES:
                continue  # shortened/special session — excluded in v1, keep looking

        return ShortSessionResolution(
            status="eligible", universe=universe, exchange=exchange,
            session_date=session_date, open_utc=open_utc, close_utc=close_utc,
            reason="ok",
        )

    return _unknown(universe, "no_completed_session_in_lookback_window", exchange)


# ── V-SCHED1C2D — shared VALIDATION_AUTO_SHORT_UNIVERSES parser ────────────
# Originally defined only in api/main.py (V-SCHED1C2B) for the scheduler's
# own gate. Moved here so BOTH the scheduler (api.main, which re-reads the
# environment every cycle) and the read-time freshness classification
# (services.validation_engine._short_universe_auto_scheduled) call the
# exact same parser — never two independently maintained copies of the
# same allowlist semantics that could silently drift apart. This module
# has no import of api.main or validation_engine, so importing this
# function from either introduces no circular import.

AUTO_SHORT_VALID_UNIVERSES = ("nifty100", "midcap", "us")


def parse_auto_short_universes(raw: str | None) -> tuple[str, ...]:
    """Strict parser for VALIDATION_AUTO_SHORT_UNIVERSES. This is both the
    automatic-short scheduler's feature gate and the staged-rollout
    control, and (as of V-SCHED1C2D) the single source of truth freshness
    classification also consults to decide whether a given short
    horizon/universe is currently auto-scheduled.

    Semantics:
      - missing/None or blank (after stripping): no automatic short
        scheduling — returns ().
      - a comma-separated subset of exactly "nifty100", "midcap", "us"
        (whitespace/case-normalized, duplicates collapsed) — returns
        that subset in the fixed canonical order (nifty100, midcap, us),
        regardless of input order.
      - ANY unrecognized token (including ambiguous values like "all",
        "true", "1") fails closed for the ENTIRE value — returns (),
        enabling NO universe, never a partial enable. This is
        deliberate: an operator typo must never silently activate a
        subset different from what was intended.

    Never logs the raw environment value — only that it was rejected —
    so a typo'd or malformed value can never leak into logs verbatim.
    """
    if not raw or not raw.strip():
        return ()
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    if not tokens:
        return ()
    seen: list[str] = []
    for token in tokens:
        if token not in AUTO_SHORT_VALID_UNIVERSES:
            log.warning(
                "[validation_short_scheduler] VALIDATION_AUTO_SHORT_UNIVERSES contains an "
                "unrecognized token — disabling all automatic short scheduling until corrected."
            )
            return ()
        if token not in seen:
            seen.append(token)
    return tuple(u for u in AUTO_SHORT_VALID_UNIVERSES if u in seen)


# ---------------------------------------------------------------------------
# Weekly validation schedule (medium + long horizons) — 2026-09 change from
# "medium daily / long weekly-Sunday" (both anchored to 06:00 IST) to a
# single consolidated weekly window: every Saturday at 12:00 UTC. Anchored
# to UTC (not IST/Dubai) so the trigger instant never depends on which
# display timezone a reader has in mind — IST/Dubai equivalents are for
# logging/display only, computed FROM this UTC value, never the reverse.
#
# `calendar.SATURDAY` (not a bare numeric literal) is used deliberately —
# datetime.weekday()'s Monday=0..Sunday=6 convention is easy to
# transpose-error against a library that uses a different day-zero (e.g.
# APScheduler's cron trigger, ISO 8601 weekday numbers, or crontab's
# Sunday=0). This module doesn't use such a library, but naming the
# constant removes any ambiguity for a future reader or a future library
# migration.
#
# Single shared implementation: api.main's `_validation_schedule_loop` (the
# live trigger) and `_catchup_validation` (startup catch-up), and
# api.routers.validation's `/status` endpoint (the displayed "next run"),
# all call these two functions — never three independently maintained
# copies of the same day-of-week arithmetic.
import calendar as _calendar

VALIDATION_WEEKLY_SCHEDULE_HOUR_UTC = 12
VALIDATION_WEEKLY_SCHEDULE_WEEKDAY = _calendar.SATURDAY


def next_saturday_1200_utc(now_utc: datetime) -> datetime:
    """The next Saturday 12:00 UTC strictly after `now_utc` (if `now_utc` is
    already at/past this week's Saturday 12:00 UTC, returns next week's;
    otherwise returns this week's). `now_utc` must be timezone-aware UTC."""
    candidate = now_utc.replace(
        hour=VALIDATION_WEEKLY_SCHEDULE_HOUR_UTC, minute=0, second=0, microsecond=0,
    )
    days_ahead = (VALIDATION_WEEKLY_SCHEDULE_WEEKDAY - candidate.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now_utc:
        candidate += timedelta(days=7)
    return candidate


def last_saturday_1200_utc(now_utc: datetime) -> datetime:
    """The most recent Saturday 12:00 UTC at or before `now_utc` — the
    inverse of next_saturday_1200_utc, used by startup catch-up to identify
    "this week's" scheduled slot regardless of which day catch-up runs on.
    `now_utc` must be timezone-aware UTC."""
    candidate = now_utc.replace(
        hour=VALIDATION_WEEKLY_SCHEDULE_HOUR_UTC, minute=0, second=0, microsecond=0,
    )
    days_back = (candidate.weekday() - VALIDATION_WEEKLY_SCHEDULE_WEEKDAY) % 7
    candidate -= timedelta(days=days_back)
    if candidate > now_utc:
        candidate -= timedelta(days=7)
    return candidate
