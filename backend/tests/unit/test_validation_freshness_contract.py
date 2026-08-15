"""
V-FRESH1B — durable validation evidence metadata and honest freshness
disclosure (Option A from V-FRESH1A2: a durable metadata-and-disclosure
foundation, NOT a complete fresh/stale classifier).

Confirmed forensic findings this phase closes (V-FRESH1A/V-FRESH1A2):
  - `run_at` is a durable validation-completion timestamp (unchanged here).
  - No durable run-level input-data-through or outcome-through metadata
    existed before this phase.
  - `n_stocks_with_signals` means only "symbols that produced signals" —
    never "coverage" or "eligibility." This file's assertions use factual
    field names throughout and never label that count as coverage.
  - `signal_date` means a signal-generation date, not "latest input bar
    fetched." The actual first/last input-bar dates were available in
    `_backtest_stock()` but discarded before this phase.
  - The actual evaluated exit date is available as `df.index[i+fwd_days]`
    at calculation time — never reconstructed via calendar arithmetic.
  - Insufficient-data, fetch failure, and calculation exception used to
    collapse into the same silent `[]` return with no durable distinction
    (the root cause of long-US run 221's unresolved 6/42 finding) — this
    phase adds a concurrency-safe, per-symbol-owned diagnostic object that
    distinguishes them.
  - No exchange-calendar utility and no completion-SLO contract exist yet
    — so no response may be labelled "fresh"/"stale"/"schedule-consistent"
    /"schedule-missed" in this phase. Every freshness status resolves to
    "unknown" with one specific, honest, machine-readable reason.

No schema migration in this phase — all new evidence lives inside the
existing immutable `summary` JSON/JSONB column already persisted in
`val_runs`, exactly like `n_stocks_requested`/`n_stocks_with_signals`
already do. Legacy rows (100% of production today) simply lack the new
`validation_evidence` key and get `unknown`/`legacy_run_without_evidence_
metadata` — never a fabricated or derived value.
"""
import json
import math
import sqlite3

import numpy as np
import pandas as pd
import pytest

import services.validation_engine as ve
from services.validation_engine import (
    _backtest_stock,
    _build_validation_evidence,
    _compute_freshness,
)


# ─────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "freshness_test.db")
    monkeypatch.setattr(ve, "_DB_PATH", db_path)
    monkeypatch.setattr(ve, "_db_initialised", False)
    ve._init_db()
    return db_path


def _insert_run(db_path, horizon="medium", universe="nifty100", summary=None, run_at="2026-08-01T00:00:00"):
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
            "VALUES (?, ?, 1, 0, ?, ?)",
            (run_at, horizon, json.dumps(summary) if summary is not None else None, universe),
        )
        return cur.lastrowid


def _synthetic_stock_ohlcv(n=260, seed=42, start="2019-01-01"):
    """Matches the established convention already used by
    test_validation_nonfinite_forward_returns.py / test_validation_
    benchmark_evidence.py — duplicated here per that same self-contained-
    test-file convention."""
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


class _FakeTicker:
    def __init__(self, *a, **k):
        pass

    @property
    def info(self):
        return {}


def _bench_df_for(stock_df):
    return pd.DataFrame(
        {"Close": np.linspace(100, 140, len(stock_df.index))}, index=stock_df.index
    )


# ── A. _backtest_stock's per-symbol diagnostic terminal-path model ────────

