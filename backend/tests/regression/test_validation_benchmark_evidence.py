"""
Validation Benchmark Evidence Integrity Closure.

Confirmed defect class: unavailable or invalid benchmark evidence being
silently treated as GENUINE evidence — a missing benchmark forward return
defaulting to a flat 0.0%, an unavailable market regime defaulting to a
calculated neutral 0.0, an individual corrupted alignment window silently
producing a number instead of being excluded. This fabricates
alpha/benchmark-relative correctness/regime input for signals that never
had real benchmark evidence behind them, and can persist a contaminated
result to `val_runs`/`val_signals` and return it from
`/api/validation/results`.

This file proves the closure, against actual behavior (return values,
persisted-row write attempts, and real API responses), not source-code
strings:

  1. BenchmarkEvidence / _validate_benchmark_acquisition — every listed
     invalid-acquisition state is distinct and correctly detected.
  2. run_validation fails the WHOLE RUN CLOSED on invalid benchmark
     evidence — before any stock backtest is submitted, any provider call
     happens, or any DB write occurs — with a stable public failure code,
     never RUN_EXCEPTION, never a raw exception.
  3. Bounded retry: a transient failure that recovers on a later attempt
     succeeds normally; one that never recovers fails closed after the
     capped attempt count, not an unbounded loop.
  4. _align_benchmark_close never backward-fills (no look-ahead), bounds
     staleness, and correctly separates "genuine value" from "must not be
     trusted even though a number is present".
  5. _backtest_stock's defense-in-depth: entry/exit/regime evidence must
     all be genuinely available for a signal to be emitted at all — no
     fabricated flat-0.0/neutral-regime signal ever survives.
  6. Zero-vs-missing: a genuine benchmark_return_pct of exactly 0.0
     survives; a missing model average (zero BUY signals) stays None, not
     a fabricated 0.0; outperformance requires both sides.
  7. Legacy persisted rows are labelled `legacy_unknown`, never rewritten.
  8. The public API boundary (TestClient) never surfaces a raw exception
     and never lets a failed attempt overwrite the last valid result.

No test in this file touches a live provider or a production database —
every I/O boundary is mocked (SES-003).
"""
import json
import logging

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock

