"""
Trade Postmortem Sprint 3A, Stage J4B.1 — invariant-hardening unit
tests for the observed numerical-crossing model
(price_path_calculator.py's NumericalCrossingContractError,
_validate_supplied_numerical_value, NumericalCrossingObservationContext,
the hardened classify_bar_session_attribution/
is_safely_attributable_session, observe_numerical_level_crossing's
every-bar-attributed-before-crossing contract, strict GAP_THROUGH
semantics, NumericalLevelCrossingObservation's __post_init__
invariants, evidence-ID validation, and
ObservedNumericalCrossingSummary's anti-mixing control).

IMPLEMENTED, UNIT-TESTED, NOT REPORT-WIRED, NOT PERSISTED, NOT
ENDPOINT-VERIFIED. Purely additive hardening of the J4B internal model;
nothing here is imported by price_path_generation.py,
price_path_claims.py, or paper_trading.py.
"""
import dataclasses
import datetime as dt

import pytest

from services.market_hours import ET
from services.postmortem.price_path_calculator import (
    CROSSING_TYPE_GAP_THROUGH,
    CROSSING_TYPE_NORMAL,
    INCONSISTENT_CROSSING_OBSERVATION,
    INCONSISTENT_CROSSING_SUMMARY,
    INVALID_CROSSING_EVIDENCE_ID,
    INVALID_LEVEL_KIND,
    INVALID_OBSERVATION_CONTEXT,
    INVALID_SUPPLIED_VALUE_TYPE,
    MIXED_OBSERVATION_CONTEXT,
    NON_FINITE_SUPPLIED_VALUE,
    NON_POSITIVE_SUPPLIED_VALUE,
    NumericalCrossingContractError,
    NumericalCrossingObservationContext,
    NumericalLevelCrossingObservation,
    ObservedNumericalCrossingSummary,
    SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN,
    SESSION_ATTRIBUTION_INTERIOR,
    STOP_VALUE,
    SUPPLIED_AT_CALCULATION,
    TARGET_VALUE,
    SessionAttributionError,
    _bar_crossing_type,
    _crossing_evidence_id,
    _validate_supplied_numerical_value,
    classify_bar_session_attribution,
    is_safely_attributable_session,
    observe_numerical_level_crossing,
    summarize_observed_numerical_crossings,
)
from services.postmortem.price_path_evidence import PricePathBar, PricePathEvidenceBundle, STATUS_COMPLETE, UNADJUSTED
from tests.unit.test_price_path_observed_crossing import (
    _D,
    _FakeBar,
    _FakeBundle,
    _IN_ENTRY_INTERIOR,
    _IN_EXIT_INTERIOR,
    _US_ENTRY_INTERIOR,
    _US_ENTRY_PARTIAL,
    _US_EXIT_INTERIOR,
    _four_day_bars,
    _in_bundle,
    _raw,
    _us_bundle,
)


