"""
DP-026 — walk-forward validation's fund_score is a single present-day
fundamentals snapshot reused unchanged across every historical signal date
for a symbol (look-ahead bias on the fundamentals component of
composite_score). Investigated 2026-07-21: neither of StockSense360's
currently integrated sources (yfinance, screener.in, BSE) nor any data it
internally retains (stock_fundamentals_cache — overwrite-only, no history
retained) can supply a point-in-time fundamentals vintage for a historical
signal date. This is a precise, repository-scoped finding, not a market-wide
claim that no provider anywhere offers point-in-time historical
fundamentals — no vendor survey was performed. Governed by DPD-009 (DECIDED
— DISCLOSURE HOLD), which already authorizes disclosing validation
methodology limitations rather than silently correcting or fabricating
them.

This is DISCLOSURE, not remediation: `fund_score`'s underlying calculation
is completely unchanged by this fix. This file proves the DP-026
containment: `_compute_metrics()` now attaches an honest, machine-readable
`data_limitations` block to every non-empty validation summary, and that
this is purely additive — no composite score, factor IC, weight, threshold,
or BUY/SELL/HOLD classification is touched by it, in either the pure
aggregation function or the end-to-end `run_validation()` persistence path.
It also proves the disclosure itself does not overclaim: it does not assert
that a fund_score of 50.0 means "unavailable" (see
TestFiftyAmbiguityDisclosure below — that distinction is explicitly
unresolved and owned by DP-031, not this finding).

No network access anywhere in this file — every yfinance/DB boundary is
monkeypatched, following the existing pattern in
test_validation_engine_market_routing.py.
"""
import copy
import json
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from services import validation_engine as ve
from services.validation_engine import _compute_metrics


# ── Synthetic signal fixtures (no network, no DB) ──────────────────────────

def _signal(symbol="AAPL", horizon="medium", date="2024-01-02",
            composite=72.0, predicted="BUY", correct=True,
            fwd_return_pct=3.5, alpha_pct=1.2, confidence=60,
            tech=65.0, rs=70.0, obv=68.0, mfi=61.0):
    return {
        "symbol": symbol,
        "horizon": horizon,
        "signal_date": date,
        "composite_score": composite,
        "tech_score": tech,
        "rs_score": rs,
        "obv_score": obv,
        "mfi_score": mfi,
        "predicted": predicted,
        "confidence": confidence,
        "fwd_return_pct": fwd_return_pct,
        "nifty_fwd_ret_pct": fwd_return_pct - alpha_pct,
        "alpha_pct": alpha_pct,
        "actual_direction": "UP" if fwd_return_pct > 0 else "DOWN",
        "correct": correct,
    }


def _synthetic_signals(n=40, seed=7):
    rng = np.random.default_rng(seed)
    signals = []
    for i in range(n):
        composite = float(rng.uniform(55, 90))
        predicted = "BUY" if composite >= 70 else ("SELL" if composite <= 35 else "HOLD")
        fwd = float(rng.normal(2.0, 5.0))
        alpha = fwd - float(rng.normal(0.5, 2.0))
        correct = (alpha > 0) if predicted == "BUY" else ((alpha < 0) if predicted == "SELL" else abs(alpha) <= 5)
        signals.append(_signal(
            symbol=f"SYM{i % 5}",
            date=f"2023-{(i % 12) + 1:02d}-01",
            composite=composite,
            predicted=predicted,
            correct=bool(correct),
            fwd_return_pct=fwd,
            alpha_pct=alpha,
            confidence=int(rng.uniform(0, 100)),
            tech=float(rng.uniform(30, 90)),
            rs=float(rng.uniform(30, 90)),
            obv=float(rng.uniform(30, 90)),
            mfi=float(rng.uniform(30, 90)),
        ))
    return signals


