"""
Trade Postmortem Engine, Sprint 2 — unit tests for
services.postmortem.exit_snapshot: pure classification functions and
build_exit_snapshot's own construction-time validation. No I/O anywhere
in this module, so no database mocking is needed here.
"""
import datetime as dt

import pytest

from services.postmortem.deterministic import Outcome
from services.postmortem.exit_snapshot import (
    CloseExitMechanism,
    ClosureClassification,
    TriggerTimingVerification,
    build_exit_snapshot,
    classify_closure,
    classify_financial_outcome,
    classify_trigger_timing_verification,
)


@pytest.mark.unit
class TestClassifyClosure:
    @pytest.mark.parametrize("mechanism,expected", [
        (CloseExitMechanism.MANUAL, ClosureClassification.TRADING_EXIT),
        (CloseExitMechanism.STOP_LOSS, ClosureClassification.SYSTEM_EXIT),
        (CloseExitMechanism.TARGET_HIT, ClosureClassification.SYSTEM_EXIT),
        (CloseExitMechanism.TRAILING_STOP, ClosureClassification.SYSTEM_EXIT),
        (CloseExitMechanism.TIME_BASED, ClosureClassification.SYSTEM_EXIT),
        (CloseExitMechanism.MARKET_CLOSE, ClosureClassification.SYSTEM_EXIT),
        (CloseExitMechanism.RISK_RULE, ClosureClassification.SYSTEM_EXIT),
        (CloseExitMechanism.ADMINISTRATIVE, ClosureClassification.ADMINISTRATIVE_CLOSE),
        (CloseExitMechanism.RESET, ClosureClassification.RESET_CLOSE),
        (CloseExitMechanism.OTHER, ClosureClassification.UNKNOWN_CLOSE),
        (CloseExitMechanism.UNKNOWN, ClosureClassification.UNKNOWN_CLOSE),
    ])
    def test_every_mechanism_maps_deterministically(self, mechanism, expected):
        assert classify_closure(mechanism) == expected

    def test_every_enum_member_has_a_mapping(self):
        """A future mechanism added to the enum without a mapping entry
        must fail loudly here, not silently default at runtime."""
        for mechanism in CloseExitMechanism:
            classify_closure(mechanism)  # raises ValueError if unmapped


@pytest.mark.unit
class TestClassifyTriggerTimingVerification:
    @pytest.mark.parametrize("mechanism", [
        CloseExitMechanism.STOP_LOSS, CloseExitMechanism.TARGET_HIT,
        CloseExitMechanism.TRAILING_STOP, CloseExitMechanism.TIME_BASED,
        CloseExitMechanism.MARKET_CLOSE, CloseExitMechanism.RISK_RULE,
    ])
    def test_browser_detected_mechanisms_are_client_reported_unverified(self, mechanism):
        assert classify_trigger_timing_verification(mechanism) == TriggerTimingVerification.CLIENT_REPORTED_UNVERIFIED

    @pytest.mark.parametrize("mechanism", [
        CloseExitMechanism.MANUAL, CloseExitMechanism.ADMINISTRATIVE,
        CloseExitMechanism.RESET, CloseExitMechanism.OTHER, CloseExitMechanism.UNKNOWN,
    ])
    def test_non_browser_mechanisms_are_not_applicable(self, mechanism):
        assert classify_trigger_timing_verification(mechanism) == TriggerTimingVerification.NOT_APPLICABLE

    def test_never_produces_server_verified(self):
        """No genuine backend-observed trigger evidence exists anywhere in
        this codebase today — this function must never fabricate that
        trust level for any mechanism."""
        for mechanism in CloseExitMechanism:
            assert classify_trigger_timing_verification(mechanism) != TriggerTimingVerification.SERVER_VERIFIED


@pytest.mark.unit
class TestClassifyFinancialOutcome:
    def test_positive_pnl_is_win(self):
        assert classify_financial_outcome(150.0) == Outcome.WIN

    def test_negative_pnl_is_loss(self):
        assert classify_financial_outcome(-50.0) == Outcome.LOSS

    def test_zero_pnl_is_breakeven(self):
        assert classify_financial_outcome(0.0) == Outcome.BREAKEVEN

    def test_none_pnl_is_indeterminate(self):
        assert classify_financial_outcome(None) == Outcome.INDETERMINATE


@pytest.mark.unit
class TestBuildExitSnapshot:
    _BASE_KWARGS = dict(
        paper_trade_id=1,
        user_id="user-aaa",
        symbol="AAPL",
        market="US",
        exit_mechanism=CloseExitMechanism.MANUAL,
        exit_mechanism_raw="MANUAL",
        exit_quantity=10,
        closed_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        realized_pnl_abs=100.0,
        management_mode="manual",
    )

    def test_valid_snapshot_constructs_with_derived_fields(self):
        snap = build_exit_snapshot(exit_price=120.0, **self._BASE_KWARGS)
        assert snap.financial_outcome == Outcome.WIN.value
        assert snap.closure_classification == ClosureClassification.TRADING_EXIT.value
        assert snap.exit_mechanism == CloseExitMechanism.MANUAL.value
        assert snap.trigger_timing_verification == TriggerTimingVerification.NOT_APPLICABLE.value
        assert snap.exit_snapshot_schema_version

    @pytest.mark.parametrize("bad_price", [0, -5.0, float("nan"), float("inf"), float("-inf"), None])
    def test_rejects_non_finite_or_non_positive_exit_price(self, bad_price):
        with pytest.raises(ValueError):
            build_exit_snapshot(exit_price=bad_price, **self._BASE_KWARGS)

    @pytest.mark.parametrize("bad_qty", [0, -1, 1.5, True])
    def test_rejects_non_positive_or_non_int_exit_quantity(self, bad_qty):
        kwargs = {**self._BASE_KWARGS, "exit_quantity": bad_qty}
        with pytest.raises(ValueError):
            build_exit_snapshot(exit_price=100.0, **kwargs)

    def test_bool_exit_price_rejected_despite_being_an_int_subtype(self):
        """isinstance(True, int) is True in Python — must not silently
        pass validation as if it were the number 1."""
        with pytest.raises(ValueError):
            build_exit_snapshot(exit_price=True, **self._BASE_KWARGS)

    def test_system_exit_mechanism_reflects_client_reported_trust_level(self):
        kwargs = {**self._BASE_KWARGS, "exit_mechanism": CloseExitMechanism.STOP_LOSS, "exit_mechanism_raw": "STOP_LOSS"}
        snap = build_exit_snapshot(exit_price=90.0, **kwargs)
        assert snap.closure_classification == ClosureClassification.SYSTEM_EXIT.value
        assert snap.trigger_timing_verification == TriggerTimingVerification.CLIENT_REPORTED_UNVERIFIED.value

    def test_deterministic_for_identical_inputs(self):
        a = build_exit_snapshot(exit_price=120.0, **self._BASE_KWARGS)
        b = build_exit_snapshot(exit_price=120.0, **self._BASE_KWARGS)
        assert a == b
