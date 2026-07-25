"""
Integration tests for scripts/leadership_shadow_experiment.py — the
harness itself (correlation math, decile logic, observation counting, and
the min-observation headline-suppression gate), fully mocked (no live
network, SES-003). A real smoke-test run against live data was performed
manually and is documented in the release evidence
(Documentation/Engineering-Handbook/Releases/ — Market Leadership sprint
report) — this file locks in the harness's own logic deterministically.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # backend/scripts import

from scripts.leadership_shadow_experiment import run_experiment, _spearman, MIN_OBSERVATIONS_FOR_HEADLINE
from tests.unit.market_leadership.conftest import make_price_df, make_uptrend_df, make_downtrend_df, make_flat_df


@pytest.mark.integration
class TestSpearman:
    def test_perfect_positive_correlation(self):
        assert _spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)

    def test_perfect_negative_correlation(self):
        assert _spearman([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_too_few_points_returns_none(self):
        assert _spearman([1, 2], [3, 4]) is None

    def test_constant_input_returns_none_not_nan(self):
        assert _spearman([5, 5, 5, 5], [1, 2, 3, 4]) is None


@pytest.mark.integration
class TestRunExperiment:
    def test_no_symbols_fetched_returns_error_not_a_crash(self, monkeypatch):
        import scripts.leadership_shadow_experiment as harness

        def boom(*a, **k):
            raise RuntimeError("provider down")

        monkeypatch.setattr(harness, "_fetch_history", boom)
        result = harness.run_experiment("US", ["AAPL"], 21, 300, 21)
        assert result["status"] == "error"

    def test_functional_run_with_mocked_symbols_produces_real_metrics(self, monkeypatch):
        import scripts.leadership_shadow_experiment as harness

        symbols = ["A", "B", "C", "D", "E", "F"]
        price_data = {
            sym: (make_uptrend_df(400, seed=i) if i % 2 == 0 else make_downtrend_df(400, seed=i))
            for i, sym in enumerate(symbols)
        }
        benchmark = make_flat_df(400, seed=99)

        def fake_fetch(symbol, market, period):
            return price_data[symbol]

        monkeypatch.setattr(harness, "_fetch_history", fake_fetch)

        class _FakeTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                return benchmark

        monkeypatch.setattr(harness.yf, "Ticker", _FakeTicker)

        result = run_experiment("US", symbols, horizon_days=21, lookback_days=300, step_days=21)
        assert result["status"] == "ok"
        assert result["n_observations"] > 0
        assert result["n_symbols"] == 6
        assert "spearman_rs_percentile_vs_fwd_return" in result

    def test_headline_suppressed_below_observation_floor(self, monkeypatch):
        """A small run must set headline_suppressed=True — Section 15's
        own 'no conclusions from too few observations' rule, enforced
        mechanically rather than left to the reader's judgment."""
        import scripts.leadership_shadow_experiment as harness

        symbols = ["A", "B", "C", "D", "E"]
        price_data = {sym: make_flat_df(320, seed=i) for i, sym in enumerate(symbols)}
        benchmark = make_flat_df(320, seed=50)

        def fake_fetch(symbol, market, period):
            return price_data[symbol]

        monkeypatch.setattr(harness, "_fetch_history", fake_fetch)

        class _FakeTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                return benchmark

        monkeypatch.setattr(harness.yf, "Ticker", _FakeTicker)

        result = run_experiment("US", symbols, horizon_days=21, lookback_days=200, step_days=21)
        assert result["n_observations"] < MIN_OBSERVATIONS_FOR_HEADLINE
        assert result["headline_suppressed"] is True

    def test_market_isolation_benchmark_selection(self, monkeypatch):
        import scripts.leadership_shadow_experiment as harness

        seen_tickers = []

        class _RecordingTicker:
            def __init__(self, ticker, *a, **k):
                seen_tickers.append(ticker)

            def history(self, **kw):
                return make_flat_df(320, seed=1)

        monkeypatch.setattr(harness.yf, "Ticker", _RecordingTicker)
        monkeypatch.setattr(harness, "_fetch_history", lambda sym, market, period: make_flat_df(320, seed=2))

        run_experiment("IN", ["TCS"], horizon_days=21, lookback_days=200, step_days=21)
        assert "^NSEI" in seen_tickers

        seen_tickers.clear()
        run_experiment("US", ["AAPL"], horizon_days=21, lookback_days=200, step_days=21)
        assert "^GSPC" in seen_tickers
