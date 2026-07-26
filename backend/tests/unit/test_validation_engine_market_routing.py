"""
Phase GPI-1 — explicit market routing for the walk-forward validation engine.

Confirmed defect (D1 from the global price-integrity audit,
Documentation/Engineering-Handbook/Releases/Product-Integrity-005-...md):
services/validation_engine.py's _backtest_stock() used to determine Yahoo
symbol routing from a ticker-shape heuristic —

    is_us = not symbol.endswith(".NS") and "." not in symbol and len(symbol) <= 5
    yf_sym = symbol if is_us else symbol + ".NS"

— which misrouted short NSE symbols (INFY, TCS, SBIN, M&M, ...) to a bare
Yahoo ticker, in at least one confirmed live case (INFY) silently resolving
to an unrelated US-listed instrument (its own NYSE ADR) instead of the NSE
line or a clear failure.

This file proves the replacement contract: routing is driven entirely by an
explicit `market` ("IN" | "US") the caller supplies — never inferred from
the symbol's own length, punctuation, or casing — and that an unsupported
market value fails closed instead of silently defaulting.

No network access anywhere in this file: every yfinance call is monkeypatched
to a synthetic, deterministic OHLCV DataFrame. No database (Postgres or
SQLite) is touched — run_validation()'s DB/init boundaries are stubbed out
in the tests that exercise it.
"""
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from services import validation_engine as ve
from services.validation_engine import (
    NIFTY_100,
    NSE_MIDCAP,
    US_BASKET,
    UNIVERSE_MARKET,
    _backtest_stock,
    _compute_metrics,
    _require_known_universe,
    _resolve_yahoo_symbol,
)


# ── Synthetic, deterministic OHLCV fixture (no network) ───────────────────────

def _synthetic_ohlcv(n=220, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)
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
    """yfinance.Ticker stand-in. Records every symbol it was constructed
    with (for routing assertions) and returns a fixed synthetic OHLCV
    history / empty info — no network access."""

    calls: list = []

    def __init__(self, symbol):
        _FakeTicker.calls.append(symbol)
        self.symbol = symbol

    def history(self, period=None):
        return _synthetic_ohlcv()

    @property
    def info(self):
        return {}


@pytest.fixture(autouse=True)
def _reset_fake_ticker_calls():
    _FakeTicker.calls = []
    yield
    _FakeTicker.calls = []


@pytest.fixture(autouse=True)
def _stub_sec_edgar_as_of(monkeypatch):
    """
    DP-026 remediation (2026-07-21): _backtest_stock() now calls
    sec_edgar_adapter.get_fundamentals_as_of() once per US signal date.
    None of the tests in this file are about SEC EDGAR/fundamentals — they
    predate that remediation entirely — so this autouse stub keeps the
    file's own "No network access anywhere in this file" guarantee true by
    always returning a deterministic "unavailable" result, never reaching
    the real network. Tests that specifically need fundamentals-available
    behavior live in test_dp026_point_in_time_remediation.py instead.
    """
    monkeypatch.setattr(
        ve.sec_edgar_adapter, "get_fundamentals_as_of",
        lambda symbol, as_of: {"available": False, "reason": "stubbed in test"},
    )


class _NoWriteCursor:
    lastrowid = 0


class _NoWriteConn:
    """SQLite connection stand-in that performs no I/O at all — proves a
    mocked run_validation() call writes nothing to disk."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return _NoWriteCursor()

    def executemany(self, *args, **kwargs):
        pass


def _valid_benchmark_df(n=300, seed=99):
    """A benchmark DataFrame that passes _validate_benchmark_acquisition
    for every horizon — sorted, unique, finite, positive Close, enough
    rows. Default mocked yfinance response for tests whose real concern
    is symbol routing / run wiring, not benchmark evidence itself."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n)
    rets = rng.normal(0.0003, 0.008, n)
    close = 100.0 * np.cumprod(1 + rets)
    return pd.DataFrame({"Close": close}, index=dates)