class TestBacktestStockDiagnostics:
    def test_fetch_exception_is_classified_distinctly(self, monkeypatch):
        class _RaisingTicker(_FakeTicker):
            def history(self, period=None, timeout=None):
                raise ConnectionError("provider unreachable")

        monkeypatch.setattr(ve.yf, "Ticker", _RaisingTicker)
        diag = {}
        signals = _backtest_stock("XXX", "short", None, "US", universe="us", _diag=diag)
        assert signals == []
        assert diag["terminal_path"] == "fetch_exception"
        assert diag["input_first_date"] is None
        assert diag["input_last_date"] is None

    def test_empty_dataframe_is_classified_distinctly(self, monkeypatch):
        class _EmptyTicker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return pd.DataFrame()

        monkeypatch.setattr(ve.yf, "Ticker", _EmptyTicker)
        diag = {}
        signals = _backtest_stock("XXX", "short", None, "US", universe="us", _diag=diag)
        assert signals == []
        assert diag["terminal_path"] == "empty_data"
        assert diag["input_last_date"] is None

    def test_insufficient_bars_is_classified_distinctly_and_still_captures_input_dates(self, monkeypatch):
        stock_df = _synthetic_stock_ohlcv(n=100)  # well below MIN_WARMUP(200)+fwd_days

        class _ShortTicker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _ShortTicker)
        diag = {}
        signals = _backtest_stock("XXX", "short", None, "US", universe="us", _diag=diag)
        assert signals == []
        assert diag["terminal_path"] == "insufficient_data"
        # A non-empty (if too-short) dataframe still yields real input dates —
        # this symbol genuinely returned data, just not enough of it.
        assert diag["input_first_date"] == str(stock_df.index[0])[:10]
        assert diag["input_last_date"] == str(stock_df.index[-1])[:10]

    def test_calculation_exception_is_classified_distinctly(self, monkeypatch):
        stock_df = _synthetic_stock_ohlcv(n=260)

        class _Ticker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        # Force a downstream calculation exception (after the length gate,
        # inside the per-window loop) via a broken benchmark_df that raises
        # when its Close column is accessed for alignment.
        class _BrokenBenchDf:
            empty = False
            columns = ["Close"]

            def __getitem__(self, key):
                raise RuntimeError("simulated downstream failure")

        diag = {}
        signals = _backtest_stock("XXX", "short", _BrokenBenchDf(), "US", universe="us", _diag=diag)
        assert signals == []
        assert diag["terminal_path"] == "calculation_exception"
        # Input dates were still captured before the failure occurred —
        # a calculation exception must not erase already-known input evidence.
        assert diag["input_first_date"] == str(stock_df.index[0])[:10]
        assert diag["input_last_date"] == str(stock_df.index[-1])[:10]

    def test_processed_symbol_with_zero_signals_is_not_a_failure(self, monkeypatch):
        """A symbol may load complete, valid, sufficient data and still
        legitimately emit zero signals (e.g. no benchmark evidence at any
        window) — this must be terminal_path='processed', never one of
        the failure paths."""
        stock_df = _synthetic_stock_ohlcv(n=260)

        class _Ticker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        diag = {}
        # No benchmark_df at all -> every window's benchmark_ok is False ->
        # zero signals, but the symbol still passed the length gate and
        # entered the loop.
        signals = _backtest_stock("XXX", "short", None, "US", universe="us", _diag=diag)
        assert signals == []
        assert diag["terminal_path"] == "processed"

    def test_sufficient_data_produces_signals_and_captures_input_dates(self, monkeypatch):
        stock_df = _synthetic_stock_ohlcv(n=260)

        class _Ticker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        bench_df = _bench_df_for(stock_df)
        diag = {}
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us", _diag=diag)
        assert len(signals) > 0
        assert diag["terminal_path"] == "processed"
        assert diag["input_first_date"] == str(stock_df.index[0])[:10]
        assert diag["input_last_date"] == str(stock_df.index[-1])[:10]

    def test_diag_none_preserves_existing_backward_compatible_behavior(self, monkeypatch):
        """Existing callers that never pass _diag must be completely
        unaffected — same signature, same return type, no crash."""
        stock_df = _synthetic_stock_ohlcv(n=260)

        class _Ticker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        bench_df = _bench_df_for(stock_df)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        assert len(signals) > 0


# ── B. Evaluated exit-date capture — real dataframe index, no calendar math ─

