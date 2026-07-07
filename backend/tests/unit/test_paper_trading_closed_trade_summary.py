"""
Paper Trading — Trade History by Horizon. Unit tests for the pure
summary functions backing GET /portfolio's `closed_trade_summary` field
(api/routers/paper_trading.py). No DB, no HTTP — these test the exact
same functions the endpoint calls, matching this repo's existing pattern
of testing pure business-logic functions directly (e.g. the Multibagger
scorecard, Intelligence Engine gates).
"""
import pytest

from api.routers.paper_trading import _summarize_closed_bucket, _closed_trade_summary_for_market


def _trade(entry, exit_, exit_reason=None, market="IN", horizon="short"):
    return {
        "market": market,
        "horizon": horizon,
        "entry_price": entry,
        "exit_price": exit_,
        "realized_pnl": round((exit_ - entry), 2) if exit_ is not None else 0.0,
        "exit_reason": exit_reason,
    }


@pytest.mark.unit
class TestSummarizeClosedBucket:
    def test_empty_bucket(self):
        result = _summarize_closed_bucket([])
        assert result["closed_trade_count"] == 0
        assert result["target_hit_rate_pct"] is None
        assert result["net_realized_pnl"] == 0.0
        assert result["avg_realized_return_pct"] is None

    def test_target_hit_rate_denominator_excludes_non_conclusive(self):
        # 2 TARGET_HIT, 1 STOP_LOSS (conclusive=3), plus 2 MANUAL and 1 with
        # no exit_reason at all (legacy) — none of those 3 may affect the
        # hit-rate denominator, only the numerator/denominator of conclusive
        # outcomes, per the exact spec definition (not total closed trades).
        trades = [
            _trade(100, 120, "TARGET_HIT"),
            _trade(100, 110, "TARGET_HIT"),
            _trade(100, 90, "STOP_LOSS"),
            _trade(100, 105, "MANUAL"),
            _trade(100, 95, None),
        ]
        result = _summarize_closed_bucket(trades)
        assert result["closed_trade_count"] == 5
        assert result["target_hit_count"] == 2
        assert result["stop_loss_count"] == 1
        assert result["conclusive_count"] == 3
        assert result["other_count"] == 2
        assert result["target_hit_rate_pct"] == pytest.approx(66.7, abs=0.1)

    def test_positive_pnl_non_conclusive_trade_does_not_inflate_hit_rate(self):
        # A profitable MANUAL close must not count toward target_hit_count —
        # only exit_reason == "TARGET_HIT" may, regardless of P&L sign.
        trades = [
            _trade(100, 150, "MANUAL"),   # big winner, but not a target hit
            _trade(100, 120, "TARGET_HIT"),
            _trade(100, 90, "STOP_LOSS"),
        ]
        result = _summarize_closed_bucket(trades)
        assert result["target_hit_count"] == 1
        assert result["conclusive_count"] == 2
        assert result["target_hit_rate_pct"] == 50.0

    def test_non_conclusive_trades_still_count_in_realized_pnl_and_avg_return(self):
        trades = [
            _trade(100, 90, "MANUAL"),     # -10 realized, -10%
            _trade(100, 120, "TARGET_HIT"),  # +20 realized, +20%
        ]
        result = _summarize_closed_bucket(trades)
        assert result["net_realized_pnl"] == pytest.approx(10.0)  # -10 + 20
        assert result["avg_realized_return_pct"] == pytest.approx(5.0, abs=0.01)  # mean(-10%, +20%)

    def test_all_non_conclusive_gives_null_hit_rate_not_zero(self):
        trades = [_trade(100, 110, "MANUAL"), _trade(100, 90, None)]
        result = _summarize_closed_bucket(trades)
        assert result["conclusive_count"] == 0
        assert result["target_hit_rate_pct"] is None


@pytest.mark.unit
class TestClosedTradeSummaryForMarket:
    def test_groups_by_stored_horizon_only(self):
        trades = [
            _trade(100, 120, "TARGET_HIT", horizon="short"),
            _trade(100, 120, "TARGET_HIT", horizon="medium"),
            _trade(100, 120, "TARGET_HIT", horizon="long"),
        ]
        summary = _closed_trade_summary_for_market(trades)
        assert summary["short"]["closed_trade_count"] == 1
        assert summary["medium"]["closed_trade_count"] == 1
        assert summary["long"]["closed_trade_count"] == 1
        assert "unclassified" not in summary

    def test_empty_horizons_still_present_with_zero_count(self):
        trades = [_trade(100, 120, "TARGET_HIT", horizon="short")]
        summary = _closed_trade_summary_for_market(trades)
        assert summary["medium"]["closed_trade_count"] == 0
        assert summary["long"]["closed_trade_count"] == 0

    def test_unclassified_bucket_only_appears_when_present(self):
        trades = [
            _trade(100, 120, "TARGET_HIT", horizon="short"),
            _trade(100, 90, "STOP_LOSS", horizon="unknown_legacy_value"),
            _trade(100, 100, None, horizon=None),
        ]
        summary = _closed_trade_summary_for_market(trades)
        assert summary["short"]["closed_trade_count"] == 1
        assert "unclassified" in summary
        assert summary["unclassified"]["closed_trade_count"] == 2
        # legacy/unclassified trades must never leak into an official bucket
        assert summary["medium"]["closed_trade_count"] == 0
        assert summary["long"]["closed_trade_count"] == 0

    def test_no_unclassified_key_when_no_such_trades_exist(self):
        trades = [_trade(100, 120, "TARGET_HIT", horizon="short")]
        summary = _closed_trade_summary_for_market(trades)
        assert "unclassified" not in summary