def _mock_validation_io_boundaries(monkeypatch, backtest_stock_fake):
    """
    Mocks every I/O boundary run_validation() touches — yfinance, DB init,
    and the SQLite connection — so a call to run_validation() in a test
    performs zero network calls and zero writes anywhere (no
    validation_results.db, no alpha_engine.db, no Postgres). Also resets
    the module's shared _run_status so a prior failed test can't leave a
    stuck "running" flag behind.
    """
    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()

    monkeypatch.setattr(ve, "_backtest_stock", backtest_stock_fake)
    monkeypatch.setattr(ve, "yf", mock_yf)
    monkeypatch.setattr(ve, "_init_db", lambda: None)
    monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _NoWriteConn())
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)

    with ve._status_lock:
        ve._run_status["running"] = False


# ── _resolve_yahoo_symbol — explicit IN/US resolution ──────────────────────────

@pytest.mark.unit
class TestResolveYahooSymbol:
    @pytest.mark.parametrize("symbol,expected", [
        ("INFY", "INFY.NS"),
        ("TCS", "TCS.NS"),
        ("SBIN", "SBIN.NS"),
        ("M&M", "M&M.NS"),
        ("RELIANCE", "RELIANCE.NS"),
    ])
    def test_india_bare_symbols_get_ns_suffix(self, symbol, expected):
        assert _resolve_yahoo_symbol(symbol, "IN") == expected

    def test_india_already_suffixed_symbol_not_double_suffixed(self):
        assert _resolve_yahoo_symbol("INFY.NS", "IN") == "INFY.NS"
        assert _resolve_yahoo_symbol("TCS.NS", "IN") == "TCS.NS"

    @pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "GOOGL", "BRK.B", "BRK-B"])
    def test_us_symbols_preserved_verbatim_including_punctuation(self, symbol):
        assert _resolve_yahoo_symbol(symbol, "US") == symbol

    def test_us_symbol_never_gets_ns_suffix(self):
        resolved = _resolve_yahoo_symbol("AAPL", "US")
        assert resolved == "AAPL"
        assert not resolved.endswith(".NS")

    def test_invalid_market_fails_closed_with_clear_error(self):
        with pytest.raises(ValueError, match="unsupported market"):
            _resolve_yahoo_symbol("INFY", "XX")

    def test_invalid_market_does_not_silently_default_to_in_or_us(self):
        with pytest.raises(ValueError):
            _resolve_yahoo_symbol("AAPL", "")
        with pytest.raises(ValueError):
            _resolve_yahoo_symbol("AAPL", None)
        with pytest.raises(ValueError):
            _resolve_yahoo_symbol("AAPL", "in")  # case-sensitive — not silently normalized


# ── Universe -> market wiring ───────────────────────────────────────────────────

@pytest.mark.unit
class TestUniverseMarketWiring:
    def test_nifty100_maps_to_in(self):
        assert UNIVERSE_MARKET["nifty100"] == "IN"

    def test_midcap_maps_to_in(self):
        assert UNIVERSE_MARKET["midcap"] == "IN"

    def test_us_maps_to_us(self):
        assert UNIVERSE_MARKET["us"] == "US"

    def test_unrecognized_universe_is_not_a_key_no_silent_fallback(self):
        # run_validation() no longer falls back an unrecognized universe to
        # NIFTY_100/market="IN" — it rejects it outright via
        # _require_known_universe (see TestUnknownUniverseFailsClosed).
        # Strict indexing (as run_validation uses post-guard) must KeyError,
        # not silently resolve — .get() with a default is never used in the
        # production code path.
        assert "bogus-universe" not in UNIVERSE_MARKET
        with pytest.raises(KeyError):
            _ = UNIVERSE_MARKET["bogus-universe"]


# ── Unknown/malformed universe fails closed ─────────────────────────────────────