class TestExitDateCapture:
    def test_evaluated_signal_carries_the_actual_exit_bar_date(self, monkeypatch):
        stock_df = _synthetic_stock_ohlcv(n=260)

        class _Ticker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        bench_df = _bench_df_for(stock_df)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        fwd_days = ve.HORIZON_DAYS["short"]
        evaluated = [s for s in signals if s["fwd_return_pct"] is not None]
        assert evaluated, "expected at least one evaluated signal"
        for s in evaluated:
            entry_idx = stock_df.index.get_loc(pd.Timestamp(s["signal_date"]))
            expected_exit_date = str(stock_df.index[entry_idx + fwd_days])[:10]
            assert s["exit_date"] == expected_exit_date

    def test_unresolved_outcome_never_fabricates_an_exit_date(self, monkeypatch):
        """A signal whose exit price is non-finite (fwd_return_pct=None)
        must carry exit_date=None too — never a calendar-derived guess."""
        stock_df = _synthetic_stock_ohlcv(n=260)
        stock_df = stock_df.copy()
        fwd_days = ve.HORIZON_DAYS["short"]
        # Corrupt one specific future exit close to NaN so its window's
        # outcome is genuinely unresolved.
        stock_df.iloc[250, stock_df.columns.get_loc("Close")] = np.nan

        class _Ticker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        bench_df = _bench_df_for(stock_df)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        unresolved = [s for s in signals if s["fwd_return_pct"] is None]
        for s in unresolved:
            assert s["exit_date"] is None


# ── C. _build_validation_evidence — factual counters and date aggregation ──

