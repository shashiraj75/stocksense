"""
Trade Postmortem Sprint 3A, Stage J1 — unit tests for
services.postmortem.price_path_eligibility.evaluate_eligibility: the
single, pure, typed decision point evaluated before any provider call,
any replay lookup, or any outbox row creation.
"""
import datetime as dt

import pytest

from services.postmortem.price_path_eligibility import (
    ELIGIBLE_COMPLETE_CONTEXT,
    ELIGIBLE_PARTIAL_CONTEXT,
    INVALID_MARKET,
    INVALID_TRADE_PRICE,
    INVALID_TRADE_TIMELINE,
    MISSING_ENTRY_CONTEXT,
    MISSING_EXIT_CONTEXT,
    evaluate_eligibility,
)

ET = dt.timezone(dt.timedelta(hours=-5))


def _eval(**overrides):
    kwargs = dict(
        market="US", entry_price=100.0, exit_price=110.0,
        opened_at=dt.datetime(2026, 6, 1, tzinfo=ET), closed_at=dt.datetime(2026, 6, 5, tzinfo=ET),
        entry_snapshot_present=True, exit_snapshot_present=True,
    )
    kwargs.update(overrides)
    return evaluate_eligibility(**kwargs)


@pytest.mark.unit
class TestValidTrade:
    def test_complete_context_eligible(self):
        result = _eval()
        assert result.eligibility_status == ELIGIBLE_COMPLETE_CONTEXT
        assert result.acquisition_allowed is True
        assert result.replay_allowed is True
        assert result.calculation_allowed is True
        assert result.report_completeness_ceiling == "COMPLETE"

    def test_null_exit_price_is_not_invalid(self):
        """Sprint 2's own indeterminate-P&L preservation (Stage 7) — a
        None exit_price is a legitimate 'unknown', not corrupt data."""
        result = _eval(exit_price=None)
        assert result.acquisition_allowed is True
        assert result.eligibility_status != INVALID_TRADE_PRICE


@pytest.mark.unit
class TestInvalidMarket:
    def test_unsupported_market_blocks_acquisition(self):
        result = _eval(market="XX")
        assert result.eligibility_status == INVALID_MARKET
        assert result.acquisition_allowed is False
        assert result.replay_allowed is False
        assert result.calculation_allowed is False


@pytest.mark.unit
class TestInvalidTimeline:
    def test_missing_entry_timestamp(self):
        result = _eval(opened_at=None)
        assert result.eligibility_status == INVALID_TRADE_TIMELINE
        assert result.acquisition_allowed is False

    def test_missing_exit_timestamp(self):
        result = _eval(closed_at=None)
        assert result.eligibility_status == INVALID_TRADE_TIMELINE
        assert result.acquisition_allowed is False

    def test_entry_after_exit(self):
        result = _eval(
            opened_at=dt.datetime(2026, 6, 5, tzinfo=ET), closed_at=dt.datetime(2026, 6, 1, tzinfo=ET),
        )
        assert result.eligibility_status == INVALID_TRADE_TIMELINE
        assert result.acquisition_allowed is False

    def test_timezone_naive_entry_rejected(self):
        result = _eval(opened_at=dt.datetime(2026, 6, 1))
        assert result.eligibility_status == INVALID_TRADE_TIMELINE

    def test_timezone_naive_exit_rejected(self):
        result = _eval(closed_at=dt.datetime(2026, 6, 5))
        assert result.eligibility_status == INVALID_TRADE_TIMELINE


@pytest.mark.unit
class TestInvalidPrice:
    def test_missing_entry_price(self):
        result = _eval(entry_price=None)
        assert result.eligibility_status == INVALID_TRADE_PRICE
        assert result.acquisition_allowed is False

    @pytest.mark.parametrize("bad_price", [0, -5.0, float("nan"), float("inf")])
    def test_non_finite_or_non_positive_entry_price(self, bad_price):
        result = _eval(entry_price=bad_price)
        assert result.eligibility_status == INVALID_TRADE_PRICE

    def test_non_finite_exit_price_rejected(self):
        result = _eval(exit_price=float("nan"))
        assert result.eligibility_status == INVALID_TRADE_PRICE


@pytest.mark.unit
class TestContextCeiling:
    def test_missing_entry_snapshot_caps_ceiling(self):
        result = _eval(entry_snapshot_present=False)
        assert result.eligibility_status == ELIGIBLE_PARTIAL_CONTEXT
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"
        assert MISSING_ENTRY_CONTEXT in result.reason_codes
        assert result.acquisition_allowed is True  # still eligible for market-data work

    def test_missing_exit_snapshot_caps_ceiling(self):
        result = _eval(exit_snapshot_present=False)
        assert result.eligibility_status == ELIGIBLE_PARTIAL_CONTEXT
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"
        assert MISSING_EXIT_CONTEXT in result.reason_codes

    def test_both_missing_still_permits_price_path_work(self):
        """A price-path calculation may be valid while the overall
        report stays LIMITED_EVIDENCE — missing context never blocks
        market-data acquisition/calculation."""
        result = _eval(entry_snapshot_present=False, exit_snapshot_present=False)
        assert result.acquisition_allowed is True
        assert result.replay_allowed is True
        assert result.calculation_allowed is True
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_missing_evidence_gap_strings_present(self):
        result = _eval(entry_snapshot_present=False)
        assert "entry_snapshot" in result.required_missing_evidence
