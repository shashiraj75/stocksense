"""
Unit tests for services/postmortem/daily_report.py — the Trade Postmortem
Engine's Stage 3 day-level aggregation. No DB, no HTTP; pure aggregation
over already-computed per-trade results.
"""
import datetime as dt

import pytest

from services.postmortem.causal_analysis import build_causal_analysis
from services.postmortem.daily_report import DailyTradePostmortem, build_daily_report
from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem

UTC = dt.timezone.utc


def _closed_trade(*, trade_id, exit_price, entry_price=100.0, quantity=10, exit_reason="MANUAL"):
    return ClosedTradeRecord(
        trade_id=trade_id,
        status="CLOSED",
        symbol="AAPL",
        market="US",
        quantity=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_loss=90.0,
        target_price=130.0,
        opened_at=dt.datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
        closed_at=dt.datetime(2026, 6, 2, 9, 30, tzinfo=UTC),
        trade_management_mode="manual",
        exit_reason=exit_reason,
    )


def _daily_entry(*, trade_id, exit_price, entry_price=100.0, quantity=10, exit_reason="MANUAL") -> DailyTradePostmortem:
    record = _closed_trade(
        trade_id=trade_id, exit_price=exit_price, entry_price=entry_price, quantity=quantity, exit_reason=exit_reason
    )
    pm = compute_postmortem(record)
    narrative = build_causal_analysis(pm, None)
    return DailyTradePostmortem(trade_id=trade_id, symbol=record.symbol, market=record.market, postmortem=pm, narrative=narrative)


class TestEmptyDay:
    def test_empty_list_produces_zeroed_summary(self):
        report = build_daily_report("2026-06-02", "US", [])
        assert report.summary.trade_count == 0
        assert report.summary.win_count == 0
        assert report.summary.total_realized_pnl_abs is None
        assert report.summary.pnl_excluded_trade_count == 0
        assert report.trades == []


class TestOutcomeTallying:
    def test_mixed_outcomes_tallied_correctly(self):
        entries = [
            _daily_entry(trade_id=1, exit_price=120.0),  # WIN
            _daily_entry(trade_id=2, exit_price=80.0),   # LOSS
            _daily_entry(trade_id=3, exit_price=100.0),  # BREAKEVEN
            _daily_entry(trade_id=4, exit_price=120.0, entry_price=None),  # INDETERMINATE
        ]
        report = build_daily_report("2026-06-02", "US", entries)
        s = report.summary
        assert s.trade_count == 4
        assert s.win_count == 1
        assert s.loss_count == 1
        assert s.breakeven_count == 1
        assert s.indeterminate_count == 1

    def test_root_cause_breakdown_counts_every_trade(self):
        entries = [_daily_entry(trade_id=1, exit_price=120.0), _daily_entry(trade_id=2, exit_price=80.0)]
        report = build_daily_report("2026-06-02", "US", entries)
        assert sum(report.summary.root_cause_breakdown.values()) == 2


class TestPartialPnlSum:
    def test_sums_only_valid_pnl_and_flags_excluded_count(self):
        entries = [
            _daily_entry(trade_id=1, exit_price=120.0),  # valid P&L: +200
            _daily_entry(trade_id=2, exit_price=110.0, entry_price=None),  # indeterminate, no P&L
        ]
        report = build_daily_report("2026-06-02", "US", entries)
        s = report.summary
        assert s.total_realized_pnl_abs == pytest.approx(200.0)
        assert s.pnl_excluded_trade_count == 1

    def test_all_valid_pnl_zero_excluded(self):
        entries = [_daily_entry(trade_id=1, exit_price=120.0), _daily_entry(trade_id=2, exit_price=80.0)]
        report = build_daily_report("2026-06-02", "US", entries)
        assert report.summary.pnl_excluded_trade_count == 0
        assert report.summary.total_realized_pnl_abs == pytest.approx(0.0)

    def test_all_indeterminate_pnl_is_none_but_excluded_count_is_visible(self):
        entries = [_daily_entry(trade_id=1, exit_price=120.0, entry_price=None)]
        report = build_daily_report("2026-06-02", "US", entries)
        assert report.summary.total_realized_pnl_abs is None
        assert report.summary.pnl_excluded_trade_count == 1
        assert report.summary.trade_count == 1