class TestBuildValidationEvidence:
    def _diag(self, terminal_path, first=None, last=None):
        return {"terminal_path": terminal_path, "input_first_date": first, "input_last_date": last}

    def test_terminal_path_counts_reconcile_to_symbols_requested(self):
        diag_by_symbol = {
            "A": self._diag("processed", "2026-01-01", "2026-08-01"),
            "B": self._diag("fetch_exception"),
            "C": self._diag("empty_data"),
            "D": self._diag("insufficient_data", "2026-01-01", "2026-02-01"),
            "E": self._diag("calculation_exception", "2026-01-01", "2026-03-01"),
        }
        window_stats = {"A": {"considered": 5, "benchmark_valid": 5}}
        evidence = _build_validation_evidence(5, diag_by_symbol, window_stats, {"A"}, [])
        assert evidence["symbols_requested"] == 5
        total = (
            evidence["fetch_exception_count"] + evidence["empty_data_count"]
            + evidence["insufficient_data_count"] + evidence["calculation_exception_count"]
            + evidence["symbols_with_sufficient_data"]
        )
        assert total == 5

    def test_symbols_with_input_data_counts_every_nonempty_fetch_regardless_of_outcome(self):
        diag_by_symbol = {
            "A": self._diag("processed", "2026-01-01", "2026-08-01"),
            "B": self._diag("insufficient_data", "2026-01-01", "2026-02-01"),
            "C": self._diag("calculation_exception", "2026-01-01", "2026-03-01"),
            "D": self._diag("fetch_exception"),
        }
        evidence = _build_validation_evidence(4, diag_by_symbol, {}, set(), [])
        assert evidence["symbols_with_input_data"] == 3  # A, B, C had real dataframes; D did not

    def test_zero_signal_symbol_is_not_counted_as_a_failure(self):
        diag_by_symbol = {"A": self._diag("processed", "2026-01-01", "2026-08-01")}
        window_stats = {"A": {"considered": 10, "benchmark_valid": 0}}
        evidence = _build_validation_evidence(1, diag_by_symbol, window_stats, set(), [])
        assert evidence["symbols_with_signals"] == 0
        assert evidence["fetch_exception_count"] == 0
        assert evidence["empty_data_count"] == 0
        assert evidence["insufficient_data_count"] == 0
        assert evidence["calculation_exception_count"] == 0
        assert evidence["symbols_processed"] == 1  # entered the loop, considered=10>0

    def test_processed_symbol_that_never_entered_the_loop_is_not_double_counted(self):
        """Edge case: len(df) exactly equals the sufficiency threshold, so
        terminal_path='processed' is set, but the window range is empty
        (considered=0). symbols_with_sufficient_data must still count it;
        symbols_processed must not."""
        diag_by_symbol = {"A": self._diag("processed", "2026-01-01", "2026-08-01")}
        window_stats = {"A": {"considered": 0, "benchmark_valid": 0}}
        evidence = _build_validation_evidence(1, diag_by_symbol, window_stats, set(), [])
        assert evidence["symbols_with_sufficient_data"] == 1
        assert evidence["symbols_processed"] == 0

    def test_signal_and_evaluated_counts_never_exceed_processed(self):
        diag_by_symbol = {
            "A": self._diag("processed", "2026-01-01", "2026-08-01"),
            "B": self._diag("insufficient_data"),
        }
        window_stats = {"A": {"considered": 3, "benchmark_valid": 3}}
        all_signals = [
            {"symbol": "A", "signal_date": "2026-01-05", "exit_date": "2026-01-10", "fwd_return_pct": 1.0},
            {"symbol": "A", "signal_date": "2026-01-06", "exit_date": None, "fwd_return_pct": None},
        ]
        evidence = _build_validation_evidence(2, diag_by_symbol, window_stats, {"A"}, all_signals)
        assert evidence["symbols_with_signals"] <= evidence["symbols_processed"]
        assert evidence["symbols_with_evaluated_outcomes"] <= evidence["symbols_processed"]
        assert evidence["symbols_with_signals"] == 1
        assert evidence["symbols_with_evaluated_outcomes"] == 1

    def test_date_fields_are_min_max_pairs_that_never_invert(self):
        diag_by_symbol = {
            "A": self._diag("processed", "2026-01-01", "2026-08-01"),
            "B": self._diag("processed", "2026-02-01", "2026-07-15"),
        }
        window_stats = {"A": {"considered": 1}, "B": {"considered": 1}}
        all_signals = [
            {"symbol": "A", "signal_date": "2026-03-01", "exit_date": "2026-03-10", "fwd_return_pct": 1.0},
            {"symbol": "B", "signal_date": "2026-04-01", "exit_date": "2026-04-15", "fwd_return_pct": 2.0},
        ]
        evidence = _build_validation_evidence(2, diag_by_symbol, window_stats, {"A", "B"}, all_signals)
        assert evidence["input_latest_bar_date_min"] == "2026-07-15"
        assert evidence["input_latest_bar_date_max"] == "2026-08-01"
        assert evidence["signal_date_min"] == "2026-03-01"
        assert evidence["signal_date_max"] == "2026-04-01"
        assert evidence["evaluated_exit_date_min"] == "2026-03-10"
        assert evidence["evaluated_exit_date_max"] == "2026-04-15"
        for lo_key, hi_key in [
            ("input_latest_bar_date_min", "input_latest_bar_date_max"),
            ("signal_date_min", "signal_date_max"),
            ("evaluated_exit_date_min", "evaluated_exit_date_max"),
        ]:
            assert evidence[lo_key] <= evidence[hi_key]

    def test_missing_evidence_is_null_never_fabricated(self):
        evidence = _build_validation_evidence(0, {}, {}, set(), [])
        assert evidence["input_latest_bar_date_min"] is None
        assert evidence["input_latest_bar_date_max"] is None
        assert evidence["signal_date_min"] is None
        assert evidence["signal_date_max"] is None
        assert evidence["evaluated_exit_date_min"] is None
        assert evidence["evaluated_exit_date_max"] is None

    def test_all_counters_are_non_negative_integers(self):
        diag_by_symbol = {"A": self._diag("processed", "2026-01-01", "2026-08-01")}
        window_stats = {"A": {"considered": 1}}
        evidence = _build_validation_evidence(1, diag_by_symbol, window_stats, {"A"}, [])
        int_keys = [k for k in evidence if k.endswith("_count") or k.startswith("symbols_")]
        for k in int_keys:
            assert isinstance(evidence[k], int) and evidence[k] >= 0


# ── D. Concurrency safety — per-symbol-owned diagnostics, no cross-talk ────