@pytest.mark.unit
class TestDataLimitationsDisclosure:
    def test_present_on_nonempty_summary(self):
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        assert "data_limitations" in metrics

    def test_fundamentals_flagged_not_point_in_time(self):
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        assert metrics["data_limitations"]["fundamentals_point_in_time"] is False

    def test_dp_id_is_dp_026(self):
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        assert metrics["data_limitations"]["dp_id"] == "DP-026"

    def test_affected_pct_is_full_coverage_regardless_of_signal_mix(self):
        # fund_score contaminates every signal identically (it's blended into
        # every composite_score before BUY/SELL/HOLD classification even
        # happens) — this must be 100% for any non-empty, mixed-signal run,
        # not scaled by how many signals happened to be BUY vs SELL vs HOLD.
        metrics = _compute_metrics(_synthetic_signals(n=91, seed=3), benchmark_return_pct=-0.5, horizon="long")
        assert metrics["data_limitations"]["fundamentals_affected_signals_pct"] == 100.0

    def test_reason_is_a_nonempty_explanatory_string(self):
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="short")
        reason = metrics["data_limitations"]["reason"]
        assert isinstance(reason, str) and len(reason) > 40

    def test_absent_when_no_signals(self):
        # Empty-signals early return ({}) must remain untouched by this change.
        assert _compute_metrics([], benchmark_return_pct=1.0, horizon="medium") == {}

    def test_present_for_every_supported_horizon(self):
        for horizon in ("short", "medium", "long"):
            metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=0.8, horizon=horizon)
            assert metrics["data_limitations"]["dp_id"] == "DP-026"

    def test_deterministic_across_repeated_calls(self):
        signals = _synthetic_signals()
        first = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium")["data_limitations"]
        second = _compute_metrics(copy.deepcopy(signals), benchmark_return_pct=1.0, horizon="medium")["data_limitations"]
        assert first == second

    def test_json_serializable(self):
        # This dict flows straight into json.dumps(clean_metrics) in
        # run_validation() and must round-trip cleanly.
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        round_tripped = json.loads(json.dumps(metrics["data_limitations"]))
        assert round_tripped == metrics["data_limitations"]

    def test_status_reflects_disclosure_not_remediation(self):
        # A PR reviewer scanning the JSON alone must not be able to
        # conclude the underlying look-ahead bias was fixed.
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        status = metrics["data_limitations"]["status"].lower()
        assert "disclos" in status
        assert "remediat" in status
        assert "not remediated" in status or "not resolved" in status


@pytest.mark.unit
class TestScopeOfDataAvailabilityClaim:
    """DP-026's finding must be scoped to what StockSense360 currently
    integrates/retains — never phrased as a market-wide claim that no
    provider anywhere offers point-in-time historical fundamentals (no
    vendor survey was performed to support that broader claim)."""

    def test_reason_names_the_currently_integrated_sources(self):
        reason = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")["data_limitations"]["reason"]
        for source in ("yfinance", "screener.in", "BSE", "stock_fundamentals_cache"):
            assert source in reason

    def test_reason_does_not_claim_no_provider_anywhere_has_this_data(self):
        reason = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")["data_limitations"]["reason"]
        # Must explicitly disclaim the market-wide reading, not merely omit it.
        assert "does not establish that no such data exists" in reason
        assert "out of scope" in reason


@pytest.mark.unit
class TestFiftyAmbiguityDisclosure:
    """fund_score == 50.0 can mean (a) the .info fetch failed, (b) .info
    succeeded but pe/roe/revenueGrowth were individually absent, or (c)
    .info succeeded and all three genuinely landed in the no-adjustment
    band. These are not currently distinguishable. The disclosure must say
    so plainly and must not claim '100% affected' means '100% unavailable'."""

    def test_availability_vs_neutral_flag_present_and_false(self):
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        assert metrics["data_limitations"]["fundamentals_availability_vs_neutral_distinguishable"] is False

    def test_reason_explicitly_disclaims_100pct_unavailable_reading(self):
        reason = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")["data_limitations"]["reason"]
        assert "not currently distinguishable" in reason
        assert "100% unavailable" in reason

    def test_reason_references_dp031_as_owner(self):
        reason = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")["data_limitations"]["reason"]
        assert "DP-031" in reason