# ============================================================================
# VALUE CONTRACT
# ============================================================================
@pytest.mark.unit
class TestSuppliedValueContract:
    def test_valid_target_float(self):
        assert _validate_supplied_numerical_value(120.5, TARGET_VALUE) == 120.5

    def test_valid_stop_integer_normalized_to_float(self):
        result = _validate_supplied_numerical_value(80, STOP_VALUE)
        assert result == 80.0
        assert isinstance(result, float)

    def test_none_accepted(self):
        assert _validate_supplied_numerical_value(None, TARGET_VALUE) is None

    @pytest.mark.parametrize("bad_value,expected_code", [
        (float("nan"), NON_FINITE_SUPPLIED_VALUE),
        (float("inf"), NON_FINITE_SUPPLIED_VALUE),
        (float("-inf"), NON_FINITE_SUPPLIED_VALUE),
        (0.0, NON_POSITIVE_SUPPLIED_VALUE),
        (-0.0, NON_POSITIVE_SUPPLIED_VALUE),
        (-5.0, NON_POSITIVE_SUPPLIED_VALUE),
        (True, INVALID_SUPPLIED_VALUE_TYPE),
        (False, INVALID_SUPPLIED_VALUE_TYPE),
        ("120.0", INVALID_SUPPLIED_VALUE_TYPE),
        (object(), INVALID_SUPPLIED_VALUE_TYPE),
    ])
    def test_invalid_values_rejected_with_reason_code(self, bad_value, expected_code):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(bad_value, TARGET_VALUE)
        assert exc_info.value.reason_code == expected_code

    def test_decimal_rejected_by_default(self):
        from decimal import Decimal
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(Decimal("120.0"), TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_SUPPLIED_VALUE_TYPE

    def test_invalid_level_kind_rejected_before_scanning_bars(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            observe_numerical_level_crossing(bundle, 120.0, "NOT_A_REAL_KIND")
        assert exc_info.value.reason_code == INVALID_LEVEL_KIND

    def test_sanitized_error_never_includes_raw_value_in_message(self):
        """Reason code + field name + level kind only -- never the raw
        supplied numeric value itself interpolated into the message
        text (a distinct, specific value like 123456.789 must never
        appear, even though the generic word "nan" describing the
        category of rejection is fine)."""
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(-123456.789, TARGET_VALUE)
        assert "123456.789" not in str(exc_info.value)


# ============================================================================
# SESSION AND BOUNDARY CONTRACT
# ============================================================================
def _forge_bundle(base_bundle, **overrides):
    """Stage J4B.2 -- since classify_bar_session_attribution now
    validates `isinstance(bundle, PricePathEvidenceBundle)` before
    anything else, a plain duck-typed _FakeBundle can no longer reach
    the POLICY-validation branch (it is correctly rejected earlier, at
    the type-check gate -- see TestFunctionBoundaryTotality below for
    that gate's own dedicated tests). To exercise policy/window
    validation specifically, forge a REAL PricePathEvidenceBundle via
    dataclasses.replace on an already-valid instance -- the bundle's
    own __post_init__ does not validate boundary-policy STRING values
    (only OHLC/duplicate/ordering invariants), so this legitimately
    produces a real, isinstance-passing bundle carrying an invalid
    policy for the test to exercise."""
    return dataclasses.replace(base_bundle, **overrides)


def _forge_bar(base_bar, **overrides):
    return dataclasses.replace(base_bar, **overrides)


@pytest.mark.unit
class TestSessionBoundaryHardening:
    def test_unknown_attribution_to_is_safely_attributable_raises(self):
        with pytest.raises(SessionAttributionError):
            is_safely_attributable_session("NOT_A_REAL_ATTRIBUTION")

    def test_unknown_entry_policy_on_interior_bar_bundle_fails_before_crossing(self):
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        forged = _forge_bundle(base, entry_bar_policy="MADE_UP")
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(forged, forged.bars[1])

    def test_unknown_exit_policy_on_interior_bar_bundle_fails_before_crossing(self):
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        forged = _forge_bundle(base, exit_bar_policy="MADE_UP")
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(forged, forged.bars[1])

    def test_same_day_unknown_entry_plus_recognized_partial_exit_fails(self):
        base = _us_bundle(_US_ENTRY_PARTIAL, dt.datetime(2026, 6, 1, 15, 0, tzinfo=ET), [_raw(_D(1), 100, 101, 99, 100)])
        forged = _forge_bundle(base, entry_bar_policy="MADE_UP")
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(forged, forged.bars[0])

    def test_same_day_recognized_partial_entry_plus_unknown_exit_fails(self):
        base = _us_bundle(_US_ENTRY_PARTIAL, dt.datetime(2026, 6, 1, 15, 0, tzinfo=ET), [_raw(_D(1), 100, 101, 99, 100)])
        forged = _forge_bundle(base, exit_bar_policy="MADE_UP")
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(forged, forged.bars[0])

    def test_same_day_both_recognized_full_is_included_full(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, dt.datetime(2026, 6, 1, 16, 0, tzinfo=ET), [_raw(_D(1), 100, 101, 99, 100)])
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL
        assert classify_bar_session_attribution(bundle, bundle.bars[0]) == SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL

    @pytest.mark.parametrize("entry_policy,exit_policy", [
        ("ENTRY_BAR_INCLUDED_FULL", "EXIT_BAR_PARTIAL_UNKNOWN"),
        ("ENTRY_BAR_PARTIAL_UNKNOWN", "EXIT_BAR_INCLUDED_FULL"),
        ("ENTRY_BAR_PARTIAL_UNKNOWN", "EXIT_BAR_PARTIAL_UNKNOWN"),
    ])
    def test_all_other_recognized_same_day_combinations_are_partial_unknown(self, entry_policy, exit_policy):
        base = _us_bundle(_US_ENTRY_PARTIAL, dt.datetime(2026, 6, 1, 15, 0, tzinfo=ET), [_raw(_D(1), 100, 101, 99, 100)])
        forged = _forge_bundle(base, entry_bar_policy=entry_policy, exit_bar_policy=exit_policy)
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN
        assert classify_bar_session_attribution(forged, forged.bars[0]) == SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN

    def test_out_of_window_crossing_bar_fails(self):
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        out_of_window_bar = _forge_bar(base.bars[0], session_date=_D(10))
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(base, out_of_window_bar)

    def test_out_of_window_non_crossing_bar_also_fails(self):
        """Same assertion as above -- classify_bar_session_attribution
        has no concept of 'crossing' at all, so an out-of-window bar
        fails identically regardless of whether it would have crossed
        anything."""
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        out_of_window_bar = _forge_bar(base.bars[0], session_date=_D(11))
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(base, out_of_window_bar)


@pytest.mark.unit
class TestFunctionBoundaryTotality:
    """Stage J4B.2, Group B -- an object that is NOT a real
    PricePathEvidenceBundle/PricePathBar must never reach attribute
    access on classify_bar_session_attribution/
    observe_numerical_level_crossing; it must raise the governed typed
    error at the type-check gate, never a raw AttributeError."""

    def test_none_bundle_to_observe_numerical_level_crossing_raises_governed_error(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            observe_numerical_level_crossing(None, 120.0, TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_invalid_bundle_object_to_observe_numerical_level_crossing_raises_governed_error(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            observe_numerical_level_crossing("not a bundle", 120.0, TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_none_bundle_to_classify_bar_session_attribution_raises_session_attribution_error(self):
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(None, _FakeBar(session_date=_D(1)))

    def test_invalid_bundle_to_classify_bar_session_attribution_raises_session_attribution_error(self):
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(_FakeBundle(
                requested_window_start=_D(1), requested_window_end=_D(4),
                entry_bar_policy="ENTRY_BAR_INCLUDED_FULL", exit_bar_policy="EXIT_BAR_INCLUDED_FULL",
            ), _FakeBar(session_date=_D(2)))

    def test_invalid_bar_to_classify_bar_session_attribution_raises_session_attribution_error(self):
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(base, "not a bar")

    def test_unhashable_attribution_to_is_safely_attributable_raises_session_attribution_error_not_typeerror(self):
        with pytest.raises(SessionAttributionError):
            is_safely_attributable_session(["unhashable"])

    def test_invalid_attribution_not_rendered_into_message(self):
        with pytest.raises(SessionAttributionError) as exc_info:
            is_safely_attributable_session("DO_NOT_RENDER_SECRET_CANARY")
        assert "DO_NOT_RENDER_SECRET_CANARY" not in str(exc_info.value)

    def test_stored_chronological_order_used_without_sorting(self):
        """observe_numerical_level_crossing must not call sorted() on
        bundle.bars -- proven both structurally (source inspection) and
        behaviorally (a bundle's own already-ascending bars are scanned
        in that exact order)."""
        import inspect
        from services.postmortem import price_path_calculator
        source = inspect.getsource(price_path_calculator.observe_numerical_level_crossing)
        assert "sorted(bundle.bars" not in source

        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        assert [b.session_date for b in bundle.bars] == [_D(1), _D(2), _D(3), _D(4)]
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(2)


# ============================================================================
# GAP SEMANTICS (strict)
# ============================================================================
@pytest.mark.unit
class TestStrictGapSemantics:
    def _bar(self, open_, high=200, low=1):
        return PricePathBar(
            timestamp=dt.datetime(2026, 6, 2, tzinfo=ET), interval="1d",
            open=open_, high=high, low=low, close=open_, volume=1000.0,
            session_date=_D(2), source_id="yfinance_daily",
            adjustment_basis=UNADJUSTED, verification_level="DIRECTLY_OBSERVED",
        )

    def test_target_open_greater_than_value_is_gap_through(self):
        assert _bar_crossing_type(self._bar(130.0), TARGET_VALUE, 120.0) == CROSSING_TYPE_GAP_THROUGH

    def test_target_open_equal_to_value_is_normal(self):
        assert _bar_crossing_type(self._bar(120.0), TARGET_VALUE, 120.0) == CROSSING_TYPE_NORMAL

    def test_stop_open_lower_than_value_is_gap_through(self):
        assert _bar_crossing_type(self._bar(70.0), STOP_VALUE, 80.0) == CROSSING_TYPE_GAP_THROUGH

    def test_stop_open_equal_to_value_is_normal(self):
        assert _bar_crossing_type(self._bar(80.0), STOP_VALUE, 80.0) == CROSSING_TYPE_NORMAL

    def test_end_to_end_target_open_equal_to_value_yields_normal_observation(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 120, 150, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_crossing_type == CROSSING_TYPE_NORMAL


# ============================================================================
# OBSERVATION CONTEXT
# ============================================================================
@pytest.mark.unit
class TestObservationContext:
    def _valid_kwargs(self, **overrides):
        kwargs = dict(
            paper_trade_id=1, symbol="AAPL", market="US",
            evidence_bundle_version="1.0.0", source_id="yfinance_daily", source_version="1.1.0",
            evidence_hash="a" * 64, source_manifest_integrity_hash="b" * 64,
            bar_interval="1d", price_adjustment_basis=UNADJUSTED, market_timezone="America/New_York",
            requested_window_start=_D(1), requested_window_end=_D(4),
            entry_bar_policy="ENTRY_BAR_INCLUDED_FULL", exit_bar_policy="EXIT_BAR_INCLUDED_FULL",
        )
        kwargs.update(overrides)
        return kwargs

    def test_valid_context_constructed_from_india_bundle(self):
        bars = [_raw(_D(1), 2800, 2850, 2790, 2820), _raw(_D(2), 2800, 2900, 2790, 2820),
                _raw(_D(3), 2800, 2850, 2790, 2820), _raw(_D(4), 2800, 2850, 2790, 2820)]
        bundle = _in_bundle(_IN_ENTRY_INTERIOR, _IN_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 2870.0, TARGET_VALUE)
        assert obs.context.market == "IN"
        assert obs.context.paper_trade_id == 1

    def test_valid_context_constructed_from_us_bundle(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        obs = observe_numerical_level_crossing(bundle, None, TARGET_VALUE)
        assert obs.context.market == "US"

    def test_user_id_absent_from_context(self):
        assert not hasattr(NumericalCrossingObservationContext, "user_id")
        fields = {f.name for f in dataclasses.fields(NumericalCrossingObservationContext)}
        assert "user_id" not in fields

    def test_invalid_paper_trade_id_rejected(self):
        for bad in (0, -1, True, "1", 1.5, None):
            with pytest.raises(NumericalCrossingContractError) as exc_info:
                NumericalCrossingObservationContext(**self._valid_kwargs(paper_trade_id=bad))
            assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_malformed_evidence_hash_rejected(self):
        for bad in ("short", "A" * 64, "g" * 64, 12345, None):
            with pytest.raises(NumericalCrossingContractError) as exc_info:
                NumericalCrossingObservationContext(**self._valid_kwargs(evidence_hash=bad))
            assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_malformed_manifest_hash_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            NumericalCrossingObservationContext(**self._valid_kwargs(source_manifest_integrity_hash="not-a-hash"))
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_invalid_market_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            NumericalCrossingObservationContext(**self._valid_kwargs(market="UK"))
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_invalid_requested_window_ordering_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            NumericalCrossingObservationContext(**self._valid_kwargs(requested_window_start=_D(4), requested_window_end=_D(1)))
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_invalid_boundary_policy_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            NumericalCrossingObservationContext(**self._valid_kwargs(entry_bar_policy="MADE_UP"))
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT


# ============================================================================
# EVIDENCE-ID CONTRACT
# ============================================================================
@pytest.mark.unit
class TestEvidenceIdContract:
    def test_canonical_id_format(self):
        assert _crossing_evidence_id(42, _D(2), TARGET_VALUE) == f"NUMERICAL-CROSSING-42-{_D(2).isoformat()}-TARGET_VALUE"

    def test_evidence_id_never_contains_symbol_or_market(self):
        eid = _crossing_evidence_id(42, _D(2), TARGET_VALUE)
        assert "AAPL" not in eid and "US" not in eid.split("-")


# ============================================================================
# OBSERVATION DATACLASS INVARIANTS
# ============================================================================
@pytest.mark.unit
class TestObservationInvariants:
    def _real_observation_and_context(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        return obs, bundle

    def test_no_value_observation_carrying_a_value_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(obs, value_supplied=False, supplied_level_value=120.0)
        assert exc_info.value.reason_code == INCONSISTENT_CROSSING_OBSERVATION

    def test_no_value_observation_claiming_a_crossing_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, value_supplied=False, supplied_level_value=None, crossed_anywhere=True)

    def test_crossed_observation_without_bar_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_bar=None)

    def test_crossed_observation_without_session_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session=None)

    def test_crossed_observation_without_evidence_id_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_evidence_id=None)

    def test_session_differing_from_bar_session_date_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session=_D(3))

    def test_evidence_id_trade_mismatch_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(obs, first_observed_evidence_id=f"NUMERICAL-CROSSING-999-{_D(2).isoformat()}-TARGET_VALUE")
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_evidence_id_date_mismatch_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(obs, first_observed_evidence_id=f"NUMERICAL-CROSSING-1-{_D(3).isoformat()}-TARGET_VALUE")
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_evidence_id_level_kind_mismatch_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(obs, first_observed_evidence_id=f"NUMERICAL-CROSSING-1-{_D(2).isoformat()}-STOP_VALUE")
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_bar_source_differing_from_context_source_rejected(self):
        obs, _ = self._real_observation_and_context()
        forged_bar = dataclasses.replace(obs.first_observed_bar, source_id="some_other_source")
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_bar=forged_bar)

    def test_attached_target_bar_that_does_not_cross_rejected(self):
        obs, _ = self._real_observation_and_context()
        non_crossing_bar = dataclasses.replace(obs.first_observed_bar, high=110.0)
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_bar=non_crossing_bar)

    def test_forged_gap_through_inconsistent_with_bar_open_rejected(self):
        obs, _ = self._real_observation_and_context()
        assert obs.first_observed_crossing_type == CROSSING_TYPE_NORMAL
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_crossing_type=CROSSING_TYPE_GAP_THROUGH)

    def test_safe_group_with_partial_attribution_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(
                obs,
                first_safely_attributable_session=obs.first_observed_session,
                first_safely_attributable_bar=obs.first_observed_bar,
                first_safely_attributable_evidence_id=obs.first_observed_evidence_id,
                first_safely_attributable_session_attribution=SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN,
                first_safely_attributable_crossing_type=obs.first_observed_crossing_type,
            )

    def test_safe_session_earlier_than_first_observed_session_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(
                obs,
                first_safely_attributable_session=_D(1),
                first_safely_attributable_bar=dataclasses.replace(obs.first_observed_bar, session_date=_D(1)),
                first_safely_attributable_evidence_id=f"NUMERICAL-CROSSING-1-{_D(1).isoformat()}-TARGET_VALUE",
                first_safely_attributable_session_attribution=SESSION_ATTRIBUTION_INTERIOR,
                first_safely_attributable_crossing_type=CROSSING_TYPE_NORMAL,
            )

    def test_first_observed_safe_but_safe_group_differs_rejected(self):
        """The first-observed crossing IS itself safely attributable
        (interior bar) -- the safe group must identify that SAME bar,
        not a different one."""
        obs, _ = self._real_observation_and_context()
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_INTERIOR
        other_bar = PricePathBar(
            timestamp=dt.datetime(2026, 6, 3, tzinfo=ET), interval="1d",
            open=100, high=150, low=99, close=100, volume=1000.0,
            session_date=_D(3), source_id=obs.context.source_id,
            adjustment_basis=UNADJUSTED, verification_level="DIRECTLY_OBSERVED",
        )
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(
                obs,
                first_safely_attributable_session=_D(3), first_safely_attributable_bar=other_bar,
                first_safely_attributable_evidence_id=f"NUMERICAL-CROSSING-1-{_D(3).isoformat()}-TARGET_VALUE",
                first_safely_attributable_session_attribution=SESSION_ATTRIBUTION_INTERIOR,
                first_safely_attributable_crossing_type=CROSSING_TYPE_NORMAL,
            )

    def test_partial_boolean_without_partial_group_rejected(self):
        obs, _ = self._real_observation_and_context()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, partial_boundary_crossing_observed=True)

    def test_partial_group_with_false_partial_boolean_rejected(self):
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 130, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.partial_boundary_crossing_observed is True
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, partial_boundary_crossing_observed=False)


