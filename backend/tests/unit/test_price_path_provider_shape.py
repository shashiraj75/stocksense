"""
Trade Postmortem Sprint 3A, Stage 1 — provider response-shape handling
in fetch_raw_daily_bars: timezone-naive index, timezone-aware index,
MultiIndex rejection, missing actions columns, and one extra returned
row. Exercises the real function against a monkeypatched `yfinance`
module backed by hand-built pandas DataFrames — no live network call.
"""
import datetime as dt
import sys
import types

import pandas as pd
import pytest

from services.postmortem.price_path_acquisition import PriceProviderAcquisitionError, fetch_raw_daily_bars


class _FakeTicker:
    def __init__(self, df):
        self._df = df

    def history(self, **kwargs):
        return self._df


def _install_fake_yfinance(monkeypatch, df):
    fake_module = types.SimpleNamespace(Ticker=lambda symbol: _FakeTicker(df))
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)


def _flat_df(dates, tz=None):
    index = pd.DatetimeIndex(dates, tz=tz)
    return pd.DataFrame({
        "Open": [100.0] * len(dates), "High": [101.0] * len(dates),
        "Low": [99.0] * len(dates), "Close": [100.5] * len(dates),
        "Volume": [1000.0] * len(dates), "Dividends": [0.0] * len(dates),
    }, index=index)


@pytest.mark.unit
class TestTimezoneHandling:
    def test_timezone_naive_index_parsed(self, monkeypatch):
        _install_fake_yfinance(monkeypatch, _flat_df([dt.date(2026, 6, 2)]))
        bars = fetch_raw_daily_bars("AAPL", dt.date(2026, 6, 2), dt.date(2026, 6, 2))
        assert bars[0]["date"] == dt.date(2026, 6, 2)

    def test_timezone_aware_index_parsed(self, monkeypatch):
        _install_fake_yfinance(monkeypatch, _flat_df([dt.date(2026, 6, 2)], tz="America/New_York"))
        bars = fetch_raw_daily_bars("AAPL", dt.date(2026, 6, 2), dt.date(2026, 6, 2))
        assert bars[0]["date"] == dt.date(2026, 6, 2)


@pytest.mark.unit
class TestMultiIndexRejected:
    def test_multiindex_columns_raise_sanitized_error(self, monkeypatch):
        df = _flat_df([dt.date(2026, 6, 2)])
        df.columns = pd.MultiIndex.from_product([df.columns, ["AAPL"]])
        _install_fake_yfinance(monkeypatch, df)
        with pytest.raises(PriceProviderAcquisitionError) as excinfo:
            fetch_raw_daily_bars("AAPL", dt.date(2026, 6, 2), dt.date(2026, 6, 2))
        assert excinfo.value.code == "PROVIDER_UNEXPECTED_COLUMN_SHAPE"


@pytest.mark.unit
class TestMissingActionsColumns:
    def test_missing_dividends_column_defaults_to_no_dividend(self, monkeypatch):
        df = pd.DataFrame({
            "Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5], "Volume": [1000.0],
        }, index=pd.DatetimeIndex([dt.date(2026, 6, 2)]))
        _install_fake_yfinance(monkeypatch, df)
        bars = fetch_raw_daily_bars("AAPL", dt.date(2026, 6, 2), dt.date(2026, 6, 2))
        assert bars[0]["dividend"] == 0.0
        assert bars[0]["adj_close"] is None


@pytest.mark.unit
class TestProviderReturnsOneExtraRow:
    def test_extra_row_beyond_requested_end_still_returned_raw(self, monkeypatch):
        """fetch_raw_daily_bars itself does not filter by window — that
        is build_price_path_evidence's job (see
        test_price_path_date_boundary.py). This test just proves the raw
        fetch layer faithfully returns whatever the provider handed
        back, including an extra row, without silently dropping or
        fabricating anything at this layer."""
        _install_fake_yfinance(monkeypatch, _flat_df([dt.date(2026, 6, 2), dt.date(2026, 6, 3)]))
        bars = fetch_raw_daily_bars("AAPL", dt.date(2026, 6, 2), dt.date(2026, 6, 2))
        assert len(bars) == 2