@pytest.mark.unit
class TestUnknownUniverseFailsClosed:
    """
    run_validation() accepts only "nifty100", "midcap", "us" (case-sensitive,
    exact match — matching the API layer's Literal["nifty100","midcap","us"]
    contract). Any other value must raise before database init, benchmark
    fetch, or executor submission, and must never silently fall back to
    NIFTY_100 or silently default market to "IN".
    """

    def _assert_fails_before_any_io(self, monkeypatch, universe):
        def _boom_init_db():
            raise AssertionError("_init_db was called — universe guard did not run first")

        def _boom_backtest_stock(*args, **kwargs):
            raise AssertionError("_backtest_stock was called — executor submission happened before the guard")

        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = AssertionError(
            "yf.Ticker was called — benchmark fetch happened before the guard"
        )

        monkeypatch.setattr(ve, "_init_db", _boom_init_db)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve, "_backtest_stock", _boom_backtest_stock)

        with ve._status_lock:
            ve._run_status["running"] = False

        with pytest.raises(ValueError, match="unsupported universe"):
            ve.run_validation(horizon="short", universe=universe)

        # The guard fires before _run_status is ever touched, so a rejected
        # call leaves no stuck "running" state behind.
        assert ve._run_status["running"] is False

    def test_unknown_universe_string_rejected(self, monkeypatch):
        self._assert_fails_before_any_io(monkeypatch, "nifty50")  # plausible-looking, not accepted

    def test_empty_universe_string_rejected(self, monkeypatch):
        self._assert_fails_before_any_io(monkeypatch, "")

    def test_whitespace_padded_universe_rejected_no_trimming(self, monkeypatch):
        self._assert_fails_before_any_io(monkeypatch, " nifty100")
        self._assert_fails_before_any_io(monkeypatch, "nifty100 ")

    def test_wrong_case_universe_rejected_no_normalization(self, monkeypatch):
        self._assert_fails_before_any_io(monkeypatch, "Nifty100")
        self._assert_fails_before_any_io(monkeypatch, "NIFTY100")
        self._assert_fails_before_any_io(monkeypatch, "US")

    def test_none_universe_rejected(self, monkeypatch):
        self._assert_fails_before_any_io(monkeypatch, None)

    def test_require_known_universe_rejects_directly(self):
        with pytest.raises(ValueError, match="unsupported universe"):
            _require_known_universe("bogus-universe")

    @pytest.mark.parametrize("universe", ["nifty100", "midcap", "us"])
    def test_all_three_known_universes_pass_the_guard(self, universe):
        _require_known_universe(universe)  # must not raise


# ── Exhaustive configured-universe routing ──────────────────────────────────────

@pytest.mark.unit
class TestExhaustiveUniverseRouting:
    def test_every_nifty100_symbol_resolves_to_ns_under_explicit_in_market(self):
        for sym in NIFTY_100:
            resolved = _resolve_yahoo_symbol(sym, "IN")
            assert resolved.endswith(".NS"), f"{sym} -> {resolved} missing .NS"
            assert resolved == (sym if sym.endswith(".NS") else f"{sym}.NS")

    def test_every_midcap_symbol_resolves_to_ns_under_explicit_in_market(self):
        for sym in NSE_MIDCAP:
            resolved = _resolve_yahoo_symbol(sym, "IN")
            assert resolved.endswith(".NS"), f"{sym} -> {resolved} missing .NS"

    def test_every_us_basket_symbol_resolves_verbatim_under_explicit_us_market(self):
        for sym in US_BASKET:
            resolved = _resolve_yahoo_symbol(sym, "US")
            assert resolved == sym
            assert not resolved.endswith(".NS")

    def test_old_ticker_shape_heuristic_would_have_misrouted_a_confirmed_subset(self):
        """
        Regression proof for the exact defect this phase fixes. The OLD
        heuristic is recreated HERE only (it no longer exists in production
        code) to demonstrate: (a) it did misroute a material, confirmed
        subset of short NSE symbols, and (b) the new explicit contract
        resolves every one of them correctly regardless.
        """
        def _old_heuristic_is_us(symbol):
            return not symbol.endswith(".NS") and "." not in symbol and len(symbol) <= 5

        confirmed_misrouted = [s for s in NIFTY_100 if _old_heuristic_is_us(s)]
        assert "INFY" in confirmed_misrouted
        assert "TCS" in confirmed_misrouted
        assert "SBIN" in confirmed_misrouted
        assert len(confirmed_misrouted) >= 20  # material fraction, not an edge case

        for sym in confirmed_misrouted:
            # Old heuristic would have requested a bare (non-.NS) symbol.
            # New explicit-market contract always appends .NS under "IN".
            assert _resolve_yahoo_symbol(sym, "IN") == f"{sym}.NS"