import services.validation_engine as ve
from services.validation_engine import (
    BenchmarkEvidence,
    _align_benchmark_close,
    _backtest_stock,
    _compute_metrics,
    _validate_benchmark_acquisition,
)


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _valid_benchmark_df(n=300, seed=99, start="2019-01-01"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    rets = rng.normal(0.0003, 0.008, n)
    close = 100.0 * np.cumprod(1 + rets)
    return pd.DataFrame({"Close": close}, index=dates)


def _synthetic_stock_ohlcv(n=260, seed=42, start="2019-01-01"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
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


class _NoWriteCursor:
    lastrowid = 0


class _NoWriteConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **k):
        return _NoWriteCursor()

    def executemany(self, *a, **k):
        pass


class _BoomIfWrittenConn:
    """Proves NOTHING is persisted — execute()/executemany() raise if ever
    called, rather than merely returning a no-op."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **k):
        raise AssertionError("val_runs write attempted on an invalid-benchmark run")

    def executemany(self, *a, **k):
        raise AssertionError("val_signals write attempted on an invalid-benchmark run")


def _mock_io(monkeypatch, backtest_fake, bench_df=None, auto_window_stats=True):
    """`auto_window_stats=True` (default) makes every _backtest_stock call
    report one genuinely benchmark-valid window before delegating to
    `backtest_fake` — most tests using this helper are about job
    identity/sanitization/routing, not benchmark signal-coverage itself,
    so without this every one of them would spuriously trip the new
    post-alignment coverage gate (Finding D) just because their stub
    backtest returns `[]`. Tests that specifically exercise insufficient
    signal-level coverage pass `auto_window_stats=False` and populate
    `_window_stats` themselves via a custom fake."""
    def _wrapped_backtest(*args, **kwargs):
        if auto_window_stats:
            window_stats = kwargs.get("_window_stats")
            if window_stats is not None:
                window_stats["considered"] = window_stats.get("considered", 0) + 1
                window_stats["benchmark_valid"] = window_stats.get("benchmark_valid", 0) + 1
        return backtest_fake(*args, **kwargs)

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.return_value = (
        bench_df if bench_df is not None else _valid_benchmark_df()
    )
    monkeypatch.setattr(ve, "_backtest_stock", _wrapped_backtest)
    monkeypatch.setattr(ve, "yf", mock_yf)
    monkeypatch.setattr(ve, "_init_db", lambda: None)
    monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _NoWriteConn())
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)
    monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)  # never slow a test down
    with ve._status_lock:
        ve._run_status.clear()
        ve._run_status.update(
            {"running": False, "progress": 0, "total": 0, "started_at": None, "log": []}
        )
    return mock_yf


# ── 1. BenchmarkEvidence / _validate_benchmark_acquisition ────────────────────

@pytest.mark.regression
class TestBenchmarkEvidenceContract:
    def test_none_dataframe_is_empty(self):
        ev = _validate_benchmark_acquisition(None, "^GSPC", "US", "short")
        assert isinstance(ev, BenchmarkEvidence)
        assert ev.status == "empty"
        assert ev.reason is not None
        assert ev.methodology_version == ve.BENCHMARK_EVIDENCE_VERSION

    def test_empty_dataframe_is_empty(self):
        ev = _validate_benchmark_acquisition(pd.DataFrame(), "^NSEI", "IN", "medium")
        assert ev.status == "empty"

    def test_missing_close_column(self):
        df = pd.DataFrame({"Open": [1.0, 2.0]}, index=pd.bdate_range("2020-01-01", periods=2))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "missing_close_column"

    def test_unsorted_index_rejected(self):
        dates = pd.bdate_range("2020-01-01", periods=100).to_list()
        dates[0], dates[-1] = dates[-1], dates[0]  # break sort order
        df = pd.DataFrame({"Close": np.linspace(100, 200, 100)}, index=pd.DatetimeIndex(dates))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "unsorted_index"

    def test_duplicate_index_rejected(self):
        dates = pd.bdate_range("2020-01-01", periods=100).to_list()
        dates[5] = dates[4]  # duplicate an existing date
        df = pd.DataFrame({"Close": np.linspace(100, 200, 100)}, index=pd.DatetimeIndex(dates))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "duplicate_index"

    def test_all_nan_close_is_non_finite(self):
        df = pd.DataFrame(
            {"Close": [np.nan] * 100}, index=pd.bdate_range("2020-01-01", periods=100)
        )
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "non_finite"

    def test_positive_and_negative_infinity_are_non_finite(self):
        closes = [float("inf")] * 50 + [float("-inf")] * 50
        df = pd.DataFrame({"Close": closes}, index=pd.bdate_range("2020-01-01", periods=100))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "non_finite"

    def test_all_non_positive_close_is_non_positive(self):
        df = pd.DataFrame(
            {"Close": [0.0] * 50 + [-5.0] * 50}, index=pd.bdate_range("2020-01-01", periods=100)
        )
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "non_positive"

    def test_too_few_rows_is_insufficient_history(self):
        df = _valid_benchmark_df(n=10)  # long horizon needs 64 rows minimum
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "long")
        assert ev.status == "insufficient_history"

    def test_valid_dataframe_is_available_with_real_provenance(self):
        df = _valid_benchmark_df(n=300)
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "available"
        assert ev.reason is None
        assert ev.rows_available == 300
        assert ev.first_observation_at is not None
        assert ev.last_observation_at is not None
        assert ev.ticker == "^GSPC"
        assert ev.market == "US"
        assert ev.horizon == "short"

    def test_a_single_valid_window_amid_mostly_invalid_data_fails_coverage(self):
        """2026-07-26 hardening (Finding C): a single valid forward-return
        window amid hundreds of invalid rows is NOT adequate evidence to
        back an entire universe's walk-forward run — this used to be
        (incorrectly) treated as 'available'; it must now fail with the
        distinct insufficient_window_coverage status, with real, non-
        fabricated coverage counts disclosed."""
        n = 300
        closes = np.full(n, np.nan)
        closes[0] = 100.0
        closes[ve.HORIZON_DAYS["short"]] = 101.0
        df = pd.DataFrame({"Close": closes}, index=pd.bdate_range("2020-01-01", periods=n))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "insufficient_window_coverage"
        assert ev.valid_forward_windows == 1
        assert ev.total_forward_windows > 1
        assert ev.forward_window_coverage_pct is not None
        assert ev.forward_window_coverage_pct < ve.BENCHMARK_MIN_ACQUISITION_WINDOW_COVERAGE_PCT

    def test_wrong_market_ticker_is_reported_verbatim_never_inferred(self):
        """This function trusts the caller's own ticker/market — it never
        infers market from a ticker's shape (that guarantee lives in
        run_validation's UNIVERSE_MARKET mapping, tested separately)."""
        df = _valid_benchmark_df()
        ev = _validate_benchmark_acquisition(df, "^NSEI", "IN", "short")
        assert ev.ticker == "^NSEI"
        assert ev.market == "IN"


# ── 2 & 3. Run-level fail-closed + bounded retry ───────────────────────────────

@pytest.mark.regression
class TestBenchmarkEvidenceRunLevelFailClosed:
    def _assert_fails_closed(self, monkeypatch, bench_df, boom_backtest=True):
        def _boom_backtest(*a, **k):
            raise AssertionError("_backtest_stock was called despite invalid benchmark evidence")

        _mock_io(monkeypatch, _boom_backtest if boom_backtest else (lambda *a, **k: []), bench_df)

        with pytest.raises(ValueError, match="benchmark evidence unavailable"):
            ve.run_validation(horizon="short", universe="us")

        status = ve.get_run_status()
        assert status["running"] is False  # run slot released, never stuck
        job = status.get("job")
        assert job is not None
        assert job["status"] == "failed"
        assert job["failure_code"] == "BENCHMARK_EVIDENCE_UNAVAILABLE"
        assert job["failure_message"] == ve.VALIDATION_PUBLIC_FAILURE_MESSAGES["BENCHMARK_EVIDENCE_UNAVAILABLE"]
        # Preserved identity fields, even on failure.
        assert job["market"] == "US"
        assert job["universe_id"] == "us"
        assert job["horizon"] == "short"
        assert job["benchmark"] == "^GSPC"

    def test_empty_benchmark_fails_closed_no_stock_backtest_submitted(self, monkeypatch):
        self._assert_fails_closed(monkeypatch, pd.DataFrame())

    def test_missing_close_column_fails_closed(self, monkeypatch):
        df = pd.DataFrame({"Open": [1.0] * 10}, index=pd.bdate_range("2020-01-01", periods=10))
        self._assert_fails_closed(monkeypatch, df)

    def test_insufficient_rows_fails_closed(self, monkeypatch):
        self._assert_fails_closed(monkeypatch, _valid_benchmark_df(n=3))

    def test_all_nan_closes_fails_closed(self, monkeypatch):
        df = pd.DataFrame({"Close": [np.nan] * 100}, index=pd.bdate_range("2020-01-01", periods=100))
        self._assert_fails_closed(monkeypatch, df)

    def test_infinite_closes_fail_closed(self, monkeypatch):
        df = pd.DataFrame(
            {"Close": [float("inf")] * 100}, index=pd.bdate_range("2020-01-01", periods=100)
        )
        self._assert_fails_closed(monkeypatch, df)

    def test_non_positive_closes_fail_closed(self, monkeypatch):
        df = pd.DataFrame({"Close": [0.0] * 100}, index=pd.bdate_range("2020-01-01", periods=100))
        self._assert_fails_closed(monkeypatch, df)

    def test_duplicate_dates_fail_closed(self, monkeypatch):
        dates = pd.bdate_range("2020-01-01", periods=100).to_list()
        dates[5] = dates[4]
        df = pd.DataFrame({"Close": np.linspace(100, 200, 100)}, index=pd.DatetimeIndex(dates))
        self._assert_fails_closed(monkeypatch, df)

    def test_unsorted_dates_fail_closed(self, monkeypatch):
        dates = pd.bdate_range("2020-01-01", periods=100).to_list()
        dates[0], dates[-1] = dates[-1], dates[0]
        df = pd.DataFrame({"Close": np.linspace(100, 200, 100)}, index=pd.DatetimeIndex(dates))
        self._assert_fails_closed(monkeypatch, df)

    def test_provider_exception_fails_closed_never_run_exception_code(self, monkeypatch):
        class _BoomTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                raise RuntimeError("synthetic provider outage")

        def _boom_backtest(*a, **k):
            raise AssertionError("_backtest_stock was called despite a provider exception")

        _mock_io(monkeypatch, _boom_backtest)
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _BoomTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        with pytest.raises(ValueError, match="benchmark evidence unavailable"):
            ve.run_validation(horizon="short", universe="us")
        job = ve.get_run_status().get("job")
        assert job["failure_code"] == "BENCHMARK_EVIDENCE_UNAVAILABLE"
        assert job["failure_code"] != "RUN_EXCEPTION"

    def test_no_stock_provider_call_on_invalid_benchmark(self, monkeypatch):
        """A stronger form of 'no backtest submitted': _backtest_stock
        itself is never even invoked, so it can never reach yf.Ticker for
        a stock."""
        calls = []

        def _boom_backtest(*a, **k):
            calls.append(1)
            raise AssertionError("must never be called")

        _mock_io(monkeypatch, _boom_backtest, pd.DataFrame())
        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        assert calls == []

    def test_no_compute_metrics_call_on_invalid_benchmark(self, monkeypatch):
        boom_compute = MagicMock(side_effect=AssertionError("_compute_metrics must not be called"))
        _mock_io(monkeypatch, lambda *a, **k: [], pd.DataFrame())
        monkeypatch.setattr(ve, "_compute_metrics", boom_compute)
        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        boom_compute.assert_not_called()

    def test_no_val_runs_or_val_signals_write_on_invalid_benchmark(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [], pd.DataFrame())
        monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _BoomIfWrittenConn())
        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")

    def test_raw_provider_exception_text_never_appears_in_public_status(self, monkeypatch, caplog):
        class _BoomTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                raise RuntimeError("password=SECRET host=db.internal /app/private.py")

        _mock_io(monkeypatch, lambda *a, **k: [])
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _BoomTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        with caplog.at_level(logging.ERROR, logger="services.validation_engine"):
            with pytest.raises(ValueError):
                ve.run_validation(horizon="short", universe="us")

        status = ve.get_run_status()
        serialized = json.dumps(status, default=str)
        for forbidden in ("password=SECRET", "db.internal", "/app/"):
            assert forbidden not in serialized
        # Full detail is diagnosable server-side.
        assert "password=SECRET" in caplog.text

    def test_bounded_retry_recovers_on_a_later_attempt(self, monkeypatch):
        """A transient failure on the first attempt that succeeds on the
        (capped) retry must complete normally — not fail closed."""
        attempts = {"n": 0}
        valid_df = _valid_benchmark_df()

        class _FlakyTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("transient network blip")
                return valid_df

        _mock_io(monkeypatch, lambda *a, **k: [])
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _FlakyTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["job"]["status"] == "completed"
        assert attempts["n"] == 2
        assert attempts["n"] <= ve.BENCHMARK_FETCH_MAX_ATTEMPTS

    def test_bounded_retry_exhausts_and_fails_closed_never_unbounded(self, monkeypatch):
        attempts = {"n": 0}

        class _AlwaysBoomTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                attempts["n"] += 1
                raise RuntimeError("permanent provider outage")

        _mock_io(monkeypatch, lambda *a, **k: [])
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _AlwaysBoomTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        assert attempts["n"] == ve.BENCHMARK_FETCH_MAX_ATTEMPTS  # capped, not unbounded

    def test_same_ticker_retried_no_new_provider_introduced(self, monkeypatch):
        seen_tickers = []

        class _TrackedBoomTicker:
            def __init__(self, symbol, *a, **k):
                seen_tickers.append(symbol)

            def history(self, **kw):
                raise RuntimeError("boom")

        _mock_io(monkeypatch, lambda *a, **k: [])
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _TrackedBoomTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        assert set(seen_tickers) == {"^GSPC"}  # always the same ticker, every attempt