@pytest.mark.unit
class TestDataLimitationsIsPurelyAdditive:
    """Proves DP-026's disclosure changes nothing else in the summary —
    scoring, thresholds, and every other existing metric are byte-identical
    to what they would be without this change."""

    EXPECTED_PRE_EXISTING_KEYS = {
        "total_signals", "buy_signals", "sell_signals", "hold_signals",
        "buy_hit_rate_pct", "sell_hit_rate_pct", "overall_accuracy_pct",
        "avg_return_on_buy_pct", "avg_alpha_on_buy_pct", "avg_return_on_sell_pct",
        "avg_return_benchmark_pct", "buy_outperformance_pct", "sharpe_on_buys",
        "sharpe_on_alphas", "profitable_buy_pct", "beat_benchmark_pct",
        "max_consecutive_wrong", "max_consecutive_right", "max_drawdown_pct",
        "score_buckets", "confidence_buckets", "sell_confidence_buckets",
        "factor_ic",
    }

    def test_all_pre_existing_keys_still_present(self):
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        assert self.EXPECTED_PRE_EXISTING_KEYS.issubset(metrics.keys())

    def test_only_disclosure_keys_added(self):
        """DP-026 added data_limitations; the later benchmark-evidence-
        integrity fix additively disclosed signals_excluded_benchmark
        (a real, caller-supplied exclusion count — see _compute_metrics'
        own docstring); V-VAL1 additively disclosed the evaluated-BUY-
        cohort fields (buy_signal_count/evaluated_buy_count/buy_hits/
        buy_return_count — see get_per_stock_results' own docstring for
        the same cohort contract at the per-stock level) so the frontend's
        Wilson interval has an exact, non-reconstructed n/hits to read
        instead of inferring them from a rounded percentage. All of these
        are pure disclosure — no existing key's value or type changed."""
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        assert set(metrics.keys()) - self.EXPECTED_PRE_EXISTING_KEYS == {
            "data_limitations", "signals_excluded_benchmark",
            "buy_signal_count", "evaluated_buy_count", "buy_hits", "buy_return_count",
        }

    def test_factor_ic_values_unaffected(self):
        signals = _synthetic_signals(n=60, seed=11)
        m1 = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium")
        m2 = _compute_metrics(copy.deepcopy(signals), benchmark_return_pct=1.0, horizon="medium")
        assert m1["factor_ic"] == m2["factor_ic"]

    def test_hit_rates_and_buckets_unaffected_by_repeated_computation(self):
        signals = _synthetic_signals(n=60, seed=11)
        m1 = _compute_metrics(signals, benchmark_return_pct=1.0, horizon="medium")
        m2 = _compute_metrics(copy.deepcopy(signals), benchmark_return_pct=1.0, horizon="medium")
        assert m1["buy_hit_rate_pct"] == m2["buy_hit_rate_pct"]
        assert m1["score_buckets"] == m2["score_buckets"]
        assert m1["confidence_buckets"] == m2["confidence_buckets"]


# ── End-to-end: run_validation() persists data_limitations in summary JSON ──

def _valid_bench_df(n=300, seed=99):
    """A benchmark DataFrame that passes _validate_benchmark_acquisition —
    this file's own concern is DP-026 fundamentals disclosure, not
    benchmark evidence, so run_validation() needs real benchmark evidence
    to reach the persistence step this test actually exercises."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n)
    rets = rng.normal(0.0003, 0.008, n)
    close = 100.0 * np.cumprod(1 + rets)
    return pd.DataFrame({"Close": close}, index=dates)


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period=None, timeout=None):
        return _valid_bench_df()

    @property
    def info(self):
        return {}


class _CapturingCursor:
    def __init__(self, sink):
        self._sink = sink
        self.lastrowid = 1

    def execute(self, sql, params=None):
        if "INSERT INTO val_runs" in sql:
            self._sink["summary_json"] = params[4] if len(params) > 4 else None

    def executemany(self, *args, **kwargs):
        pass


class _CapturingConn:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        return _CapturingCursor(self._sink).execute(sql, params) or _CapturingCursor(self._sink)

    def executemany(self, *args, **kwargs):
        pass


@pytest.mark.unit
class TestRunValidationPersistsDisclosure:
    def test_summary_json_contains_data_limitations(self, monkeypatch):
        sink = {}
        signals = _synthetic_signals(n=25, seed=5)

        def fake_backtest_stock(symbol, horizon, benchmark_df, market, *, universe=None, **kwargs):
            window_stats = kwargs.get("_window_stats")
            if window_stats is not None:
                # Matches the 5 signals actually returned below — this
                # test's own concern is DP-026 disclosure, not benchmark
                # signal-coverage (2026-07-26 hardening, Finding D).
                window_stats["considered"] = 5
                window_stats["benchmark_valid"] = 5
            return [dict(s, symbol=symbol) for s in signals[:5]]

        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _FakeTicker

        monkeypatch.setattr(ve, "_backtest_stock", fake_backtest_stock)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _CapturingConn(sink))
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)

        with ve._status_lock:
            ve._run_status["running"] = False

        ve.run_validation(horizon="medium", universe="nifty100", max_workers=1)

        assert sink.get("summary_json"), "run_validation() did not persist a summary at all"
        persisted = json.loads(sink["summary_json"])
        assert persisted["data_limitations"]["dp_id"] == "DP-026"
        assert persisted["data_limitations"]["fundamentals_point_in_time"] is False