# ── Regression: symbols cannot cross markets ───────────────────────────────────

@pytest.mark.unit
class TestRegressionCrossMarketMisrouting:
    def test_infy_in_nifty100_cannot_resolve_to_bare_ticker_or_adr(self):
        resolved = _resolve_yahoo_symbol("INFY", "IN")
        assert resolved == "INFY.NS"
        assert resolved != "INFY"  # bare "INFY" on Yahoo is Infosys's NYSE ADR, not the NSE line

    def test_tcs_and_sbin_cannot_be_treated_as_us_tickers(self):
        assert _resolve_yahoo_symbol("TCS", "IN") == "TCS.NS"
        assert _resolve_yahoo_symbol("SBIN", "IN") == "SBIN.NS"

    def test_us_symbols_with_punctuation_cannot_be_treated_as_indian(self):
        # No dotted/dashed symbol currently exists in US_BASKET (confirmed by
        # inspection) — these synthetic class-share tickers stand in for that
        # shape to prove market="US" never appends .NS regardless of the
        # symbol's own punctuation.
        for synthetic in ("BRK.B", "BRK-B", "BF.B"):
            resolved = _resolve_yahoo_symbol(synthetic, "US")
            assert resolved == synthetic
            assert not resolved.endswith(".NS")


# ── _backtest_stock routing (mocked yfinance, no network) ─────────────────────