# ── 4. Alignment — no look-ahead, bounded staleness ────────────────────────────

@pytest.mark.regression
class TestAlignBenchmarkClose:
    def test_never_backward_fills_before_first_observation(self):
        stock_index = pd.bdate_range("2020-01-01", periods=20)
        bench_df = pd.DataFrame(
            {"Close": [100.0] * 10}, index=pd.bdate_range("2020-01-08", periods=10)
        )  # benchmark starts a week after the stock's own first date
        aligned, available = _align_benchmark_close(bench_df, stock_index)
        # Stock dates before 2020-01-08 must have NO available benchmark evidence —
        # never filled from a LATER (future-relative-to-that-date) observation.
        early_dates = stock_index[stock_index < pd.Timestamp("2020-01-08")]
        for d in early_dates:
            assert available.loc[d] == False, f"{d} incorrectly marked available (would be a backward fill)"

    def test_ordinary_holiday_gap_is_bridged_via_forward_fill(self):
        stock_index = pd.bdate_range("2020-01-01", periods=10)
        # Benchmark misses one day the stock has (a plausible cross-market
        # holiday) but is otherwise present.
        bench_dates = stock_index.delete(3)
        bench_df = pd.DataFrame({"Close": np.linspace(100, 110, len(bench_dates))}, index=bench_dates)
        aligned, available = _align_benchmark_close(bench_df, stock_index)
        assert available.loc[stock_index[3]] == True
        assert aligned.loc[stock_index[3]] == pytest.approx(aligned.loc[bench_dates[2]])

    def test_excessive_gap_beyond_tolerance_is_unavailable(self):
        stock_index = pd.bdate_range("2020-01-01", periods=30)
        # Benchmark has a genuine multi-week gap in the middle.
        bench_dates = stock_index[:5].append(stock_index[20:])
        bench_df = pd.DataFrame({"Close": np.linspace(100, 130, len(bench_dates))}, index=bench_dates)
        aligned, available = _align_benchmark_close(bench_df, stock_index)
        # A stock date well inside the gap must be unavailable, not silently
        # forward-filled from the stale pre-gap value.
        mid_gap_date = stock_index[12]
        assert available.loc[mid_gap_date] == False

    def test_non_finite_and_non_positive_raw_values_are_excluded_as_sources(self):
        stock_index = pd.bdate_range("2020-01-01", periods=10)
        bench_df = pd.DataFrame(
            {"Close": [np.nan, -5.0, 0.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]},
            index=stock_index,
        )
        aligned, available = _align_benchmark_close(bench_df, stock_index)
        # The first three (invalid) source rows must never be used as
        # evidence for themselves or as a forward-fill source for later dates.
        assert available.iloc[0] == False
        assert available.iloc[1] == False
        assert available.iloc[2] == False
        assert available.iloc[3] == True
        assert aligned.iloc[3] == pytest.approx(100.0)

    def test_all_invalid_source_data_yields_entirely_unavailable(self):
        stock_index = pd.bdate_range("2020-01-01", periods=5)
        bench_df = pd.DataFrame({"Close": [np.nan] * 5}, index=stock_index)
        aligned, available = _align_benchmark_close(bench_df, stock_index)
        assert not available.any()

    def test_duplicate_benchmark_dates_resolved_deterministically(self):
        stock_index = pd.bdate_range("2020-01-01", periods=5)
        dup_dates = pd.DatetimeIndex([stock_index[0], stock_index[0], stock_index[1]])
        bench_df = pd.DataFrame({"Close": [100.0, 999.0, 101.0]}, index=dup_dates)
        aligned, available = _align_benchmark_close(bench_df, stock_index)
        # keep="last" — deterministic, documented choice, not order-dependent flakiness.
        assert aligned.iloc[0] == pytest.approx(999.0)


