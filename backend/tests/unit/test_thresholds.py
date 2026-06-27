"""
Unit tests for the central threshold registry (services/thresholds.py).

These are intentionally simple "did the number get migrated correctly"
checks — the registry's whole purpose is to be a boring, frozen source of
truth, so the tests just pin the exact values it must hold.
"""

import pytest

from services.thresholds import (
    DEBT_TO_EQUITY,
    PROFITABILITY,
    CASH_FLOW,
    GROWTH,
    VALUATION,
    GOVERNANCE,
    RISK_PENALTY,
)


class TestDebtToEquityOrdering:
    @pytest.mark.unit
    def test_thresholds_are_monotonically_increasing(self):
        """The five D/E cutoffs SEAR-001 found scattered across two files
        must remain in a sane ascending order once centralized, or the gate/
        penalty/checklist tiers would silently contradict each other."""
        # TURNAROUND_EXCEPTION_MAX and ELEVATED_PENALTY_MIN are both 150 in
        # the original code (prediction_engine.py:1378 and :1064 /
        # multibagger_scorecard.py:76) — a genuine tie, not a migration
        # error, so this allows <= there and strict < everywhere else.
        assert (
            DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX
            <= DEBT_TO_EQUITY.TURNAROUND_EXCEPTION_MAX
            <= DEBT_TO_EQUITY.ELEVATED_PENALTY_MIN
            < DEBT_TO_EQUITY.RISK_PENALTY_ELEVATED_MIN
            < DEBT_TO_EQUITY.RISK_PENALTY_SEVERE_MIN
            < DEBT_TO_EQUITY.HARD_REJECT_MIN
        )


class TestThresholdsAreImmutable:
    @pytest.mark.unit
    def test_dataclasses_are_frozen(self):
        with pytest.raises(Exception):
            DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX = 999.0


class TestMigratedValuesMatchOriginalCode:
    """Pins the exact values found at each file:line cited in SEAR-001, so a
    future refactor can't silently drift a threshold while moving it."""

    @pytest.mark.unit
    def test_debt_to_equity_values(self):
        assert DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX == 50.0
        assert DEBT_TO_EQUITY.TURNAROUND_EXCEPTION_MAX == 150.0
        assert DEBT_TO_EQUITY.ELEVATED_PENALTY_MIN == 150.0
        assert DEBT_TO_EQUITY.RISK_PENALTY_ELEVATED_MIN == 200.0
        assert DEBT_TO_EQUITY.RISK_PENALTY_SEVERE_MIN == 300.0
        assert DEBT_TO_EQUITY.HARD_REJECT_MIN == 500.0

    @pytest.mark.unit
    def test_profitability_values(self):
        assert PROFITABILITY.ROE_SEVERE_NEGATIVE == -0.10
        assert PROFITABILITY.ROE_NEGATIVE_RISK_PENALTY == -0.05
        assert PROFITABILITY.ROCE_QUALITY_COMPOUNDER_MIN_PCT == 15.0
        assert PROFITABILITY.ROE_QUALITY_COMPOUNDER_MIN_PCT == 18.0

    @pytest.mark.unit
    def test_valuation_values(self):
        assert VALUATION.PE_QUALITY_COMPOUNDER_MAX == 35.0
        assert VALUATION.EV_EBITDA_QUALITY_COMPOUNDER_MAX == 20.0

    @pytest.mark.unit
    def test_governance_values(self):
        assert GOVERNANCE.INTEREST_COVERAGE_MIN == 3.0
        assert GOVERNANCE.PROMOTER_PLEDGE_CLEAN_MAX_PCT == 1.0
        assert GOVERNANCE.PROMOTER_PLEDGE_RED_FLAG_MIN_PCT == 5.0
