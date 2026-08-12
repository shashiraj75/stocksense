"""
V-PS2 — non-finite validation forward-return prevention and JSON safety.

Confirmed defect (root-caused via direct production log/query evidence,
not speculation): `_backtest_stock()`'s per-window loop guarded only
`entry == 0` before computing `fwd_ret = (exit_ - entry) / entry * 100` —
a NaN `Close` price (a genuine market-data gap) is never equal to 0, so it
slipped through, producing `fwd_ret = NaN`, which was then:
  1. persisted into `val_signals.fwd_return_pct` as a stored NaN;
  2. used to compute `alpha = fwd_ret - benchmark_fwd_ret` (also NaN);
  3. classified via `correct = alpha > 0` — which Python evaluates to
     `False` for any NaN comparison, silently recording a FALSE MISS for
     a signal whose true outcome was never actually measurable;
  4. propagated through `AVG(fwd_return_pct)` in get_per_stock_results(),
     poisoning that symbol's (and via list-wide JSON serialization, that
     entire universe/horizon's) response;
  5. hit Starlette's `JSONResponse` (which renders with allow_nan=False),
     raising `ValueError: Out of range float values are not JSON
     compliant: nan` and turning the whole per-stock endpoint unavailable.

This file proves layers 1 and 3 of the three-layer fix. Layer 2 (historical
SQL aggregation honesty) is proven in test_validation_per_stock_hit_rate.py
per the established per-stock test-file convention.
"""
import math

import numpy as np
import pandas as pd
import pytest

import services.validation_engine as ve
from services.validation_engine import _backtest_stock


def _synthetic_stock_ohlcv(n=260, seed=42, start="2019-01-01"):
    """Matches test_validation_benchmark_evidence.py's own copy exactly —
    duplicated here so this file stays self-contained per that file's own
    documented convention."""
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
    """A benchmark series spanning the full stock range — genuine evidence
    at every date, so the ONLY thing under test is the entry/exit price
    guard, not benchmark availability (a separate, already-closed concern
    — see test_validation_benchmark_evidence.py)."""
    return pd.DataFrame(
        {"Close": np.linspace(100, 140, len(stock_df.index))}, index=stock_df.index
    )


# ── A. Upstream price/outcome guard ─────────────────────────────────────────

