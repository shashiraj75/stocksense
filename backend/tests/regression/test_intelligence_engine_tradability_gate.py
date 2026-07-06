"""Epic 007 Phase 3B — Tradability Gate unit tests. Pure-function tests
only; no network, no database, no Daily Picks pipeline involved."""
import pytest

from services.intelligence_engine.tradability_gate import (
    check_tradability,
    TRADABILITY_OK,
    TRADABILITY_SUSPENDED,
    TRADABILITY_HALTED,
    TRADABILITY_DELISTED,
    TRADABILITY_INACTIVE,
    TRADABILITY_MISSING_QUOTE,
    TRADABILITY_MISSING_VOLUME,
    TRADABILITY_STALE_PRICING,
    TRADABILITY_INVALID_SYMBOL,
)


@pytest.mark.regression
class TestTradabilityGate:
    def test_healthy_symbol_passes(self):
        result = check_tradability("AAPL", trading_status="active", has_quote=True, has_volume=True, quote_age_days=0)
        assert result.passed is True
        assert result.reason == TRADABILITY_OK

    def test_invalid_symbol_fails(self):
        result = check_tradability("???", is_valid_symbol=False)
        assert result.passed is False
        assert result.reason == TRADABILITY_INVALID_SYMBOL

    def test_suspended_fails(self):
        result = check_tradability("XYZ", trading_status="suspended")
        assert result.passed is False
        assert result.reason == TRADABILITY_SUSPENDED

    def test_halted_fails(self):
        result = check_tradability("XYZ", trading_status="Halted")  # case-insensitive
        assert result.passed is False
        assert result.reason == TRADABILITY_HALTED

    def test_delisted_fails(self):
        result = check_tradability("XYZ", trading_status="delisted")
        assert result.passed is False
        assert result.reason == TRADABILITY_DELISTED

    def test_inactive_fails(self):
        result = check_tradability("XYZ", trading_status="inactive")
        assert result.passed is False
        assert result.reason == TRADABILITY_INACTIVE

    def test_missing_quote_fails(self):
        result = check_tradability("XYZ", has_quote=False)
        assert result.passed is False
        assert result.reason == TRADABILITY_MISSING_QUOTE

    def test_missing_volume_fails(self):
        result = check_tradability("XYZ", has_volume=False)
        assert result.passed is False
        assert result.reason == TRADABILITY_MISSING_VOLUME

    def test_stale_pricing_fails(self):
        result = check_tradability("XYZ", quote_age_days=30)
        assert result.passed is False
        assert result.reason == TRADABILITY_STALE_PRICING

    def test_fresh_pricing_passes(self):
        result = check_tradability("XYZ", quote_age_days=1)
        assert result.passed is True

    def test_no_signals_at_all_fails_open_to_pass(self):
        # No trading_status, no quote-age data — the gate must not invent
        # a failure from the mere absence of a signal.
        result = check_tradability("XYZ")
        assert result.passed is True
        assert result.reason == TRADABILITY_OK
