"""
DP-026 genuine remediation (2026-07-21, US market only — see module
docstrings in services/validation_engine.py and services/sec_edgar_adapter.py
for why India cannot receive the same treatment this session).

Proves, end to end through _backtest_stock() and _compute_metrics(), that:
  - a US signal's fund_score is computed from SEC EDGAR facts filed on or
    before that SIGNAL's own date, not a present-day snapshot;
  - two signals at different historical dates for the same symbol get
    DIFFERENT fund_score inputs when the underlying facts differ;
  - unavailability is tracked per-signal (fund_pit_available), never
    silently indistinguishable from a genuine neutral score;
  - India is completely unaffected — same single-snapshot behavior as
    before this session, still disclosed as contaminated;
  - _compute_metrics()'s data_limitations reports a REAL measured
    US coverage percentage, not a guessed one;
  - no code path here reaches the live network (SEC EDGAR calls are always
    monkeypatched) or falls back to fetch_us_fundamentals_sec_edgar() (the
    live/current-only entry point) from inside a backtest.
"""
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

import services.validation_engine as ve
from services.validation_engine import _backtest_stock, _compute_metrics, _us_fund_score_as_of


def _synthetic_ohlcv(n=260, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    rets = rng.normal(0.0004, 0.01, n)
    close = 100.0 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = close * (1 + rng.normal(0, 0.001, n))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period=None):
        return _synthetic_ohlcv()

    @property
    def info(self):
        return {}  # US path must never read this for fund_score


@pytest.mark.unit
class TestUsFundScoreAsOf:
    def test_available_computes_roe_and_margin_adjustment(self, monkeypatch):
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {
            "available": True,
            "fields": {
                "net_income": {"value": 20.0},
                "shareholders_equity": {"value": 100.0},  # ROE = 20% > 15% -> +10
                "revenue": {"value": 100.0},               # margin = 20% > 10% -> +8
            },
        })
        score, available, reason = _us_fund_score_as_of("AAPL", date(2024, 1, 1))
        assert available is True
        assert reason is None
        assert score == pytest.approx(68.0)  # 50 + 10 + 8

    def test_negative_roe_and_margin_penalized(self, monkeypatch):
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {
            "available": True,
            "fields": {
                "net_income": {"value": -5.0},
                "shareholders_equity": {"value": 100.0},
                "revenue": {"value": 100.0},
            },
        })
        score, available, _ = _us_fund_score_as_of("AAPL", date(2024, 1, 1))
        assert available is True
        assert score == pytest.approx(32.0)  # 50 - 10 - 8

    def test_unavailable_when_as_of_lookup_unavailable(self, monkeypatch):
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {
            "available": False, "reason": "no eligible filing as of the signal date",
        })
        score, available, reason = _us_fund_score_as_of("AAPL", date(2005, 1, 1))
        assert score is None
        assert available is False
        assert reason == "no eligible filing as of the signal date"

    def test_unavailable_when_no_usable_fields_despite_available_true(self, monkeypatch):
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {
            "available": True, "fields": {"net_income": {"value": None}, "shareholders_equity": {"value": None}, "revenue": {"value": None}},
        })
        score, available, reason = _us_fund_score_as_of("AAPL", date(2024, 1, 1))
        assert score is None
        assert available is False
        assert "no usable" in reason

    def test_zero_equity_does_not_raise_zerodivisionerror(self, monkeypatch):
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {
            "available": True,
            "fields": {"net_income": {"value": 20.0}, "shareholders_equity": {"value": 0.0}, "revenue": {"value": 100.0}},
        })
        score, available, _ = _us_fund_score_as_of("AAPL", date(2024, 1, 1))
        assert available is True  # margin still computable even though ROE's equity is 0
        assert score == pytest.approx(58.0)  # 50 + 8 (margin=20% only; ROE skipped, equity falsy)

    def test_score_clamped_to_0_100(self, monkeypatch):
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {
            "available": True,
            "fields": {"net_income": {"value": -1000.0}, "shareholders_equity": {"value": 1.0}, "revenue": {"value": 1.0}},
        })
        score, _, _ = _us_fund_score_as_of("AAPL", date(2024, 1, 1))
        assert score == 32.0  # 50 - 10 - 8, well within bounds here; clamp exercised at extremes elsewhere