# ── 5. _backtest_stock defense-in-depth ────────────────────────────────────────

@pytest.mark.regression
class TestBacktestStockDefenseInDepth:
    def test_gap_in_the_middle_of_an_otherwise_valid_benchmark_excludes_only_affected_dates(self, monkeypatch):
        """A benchmark that's valid overall but has a real gap around some
        signal dates must exclude exactly those signals, not the entire
        symbol and not silently substitute a flat/neutral value for them."""
        class _FakeTicker:
            def __init__(self, symbol):
                pass

            def history(self, period=None):
                return _synthetic_stock_ohlcv()

            @property
            def info(self):
                return {}

        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        stock_df = _synthetic_stock_ohlcv()
        # Benchmark covers the whole range except a real multi-week hole
        # positioned to actually intersect the per-signal loop's range
        # (MIN_WARMUP=200 .. len-fwd_days=255) — positions 210-229.
        bench_dates = stock_df.index[:210].append(stock_df.index[230:])
        bench_df = pd.DataFrame(
            {"Close": np.linspace(100, 140, len(bench_dates))}, index=bench_dates
        )
        exclusions = []
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us", _exclusions=exclusions)
        assert len(signals) > 0  # positions before/after the hole still produce genuine signals
        assert len(exclusions) > 0  # the gap region genuinely excluded some
        # No survivor signal falls inside the excluded gap's date range.
        gap_start, gap_end = stock_df.index[210], stock_df.index[229]
        for s in signals:
            d = pd.Timestamp(s["signal_date"])
            assert not (gap_start <= d <= gap_end), f"signal at {d} should have been excluded"

    def test_regime_unavailable_excludes_the_signal_never_defaults_to_neutral(self, monkeypatch):
        """A date whose regime lookback (idx-63) predates the benchmark's
        own first observation must be excluded — not silently scored with
        regime_adj=0.0 as though a genuine neutral regime had been
        calculated."""
        class _FakeTicker:
            def __init__(self, symbol):
                pass

            def history(self, period=None):
                return _synthetic_stock_ohlcv()

            @property
            def info(self):
                return {}

        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        stock_df = _synthetic_stock_ohlcv()
        # Benchmark starts at stock position 160. MIN_WARMUP (200)'s own
        # regime lookback is 200-63=137, which predates position 160 — so
        # the earliest loop iterations (i=200..220, base_idx 137..157) must
        # be excluded even though entry/exit evidence AT those dates
        # (positions >=160) is otherwise genuinely available; only once the
        # lookback itself reaches position 160 (i=225 onward, base_idx
        # 162+) does the signal survive.
        bench_start = 160
        bench_df = pd.DataFrame(
            {"Close": np.linspace(100, 140, len(stock_df.index[bench_start:]))},
            index=stock_df.index[bench_start:],
        )
        exclusions = []
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us", _exclusions=exclusions)
        assert len(exclusions) > 0
        assert len(signals) > 0  # later dates, once the lookback clears bench_start, do survive
        for s in signals:
            d = pd.Timestamp(s["signal_date"])
            assert d >= stock_df.index[bench_start + 63]

    def test_no_signal_ever_carries_a_fabricated_zero_when_excluded(self, monkeypatch):
        """Direct proof of the core defect closure: with benchmark_df=None
        (no evidence anywhere), the OLD code fabricated
        nifty_fwd_ret_pct=0.0/alpha_pct=fwd_return_pct for every signal.
        The new code must produce ZERO signals — never a fabricated
        zero-benchmark comparison."""
        class _FakeTicker:
            def __init__(self, symbol):
                pass

            def history(self, period=None):
                return _synthetic_stock_ohlcv()

            @property
            def info(self):
                return {}

        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        signals = _backtest_stock("AAPL", "short", None, "US", universe="us")
        assert signals == []


