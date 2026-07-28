"""
Trade Postmortem Sprint 3A, Pre-Stage-H Correction 1 — session-boundary
resolution tests. Covers the full enumerated scenario list: exact
session open/close for both markets, intraday entry, DST, holiday,
early-close (documented as unsupported), before-open/after-close entry,
before-open exit, and same-day full/partial session trades.
"""
import datetime as dt

import pytest

from services.market_hours import ET, IST
from services.postmortem.price_path_evidence import (
    ENTRY_BAR_INCLUDED_FULL,
    ENTRY_BAR_PARTIAL_UNKNOWN,
    EXIT_BAR_INCLUDED_FULL,
    EXIT_BAR_PARTIAL_UNKNOWN,
)
from services.postmortem.session_boundary import (
    AMBIGUOUS_RESOLUTION,
    EARLY_CLOSE_UNSUPPORTED,
    SAME_DAY_FULL_SESSION,
    SessionBoundaryError,
    classify_entry_boundary,
    classify_exit_boundary,
    classify_same_day_trade,
    is_trading_day,
    next_trading_session,
    previous_trading_session,
    resolve_session,
)

# 2026-06-02 is a Tuesday, a normal NSE/US trading day for both markets.
IN_TRADING_DAY = dt.date(2026, 6, 2)
US_TRADING_DAY = dt.date(2026, 6, 2)
US_DST_DAY = dt.date(2026, 6, 15)  # well within US daylight saving time
US_HOLIDAY = dt.date(2026, 7, 4)   # Independence Day (observed)


@pytest.mark.unit
class TestIndiaSessionBoundaries:
    def test_exact_session_open(self):
        ts = dt.datetime(2026, 6, 2, 9, 15, tzinfo=IST)
        result = classify_entry_boundary("IN", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL

    def test_intraday_entry(self):
        ts = dt.datetime(2026, 6, 2, 12, 0, tzinfo=IST)
        result = classify_entry_boundary("IN", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN

    def test_exact_session_close_exit(self):
        ts = dt.datetime(2026, 6, 2, 15, 30, tzinfo=IST)
        result = classify_exit_boundary("IN", ts)
        assert result["policy"] == EXIT_BAR_INCLUDED_FULL


@pytest.mark.unit
class TestUSSessionBoundaries:
    def test_exact_session_open(self):
        ts = dt.datetime(2026, 6, 2, 9, 30, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL

    def test_intraday_entry(self):
        ts = dt.datetime(2026, 6, 2, 13, 0, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN

    def test_exact_session_close_exit(self):
        ts = dt.datetime(2026, 6, 2, 16, 0, tzinfo=ET)
        result = classify_exit_boundary("US", ts)
        assert result["policy"] == EXIT_BAR_INCLUDED_FULL

    def test_dst_date_session_open_still_930_local(self):
        """US_DST_DAY is well inside daylight saving — 9:30 ET local
        time must still resolve to session open regardless of the
        underlying UTC offset ET/DST introduces."""
        ts = dt.datetime(2026, 6, 15, 9, 30, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL
        session = resolve_session("US", US_DST_DAY)
        assert session["open"].utcoffset() == dt.timedelta(hours=-4)  # EDT, not EST

    def test_holiday_is_not_a_trading_day(self):
        assert is_trading_day("US", US_HOLIDAY) is False
        ts = dt.datetime(2026, 7, 4, 10, 0, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert "non-trading day" in " ".join(result["limitations"])


@pytest.mark.unit
class TestEarlyCloseDocumentedLimitation:
    def test_every_session_resolution_discloses_early_close_unsupported(self):
        """The current market calendar cannot prove any early-close
        schedule — every resolution explicitly says so rather than
        silently assuming standard hours are always correct."""
        session = resolve_session("US", US_TRADING_DAY)
        assert EARLY_CLOSE_UNSUPPORTED in session["limitations"]
        session_in = resolve_session("IN", IN_TRADING_DAY)
        assert EARLY_CLOSE_UNSUPPORTED in session_in["limitations"]


@pytest.mark.unit
class TestBeforeOpenAfterClose:
    def test_before_open_entry(self):
        ts = dt.datetime(2026, 6, 2, 6, 0, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN
        assert "before official session open" in " ".join(result["limitations"])

    def test_after_close_entry_shifts_to_next_session(self):
        ts = dt.datetime(2026, 6, 2, 20, 0, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] is None
        assert result["effective_session_date"] == next_trading_session("US", US_TRADING_DAY)

    def test_before_open_exit_shifts_to_previous_session(self):
        ts = dt.datetime(2026, 6, 2, 6, 0, tzinfo=ET)
        result = classify_exit_boundary("US", ts)
        assert result["policy"] is None
        assert result["effective_session_date"] == previous_trading_session("US", US_TRADING_DAY)


@pytest.mark.unit
class TestSameDayTrades:
    def test_same_day_full_session_trade(self):
        entry = dt.datetime(2026, 6, 2, 9, 30, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 2, 16, 0, tzinfo=ET)
        result = classify_same_day_trade("US", entry, exit_ts)
        assert result["resolution"] == SAME_DAY_FULL_SESSION

    def test_same_day_partial_session_trade_is_ambiguous(self):
        entry = dt.datetime(2026, 6, 2, 10, 0, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 2, 14, 0, tzinfo=ET)
        result = classify_same_day_trade("US", entry, exit_ts)
        assert result["resolution"] == AMBIGUOUS_RESOLUTION

    def test_same_day_entry_after_open_but_exit_at_close_is_ambiguous(self):
        entry = dt.datetime(2026, 6, 2, 10, 0, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 2, 16, 0, tzinfo=ET)
        result = classify_same_day_trade("US", entry, exit_ts)
        assert result["resolution"] == AMBIGUOUS_RESOLUTION


@pytest.mark.unit
class TestMiscBoundaryHygiene:
    def test_naive_timestamp_rejected(self):
        with pytest.raises(SessionBoundaryError):
            classify_entry_boundary("US", dt.datetime(2026, 6, 2, 9, 30))

    def test_unknown_market_rejected(self):
        with pytest.raises(SessionBoundaryError):
            resolve_session("XX", US_TRADING_DAY)

    def test_next_trading_session_skips_weekend(self):
        friday = dt.date(2026, 6, 5)  # Friday
        assert next_trading_session("US", friday) == dt.date(2026, 6, 8)  # Monday

    def test_previous_trading_session_skips_weekend(self):
        monday = dt.date(2026, 6, 8)
        assert previous_trading_session("US", monday) == dt.date(2026, 6, 5)