@pytest.mark.regression
class TestBacktestStockRejectsNonFiniteClosePrices:
    def test_all_finite_prices_produce_signals_as_before(self, monkeypatch):
        """Control: finite entry/exit prices throughout must be unaffected
        — same signal count, same fields, nothing regresses."""
        stock_df = _synthetic_stock_ohlcv()

        class _Ticker(_FakeTicker):
            def history(self, period=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        bench_df = _bench_df_for(stock_df)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        assert len(signals) > 0
        for s in signals:
            assert math.isfinite(s["fwd_return_pct"])

    def _run_with_corrupted_close(self, monkeypatch, corrupt_value, corrupt_at="entry"):
        stock_df = _synthetic_stock_ohlcv()
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        bench_df = _bench_df_for(stock_df)
        # short horizon fwd_days=5, MIN_WARMUP=200 — position 220 is deep
        # inside the loop's iterated range for both an entry (i=220) and,
        # for the exit case, a date whose entry (i=215) maps to this exit
        # (i+fwd_days=220).
        target_pos = 220
        stock_df = stock_df.copy()
        if corrupt_at == "entry":
            stock_df.iloc[target_pos, stock_df.columns.get_loc("Close")] = corrupt_value
        else:
            stock_df.iloc[target_pos, stock_df.columns.get_loc("Close")] = corrupt_value
        return stock_df, bench_df, target_pos

    @pytest.mark.parametrize("corrupt_value", [float("nan"), float("inf"), float("-inf")])
    def test_nonfinite_entry_price_produces_no_signal_for_that_window(self, monkeypatch, corrupt_value):
        """Entry invalidity is architecturally distinct from exit
        invalidity (V-PS2A): the entry-date row directly feeds
        _score_at()'s technical sub-scores, so an invalid entry poisons
        the PREDICTION itself, not just its outcome — no signal can exist
        at all for this window, unlike a missing exit (see below)."""
        stock_df, bench_df, target_pos = self._run_with_corrupted_close(monkeypatch, corrupt_value, "entry")

        class _Ticker(_FakeTicker):
            def history(self, period=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        bad_date = stock_df.index[target_pos]
        for s in signals:
            assert pd.Timestamp(s["signal_date"]) != bad_date, (
                "a window whose ENTRY price is non-finite must never produce a signal"
            )
            if s["fwd_return_pct"] is not None:
                assert math.isfinite(s["fwd_return_pct"])

    @pytest.mark.parametrize("corrupt_value", [float("nan"), float("inf"), float("-inf")])
    def test_nonfinite_exit_price_retains_signal_as_unevaluated(self, monkeypatch, corrupt_value):
        """V-PS2A — the prediction already existed at the (valid) entry
        date; a non-finite EXIT price means only the OUTCOME could never
        be measured. The signal must be retained (counted in
        buy_signal_count) with honest null outcome fields, never
        skipped and never fabricated into a loss."""
        stock_df = _synthetic_stock_ohlcv()
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)
        bench_df = _bench_df_for(stock_df)
        # fwd_days for "short" = 5; corrupt the EXIT of the window whose
        # entry is at position 215 (exit at 215+5=220).
        entry_pos, exit_pos = 215, 220
        stock_df = stock_df.copy()
        stock_df.iloc[exit_pos, stock_df.columns.get_loc("Close")] = corrupt_value

        class _Ticker(_FakeTicker):
            def history(self, period=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        entry_date = stock_df.index[entry_pos]
        matching = [s for s in signals if pd.Timestamp(s["signal_date"]) == entry_date]
        assert len(matching) == 1, "a valid entry with an unmeasurable exit must still produce a retained signal"
        s = matching[0]
        assert s["predicted"] in ("BUY", "SELL", "HOLD")  # the prediction itself survives
        assert s["fwd_return_pct"] is None
        assert s["correct"] is None
        assert s["alpha_pct"] is None
        assert s["actual_direction"] is None

    def test_no_persisted_signal_ever_carries_a_nonfinite_forward_return(self, monkeypatch):
        """Direct proof of the persistence contract: across an entire run
        with SEVERAL corrupted EXIT dates scattered through the window,
        not one surviving signal's fwd_return_pct/alpha_pct/
        nifty_fwd_ret_pct may be non-finite (None is fine, NaN/Inf is
        not) — this is what get_per_stock_results()'s SQL and
        _safe_json() both ultimately depend on upstream never happening.
        Corrupting EXIT (not entry) positions specifically, since those
        signals are now retained rather than skipped."""
        stock_df = _synthetic_stock_ohlcv().copy()
        close_col = stock_df.columns.get_loc("Close")
        # fwd_days=5 for "short" — corrupt exit positions (entry+5) for
        # entries at 200, 210, 220, 230, 240.
        for entry_pos in (200, 210, 220, 230, 240):
            stock_df.iloc[entry_pos + 5, close_col] = float("nan")

        class _Ticker(_FakeTicker):
            def history(self, period=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        bench_df = _bench_df_for(stock_df)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        assert len(signals) > 0
        retained_unevaluated = 0
        for s in signals:
            # Either a genuine finite outcome, or an honest None — never
            # a non-finite value reaching persistence.
            if s["fwd_return_pct"] is None:
                retained_unevaluated += 1
                assert s["correct"] is None
                assert s["alpha_pct"] is None
                assert s["actual_direction"] is None
            else:
                assert math.isfinite(s["fwd_return_pct"])
                assert math.isfinite(s["alpha_pct"])
                # The core fabricated-miss defect: a genuinely evaluated
                # signal's `correct` must be a real 0/1, never derived
                # from a non-finite alpha.
                assert s["correct"] in (0, 1)
            assert math.isfinite(s["nifty_fwd_ret_pct"])
        # At least one of the 5 corrupted-exit windows must have survived
        # as a retained, unevaluated signal (proves the new contract is
        # actually exercised, not just vacuously true).
        assert retained_unevaluated > 0

    def test_exact_negative_100_percent_total_loss_with_literal_zero_exit(self, monkeypatch):
        """V-PS2A — the boundary must be tested with a LITERAL exit price
        of exactly 0.0 (a genuine, finite total loss), not an approximation
        like 1e-9. entry==0 is the only rejected division case; exit==0 is
        finite and fully valid, and must yield fwd_return_pct EXACTLY
        -100.0, not merely close to it."""
        stock_df = _synthetic_stock_ohlcv().copy()
        close_col = stock_df.columns.get_loc("Close")
        entry_pos, exit_pos = 215, 220
        stock_df.iloc[entry_pos, close_col] = 50.0
        stock_df.iloc[exit_pos, close_col] = 0.0  # exact, literal total loss

        class _Ticker(_FakeTicker):
            def history(self, period=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        bench_df = _bench_df_for(stock_df)
        signals = _backtest_stock("AAPL", "short", bench_df, "US", universe="us")
        entry_date = stock_df.index[entry_pos]
        matching = [s for s in signals if pd.Timestamp(s["signal_date"]) == entry_date]
        assert len(matching) == 1, "a finite exact total loss must still survive as a signal"
        assert matching[0]["fwd_return_pct"] == -100.0
        # A genuine total loss is a fully evaluated outcome, not an
        # unmeasurable one — correct must be a real 0/1, never None.
        assert matching[0]["correct"] in (0, 1)


# ── B(bench). Non-finite benchmark return with a finite stock return ───────

@pytest.mark.regression
class TestNonfiniteBenchmarkWithFiniteStockReturn:
    """V-PS2A architectural finding, documented here rather than silently
    implemented: this codebase already has a deliberate, separately
    hardened governance decision — the Benchmark Evidence Integrity
    Closure (see test_validation_benchmark_evidence.py) — that a signal
    must not be published AT ALL unless benchmark evidence is genuinely
    available, specifically BECAUSE _score_at() would otherwise compute a
    fabricated neutral (50.0) relative-strength sub-score when
    benchmark_close is unavailable, silently misrepresenting the
    prediction itself, not just its correctness. That closure's own tests
    (test_regime_unavailable_excludes_the_signal_never_defaults_to_neutral,
    test_no_signal_ever_carries_a_fabricated_zero_when_excluded) exist
    specifically to prevent reintroducing this.

    V-PS2A's Stage 3.D asks for "finite stock return, non-finite
    benchmark return" to be RETAINED with fwd_return_pct counted but
    correct=None. That contract conflicts directly with the existing
    closure, because in the current architecture the benchmark gate
    (b_entry/b_exit finiteness) executes BEFORE `predicted` is even
    computed — a signal can never reach the point of having a
    "predicted" label without the benchmark already being valid. This is
    proven here, not assumed: the benchmark gate already unconditionally
    rejects the window before scoring, so there is no "finite stock
    return with non-finite benchmark AND a predicted label" case
    reachable in the current code — implementing Stage 3.D would require
    reordering scoring ahead of the benchmark gate, directly reversing
    the prior closure. That reordering is explicitly NOT done under this
    phase; see this file's own final report for the recommendation to
    scope it as a separate, deliberately-reviewed phase."""

    def test_benchmark_gate_still_rejects_before_any_predicted_label_exists(self, monkeypatch):
        """Direct proof: a window with a fully finite stock entry/exit but
        an unavailable benchmark produces NO signal at all (unchanged from
        the pre-existing Benchmark Evidence Integrity Closure) — there is
        no in-between state of "predicted but uncorrected" reachable here."""
        stock_df = _synthetic_stock_ohlcv()
        monkeypatch.setattr(ve.yf, "Ticker", _FakeTicker)

        class _Ticker(_FakeTicker):
            def history(self, period=None):
                return stock_df

        monkeypatch.setattr(ve.yf, "Ticker", _Ticker)
        # benchmark_df=None => benchmark_ok is False for every window —
        # the existing, unchanged closure's own documented case.
        signals = _backtest_stock("AAPL", "short", None, "US", universe="us")
        assert signals == []


# ── C. Recursive JSON safety (_safe_json) ───────────────────────────────────

@pytest.mark.regression
class TestSafeJsonNormalizesNonFiniteFloats:
    @pytest.fixture(autouse=True)
    def _import_safe_json(self):
        from api.routers.validation import _safe_json
        self.safe_json = staticmethod(_safe_json).__func__
        self.safe_json = _safe_json

    def test_builtin_nan_becomes_none(self):
        assert self.safe_json(float("nan")) is None

    def test_builtin_positive_infinity_becomes_none(self):
        assert self.safe_json(float("inf")) is None

    def test_builtin_negative_infinity_becomes_none(self):
        assert self.safe_json(float("-inf")) is None

    def test_numpy_nan_becomes_none(self):
        assert self.safe_json(np.float64("nan")) is None

    def test_numpy_infinity_becomes_none(self):
        assert self.safe_json(np.float64("inf")) is None

    def test_finite_float_unchanged(self):
        assert self.safe_json(3.14) == pytest.approx(3.14)

    def test_finite_zero_and_negative_100_not_converted(self):
        """Non-finite normalization must never be confused with "falsy" or
        "extreme" — a genuine 0.0 or -100.0 is a real, valid value."""
        assert self.safe_json(0.0) == 0.0
        assert self.safe_json(-100.0) == -100.0

    def test_integers_strings_none_bool_unchanged(self):
        assert self.safe_json(5) == 5
        assert self.safe_json("hello") == "hello"
        assert self.safe_json(None) is None
        assert self.safe_json(True) is True

    def test_nested_dict_with_nan_normalizes_only_the_nan(self):
        payload = {"a": 1.5, "b": float("nan"), "c": {"d": float("inf")}}
        result = self.safe_json(payload)
        assert result == {"a": 1.5, "b": None, "c": {"d": None}}

    def test_list_and_tuple_with_mixed_values(self):
        result = self.safe_json([1, float("nan"), "x", (float("inf"), 2.0)])
        assert result == [1, None, "x", [None, 2.0]]

    def test_result_survives_real_json_response_render(self):
        """Prove the actual API boundary, not just the helper in
        isolation — this is what the original production bug bypassed."""
        import json
        from api.routers.validation import _json_response

        payload = {
            "available": True,
            "stocks": [
                {"symbol": "AAPL", "avg_fwd_return_pct": float("nan"), "buy_hits": 3},
                {"symbol": "MSFT", "avg_fwd_return_pct": 1.23, "buy_hits": 5},
            ],
        }
        response = _json_response(payload)
        decoded = json.loads(bytes(response.body))
        assert decoded["stocks"][0]["avg_fwd_return_pct"] is None
        assert decoded["stocks"][1]["avg_fwd_return_pct"] == 1.23