class TestConcurrencySafety:
    def test_concurrent_workers_produce_independent_uncorrupted_diagnostics(self, monkeypatch):
        """Simulates run_validation()'s exact pattern — one fresh diag dict
        per symbol, submitted to a ThreadPoolExecutor, never a shared
        mutable dict written by multiple threads."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        good_df = _synthetic_stock_ohlcv(n=260, seed=1)
        short_df = _synthetic_stock_ohlcv(n=50, seed=2)

        def _history_for(symbol):
            if symbol == "GOOD":
                return good_df
            if symbol == "SHORT":
                return short_df
            raise ConnectionError("simulated provider failure")

        class _Ticker(_FakeTicker):
            def __init__(self, sym, *a, **k):
                self._sym = sym

            def history(self, period=None, timeout=None):
                return _history_for(self._sym)

        monkeypatch.setattr(ve.yf, "Ticker", lambda sym: _Ticker(sym))
        symbols = ["GOOD", "SHORT", "BAD"] * 5  # 15 concurrent calls, deliberate repeats
        bench_df = _bench_df_for(good_df)

        diag_by_symbol = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {}
            for i, sym in enumerate(symbols):
                key = f"{sym}_{i}"
                diag = {}
                diag_by_symbol[key] = diag
                futures[pool.submit(
                    _backtest_stock, sym, "short", bench_df, "US", universe="us", _diag=diag,
                )] = key
            for future in as_completed(futures):
                future.result()

        for key, diag in diag_by_symbol.items():
            sym = key.rsplit("_", 1)[0]
            if sym == "GOOD":
                assert diag["terminal_path"] == "processed"
            elif sym == "SHORT":
                assert diag["terminal_path"] == "insufficient_data"
            elif sym == "BAD":
                assert diag["terminal_path"] == "fetch_exception"


# ── E. Freshness status contract — always "unknown", never fabricated ─────

class TestComputeFreshness:
    def test_short_horizon_is_always_schedule_not_defined(self):
        data = {"horizon": "short", "run_at": "2026-08-01T00:00:00+00:00", "validation_evidence": {"x": 1}}
        result = _compute_freshness(data)
        assert result["status"] == "unknown"
        assert result["reason"] == "schedule_not_defined"

    def test_short_horizon_is_schedule_not_defined_even_without_evidence(self):
        data = {"horizon": "short", "run_at": "2026-08-01T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "schedule_not_defined"

    def test_legacy_medium_run_without_evidence_gets_legacy_reason(self):
        data = {"horizon": "medium", "run_at": "2026-08-01T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["status"] == "unknown"
        assert result["reason"] == "legacy_run_without_evidence_metadata"

    def test_legacy_long_run_without_evidence_gets_legacy_reason(self):
        data = {"horizon": "long", "run_at": "2026-08-01T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "legacy_run_without_evidence_metadata"

    def test_future_medium_run_with_evidence_gets_calendar_slo_reason(self):
        data = {
            "horizon": "medium", "run_at": "2026-08-01T00:00:00+00:00",
            "validation_evidence": {"symbols_requested": 42},
        }
        result = _compute_freshness(data)
        assert result["status"] == "unknown"
        assert result["reason"] == "calendar_or_completion_slo_unavailable"

    def test_future_long_run_with_evidence_gets_calendar_slo_reason(self):
        data = {
            "horizon": "long", "run_at": "2026-08-01T00:00:00+00:00",
            "validation_evidence": {"symbols_requested": 6},
        }
        result = _compute_freshness(data)
        assert result["reason"] == "calendar_or_completion_slo_unavailable"

    def test_never_returns_fresh_or_stale_or_schedule_consistent_labels(self):
        forbidden = {
            "fresh", "stale", "schedule-consistent", "schedule-missed",
            "within_expected_cadence", "past_expected_cadence",
        }
        for horizon in ("short", "medium", "long"):
            for evidence in (None, {"symbols_requested": 1}):
                data = {"horizon": horizon, "run_at": "2026-08-01T00:00:00+00:00", "validation_evidence": evidence}
                result = _compute_freshness(data)
                assert result["status"] not in forbidden
                assert result["reason"] not in forbidden
                assert result["status"] == "unknown"

    def test_validation_completed_at_copies_run_at_without_altering_it(self):
        data = {"horizon": "medium", "run_at": "2026-08-01T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["validation_completed_at"] == "2026-08-01T00:00:00+00:00"
        assert data["run_at"] == "2026-08-01T00:00:00+00:00"  # unchanged, not removed

    def test_input_and_outcome_recency_remain_unknown_in_this_phase(self):
        data = {
            "horizon": "medium", "run_at": "2026-08-01T00:00:00+00:00",
            "validation_evidence": {"symbols_requested": 42},
        }
        result = _compute_freshness(data)
        assert result["input_data_recency"] == "unknown"
        assert result["outcome_evidence_recency"] == "unknown"


# ── V-SCHED1C2D — freshness follows the runtime VALIDATION_AUTO_SHORT_
# UNIVERSES allowlist, per-universe, instead of unconditionally treating
# every short-horizon run as unscheduled. Enabled short universes are
# still never claimed "fresh" or "current" merely from being scheduled —
# they get the SAME calendar_or_completion_slo_unavailable reason medium/
# long already use once evidence exists, never a new "scheduled"/"fresh"
# label. A disabled short universe (including short/us today) keeps the
# original honest schedule_not_defined reason.

class TestComputeFreshnessAutoShortSchedule:
    def test_missing_variable_short_nifty100_is_schedule_not_defined(self, monkeypatch):
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        data = {"horizon": "short", "universe": "nifty100", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "schedule_not_defined"

    def test_blank_variable_short_midcap_is_schedule_not_defined(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "   ")
        data = {"horizon": "short", "universe": "midcap", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "schedule_not_defined"

    def test_enabled_nifty100_gets_calendar_slo_reason_not_schedule_not_defined(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap")
        data = {"horizon": "short", "universe": "nifty100", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["status"] == "unknown"
        assert result["reason"] == "calendar_or_completion_slo_unavailable"

    def test_enabled_midcap_gets_calendar_slo_reason(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap")
        data = {"horizon": "short", "universe": "midcap", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "calendar_or_completion_slo_unavailable"

    def test_disabled_us_stays_schedule_not_defined_even_with_india_enabled(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap")
        data = {"horizon": "short", "universe": "us", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "schedule_not_defined"

    def test_us_enabled_alone_gets_calendar_slo_reason(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "us")
        data = {"horizon": "short", "universe": "us", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "calendar_or_completion_slo_unavailable"

    def test_invalid_variable_value_fails_closed_for_every_short_universe(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "all")
        for universe in ("nifty100", "midcap", "us"):
            data = {"horizon": "short", "universe": universe, "run_at": "2026-08-14T00:00:00+00:00"}
            result = _compute_freshness(data)
            assert result["reason"] == "schedule_not_defined", f"universe={universe}"

    def test_medium_freshness_unaffected_by_short_variable_state(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap,us")
        data = {
            "horizon": "medium", "universe": "nifty100", "run_at": "2026-08-14T00:00:00+00:00",
            "validation_evidence": {"symbols_requested": 134},
        }
        result = _compute_freshness(data)
        assert result["reason"] == "calendar_or_completion_slo_unavailable"

        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        result_disabled = _compute_freshness(data)
        assert result_disabled["reason"] == result["reason"]

    def test_long_freshness_unaffected_by_short_variable_state(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap,us")
        data = {
            "horizon": "long", "universe": "us", "run_at": "2026-08-14T00:00:00+00:00",
            "validation_evidence": {"symbols_requested": 42},
        }
        result = _compute_freshness(data)
        assert result["reason"] == "calendar_or_completion_slo_unavailable"

        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        result_disabled = _compute_freshness(data)
        assert result_disabled["reason"] == result["reason"]

    def test_legacy_medium_without_evidence_still_gets_legacy_reason_regardless_of_short_config(self, monkeypatch):
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap")
        data = {"horizon": "medium", "universe": "nifty100", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "legacy_run_without_evidence_metadata"

    def test_enabled_short_universe_status_never_becomes_fresh_or_current(self, monkeypatch):
        """Schedule enablement alone must never be conflated with actual
        data freshness — enabling the schedule changes the REASON, never
        the status. status must remain "unknown", and the reason must
        never be a fresh/current-sounding label."""
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,midcap")
        data = {"horizon": "short", "universe": "nifty100", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["status"] == "unknown"
        forbidden = {"fresh", "current", "up_to_date", "stale", "schedule-consistent"}
        assert result["reason"] not in forbidden
        assert result["status"] not in forbidden

    def test_parser_has_one_production_implementation_shared_by_scheduler_and_freshness(self, monkeypatch):
        """Both the scheduler (api.main) and freshness classification
        (services.validation_engine) must call the exact same parser
        object from services.market_calendar — never two independently
        maintained copies of the same allowlist semantics. Proven two
        ways: identity (api.main's name IS market_calendar's function),
        and behaviorally (patching market_calendar's parser changes what
        _compute_freshness sees, proving it isn't a local copy)."""
        import services.market_calendar as mc
        from api.main import _parse_auto_short_universes as main_parser

        assert main_parser is mc.parse_auto_short_universes

        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100")
        monkeypatch.setattr(mc, "parse_auto_short_universes", lambda raw: ())
        data = {"horizon": "short", "universe": "nifty100", "run_at": "2026-08-14T00:00:00+00:00"}
        result = _compute_freshness(data)
        assert result["reason"] == "schedule_not_defined", (
            "patching services.market_calendar.parse_auto_short_universes did not change "
            "_compute_freshness's behavior — it must be calling a local copy, not the shared function"
        )

    def test_freshness_module_imports_the_shared_parser_not_a_local_copy(self):
        import services.market_calendar as mc
        assert hasattr(mc, "parse_auto_short_universes")
        # validation_engine must not define its own competing allowlist —
        # it may only reference market_calendar's.
        assert not hasattr(ve, "_AUTO_SHORT_VALID_UNIVERSES")


# ── F. get_latest_results() integration — legacy vs future rows, DB-backed ─

class TestGetLatestResultsFreshnessIntegration:
    def test_legacy_summary_returns_valid_additive_evidence_contract(self, isolated_db):
        """A legacy row (no validation_evidence key at all) must still
        return available:true, an honest freshness.status='unknown', and
        must not crash or fabricate any evidence field."""
        _insert_run(isolated_db, horizon="medium", summary={"buy_hit_rate_pct": 55.0})
        result = ve.get_latest_results(horizon="medium", universe="nifty100")
        assert result["available"] is True
        assert result["freshness"]["status"] == "unknown"
        assert result["freshness"]["reason"] == "legacy_run_without_evidence_metadata"
        assert "validation_evidence" not in result

    def test_run_at_appears_as_validation_completed_at(self, isolated_db):
        _insert_run(isolated_db, horizon="medium",
                    summary={"buy_hit_rate_pct": 55.0, "horizon": "medium", "run_at": "2026-08-01T00:00:00+00:00"},
                    run_at="2026-08-01T00:00:00")
        result = ve.get_latest_results(horizon="medium", universe="nifty100")
        assert result["freshness"]["validation_completed_at"] == result["run_at"]
        assert result["run_at"] is not None  # run_at itself is unchanged, still present

    def test_short_horizon_legacy_run_still_gets_schedule_not_defined_not_legacy_reason(self, isolated_db):
        """Short horizon's reason must always be schedule_not_defined,
        even for a row that also happens to lack validation_evidence —
        the no-schedule fact takes priority over the legacy-row fact."""
        _insert_run(isolated_db, horizon="short", summary={"buy_hit_rate_pct": 50.0, "horizon": "short"})
        result = ve.get_latest_results(horizon="short", universe="nifty100")
        assert result["freshness"]["reason"] == "schedule_not_defined"

    def test_future_run_with_validation_evidence_exposes_it_additively(self, isolated_db):
        evidence = {
            "symbols_requested": 2, "symbols_fetch_attempted": 2, "symbols_with_input_data": 2,
            "symbols_with_sufficient_data": 2, "symbols_processed": 2, "symbols_with_signals": 2,
            "symbols_with_evaluated_outcomes": 1, "fetch_exception_count": 0, "empty_data_count": 0,
            "insufficient_data_count": 0, "calculation_exception_count": 0,
            "input_latest_bar_date_min": "2026-08-10", "input_latest_bar_date_max": "2026-08-11",
            "signal_date_min": "2026-01-01", "signal_date_max": "2026-08-01",
            "evaluated_exit_date_min": "2026-02-01", "evaluated_exit_date_max": "2026-06-01",
        }
        _insert_run(isolated_db, horizon="medium", summary={"buy_hit_rate_pct": 55.0, "validation_evidence": evidence})
        result = ve.get_latest_results(horizon="medium", universe="nifty100")
        assert result["validation_evidence"] == evidence
        assert result["freshness"]["reason"] == "calendar_or_completion_slo_unavailable"

    def test_existing_summary_keys_remain_backward_compatible(self, isolated_db):
        """n_stocks_requested/n_stocks_with_signals and every other
        pre-existing key must survive completely unchanged alongside the
        new additive fields."""
        _insert_run(isolated_db, horizon="medium", summary={
            "buy_hit_rate_pct": 55.0, "n_stocks_requested": 42, "n_stocks_with_signals": 42,
        })
        result = ve.get_latest_results(horizon="medium", universe="nifty100")
        assert result["n_stocks_requested"] == 42
        assert result["n_stocks_with_signals"] == 42
        assert result["buy_hit_rate_pct"] == 55.0

    def test_available_true_with_freshness_unknown_is_valid_and_independent(self, isolated_db):
        _insert_run(isolated_db, horizon="medium", summary={"buy_hit_rate_pct": 55.0})
        result = ve.get_latest_results(horizon="medium", universe="nifty100")
        assert result["available"] is True
        assert result["freshness"]["status"] == "unknown"

    def test_v_snap1_run_id_pinning_is_unaffected_by_freshness_addition(self, isolated_db):
        run_id = _insert_run(isolated_db, horizon="medium", summary={"buy_hit_rate_pct": 55.0})
        result = ve.get_latest_results(horizon="medium", universe="nifty100")
        assert result["run_id"] == run_id
        resolved = ve.resolve_eligible_run_id(run_id, "medium", "nifty100")
        assert resolved == run_id


# ── G. No schema/migration introduced ──────────────────────────────────────

class TestNoSchemaChange:
    def test_pg_schema_constant_has_no_new_columns_added(self):
        """V-FRESH1B stores all new evidence inside the existing summary
        JSON/JSONB column — this locks in that no ALTER TABLE / new
        column statement was introduced for validation_evidence or
        freshness fields."""
        assert "validation_evidence" not in ve._PG_SCHEMA
        assert "market_data_through" not in ve._PG_SCHEMA
        assert "freshness_status" not in ve._PG_SCHEMA
        assert "outcomes_through" not in ve._PG_SCHEMA


# ── H. V-PS2 non-finite behavior remains unchanged ─────────────────────────

class TestVPS2Unaffected:
    def test_nonfinite_entry_still_produces_no_signal_for_that_window(self, monkeypatch):
        stock_df = _synthetic_stock_ohlcv(n=260)
        stock_df = stock_df.copy()
        stock_df.iloc[210, stock_df.columns.get_loc("Close")] = np.nan

        class _Ticker(_FakeTicker):
            def history(self, period=None, timeout=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        bench_df = _bench_df_for(stock_df)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        corrupted_date = str(stock_df.index[210])[:10]
        assert not any(s["signal_date"] == corrupted_date for s in signals)
        for s in signals:
            if s["fwd_return_pct"] is not None:
                assert math.isfinite(s["fwd_return_pct"])