@pytest.mark.unit
class TestBacktestStockRouting:
    def test_india_symbol_requests_ns_suffixed_yahoo_symbol(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        _backtest_stock("INFY", "short", None, "IN", universe="nifty100")
        assert "INFY.NS" in _FakeTicker.calls
        assert "INFY" not in _FakeTicker.calls

    def test_us_symbol_requests_bare_yahoo_symbol(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        _backtest_stock("AAPL", "short", None, "US", universe="us")
        assert "AAPL" in _FakeTicker.calls
        assert "AAPL.NS" not in _FakeTicker.calls

    def test_already_suffixed_india_symbol_not_double_suffixed(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        _backtest_stock("INFY.NS", "short", None, "IN", universe="nifty100")
        assert "INFY.NS" in _FakeTicker.calls
        assert "INFY.NS.NS" not in _FakeTicker.calls

    def test_invalid_market_fails_closed_before_any_yfinance_call(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        with pytest.raises(ValueError):
            _backtest_stock("INFY", "short", None, "XX", universe="nifty100")
        assert _FakeTicker.calls == []  # never reached yf.Ticker at all


# ── Preserve validation behavior: entry/exit, alpha, benchmark alignment ──────

@pytest.mark.unit
class TestBacktestStockSignalComputationUnchanged:
    def test_no_benchmark_produces_no_signals_not_a_fabricated_flat_comparison(self, monkeypatch):
        """Benchmark evidence integrity closure: a None benchmark used to
        make every signal's benchmark forward return silently default to
        0.0 (alpha == raw forward return, as if the benchmark were
        genuinely flat) — exactly the fabricated-evidence defect this
        phase closes. _backtest_stock now excludes every signal date
        instead, since no genuine benchmark/regime evidence exists at any
        of them (see this function's own _exclusions contract)."""
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        exclusions = []
        signals = _backtest_stock("AAPL", "short", None, "US", universe="us", _exclusions=exclusions)
        assert signals == []
        assert len(exclusions) > 0

    def test_benchmark_alignment_and_alpha_with_benchmark_supplied(self, monkeypatch):
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        stock_df = _synthetic_ohlcv(seed=42)  # matches _FakeTicker.history()'s default
        bench_df = _synthetic_ohlcv(seed=7)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        assert len(signals) > 0

        fwd_days = ve.HORIZON_DAYS["short"]
        i = 200
        # Same date index on both sides (both _synthetic_ohlcv calls share
        # the same pd.bdate_range regardless of seed) — ffill-only
        # alignment (no bfill, matching _align_benchmark_close's contract)
        # produces identical values here since there is no leading gap.
        bench_close = bench_df["Close"].reindex(stock_df.index).ffill()
        entry = float(stock_df["Close"].iloc[i])
        exit_ = float(stock_df["Close"].iloc[i + fwd_days])
        fwd_ret = (exit_ - entry) / entry * 100
        b_entry = float(bench_close.iloc[i])
        b_exit = float(bench_close.iloc[i + fwd_days])
        bench_fwd_ret = (b_exit - b_entry) / b_entry * 100
        expected_alpha = round(fwd_ret - bench_fwd_ret, 3)

        first = signals[0]
        assert first["fwd_return_pct"] == pytest.approx(round(fwd_ret, 3))
        assert first["nifty_fwd_ret_pct"] == pytest.approx(round(bench_fwd_ret, 3))
        assert first["alpha_pct"] == pytest.approx(expected_alpha)

    def test_no_look_ahead_indicator_window_matches_signal_index(self, monkeypatch):
        """
        Pre-existing look-ahead-bias guarantee (untouched by this phase):
        compute_indicators() must only ever see df.iloc[:i+1] at signal date
        i, never later rows. Spies on the real compute_indicators to record
        the window length passed at each call. Uses a valid benchmark (not
        None) so signals are actually produced — this test's own concern
        is indicator windowing, not benchmark evidence.
        """
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        seen_lengths = []
        real_compute_indicators = ve.compute_indicators

        def _spy(window):
            seen_lengths.append(len(window))
            return real_compute_indicators(window)

        monkeypatch.setattr(ve, "compute_indicators", _spy)
        bench_df = _synthetic_ohlcv(seed=7)
        _backtest_stock("AAPL", "short", bench_df, "US", universe="us")

        fwd_days = ve.HORIZON_DAYS["short"]
        step = ve.HORIZON_STEP["short"]
        n = len(_synthetic_ohlcv())
        expected_indices = list(range(200, n - fwd_days, step))
        expected_lengths = [i + 1 for i in expected_indices]
        assert seen_lengths == expected_lengths


# ── _compute_metrics: benchmark_return_pct rename is a pure rename ────────────

@pytest.mark.unit
class TestComputeMetricsBenchmarkRename:
    def test_benchmark_return_pct_flows_through_unchanged(self):
        signals = [
            {
                "predicted": "BUY", "correct": 1, "fwd_return_pct": 5.0, "alpha_pct": 3.0,
                "composite_score": 70, "tech_score": 70, "rs_score": 70, "obv_score": 70,
                "mfi_score": 70, "signal_date": "2020-01-01",
            }
            for _ in range(40)
        ]
        metrics = _compute_metrics(signals, benchmark_return_pct=2.0, horizon="short")
        assert metrics["avg_return_benchmark_pct"] == 2.0
        assert metrics["buy_outperformance_pct"] == pytest.approx(round(5.0 - 2.0, 2))


# ── run_validation wiring: fully mocked, no network, no DB writes ─────────────

@pytest.mark.unit
class TestRunValidationWiringFullyMocked:
    """
    Proves run_validation passes the correct explicit `market` to every
    _backtest_stock call for each universe. Every I/O boundary is mocked —
    yfinance, _init_db, and the SQLite connection — so this test performs
    zero network calls and zero writes to validation_results.db,
    alpha_engine.db, or Postgres.
    """

    class _NullCursor:
        lastrowid = 0

    class _NullConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args, **kwargs):
            return TestRunValidationWiringFullyMocked._NullCursor()

        def executemany(self, *args, **kwargs):
            pass

    def _run_fully_mocked(self, monkeypatch, universe):
        captured_markets = []

        def _fake_backtest_stock(symbol, horizon, benchmark_df, market, universe=None, **kwargs):
            captured_markets.append(market)
            return []

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()

        monkeypatch.setattr(ve, "_backtest_stock", _fake_backtest_stock)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: self._NullConn())
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)

        with ve._status_lock:
            ve._run_status["running"] = False

        ve.run_validation(horizon="short", universe=universe, max_workers=2)
        return captured_markets

    def test_nifty100_passes_market_in_to_every_backtest_call(self, monkeypatch):
        markets = self._run_fully_mocked(monkeypatch, "nifty100")
        assert markets
        assert set(markets) == {"IN"}
        assert len(markets) == len(NIFTY_100)

    def test_midcap_passes_market_in_to_every_backtest_call(self, monkeypatch):
        markets = self._run_fully_mocked(monkeypatch, "midcap")
        assert markets
        assert set(markets) == {"IN"}
        assert len(markets) == len(NSE_MIDCAP)

    def test_us_passes_market_us_to_every_backtest_call(self, monkeypatch):
        markets = self._run_fully_mocked(monkeypatch, "us")
        assert markets
        assert set(markets) == {"US"}
        assert len(markets) == len(US_BASKET)


# ── Effective sample-size truthfulness ─────────────────────────────────────────

def _fake_signal(symbol, horizon, i):
    return {
        "symbol": symbol, "horizon": horizon, "signal_date": f"2020-01-{(i % 28) + 1:02d}",
        "composite_score": 70, "tech_score": 70, "rs_score": 70, "obv_score": 70,
        "mfi_score": 70, "predicted": "BUY", "fwd_return_pct": 1.0,
        "nifty_fwd_ret_pct": 0.0, "alpha_pct": 1.0, "actual_direction": "UP", "correct": 1,
    }


@pytest.mark.unit
class TestEffectiveSampleSizeTruthful:
    """
    n_stocks_requested must equal the configured universe size.
    n_stocks_with_signals must count DISTINCT symbols that returned at
    least one usable signal — never len(all_signals) — and can never
    exceed n_stocks_requested. n_stocks_tested is kept unchanged, purely
    for backward compatibility with any existing consumer of that key.
    """

    def _run_with_signal_counts(self, monkeypatch, universe, signal_counts):
        """signal_counts: {symbol: n_signals}. Any universe symbol not
        present returns zero signals (a real, uncounted, failed/insufficient
        fetch stand-in)."""

        def _fake_backtest_stock(symbol, horizon, benchmark_df, market, universe=None, **kwargs):
            n = signal_counts.get(symbol, 0)
            return [_fake_signal(symbol, horizon, i) for i in range(n)]

        _mock_validation_io_boundaries(monkeypatch, _fake_backtest_stock)
        return ve.run_validation(horizon="short", universe=universe, max_workers=2)

    def test_n_stocks_requested_equals_configured_universe_size(self, monkeypatch):
        metrics = self._run_with_signal_counts(monkeypatch, "nifty100", {})
        assert metrics["n_stocks_requested"] == len(NIFTY_100)
        assert metrics["n_stocks_tested"] == len(NIFTY_100)  # unchanged — backward compat only

    def test_symbol_with_multiple_signals_is_counted_once(self, monkeypatch):
        first_symbol, second_symbol = NIFTY_100[0], NIFTY_100[1]
        metrics = self._run_with_signal_counts(
            monkeypatch, "nifty100",
            {first_symbol: 5, second_symbol: 1},  # 6 signal rows, 2 distinct resolved symbols
        )
        assert metrics["total_signals"] == 6  # sanity — the rows really were all counted
        assert metrics["n_stocks_with_signals"] == 2
        assert metrics["n_stocks_with_signals"] != metrics["total_signals"]

    def test_n_stocks_with_signals_never_exceeds_n_stocks_requested(self, monkeypatch):
        every_symbol_signals = {sym: 3 for sym in NIFTY_100}
        metrics = self._run_with_signal_counts(monkeypatch, "nifty100", every_symbol_signals)
        assert metrics["n_stocks_with_signals"] == len(NIFTY_100)
        assert metrics["n_stocks_with_signals"] <= metrics["n_stocks_requested"]

    def test_zero_signal_symbols_excluded_from_n_stocks_with_signals(self, monkeypatch):
        metrics = self._run_with_signal_counts(monkeypatch, "midcap", {})
        assert metrics["n_stocks_with_signals"] == 0
        assert metrics["n_stocks_requested"] == len(NSE_MIDCAP)