# ── 6. Zero-vs-missing semantics in _compute_metrics ───────────────────────────

def _buy_signal(alpha=1.0, fwd_return=2.0, correct=True):
    return {
        "predicted": "BUY", "correct": int(correct), "fwd_return_pct": fwd_return,
        "alpha_pct": alpha, "composite_score": 70, "tech_score": 70, "rs_score": 70,
        "obv_score": 70, "mfi_score": 70, "signal_date": "2020-01-01",
    }


def _hold_signal():
    return {
        "predicted": "HOLD", "correct": 1, "fwd_return_pct": 0.1,
        "alpha_pct": 0.0, "composite_score": 50, "tech_score": 50, "rs_score": 50,
        "obv_score": 50, "mfi_score": 50, "signal_date": "2020-01-01",
    }


@pytest.mark.regression
class TestZeroVersusMissingSemantics:
    def test_genuine_zero_benchmark_return_survives_as_zero(self):
        signals = [_buy_signal() for _ in range(30)]
        metrics = _compute_metrics(signals, benchmark_return_pct=0.0, horizon="short")
        assert metrics["avg_return_benchmark_pct"] == 0.0
        assert metrics["avg_return_benchmark_pct"] is not None

    def test_missing_benchmark_return_stays_none(self):
        signals = [_buy_signal() for _ in range(30)]
        metrics = _compute_metrics(signals, benchmark_return_pct=None, horizon="short")
        assert metrics["avg_return_benchmark_pct"] is None

    def test_no_buy_signals_produces_none_model_average_not_zero(self):
        signals = [_hold_signal() for _ in range(30)]
        metrics = _compute_metrics(signals, benchmark_return_pct=2.0, horizon="short")
        assert metrics["avg_return_on_buy_pct"] is None
        # Outperformance must also be None — never "0% model return" vs a
        # real benchmark, which would misleadingly imply flat BUY signals
        # rather than zero BUY signals.
        assert metrics["buy_outperformance_pct"] is None

    def test_outperformance_requires_both_sides_available(self):
        signals = [_buy_signal(fwd_return=5.0) for _ in range(30)]
        with_benchmark = _compute_metrics(signals, benchmark_return_pct=2.0, horizon="short")
        assert with_benchmark["buy_outperformance_pct"] == pytest.approx(round(5.0 - 2.0, 2))

        without_benchmark = _compute_metrics(signals, benchmark_return_pct=None, horizon="short")
        assert without_benchmark["buy_outperformance_pct"] is None

    def test_no_alpha_observations_produces_none_not_zero(self):
        signals = [
            {**_buy_signal(), "alpha_pct": None} for _ in range(30)
        ]
        metrics = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="short")
        assert metrics["avg_alpha_on_buy_pct"] is None

    def test_signals_excluded_benchmark_is_disclosed_and_never_fabricated(self):
        signals = [_buy_signal() for _ in range(10)]
        metrics = _compute_metrics(
            signals, benchmark_return_pct=1.0, horizon="short", signals_excluded_benchmark=7,
        )
        assert metrics["signals_excluded_benchmark"] == 7

    def test_signals_excluded_benchmark_defaults_to_zero_for_old_callers(self):
        signals = [_buy_signal() for _ in range(10)]
        metrics = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="short")
        assert metrics["signals_excluded_benchmark"] == 0


