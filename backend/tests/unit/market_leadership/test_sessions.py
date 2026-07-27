"""Unit + leakage tests for sessions.py."""
import datetime

import pandas as pd
import pytest

from services.market_leadership.sessions import (
    slice_as_of, expected_latest_completed_us_session,
    expected_latest_completed_session, window_coverage,
)
from tests.unit.market_leadership.conftest import make_price_df


@pytest.mark.unit
class TestSliceAsOf:
    def test_excludes_bars_after_as_of(self):
        df = make_price_df(10)
        cutoff = df.index[5].date()
        sliced = slice_as_of(df, cutoff)
        assert sliced.index.max().date() == cutoff
        assert len(sliced) == 6

    def test_empty_df_returns_empty(self):
        empty = pd.DataFrame(columns=["Close"])
        assert len(slice_as_of(empty, datetime.date.today())) == 0

    def test_non_datetime_index_raises(self):
        df = pd.DataFrame({"Close": [1, 2, 3]})
        with pytest.raises(TypeError):
            slice_as_of(df, datetime.date.today())

    def test_leakage_property_adding_future_rows_does_not_change_sliced_result(self):
        """Section 10's core leakage guarantee, expressed as a property
        test: for a fixed as_of, appending future bars to the source frame
        must not change the sliced-and-summed result."""
        base = make_price_df(200)
        cutoff = base.index[100].date()
        result_a = slice_as_of(base.iloc[:150], cutoff)["Close"].sum()
        result_b = slice_as_of(base, cutoff)["Close"].sum()  # base has 50 more future rows
        assert result_a == result_b


@pytest.mark.unit
class TestExpectedLatestCompletedUsSession:
    def test_weekday_morning_rolls_back_to_prior_day(self):
        now = datetime.datetime(2026, 7, 22, 10, 0, tzinfo=datetime.timezone.utc)  # Wed
        result = expected_latest_completed_us_session(now)
        assert result == datetime.date(2026, 7, 21)  # Tue

    def test_monday_rolls_back_to_friday(self):
        now = datetime.datetime(2026, 7, 20, 10, 0, tzinfo=datetime.timezone.utc)  # Mon
        result = expected_latest_completed_us_session(now)
        assert result.weekday() == 4  # Friday

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError):
            expected_latest_completed_us_session(datetime.datetime(2026, 7, 22))


@pytest.mark.unit
class TestExpectedLatestCompletedSession:
    def test_unsupported_market_raises(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        with pytest.raises(ValueError, match="unsupported market"):
            expected_latest_completed_session("XX", now)

    def test_us_market_routes_to_us_helper(self):
        now = datetime.datetime(2026, 7, 22, 10, 0, tzinfo=datetime.timezone.utc)
        assert expected_latest_completed_session("US", now) == expected_latest_completed_us_session(now)

    def test_in_market_returns_a_date(self):
        now = datetime.datetime(2026, 7, 22, 10, 0, tzinfo=datetime.timezone.utc)
        result = expected_latest_completed_session("IN", now)
        assert isinstance(result, datetime.date)


@pytest.mark.unit
class TestWindowCoverage:
    def test_full_coverage(self):
        df = make_price_df(100)
        assert window_coverage(df["Close"], 63) == pytest.approx(1.0)

    def test_partial_coverage(self):
        df = make_price_df(30)
        assert window_coverage(df["Close"], 63) == pytest.approx(30 / 63)

    def test_empty_series_zero_coverage(self):
        empty = pd.Series(dtype=float)
        assert window_coverage(empty, 63) == 0.0

    def test_coverage_capped_at_one(self):
        df = make_price_df(200)
        assert window_coverage(df["Close"], 63) == 1.0
