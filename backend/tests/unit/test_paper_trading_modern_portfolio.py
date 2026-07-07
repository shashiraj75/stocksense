"""
Paper Trading closed-history payload efficiency (final hardening pass).
Unit tests for the pure aggregation-row-shaping functions backing the
include_full_closed_trades=false path of GET /portfolio
(api/routers/paper_trading.py). No DB — these test the exact same
functions the endpoint calls on top of bounded SQL aggregate query results.
"""
import pytest

from api.routers.paper_trading import (
    _summary_from_aggregate_row,
    _overview_from_closed_trades,
)


def _agg(market="IN", bucket="short", count=5, target_hit=2, stop_loss=1,
         win_trades=3, break_even=0, net_pnl=100.0, avg_return=5.0, invested=1000.0):
    return {
        "market": market, "bucket": bucket,
        "closed_trade_count": count, "target_hit_count": target_hit, "stop_loss_count": stop_loss,
        "win_trades_count": win_trades, "break_even_count": break_even,
        "net_realized_pnl": net_pnl, "avg_realized_return_pct": avg_return,
        "invested": invested,
    }


@pytest.mark.unit
class TestSummaryFromAggregateRow:
    def test_matches_summarize_closed_bucket_shape(self):
        agg = _agg(count=5, target_hit=2, stop_loss=1, win_trades=3)
        summary = _summary_from_aggregate_row(agg)
        assert summary["closed_trade_count"] == 5
        assert summary["target_hit_count"] == 2
        assert summary["stop_loss_count"] == 1
        assert summary["conclusive_count"] == 3
        assert summary["other_count"] == 2
        assert summary["target_hit_rate_pct"] == pytest.approx(66.7, abs=0.1)
        assert summary["conclusive_rate_pct"] == 60.0
        assert summary["net_realized_pnl"] == 100.0
        assert summary["avg_realized_return_pct"] == 5.0

    def test_zero_conclusive_gives_null_rate_not_zero(self):
        agg = _agg(count=3, target_hit=0, stop_loss=0)
        summary = _summary_from_aggregate_row(agg)
        assert summary["conclusive_count"] == 0
        assert summary["target_hit_rate_pct"] is None

    def test_win_rate_uses_win_trades_count_not_target_hit_count(self):
        # 5 closed, 3 wins (positive P&L), only 2 conclusive (target/stop) —
        # Win Rate and Target Hit Rate must be independently correct and
        # never merged into one number.
        agg = _agg(count=5, target_hit=1, stop_loss=1, win_trades=3, break_even=1)
        summary = _summary_from_aggregate_row(agg)
        assert summary["win_trades_count"] == 3
        assert summary["win_rate_pct"] == 60.0
        assert summary["break_even_count"] == 1
        assert summary["target_hit_rate_pct"] == 50.0  # 1/2, independent of win rate

    def test_empty_bucket_gives_null_win_rate_not_zero(self):
        agg = _agg(count=0, target_hit=0, stop_loss=0, win_trades=0, break_even=0, net_pnl=0.0, avg_return=None)
        summary = _summary_from_aggregate_row(agg)
        assert summary["win_rate_pct"] is None
        assert summary["conclusive_rate_pct"] is None


@pytest.mark.unit
class TestOverviewFromClosedTrades:
    def test_counts_and_win_rate_per_market(self):
        trades = [
            {"market": "IN", "realized_pnl": 10.0, "invested": 100.0},
            {"market": "IN", "realized_pnl": -5.0, "invested": 50.0},
            {"market": "US", "realized_pnl": 20.0, "invested": 200.0},
        ]
        overview = _overview_from_closed_trades(trades)
        assert overview["IN"]["closed_trade_count"] == 2
        assert overview["IN"]["win_trades_count"] == 1
        assert overview["IN"]["win_rate_pct"] == 50.0
        assert overview["IN"]["total_invested"] == 150.0
        assert overview["US"]["closed_trade_count"] == 1
        assert overview["US"]["win_trades_count"] == 1
        assert overview["US"]["win_rate_pct"] == 100.0
        assert overview["US"]["total_invested"] == 200.0

    def test_empty_market_gives_null_win_rate_not_zero(self):
        overview = _overview_from_closed_trades([])
        assert overview["IN"]["closed_trade_count"] == 0
        assert overview["IN"]["win_rate_pct"] is None
        assert overview["IN"]["total_invested"] == 0.0
