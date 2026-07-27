"""
Unit tests for services/paper_trade_math.py — the single authoritative
realized-P&L calculation path (Trade Postmortem Engine, Phase 1). No DB, no
HTTP.
"""
import pytest

from services.paper_trade_math import compute_realized_pnl_abs, compute_realized_pnl_pct


@pytest.mark.unit
class TestComputeRealizedPnlAbs:
    def test_winning_trade(self):
        assert compute_realized_pnl_abs(100.0, 120.0, 10) == pytest.approx(200.0)

    def test_losing_trade(self):
        assert compute_realized_pnl_abs(100.0, 90.0, 10) == pytest.approx(-100.0)

    def test_breakeven_trade(self):
        assert compute_realized_pnl_abs(100.0, 100.0, 10) == 0.0

    def test_rounds_to_2dp(self):
        assert compute_realized_pnl_abs(100.0, 100.333333, 3) == pytest.approx(1.0)

    def test_none_exit_price_returns_zero_not_negative_garbage(self):
        # Matches the pre-extraction _trade_row_to_dict guard exactly — a
        # falsy exit_price must not produce -entry_price*quantity.
        assert compute_realized_pnl_abs(100.0, None, 10) == 0.0

    def test_zero_exit_price_returns_zero(self):
        assert compute_realized_pnl_abs(100.0, 0.0, 10) == 0.0


@pytest.mark.unit
class TestComputeRealizedPnlPct:
    def test_winning_trade(self):
        assert compute_realized_pnl_pct(100.0, 120.0) == pytest.approx(20.0)

    def test_losing_trade(self):
        assert compute_realized_pnl_pct(100.0, 90.0) == pytest.approx(-10.0)

    def test_breakeven_trade(self):
        assert compute_realized_pnl_pct(100.0, 100.0) == 0.0

    def test_none_entry_price_returns_none(self):
        assert compute_realized_pnl_pct(None, 100.0) is None

    def test_zero_entry_price_returns_none_not_zerodivisionerror(self):
        assert compute_realized_pnl_pct(0.0, 100.0) is None

    def test_negative_entry_price_returns_none(self):
        assert compute_realized_pnl_pct(-5.0, 100.0) is None

    def test_none_exit_price_returns_none(self):
        assert compute_realized_pnl_pct(100.0, None) is None

    def test_rounds_to_2dp(self):
        assert compute_realized_pnl_pct(3.0, 3.1) == pytest.approx(3.33)