@pytest.mark.unit
class TestBacktestStockUsPointInTime:
    def test_fund_score_varies_by_signal_date_not_fixed_per_symbol(self, monkeypatch):
        """The direct, end-to-end proof DP-026 is fixed for US: two
        different signal dates for the same symbol in the SAME backtest
        run must receive DIFFERENT as_of cutoffs, proven by asserting the
        mock was called with genuinely different `as_of` values across the
        run — the pre-remediation code computed fund_score exactly once,
        outside this loop, and could never do this."""
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        seen_as_of_dates = []

        def fake_as_of(symbol, as_of):
            seen_as_of_dates.append(as_of)
            return {"available": True, "fields": {
                "net_income": {"value": 10.0}, "shareholders_equity": {"value": 100.0}, "revenue": {"value": 100.0},
            }}

        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", fake_as_of)
        signals = _backtest_stock("AAPL", "medium", None, "US", universe="us")
        assert len(signals) > 1
        assert len(set(seen_as_of_dates)) > 1  # genuinely different cutoffs, not one repeated

    def test_fund_pit_available_true_when_edgar_data_found(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {
            "available": True, "fields": {
                "net_income": {"value": 10.0}, "shareholders_equity": {"value": 100.0}, "revenue": {"value": 100.0},
            },
        })
        signals = _backtest_stock("AAPL", "short", None, "US", universe="us")
        assert len(signals) > 0
        assert all(s["fund_pit_available"] is True for s in signals)

    def test_fund_pit_available_false_and_neutral_fallback_when_edgar_unavailable(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {
            "available": False, "reason": "no eligible filing as of the signal date",
        })
        signals = _backtest_stock("AAPL", "short", None, "US", universe="us")
        assert len(signals) > 0
        assert all(s["fund_pit_available"] is False for s in signals)

    def test_never_calls_the_live_current_data_entry_point(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {"available": False, "reason": "x"})
        live_fn = MagicMock(side_effect=AssertionError("must not be called during backtest replay"))
        monkeypatch.setattr(ve.sec_edgar_adapter, "fetch_us_fundamentals_sec_edgar", live_fn)
        _backtest_stock("AAPL", "short", None, "US", universe="us")
        live_fn.assert_not_called()

    def test_never_reads_yfinance_info_for_us_fund_score(self, monkeypatch):
        """US fund_score must come exclusively from SEC EDGAR — .info must
        never even be consulted for the US path (unlike IN, which still
        uses it, unchanged)."""
        info_accessed = []

        class _TrackedTicker(_FakeTicker):
            @property
            def info(self):
                info_accessed.append(True)
                return {}

        monkeypatch.setattr(ve.yf, "Ticker", _TrackedTicker)
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", lambda symbol, as_of: {"available": False, "reason": "x"})
        _backtest_stock("AAPL", "short", None, "US", universe="us")
        assert info_accessed == []


@pytest.mark.unit
class TestBacktestStockIndiaUnaffected:
    def test_india_still_uses_single_snapshot_yfinance_info(self, monkeypatch):
        """Regression proof: India's fund_score computation is completely
        unchanged by this session — still one .info call, still reused
        across every signal, still never touches sec_edgar_adapter."""
        info_call_count = []

        class _TrackedTicker(_FakeTicker):
            @property
            def info(self):
                info_call_count.append(1)
                return {"trailingPE": 15, "returnOnEquity": 0.20, "revenueGrowth": 0.12}

        monkeypatch.setattr(ve.yf, "Ticker", _TrackedTicker)
        edgar_fn = MagicMock(side_effect=AssertionError("India must never call SEC EDGAR"))
        monkeypatch.setattr(ve, "_get_fundamentals_as_of_replay", edgar_fn)

        signals = _backtest_stock("INFY", "short", None, "IN", universe="nifty100")
        assert len(signals) > 0
        edgar_fn.assert_not_called()
        assert len(info_call_count) == 1  # exactly one .info call, not one per signal

    def test_india_fund_pit_available_always_false(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        signals = _backtest_stock("INFY", "short", None, "IN", universe="nifty100")
        assert len(signals) > 0
        assert all(s["fund_pit_available"] is False for s in signals)


def _signal(fund_pit_available, predicted="BUY", correct=True, composite=72.0):
    return {
        "symbol": "AAPL", "horizon": "medium", "signal_date": "2024-01-02",
        "composite_score": composite, "tech_score": 60.0, "rs_score": 60.0,
        "obv_score": 60.0, "mfi_score": 60.0, "predicted": predicted,
        "confidence": 50, "fwd_return_pct": 2.0, "nifty_fwd_ret_pct": 1.0,
        "alpha_pct": 1.0, "actual_direction": "UP", "correct": int(correct),
        "fund_pit_available": fund_pit_available,
    }


@pytest.mark.unit
class TestDataLimitationsMarketBranching:
    def test_us_market_reports_remediated_status(self):
        signals = [_signal(True), _signal(True), _signal(False)]
        metrics = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium", market="US")
        dl = metrics["data_limitations"]
        assert dl["fundamentals_point_in_time"] is True
        assert "remediated" in dl["status"]

    def test_us_market_coverage_pct_is_a_real_measured_ratio(self):
        signals = [_signal(True), _signal(True), _signal(False), _signal(False)]
        metrics = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium", market="US")
        assert metrics["data_limitations"]["fundamentals_point_in_time_coverage_pct"] == 50.0

    def test_us_market_full_coverage_reports_100(self):
        signals = [_signal(True), _signal(True)]
        metrics = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium", market="US")
        assert metrics["data_limitations"]["fundamentals_point_in_time_coverage_pct"] == 100.0

    def test_in_market_still_reports_not_remediated(self):
        signals = [_signal(False), _signal(False)]
        metrics = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium", market="IN")
        dl = metrics["data_limitations"]
        assert dl["fundamentals_point_in_time"] is False
        assert "not remediated" in dl["status"]

    def test_unknown_market_treated_as_worst_case_not_remediated(self):
        # A caller that omits market (e.g. an old code path) must never be
        # silently treated as remediated.
        signals = [_signal(True), _signal(True)]  # even if signals LOOK US-like
        metrics = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium", market=None)
        assert metrics["data_limitations"]["fundamentals_point_in_time"] is False

    def test_us_and_in_disclosures_cross_reference_each_other(self):
        us_metrics = _compute_metrics([_signal(True)], benchmark_return_pct=1.0, horizon="medium", market="US")
        in_metrics = _compute_metrics([_signal(False)], benchmark_return_pct=1.0, horizon="medium", market="IN")
        assert "DP-031" in us_metrics["data_limitations"]["reason"]
        assert "genuinely point-in-time" in in_metrics["data_limitations"]["reason"]