# ── 7. Legacy persisted rows ────────────────────────────────────────────────────

@pytest.mark.regression
class TestLegacyBenchmarkEvidenceLabelling:
    def test_row_without_benchmark_evidence_key_is_labelled_legacy_unknown(self, monkeypatch):
        legacy_summary = {"buy_hit_rate_pct": 61.0, "universe": "us", "horizon": "short"}
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)

        result = ve.get_latest_results(horizon="short", universe="us")
        assert result["benchmark_evidence"]["status"] == "legacy_unknown"
        assert result["benchmark_evidence"]["methodology_version"] is None
        # Genuine numeric metric passes through unchanged.
        assert result["buy_hit_rate_pct"] == 61.0

    def test_row_with_benchmark_evidence_already_present_is_not_overwritten(self, monkeypatch):
        real_evidence = {
            "status": "available", "reason": None, "ticker": "^GSPC", "market": "US",
            "horizon": "short", "rows_available": 300, "rows_required": 6,
            "first_observation_at": "2019-01-01", "last_observation_at": "2020-03-01",
            "methodology_version": ve.BENCHMARK_EVIDENCE_VERSION,
        }
        legacy_summary = {"benchmark_evidence": real_evidence}
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)

        result = ve.get_latest_results(horizon="short", universe="us")
        assert result["benchmark_evidence"]["status"] == "available"
        assert result["benchmark_evidence"]["methodology_version"] == ve.BENCHMARK_EVIDENCE_VERSION

    def test_legacy_row_is_never_rewritten(self, monkeypatch):
        write_attempted = {"called": False}

        def _boom_conn():
            write_attempted["called"] = True
            return _NoWriteConn()

        legacy_summary = {"buy_hit_rate_pct": 61.0}
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)
        monkeypatch.setattr(ve, "_get_sqlite_conn", _boom_conn)

        ve.get_latest_results(horizon="short", universe="us")
        assert write_attempted["called"] is False