# ============================================================================
# RETENTION (partial-then-safe / safe-then-partial)
# ============================================================================
@pytest.mark.unit
class TestRetentionAcrossHardening:
    def test_partial_entry_crossing_followed_by_safe_interior_crossing_retains_both(self):
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 130, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(1)
        assert obs.first_partial_boundary_session == _D(1)
        assert obs.first_safely_attributable_session == _D(2)
        assert obs.partial_boundary_crossing_observed is True

    def test_safe_interior_crossing_followed_by_partial_exit_crossing_retains_both(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN
        from tests.unit.test_price_path_observed_crossing import _US_EXIT_PARTIAL
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 130, 99, 100), _raw(_D(4), 100, 150, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_PARTIAL, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(3)
        assert obs.first_safely_attributable_session == _D(3)
        assert obs.first_partial_boundary_session == _D(4)
        assert obs.first_partial_boundary_session_attribution == SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN
        assert obs.partial_boundary_crossing_observed is True

    def test_first_partial_field_identifies_the_earliest_partial_crossing(self):
        from tests.unit.test_price_path_observed_crossing import _US_EXIT_PARTIAL
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 150, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_PARTIAL, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_partial_boundary_session == _D(1)
        assert obs.first_safely_attributable_session is None


# ============================================================================
# SUMMARY INVARIANTS / ANTI-MIXING
# ============================================================================
@pytest.mark.unit
class TestSummaryAntiMixing:
    def _observations(self, paper_trade_id=1):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars, paper_trade_id=paper_trade_id)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        return target, stop

    def test_reversed_target_stop_inputs_rejected(self):
        target, stop = self._observations()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            summarize_observed_numerical_crossings(stop, target)
        assert exc_info.value.reason_code == MIXED_OBSERVATION_CONTEXT

    def test_different_trades_rejected(self):
        target, _ = self._observations(paper_trade_id=1)
        _, stop2 = self._observations(paper_trade_id=2)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            summarize_observed_numerical_crossings(target, stop2)
        assert exc_info.value.reason_code == MIXED_OBSERVATION_CONTEXT

    def test_different_symbols_rejected(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle_a = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        from services.postmortem.price_path_acquisition import build_price_path_evidence
        bundle_b = build_price_path_evidence(
            paper_trade_id=1, user_id="user-aaa", symbol="MSFT", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=_US_ENTRY_INTERIOR, exit_timestamp=_US_EXIT_INTERIOR,
            raw_bars=bars, split_events=[],
        )
        target = observe_numerical_level_crossing(bundle_a, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle_b, 80.0, STOP_VALUE)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            summarize_observed_numerical_crossings(target, stop)
        assert exc_info.value.reason_code == MIXED_OBSERVATION_CONTEXT

    def test_different_requested_windows_rejected(self):
        bars4 = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                 _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle_a = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars4)
        bars5 = bars4 + [_raw(_D(5), 100, 101, 99, 100)]
        bundle_b = _us_bundle(_US_ENTRY_INTERIOR, dt.datetime(2026, 6, 5, 16, 0, tzinfo=ET), bars5)
        target = observe_numerical_level_crossing(bundle_a, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle_b, 80.0, STOP_VALUE)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            summarize_observed_numerical_crossings(target, stop)
        assert exc_info.value.reason_code == MIXED_OBSERVATION_CONTEXT

    def test_no_values_pattern_with_one_supplied_value_rejected_by_construction(self):
        """A forged ObservedNumericalCrossingSummary claiming
        NO_NUMERICAL_VALUES_SUPPLIED while one observation actually has
        a supplied value must fail closed."""
        target, stop = self._observations()
        no_value_target = observe_numerical_level_crossing(
            _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars()), None, TARGET_VALUE,
        )
        real_summary = summarize_observed_numerical_crossings(target, stop)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(real_summary, target_observation=no_value_target, any_observation_pattern="NO_NUMERICAL_VALUES_SUPPLIED")
        assert exc_info.value.reason_code in (MIXED_OBSERVATION_CONTEXT, INCONSISTENT_CROSSING_SUMMARY)

    def test_partial_summary_flag_mismatch_rejected(self):
        target, stop = self._observations()
        real_summary = summarize_observed_numerical_crossings(target, stop)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(real_summary, partial_boundary_observation_present=not real_summary.partial_boundary_observation_present)
        assert exc_info.value.reason_code == INCONSISTENT_CROSSING_SUMMARY

    def test_forged_pattern_value_rejected(self):
        target, stop = self._observations()
        real_summary = summarize_observed_numerical_crossings(target, stop)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(real_summary, any_observation_pattern="TARGET_ONLY")  # governed conclusion term, never valid here
        assert exc_info.value.reason_code == INCONSISTENT_CROSSING_SUMMARY


