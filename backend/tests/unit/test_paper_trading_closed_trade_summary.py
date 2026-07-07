"""
Paper Trading — Trade History by Horizon (backend-authoritative). Unit
tests for the pure functions backing GET /portfolio's
`closed_trade_history_by_horizon` field and the `/closed-trades/older`
endpoint's row shaping (api/routers/paper_trading.py). No DB, no HTTP —
these test the exact same functions the endpoints call, matching this
repo's existing pattern of testing pure business-logic functions directly
(e.g. the Multibagger scorecard, Intelligence Engine gates).
"""
import pytest

from api.routers.paper_trading import (
    _summarize_closed_bucket,
    _bucket_closed_trades_by_horizon,
    _closed_history_bucket,
    _closed_trade_history_by_horizon_for_market,
    _closed_trade_summary_for_market,
    _sort_closed_desc,
)


def _trade(entry, exit_, exit_reason=None, market="IN", horizon="short", id=1, closed_at="2026-01-01T00:00:00+00:00"):
    return {
        "id": id,
        "market": market,
        "horizon": horizon,
        "entry_price": entry,
        "exit_price": exit_,
        "realized_pnl": round((exit_ - entry), 2) if exit_ is not None else 0.0,
        "exit_reason": exit_reason,
        "closed_at": closed_at,
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
class TestWinRate:
    """Win Rate = closed trades with realized P&L > 0 / all closed trades
    in the bucket — the same canonical definition as the top-level Win Rate
    stat card, and strictly independent of Target Hit Rate (exit_reason)."""

    def test_profitable_trade_counts_as_a_win(self):
        trades = [_trade(100, 120, "TARGET_HIT")]  # +20, profitable
        result = _summarize_closed_bucket(trades)
        assert result["win_trades_count"] == 1
        assert result["win_rate_pct"] == 100.0

    def test_losing_trade_does_not_count_as_a_win(self):
        trades = [_trade(100, 80, "STOP_LOSS")]  # -20, a loss
        result = _summarize_closed_bucket(trades)
        assert result["win_trades_count"] == 0
        assert result["win_rate_pct"] == 0.0

    def test_break_even_trade_is_not_a_win(self):
        trades = [_trade(100, 100, "MANUAL")]  # exactly 0 P&L
        result = _summarize_closed_bucket(trades)
        assert result["win_trades_count"] == 0
        assert result["break_even_count"] == 1
        assert result["win_rate_pct"] == 0.0

    def test_manually_closed_profitable_trade_counts_as_a_win(self):
        # A MANUAL close with positive P&L is a win for Win Rate purposes,
        # even though it is non-conclusive for Target Hit Rate purposes —
        # the two metrics must not be merged.
        trades = [_trade(100, 130, "MANUAL")]  # +30, profitable, non-conclusive
        result = _summarize_closed_bucket(trades)
        assert result["win_trades_count"] == 1
        assert result["win_rate_pct"] == 100.0
        assert result["conclusive_count"] == 0
        assert result["target_hit_rate_pct"] is None

    def test_win_rate_and_target_hit_rate_are_computed_independently(self):
        trades = [
            _trade(100, 120, "TARGET_HIT"),  # win, target hit
            _trade(100, 80, "STOP_LOSS"),     # loss, stop loss (conclusive)
            _trade(100, 110, "MANUAL"),       # win, non-conclusive
            _trade(100, 90, "MANUAL"),        # loss, non-conclusive
            _trade(100, 100, None),           # break-even, non-conclusive
        ]
        result = _summarize_closed_bucket(trades)
        assert result["closed_trade_count"] == 5
        # Win Rate: 2 wins (120, 110) / 5 total = 40%
        assert result["win_trades_count"] == 2
        assert result["win_rate_pct"] == 40.0
        assert result["break_even_count"] == 1
        # Target Hit Rate: 1 target hit / 2 conclusive (target+stop) = 50%
        assert result["target_hit_count"] == 1
        assert result["stop_loss_count"] == 1
        assert result["conclusive_count"] == 2
        assert result["target_hit_rate_pct"] == 50.0
        # Conclusive outcomes as a share of all closed trades: 2/5 = 40%
        assert result["conclusive_rate_pct"] == 40.0

    def test_empty_bucket_gives_null_win_rate_not_zero(self):
        result = _summarize_closed_bucket([])
        assert result["win_trades_count"] == 0
        assert result["win_rate_pct"] is None


@pytest.mark.unit
class TestBucketClosedTradesByHorizon:
    def test_groups_by_stored_horizon_only(self):
        trades = [
            _trade(100, 120, "TARGET_HIT", horizon="short"),
            _trade(100, 120, "TARGET_HIT", horizon="medium"),
            _trade(100, 120, "TARGET_HIT", horizon="long"),
        ]
        buckets = _bucket_closed_trades_by_horizon(trades)
        assert len(buckets["short"]) == 1
        assert len(buckets["medium"]) == 1
        assert len(buckets["long"]) == 1
        assert buckets["unclassified"] == []

    def test_unrecognized_or_missing_horizon_is_unclassified(self):
        trades = [
            _trade(100, 120, "TARGET_HIT", horizon="short"),
            _trade(100, 90, "STOP_LOSS", horizon="unknown_legacy_value"),
            _trade(100, 100, None, horizon=None),
        ]
        buckets = _bucket_closed_trades_by_horizon(trades)
        assert len(buckets["short"]) == 1
        assert len(buckets["unclassified"]) == 2
        # legacy/unclassified trades must never leak into an official bucket
        assert buckets["medium"] == []
        assert buckets["long"] == []


@pytest.mark.unit
class TestSortClosedDesc:
    def test_newest_first_by_closed_at(self):
        trades = [
            _trade(100, 110, id=1, closed_at="2026-01-01T00:00:00+00:00"),
            _trade(100, 110, id=2, closed_at="2026-01-03T00:00:00+00:00"),
            _trade(100, 110, id=3, closed_at="2026-01-02T00:00:00+00:00"),
        ]
        ordered = _sort_closed_desc(trades)
        assert [t["id"] for t in ordered] == [2, 3, 1]

    def test_id_tiebreaks_identical_closed_at(self):
        trades = [
            _trade(100, 110, id=1, closed_at="2026-01-01T00:00:00+00:00"),
            _trade(100, 110, id=2, closed_at="2026-01-01T00:00:00+00:00"),
        ]
        ordered = _sort_closed_desc(trades)
        assert [t["id"] for t in ordered] == [2, 1]


@pytest.mark.unit
class TestClosedHistoryBucket:
    def test_latest_5_and_earlier_count_with_six_trades(self):
        trades = [
            _trade(100, 110, "TARGET_HIT", id=i, closed_at=f"2026-01-{i:02d}T00:00:00+00:00")
            for i in range(1, 7)  # 6 trades, ids/closed_at 1..6, newest = id 6
        ]
        bucket = _closed_history_bucket(trades)
        assert bucket["summary"]["closed_trade_count"] == 6
        assert len(bucket["latest_trades"]) == 5
        assert [t["id"] for t in bucket["latest_trades"]] == [6, 5, 4, 3, 2]
        assert bucket["earlier_trade_count"] == 1

    def test_five_or_fewer_trades_have_zero_earlier_count(self):
        trades = [
            _trade(100, 110, "TARGET_HIT", id=i, closed_at=f"2026-01-0{i}T00:00:00+00:00")
            for i in range(1, 4)
        ]
        bucket = _closed_history_bucket(trades)
        assert len(bucket["latest_trades"]) == 3
        assert bucket["earlier_trade_count"] == 0

    def test_summary_and_rows_come_from_the_same_source_list(self):
        # A positive-P&L MANUAL close must show up as a row but never in the
        # summary's target_hit_count — same list feeds both.
        trades = [
            _trade(100, 150, "MANUAL", id=1, closed_at="2026-01-02T00:00:00+00:00"),
            _trade(100, 120, "TARGET_HIT", id=2, closed_at="2026-01-01T00:00:00+00:00"),
        ]
        bucket = _closed_history_bucket(trades)
        assert bucket["summary"]["target_hit_count"] == 1
        assert bucket["summary"]["closed_trade_count"] == 2
        assert len(bucket["latest_trades"]) == 2


@pytest.mark.unit
class TestClosedTradeHistoryByHorizonForMarket:
    def test_empty_horizons_still_present_with_zero_count(self):
        trades = [_trade(100, 120, "TARGET_HIT", horizon="short")]
        history = _closed_trade_history_by_horizon_for_market(trades)
        assert history["short"]["summary"]["closed_trade_count"] == 1
        assert history["medium"]["summary"]["closed_trade_count"] == 0
        assert history["long"]["summary"]["closed_trade_count"] == 0
        assert "unclassified" not in history

    def test_unclassified_bucket_only_appears_when_present(self):
        trades = [
            _trade(100, 120, "TARGET_HIT", horizon="short"),
            _trade(100, 90, "STOP_LOSS", horizon="unknown_legacy_value"),
            _trade(100, 100, None, horizon=None),
        ]
        history = _closed_trade_history_by_horizon_for_market(trades)
        assert "unclassified" in history
        assert history["unclassified"]["summary"]["closed_trade_count"] == 2
        assert history["medium"]["summary"]["closed_trade_count"] == 0
        assert history["long"]["summary"]["closed_trade_count"] == 0


@pytest.mark.unit
class TestClosedTradeSummaryForMarketDerivation:
    def test_derived_summary_matches_history_bucket_summary(self):
        trades = [_trade(100, 120, "TARGET_HIT", horizon="short")]
        history = _closed_trade_history_by_horizon_for_market(trades)
        summary = _closed_trade_summary_for_market(history)
        assert summary["short"] == history["short"]["summary"]
        assert summary["medium"] == history["medium"]["summary"]
        assert summary["long"] == history["long"]["summary"]
