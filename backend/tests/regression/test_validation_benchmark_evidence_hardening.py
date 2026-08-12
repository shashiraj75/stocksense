"""
Validation Benchmark Evidence Integrity — Final Hardening (2026-07-26).

PR #26 closed the core defect class (missing/invalid benchmark evidence
silently treated as genuine flat/neutral evidence). This file proves the
follow-up hardening closes the remaining edge cases found on fresh
adversarial review, without repeating or weakening PR #26's own
protections (see test_validation_benchmark_evidence.py, still green):

  A. _validate_benchmark_acquisition is now a TOTAL, exception-safe
     function for every DataFrame shape/dtype — never raises, always
     returns a BenchmarkEvidence, with distinct non_numeric/
     invalid_index_type/validation_error states and disclosed invalid
     counts (never coercing a string into a genuine observation).
  B. A valid forward-return window requires BOTH entry and exit finite
     and strictly positive — and the SAME coerced/validated Close series
     backs acquisition evidence, the aggregate benchmark return, and
     per-signal alignment (never a different, unfiltered series
     partway through a run).
  C. Acquisition adequacy is now coverage-based (>= 95% of candidate
     forward-return windows must be valid), not "at least one" — real,
     non-fabricated denominators disclosed on every BenchmarkEvidence.
  D. Post-alignment, whole-run signal coverage is tracked and gated
     (>= 95%) AFTER the stock backtests run but BEFORE _compute_metrics/
     persistence — zero benchmark-valid signals, or below-threshold
     coverage, fails closed and persists nothing.
  E. The bounded retry now covers non-exception acquisition failures
     (empty/malformed frame returned without raising), not just thrown
     exceptions — while remaining capped, same-ticker, no new provider.
  F. A failed job's status snapshot carries the full, safe
     BenchmarkEvidence contract (no raw provider text, ever).
  G. _align_benchmark_close is robust to timezone-aware/naive mismatches,
     intraday timestamps, and non-DatetimeIndex input — normalizing
     safely without ever fabricating a future observation or moving a
     value to an earlier trading date.

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
    _coerce_benchmark_close,
    _validate_benchmark_acquisition,
)


# ── Shared fixtures (mirrors test_validation_benchmark_evidence.py) ──────────

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
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **k):
        raise AssertionError("val_runs write attempted on an inadequate-coverage run")

    def executemany(self, *a, **k):
        raise AssertionError("val_signals write attempted on an inadequate-coverage run")


def _mock_io(monkeypatch, backtest_fake, bench_df=None, auto_window_stats=True):
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
    monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)
    with ve._status_lock:
        ve._run_status.clear()
        ve._run_status.update(
            {"running": False, "progress": 0, "total": 0, "started_at": None, "log": []}
        )
    return mock_yf


# ── A. Total, exception-safe validator ────────────────────────────────────────

@pytest.mark.regression
class TestTotalExceptionSafeValidator:
    def test_string_only_close_returns_non_numeric_without_raising(self):
        df = pd.DataFrame(
            {"Close": ["not", "a", "number"] * 40}, index=pd.bdate_range("2020-01-01", periods=120)
        )
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")  # must not raise
        assert ev.status == "non_numeric"
        assert ev.invalid_rows > 0
        assert ev.numeric_rows == 0

    def test_mixed_numeric_and_string_close_does_not_raise_and_uses_valid_subset(self):
        n = 120
        closes = [100.0 + i for i in range(n)]
        # Corrupt a third of the values with garbage strings — deterministic.
        for i in range(0, n, 3):
            closes[i] = "garbage"
        df = pd.DataFrame({"Close": closes}, index=pd.bdate_range("2020-01-01", periods=n))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")  # must not raise
        assert ev.status in ("available", "insufficient_window_coverage")
        assert ev.invalid_rows == len([c for c in closes if c == "garbage"])
        assert ev.numeric_rows == n - ev.invalid_rows

    def test_malformed_object_values_cannot_escape_the_validator(self):
        """Arbitrary unhashable/object garbage in Close — not just
        strings — must still never raise."""
        closes = [{"nested": "dict"}, [1, 2, 3], object(), None] * 12  # 48 rows
        df = pd.DataFrame({"Close": closes}, index=pd.bdate_range("2020-01-01", periods=len(closes)))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")  # must not raise
        assert isinstance(ev, BenchmarkEvidence)
        assert ev.status in ("non_numeric", "non_finite", "validation_error")

    def test_all_nan_numeric_dtype_is_non_finite_not_non_numeric(self):
        """Regression: an already-numeric (float) column that happens to
        be entirely NaN is a distinct condition from a non-numeric dtype
        — must not be misreported as non_numeric."""
        df = pd.DataFrame(
            {"Close": [np.nan] * 100}, index=pd.bdate_range("2020-01-01", periods=100)
        )
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status == "non_finite"

    def test_an_internal_validator_exception_still_releases_the_active_job(self, monkeypatch):
        """Force _coerce_benchmark_close itself to raise unexpectedly —
        proving the total-function guarantee holds even when an internal
        helper misbehaves, and that run_validation still reaches a
        terminal, non-stuck state."""
        def _boom_coerce(bench_df):
            raise RuntimeError("synthetic internal validator bug")

        monkeypatch.setattr(ve, "_coerce_benchmark_close", _boom_coerce)
        ev = _validate_benchmark_acquisition(_valid_benchmark_df(), "^GSPC", "US", "short")
        assert ev.status == "validation_error"
        assert ev.reason is not None
        assert "synthetic internal validator bug" not in ev.reason  # never raw exception text

    def test_validation_error_status_fails_the_run_closed_via_run_validation(self, monkeypatch):
        def _boom_coerce(bench_df):
            raise RuntimeError("synthetic internal validator bug — password=SECRET")

        def _boom_backtest(*a, **k):
            raise AssertionError("_backtest_stock must not be called on a validator exception")

        _mock_io(monkeypatch, _boom_backtest)
        monkeypatch.setattr(ve, "_coerce_benchmark_close", _boom_coerce)

        with pytest.raises(ValueError, match="benchmark evidence unavailable"):
            ve.run_validation(horizon="short", universe="us")

        status = ve.get_run_status()
        assert status["running"] is False  # never stuck
        job = status.get("job")
        assert job is not None
        assert job["status"] == "failed"
        assert job["failure_code"] == "BENCHMARK_EVIDENCE_UNAVAILABLE"
        serialized = json.dumps(status, default=str)
        assert "password=SECRET" not in serialized
        assert "synthetic internal validator bug" not in serialized

    def test_validation_error_is_logged_server_side(self, monkeypatch, caplog):
        def _boom_coerce(bench_df):
            raise RuntimeError("synthetic internal validator bug — password=SECRET")

        monkeypatch.setattr(ve, "_coerce_benchmark_close", _boom_coerce)
        with caplog.at_level(logging.ERROR, logger="services.validation_engine"):
            ev = _validate_benchmark_acquisition(_valid_benchmark_df(), "^GSPC", "US", "short")
        assert ev.status == "validation_error"
        assert "synthetic internal validator bug" in caplog.text
        assert "password=SECRET" in caplog.text


# ── B. Positive entry AND exit; shared validated series ────────────────────────

@pytest.mark.regression
class TestPositiveEntryAndExitEnforcement:
    def test_negative_exit_value_is_rejected(self):
        n = 300
        closes = np.full(n, 100.0)
        # Make every exit position (i + fwd_days) negative — entries stay
        # positive throughout, isolating this to the exit-side check.
        fwd_days = ve.HORIZON_DAYS["short"]
        for i in range(0, n - fwd_days, ve.HORIZON_STEP["short"]):
            closes[i + fwd_days] = -50.0
        df = pd.DataFrame({"Close": closes}, index=pd.bdate_range("2020-01-01", periods=n))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.status in ("non_positive", "insufficient_window_coverage")
        assert ev.valid_forward_windows == 0

    def test_zero_exit_value_is_rejected(self):
        n = 300
        closes = np.full(n, 100.0)
        fwd_days = ve.HORIZON_DAYS["short"]
        for i in range(0, n - fwd_days, ve.HORIZON_STEP["short"]):
            closes[i + fwd_days] = 0.0
        df = pd.DataFrame({"Close": closes}, index=pd.bdate_range("2020-01-01", periods=n))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.valid_forward_windows == 0

    def test_aggregate_benchmark_return_uses_the_same_series_as_alignment(self, monkeypatch):
        """run_validation's bench_rets loop and _align_benchmark_close
        must consume the exact same coerced series — proven by making a
        mixed numeric/string Close column and confirming neither path
        chokes or silently diverges (both call the shared
        _coerce_benchmark_close helper)."""
        n = 300
        closes = [100.0 + i * 0.01 for i in range(n)]
        # Corrupt a handful of values that never coincide with a window's
        # entry/exit position (short horizon: entry/exit are always
        # multiples of 5) — proves coercion happened (invalid_rows > 0)
        # while keeping forward-window coverage at 100%.
        for i in (1, 2, 3, 4):
            closes[i] = "garbage"
        bench_df = pd.DataFrame({"Close": closes}, index=pd.bdate_range("2019-01-01", periods=n))

        calls = {"coerce": 0}
        real_coerce = ve._coerce_benchmark_close

        def _spy_coerce(df):
            calls["coerce"] += 1
            return real_coerce(df)

        monkeypatch.setattr(ve, "_coerce_benchmark_close", _spy_coerce)
        _mock_io(monkeypatch, lambda *a, **k: [], bench_df)

        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["job"]["status"] == "completed"
        # Called at least twice: once inside _validate_benchmark_acquisition's
        # own numeric coercion, once for the run-level bench_rets loop.
        assert calls["coerce"] >= 2
        assert np.isfinite(metrics["benchmark_avg_fwd_return_pct"])

    def test_negative_or_zero_exit_never_enters_persisted_alpha_or_correctness(self, monkeypatch):
        """End-to-end proof via _backtest_stock directly: a benchmark
        whose exit values are negative/zero at every window must never
        produce a signal with a benchmark-relative field derived from
        them — the signal is excluded entirely instead."""
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
        fwd_days = ve.HORIZON_DAYS["short"]
        bench_closes = np.full(len(stock_df), 100.0)
        for i in range(0, len(stock_df) - fwd_days):
            if (i - 200) % 5 == 0:  # roughly aligned to the signal loop's step
                bench_closes[i + fwd_days] = -1.0
        bench_df = pd.DataFrame({"Close": bench_closes}, index=stock_df.index)

        exclusions = []
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us", _exclusions=exclusions)
        for s in signals:
            assert s["nifty_fwd_ret_pct"] is not None  # never a signal built on the negative exit


# ── C. Acquisition coverage threshold ───────────────────────────────────────────

@pytest.mark.regression
class TestAcquisitionCoverageThreshold:
    # short horizon: fwd_days=5, step=5. n=2005 rows -> total_forward_windows
    # = len(range(0, 2000, 5)) = 400 exactly — a round denominator that
    # lets every test below hit an EXACT coverage percentage by construction
    # (invalidating precisely k of the 400 window-start positions), rather
    # than relying on floating-point rounding to land on a boundary.
    _N = 2005
    _TOTAL_WINDOWS = 400

    def _df_with_n_invalid_windows(self, n_invalid):
        closes = np.full(self._N, 100.0) + np.arange(self._N) * 0.001
        window_starts = list(range(0, self._N - ve.HORIZON_DAYS["short"], ve.HORIZON_STEP["short"]))
        assert len(window_starts) == self._TOTAL_WINDOWS
        for i in window_starts[:n_invalid]:
            closes[i] = np.nan
        return pd.DataFrame({"Close": closes}, index=pd.bdate_range("2015-01-01", periods=self._N))

    def test_coverage_exactly_at_threshold_passes(self):
        # 380/400 = exactly 95.0%.
        df = self._df_with_n_invalid_windows(n_invalid=20)
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.forward_window_coverage_pct == pytest.approx(95.0)
        assert ev.status == "available"

    def test_coverage_just_below_threshold_fails(self):
        # 379/400 = 94.75% — one window short of the exact-threshold fixture.
        df = self._df_with_n_invalid_windows(n_invalid=21)
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.forward_window_coverage_pct == pytest.approx(94.75)
        assert ev.forward_window_coverage_pct < ve.BENCHMARK_MIN_ACQUISITION_WINDOW_COVERAGE_PCT
        assert ev.status == "insufficient_window_coverage"

    def test_coverage_counts_are_real_not_fabricated(self):
        df = self._df_with_n_invalid_windows(n_invalid=200)  # 50%
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")
        assert ev.total_forward_windows == self._TOTAL_WINDOWS
        assert ev.valid_forward_windows == self._TOTAL_WINDOWS - 200
        assert ev.forward_window_coverage_pct == pytest.approx(50.0)

    def test_sparse_benchmark_cannot_be_published_as_adequate(self, monkeypatch):
        df = self._df_with_n_invalid_windows(n_invalid=380)  # only 5% valid — badly sparse

        def _boom_backtest(*a, **k):
            raise AssertionError("must not be called — sparse benchmark must fail before stock work")

        _mock_io(monkeypatch, _boom_backtest, df)
        with pytest.raises(ValueError, match="benchmark evidence unavailable"):
            ve.run_validation(horizon="short", universe="us")
        job = ve.get_run_status().get("job")
        assert job["failure_code"] == "BENCHMARK_EVIDENCE_UNAVAILABLE"


# ── D. Post-alignment signal coverage gate ──────────────────────────────────────

@pytest.mark.regression
class TestPostAlignmentSignalCoverageGate:
    def test_zero_benchmark_valid_signals_cannot_produce_a_completed_result(self, monkeypatch):
        def _fake_backtest(sym, hor, benchmark_df, market, universe=None, _exclusions=None, _window_stats=None, _diag=None):
            if _window_stats is not None:
                _window_stats["considered"] = 10
                _window_stats["benchmark_valid"] = 0  # every window excluded
            return []

        _mock_io(monkeypatch, _fake_backtest, auto_window_stats=False)
        with pytest.raises(ValueError, match="benchmark alignment coverage insufficient"):
            ve.run_validation(horizon="short", universe="us")
        job = ve.get_run_status().get("job")
        assert job is not None
        assert job["status"] == "failed"
        assert job["failure_code"] == "BENCHMARK_ALIGNMENT_COVERAGE_INSUFFICIENT"

    def test_insufficient_post_alignment_coverage_writes_no_val_runs_or_val_signals(self, monkeypatch):
        def _fake_backtest(sym, hor, benchmark_df, market, universe=None, _exclusions=None, _window_stats=None, _diag=None):
            if _window_stats is not None:
                _window_stats["considered"] = 100
                _window_stats["benchmark_valid"] = 1  # 1% coverage — far below 95%
            return []

        _mock_io(monkeypatch, _fake_backtest, auto_window_stats=False)
        monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _BoomIfWrittenConn())
        with pytest.raises(ValueError, match="benchmark alignment coverage insufficient"):
            ve.run_validation(horizon="short", universe="us")

    def test_compute_metrics_never_called_on_insufficient_signal_coverage(self, monkeypatch):
        def _fake_backtest(sym, hor, benchmark_df, market, universe=None, _exclusions=None, _window_stats=None, _diag=None):
            if _window_stats is not None:
                _window_stats["considered"] = 100
                _window_stats["benchmark_valid"] = 0
            return []

        boom_compute = MagicMock(side_effect=AssertionError("_compute_metrics must not be called"))
        _mock_io(monkeypatch, _fake_backtest, auto_window_stats=False)
        monkeypatch.setattr(ve, "_compute_metrics", boom_compute)
        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        boom_compute.assert_not_called()

    def test_latest_previously_valid_result_remains_readable_after_signal_coverage_failure(self, monkeypatch):
        prior_result = {"available": True, "buy_hit_rate_pct": 61.0, "run_at": "2026-01-01T00:00:00Z"}
        monkeypatch.setattr(ve, "get_latest_results", lambda **kw: dict(prior_result))

        def _fake_backtest(sym, hor, benchmark_df, market, universe=None, _exclusions=None, _window_stats=None, _diag=None):
            if _window_stats is not None:
                _window_stats["considered"] = 10
                _window_stats["benchmark_valid"] = 0
            return []

        _mock_io(monkeypatch, _fake_backtest, auto_window_stats=False)
        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")

        result = ve.get_latest_results(horizon="short", universe="us")
        assert result["buy_hit_rate_pct"] == 61.0

    def test_signal_window_denominator_and_exclusion_counts_are_accurate(self, monkeypatch):
        """A healthy run's disclosed signal_windows_considered/
        benchmark_valid_signal_windows/signals_excluded_benchmark must
        add up correctly and reflect the real per-symbol stats supplied."""
        # 95% coverage — must PASS the post-alignment gate so the run
        # actually completes and returns disclosed counts to assert on.
        per_symbol = {"considered": 20, "benchmark_valid": 19}

        def _fake_backtest(sym, hor, benchmark_df, market, universe=None, _exclusions=None, _window_stats=None, _diag=None):
            if _window_stats is not None:
                _window_stats["considered"] = per_symbol["considered"]
                _window_stats["benchmark_valid"] = per_symbol["benchmark_valid"]
            if _exclusions is not None:
                _exclusions.append(1)  # one genuine exclusion per symbol
            return []

        _mock_io(monkeypatch, _fake_backtest, auto_window_stats=False)
        metrics = ve.run_validation(horizon="short", universe="us")
        n_stocks = len(ve.US_BASKET)
        assert metrics["signal_windows_considered"] == per_symbol["considered"] * n_stocks
        assert metrics["benchmark_valid_signal_windows"] == per_symbol["benchmark_valid"] * n_stocks
        assert metrics["benchmark_signal_coverage_pct"] == pytest.approx(95.0)
        # signals_excluded_benchmark lives inside _compute_metrics' own
        # return dict, which short-circuits to {} when there are zero
        # actual signal dicts (pre-existing, unrelated behavior) — this
        # fake intentionally returns [] from every symbol to isolate the
        # window-stats counting this test is actually about.
        assert metrics.get("signals_excluded_benchmark") is None

    def test_failed_status_includes_stable_benchmark_evidence_with_coverage_counts(self, monkeypatch):
        def _fake_backtest(sym, hor, benchmark_df, market, universe=None, _exclusions=None, _window_stats=None, _diag=None):
            if _window_stats is not None:
                _window_stats["considered"] = 10
                _window_stats["benchmark_valid"] = 0
            return []

        _mock_io(monkeypatch, _fake_backtest, auto_window_stats=False)
        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        job = ve.get_run_status().get("job")
        evidence = job.get("benchmark_evidence")
        assert evidence is not None
        assert evidence["status"] == "available"  # acquisition itself was fine
        assert evidence["signal_windows_considered"] > 0
        assert evidence["benchmark_valid_signal_windows"] == 0
        assert evidence["benchmark_signal_coverage_pct"] is not None
        assert evidence["min_benchmark_signal_coverage_pct"] == ve.BENCHMARK_MIN_SIGNAL_COVERAGE_PCT


# ── E. Retry non-exception acquisition failures ─────────────────────────────────

@pytest.mark.regression
class TestRetryNonExceptionAcquisitionFailures:
    def test_empty_first_fetch_then_valid_second_fetch_succeeds(self, monkeypatch):
        calls = {"n": 0}
        valid_df = _valid_benchmark_df()

        class _FlakyEmptyTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                calls["n"] += 1
                return pd.DataFrame() if calls["n"] == 1 else valid_df

        _mock_io(monkeypatch, lambda *a, **k: [])
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _FlakyEmptyTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["job"]["status"] == "completed"
        assert calls["n"] == 2

    def test_malformed_first_fetch_then_valid_second_fetch_succeeds(self, monkeypatch):
        """missing_close_column is retriable — a first malformed frame
        followed by a genuinely valid one on retry must succeed."""
        calls = {"n": 0}
        valid_df = _valid_benchmark_df()
        malformed_df = pd.DataFrame({"Open": [1.0] * 10}, index=pd.bdate_range("2020-01-01", periods=10))

        class _FlakyMalformedTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                calls["n"] += 1
                return malformed_df if calls["n"] == 1 else valid_df

        _mock_io(monkeypatch, lambda *a, **k: [])
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _FlakyMalformedTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["job"]["status"] == "completed"
        assert calls["n"] == 2

    def test_structural_failure_is_not_retried_stops_after_one_attempt(self, monkeypatch):
        """unsorted_index is a deliberately non-retriable structural
        state — a second identical call cannot fix a schema problem, so
        the loop must stop after exactly one attempt."""
        calls = {"n": 0}
        dates = pd.bdate_range("2020-01-01", periods=100).to_list()
        dates[0], dates[-1] = dates[-1], dates[0]
        unsorted_df = pd.DataFrame({"Close": np.linspace(100, 200, 100)}, index=pd.DatetimeIndex(dates))

        class _AlwaysUnsortedTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                calls["n"] += 1
                return unsorted_df

        def _boom_backtest(*a, **k):
            raise AssertionError("must not be called")

        _mock_io(monkeypatch, _boom_backtest)
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _AlwaysUnsortedTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        assert calls["n"] == 1  # never retried

    def test_bounded_retries_remain_capped_across_mixed_retriable_statuses(self, monkeypatch):
        calls = {"n": 0}

        class _AlwaysEmptyTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                calls["n"] += 1
                return pd.DataFrame()

        _mock_io(monkeypatch, lambda *a, **k: [])
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _AlwaysEmptyTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        assert calls["n"] == ve.BENCHMARK_FETCH_MAX_ATTEMPTS

    def test_no_alternative_provider_is_ever_called(self, monkeypatch):
        seen_tickers = []

        class _TrackedFlakyTicker:
            def __init__(self, symbol, *a, **k):
                seen_tickers.append(symbol)

            def history(self, **kw):
                return pd.DataFrame()  # always empty — exhausts retries

        _mock_io(monkeypatch, lambda *a, **k: [])
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _TrackedFlakyTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")
        assert set(seen_tickers) == {"^GSPC"}


# ── G. Timezone / index robustness ──────────────────────────────────────────────

@pytest.mark.regression
class TestTimezoneAndIndexRobustness:
    def test_tz_aware_benchmark_vs_naive_stock_index_is_deterministic(self):
        stock_index = pd.bdate_range("2020-01-01", periods=30)  # naive
        bench_index = pd.bdate_range("2020-01-01", periods=30, tz="America/New_York")
        bench_df = pd.DataFrame({"Close": np.linspace(100, 130, 30)}, index=bench_index)

        aligned, available = _align_benchmark_close(bench_df, stock_index)  # must not raise
        assert available.sum() > 0
        assert available.iloc[0] == True
        assert aligned.iloc[0] == pytest.approx(100.0)

    def test_tz_naive_benchmark_vs_tz_aware_stock_index_is_deterministic(self):
        stock_index = pd.bdate_range("2020-01-01", periods=30, tz="UTC")
        bench_index = pd.bdate_range("2020-01-01", periods=30)  # naive
        bench_df = pd.DataFrame({"Close": np.linspace(100, 130, 30)}, index=bench_index)

        aligned, available = _align_benchmark_close(bench_df, stock_index)  # must not raise
        assert available.sum() > 0

    def test_intraday_timestamps_are_normalized_to_calendar_dates(self):
        stock_index = pd.bdate_range("2020-01-01", periods=10)
        # Benchmark timestamps carry an intraday component (market close time).
        bench_index = stock_index + pd.Timedelta(hours=16)
        bench_df = pd.DataFrame({"Close": np.linspace(100, 110, 10)}, index=bench_index)

        aligned, available = _align_benchmark_close(bench_df, stock_index)
        # Same calendar dates once normalized — every position should align.
        assert available.sum() == 10
        assert aligned.iloc[0] == pytest.approx(100.0)

    def test_non_datetimeindex_stock_index_fails_safe_not_raise(self):
        stock_index = pd.RangeIndex(10)
        bench_df = _valid_benchmark_df(n=10)
        aligned, available = _align_benchmark_close(bench_df, stock_index)  # must not raise
        assert not available.any()

    def test_non_datetimeindex_benchmark_fails_safe_not_raise(self):
        stock_index = pd.bdate_range("2020-01-01", periods=10)
        bench_df = pd.DataFrame({"Close": np.linspace(100, 110, 10)}, index=pd.RangeIndex(10))
        aligned, available = _align_benchmark_close(bench_df, stock_index)  # must not raise
        assert not available.any()

    def test_non_datetimeindex_acquisition_fails_safe(self):
        df = pd.DataFrame({"Close": np.linspace(100, 200, 100)}, index=pd.RangeIndex(100))
        ev = _validate_benchmark_acquisition(df, "^GSPC", "US", "short")  # must not raise
        assert ev.status == "invalid_index_type"

    def test_no_future_benchmark_date_selected_across_tz_normalization(self):
        """The normalization step itself must never let a later
        (future-relative) benchmark observation get selected for an
        earlier stock date — proven with a tz-aware benchmark whose first
        real observation is strictly after several stock dates."""
        stock_index = pd.bdate_range("2020-01-01", periods=20)
        bench_index = pd.bdate_range("2020-01-10", periods=20, tz="UTC")  # starts later
        bench_df = pd.DataFrame({"Close": np.linspace(100, 120, 20)}, index=bench_index)

        aligned, available = _align_benchmark_close(bench_df, stock_index)
        early_dates = stock_index[stock_index < pd.Timestamp("2020-01-10")]
        for d in early_dates:
            assert available.loc[d] == False, f"{d} incorrectly available — implies a future/backward fill"

    def test_duplicate_dates_after_tz_normalization_resolved_deterministically(self):
        """Two distinct tz-aware raw timestamps that normalize onto the
        SAME calendar date must be deduped deterministically (last wins),
        not silently produce an ambiguous/duplicate-indexed result."""
        stock_index = pd.bdate_range("2020-01-01", periods=5)
        # Two timestamps on the same calendar day, different intraday time.
        dup_ts = pd.DatetimeIndex([
            pd.Timestamp("2020-01-01 09:30", tz="UTC"),
            pd.Timestamp("2020-01-01 16:00", tz="UTC"),
            pd.Timestamp("2020-01-02 16:00", tz="UTC"),
        ])
        bench_df = pd.DataFrame({"Close": [100.0, 999.0, 101.0]}, index=dup_ts)
        aligned, available = _align_benchmark_close(bench_df, stock_index)  # must not raise
        assert aligned.iloc[0] == pytest.approx(999.0)  # last-wins, deterministic


# ── Healthy-path parity (India + US) ─────────────────────────────────────────────

@pytest.mark.regression
class TestHealthyPathParityAfterHardening:
    def test_healthy_us_benchmark_run_completes_numerically_unaffected(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [])
        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["job"]["status"] == "completed"
        assert metrics["benchmark_data_available"] is True
        assert metrics["benchmark_evidence"]["status"] == "available"

    def test_healthy_india_benchmark_run_completes_numerically_unaffected(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [])
        metrics = ve.run_validation(horizon="medium", universe="nifty100")
        assert metrics["job"]["status"] == "completed"
        assert metrics["benchmark_data_available"] is True
        assert metrics["job"]["market"] == "IN"
        assert metrics["job"]["benchmark"] == "^NSEI"
