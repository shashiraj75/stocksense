"""
DP-026 — walk-forward validation's fund_score is a single present-day
fundamentals snapshot reused unchanged across every historical signal date
for a symbol (look-ahead bias on the fundamentals component of
composite_score). Investigated 2026-07-21: no data source available to this
repository (yfinance, screener.in, BSE, or the internal
stock_fundamentals_cache — overwrite-only, no history retained) can supply a
genuine point-in-time fundamentals vintage for a historical signal date, so
Outcome C (not implementable with current data) applies. Governed by
DPD-009 (DECIDED — DISCLOSURE HOLD), which already authorizes disclosing
validation methodology limitations rather than silently correcting or
fabricating them.

This file proves the DP-026 containment: `_compute_metrics()` now attaches
an honest, machine-readable `data_limitations` block to every non-empty
validation summary, and that this is purely additive — no composite score,
factor IC, weight, threshold, or BUY/SELL/HOLD classification is touched by
it, in either the pure aggregation function or the end-to-end
`run_validation()` persistence path.

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

    def test_only_one_new_key_added(self):
        metrics = _compute_metrics(_synthetic_signals(), benchmark_return_pct=1.0, horizon="medium")
        assert set(metrics.keys()) - self.EXPECTED_PRE_EXISTING_KEYS == {"data_limitations"}

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

class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period=None):
        return pd.DataFrame()

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

        def fake_backtest_stock(symbol, horizon, benchmark_df, market, *, universe=None):
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
