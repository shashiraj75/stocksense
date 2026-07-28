"""
Trade Postmortem Sprint 3A, Stage 1 (final provider-contract hardening)
— proves the yfinance start-inclusive/end-exclusive convention is
handled correctly: the provider call widens its own end boundary only
to physically retrieve the exit session, but build_price_path_evidence
always re-filters against the ORIGINAL requested window so no later
session, weekend, or holiday-boundary row can ever leak into persisted
evidence. All fixtures are hand-built dicts — no live network call.
"""
import datetime as dt

import pytest

from services.market_hours import ET
from services.postmortem.price_path_acquisition import build_price_path_evidence
from services.postmortem.price_path_evidence import STATUS_COMPLETE, STATUS_PARTIAL


def _bar(d, *, o=100, h=101, l=99, c=100):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": 1000.0, "adj_close": None, "dividend": 0.0}


def _build(raw_bars, entry_date, exit_date):
    return build_price_path_evidence(
        paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
        market_timezone_name="America/New_York", market_tzinfo=ET,
        entry_timestamp=dt.datetime(entry_date.year, entry_date.month, entry_date.day, tzinfo=ET),
        exit_timestamp=dt.datetime(exit_date.year, exit_date.month, exit_date.day, tzinfo=ET),
        raw_bars=raw_bars, split_events=[],
    )


@pytest.mark.unit
class TestInclusiveExclusiveDateHandling:
    def test_entry_date_included(self):
        entry, exit_ = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bundle = _build([_bar(entry), _bar(dt.date(2026, 6, 2)), _bar(exit_)], entry, exit_)
        dates = {b.session_date for b in bundle.bars}
        assert entry in dates

    def test_exit_date_included(self):
        entry, exit_ = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bundle = _build([_bar(entry), _bar(dt.date(2026, 6, 2)), _bar(exit_)], entry, exit_)
        dates = {b.session_date for b in bundle.bars}
        assert exit_ in dates

    def test_session_after_exit_excluded(self):
        """Even if the provider (or, in this test, a hand-built fixture
        simulating one) returns an extra row for the day after exit —
        exactly what widening `end` by one day to reach the exit session
        risks — it must never survive into the persisted bundle."""
        entry, exit_ = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        leaked_row = _bar(dt.date(2026, 6, 4))  # the day AFTER exit
        bundle = _build([_bar(entry), _bar(dt.date(2026, 6, 2)), _bar(exit_), leaked_row], entry, exit_)
        dates = {b.session_date for b in bundle.bars}
        assert dt.date(2026, 6, 4) not in dates
        assert bundle.bars_observed == 3

    def test_weekend_after_exit_excluded(self):
        entry, exit_ = dt.date(2026, 6, 1), dt.date(2026, 6, 5)  # Friday exit
        weekend_row = _bar(dt.date(2026, 6, 6))  # Saturday — should never appear anyway, but prove exclusion
        bundle = _build([_bar(entry), _bar(exit_), weekend_row], entry, exit_)
        dates = {b.session_date for b in bundle.bars}
        assert dt.date(2026, 6, 6) not in dates

    def test_holiday_boundary_bar_within_window_retained(self):
        """A bar dated exactly on the requested exit date must be kept
        even though the window's own `end` gets widened by the
        acquisition layer for the underlying provider call."""
        entry, exit_ = dt.date(2026, 6, 1), dt.date(2026, 7, 3)  # US holiday (July 4) is the day after exit
        bundle = _build([_bar(entry), _bar(exit_)], entry, exit_)
        dates = {b.session_date for b in bundle.bars}
        assert exit_ in dates
        assert dt.date(2026, 7, 4) not in dates  # holiday would never be a trading bar anyway, but never leaks either

    def test_provider_extra_row_does_not_change_completeness_falsely(self):
        """One extra (post-exit) row must not fool completeness into
        thinking MORE of the requested window was observed than it was."""
        entry, exit_ = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bundle = _build([_bar(entry), _bar(exit_), _bar(dt.date(2026, 6, 4))], entry, exit_)
        assert bundle.observed_window_end == exit_
        assert bundle.data_completeness == STATUS_COMPLETE

    def test_pre_entry_row_also_excluded(self):
        entry, exit_ = dt.date(2026, 6, 2), dt.date(2026, 6, 4)
        bundle = _build([_bar(dt.date(2026, 6, 1)), _bar(entry), _bar(exit_)], entry, exit_)
        dates = {b.session_date for b in bundle.bars}
        assert dt.date(2026, 6, 1) not in dates
        assert bundle.bars_observed == 2