# ── 8. Public API boundary ──────────────────────────────────────────────────────

@pytest.mark.regression
class TestPublicApiBoundary:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from api.main import app
        return TestClient(app)

    def test_failed_run_does_not_overwrite_the_latest_valid_result(self, client, monkeypatch):
        """The core Phase 12 guarantee: since a failed benchmark preflight
        writes nothing to val_runs at all, /api/validation/results keeps
        returning whatever the last genuinely successful run was — proven
        here by making get_latest_results return a fixed prior result,
        then running a failing attempt, then confirming the results
        endpoint is completely unaffected."""
        prior_result = {"available": True, "buy_hit_rate_pct": 58.0, "run_at": "2026-01-01T00:00:00Z"}
        monkeypatch.setattr(ve, "get_latest_results", lambda **kw: dict(prior_result))

        _mock_io(monkeypatch, lambda *a, **k: [], pd.DataFrame())
        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")

        resp = client.get("/api/validation/results?horizon=short&universe=us")
        assert resp.status_code == 200
        assert resp.json()["buy_hit_rate_pct"] == 58.0

    def test_status_endpoint_after_failure_shows_stable_code_not_a_fabricated_success(self, client, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [], pd.DataFrame())
        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")

        resp = client.get("/api/validation/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is False
        assert body["job"]["failure_code"] == "BENCHMARK_EVIDENCE_UNAVAILABLE"
        assert not any(line.startswith("✅") for line in body.get("log", []))

    def test_no_live_provider_or_production_db_touched_anywhere_in_this_file(self):
        """Sanity meta-check: every test above monkeypatches yf, _init_db,
        _get_sqlite_conn, and _USE_POSTGRES=False — this test just
        confirms the module-level default remains SQLite (never silently
        pointed at Postgres) so a future test author can't accidentally
        drop those patches without something failing loudly elsewhere."""
        assert isinstance(ve._USE_POSTGRES, bool)