# ============================================================================
# REGRESSION AND COMPATIBILITY (J4B.1 on top of J4B)
# ============================================================================
@pytest.mark.unit
class TestJ4B1RegressionCompatibility:
    def test_all_existing_j4b_tests_still_collect_and_pass(self):
        """Structural smoke check: the full pre-existing J4B test module
        still imports and its fixtures still construct without error
        under the hardened implementation (the real proof is the full
        test-file run in CI/local verification, not this one test)."""
        import tests.unit.test_price_path_observed_crossing as j4b_tests
        assert hasattr(j4b_tests, "observe_numerical_level_crossing")

    def test_india_exact_open_close_observation_unchanged(self):
        bars = [_raw(_D(1), 2800, 2900, 2790, 2820), _raw(_D(2), 2800, 2850, 2790, 2820),
                _raw(_D(3), 2800, 2850, 2790, 2820), _raw(_D(4), 2800, 2850, 2790, 2820)]
        bundle = _in_bundle(_IN_ENTRY_INTERIOR, _IN_EXIT_INTERIOR, bars)
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        obs = observe_numerical_level_crossing(bundle, 2870.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL

    def test_us_exact_open_close_observation_unchanged(self):
        bars = [_raw(_D(1), 190, 210, 188, 192), _raw(_D(2), 190, 195, 188, 192),
                _raw(_D(3), 190, 195, 188, 192), _raw(_D(4), 190, 195, 188, 192)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        obs = observe_numerical_level_crossing(bundle, 205.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL

    def test_existing_touch_result_and_detect_touches_unchanged(self):
        from services.postmortem.price_path_calculator import detect_touches
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=80.0, applicable_target=120.0)
        assert target_touch.touched is True and target_touch.first_observed_bar.session_date == _D(2)
        assert stop_touch.touched is True and stop_touch.first_observed_bar.session_date == _D(2)

    def test_existing_classify_touch_order_unchanged(self):
        from services.postmortem.price_path_calculator import LEVEL_HISTORY_INCOMPLETE, classify_touch_order, detect_touches
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=80.0, applicable_target=120.0)
        order = classify_touch_order(target_touch, stop_touch, applicable_stop=80.0, applicable_target=120.0, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE

    def test_all_six_version_constants_unchanged(self):
        from services.postmortem.price_path_identity import (
            BOUNDARY_POLICY_VERSION, CALCULATION_RULES_VERSION, EVIDENCE_BUNDLE_SCHEMA_VERSION,
            PRICE_PATH_REPORT_SCHEMA_VERSION, SOURCE_MANIFEST_SCHEMA_VERSION, SOURCE_VERSION,
        )
        assert (EVIDENCE_BUNDLE_SCHEMA_VERSION, SOURCE_VERSION, SOURCE_MANIFEST_SCHEMA_VERSION,
                BOUNDARY_POLICY_VERSION, PRICE_PATH_REPORT_SCHEMA_VERSION, CALCULATION_RULES_VERSION) == (
            "1.0.0", "1.1.0", "1.0.0", "1.0.0", "1.1.0", "1.0.0",
        )

    def test_price_path_calculator_still_does_not_import_generation_or_claims(self):
        import inspect
        from services.postmortem import price_path_calculator
        source = inspect.getsource(price_path_calculator)
        for forbidden_module in ("price_path_generation", "price_path_claims"):
            assert f"import {forbidden_module}" not in source
