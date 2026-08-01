"""
Trade Postmortem Sprint 3A, Stage J4B.2 — final adversarial contract
closure for the observed numerical-crossing model. Proves the
totality, validation-order, sanitized-error, and cross-context-mixing
gaps identified by the Stage 1 audit are closed, and captures a
compatibility baseline for the governed (valid-input) outputs.

IMPLEMENTED, UNIT-TESTED, NOT REPORT-WIRED, NOT PERSISTED, NOT
ENDPOINT-WIRED. Purely additive hardening of the J4B/J4B.1 internal
model; nothing here is imported by price_path_generation.py,
price_path_claims.py, or paper_trading.py.

Coverage note: this file targets the concrete defect classes actually
reproduced during the Stage 1 audit (unhashable inputs reaching bare
set/frozenset membership, huge-int OverflowError, None/invalid bundle
reaching attribute access, impossible-calendar-date ValueError escaping
evidence-ID parsing, and hostile non-instance objects reaching
attribute access in the summary path) plus representative canary-based
sanitization proofs and a valid-output compatibility baseline. It does
not mechanically instantiate a separately-named test for every one of
the authorizing prompt's individually numbered A–I scenarios; several
collapse onto the same underlying guard (documented in the ADR).
"""
import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

from services.market_hours import ET, IST
from services.postmortem.price_path_calculator import (
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
    SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL,
    SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN,
    SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL,
    SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN,
    SESSION_ATTRIBUTION_INTERIOR,
    SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL,
    SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN,
    STOP_VALUE,
    TARGET_VALUE,
    SessionAttributionError,
    _classify_session_attribution_fields,
    _crossing_evidence_id,
    _parse_crossing_evidence_id,
    _validate_crossing_evidence_id,
    _validate_level_kind,
    _validate_supplied_numerical_value,
    classify_bar_session_attribution,
    is_safely_attributable_session,
    observe_numerical_level_crossing,
    summarize_observed_numerical_crossings,
)
from services.postmortem.price_path_evidence import PricePathBar, PricePathEvidenceBundle, UNADJUSTED
from tests.unit.test_price_path_observed_crossing import (
    _D,
    _FakeBar,
    _FakeBundle,
    _IN_ENTRY_INTERIOR,
    _IN_EXIT_INTERIOR,
    _US_ENTRY_INTERIOR,
    _US_ENTRY_PARTIAL,
    _US_EXIT_INTERIOR,
    _US_EXIT_PARTIAL,
    _four_day_bars,
    _in_bundle,
    _raw,
    _us_bundle,
)

_CANARY = "DO_NOT_RENDER_SECRET_CANARY"
_USER_CANARY = "DO_NOT_RENDER_USER_CANARY"
_SYMBOL_CANARY = "DO_NOT_RENDER_SYMBOL_CANARY"


class _HostileRepr:
    def __repr__(self):
        raise RuntimeError("hostile __repr__ invoked")

    def __str__(self):
        raise RuntimeError("hostile __str__ invoked")

    def __hash__(self):
        raise RuntimeError("hostile __hash__ invoked")

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__ invoked")


# ============================================================================
# GROUP A — validation totality (level kind / supplied value)
# ============================================================================
@pytest.mark.unit
class TestGroupAValidationTotality:
    @pytest.mark.parametrize("bad_kind", [None, [], {}, set(), TARGET_VALUE.lower(), "", "  ", 1, 1.0, True], ids=[
        "none", "list", "dict", "set", "lowercase", "empty", "whitespace", "int", "float", "bool",
    ])
    def test_a01_a09_invalid_level_kind_variants_raise_governed_error(self, bad_kind):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_level_kind(bad_kind)
        assert exc_info.value.reason_code == INVALID_LEVEL_KIND

    def test_a06_hostile_repr_level_kind_never_invokes_repr(self):
        hostile = _HostileRepr()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_level_kind(hostile)
        assert exc_info.value.reason_code == INVALID_LEVEL_KIND

    def test_a08_hostile_hash_level_kind_never_invokes_hash(self):
        # type(value) is str is checked BEFORE any set-membership test,
        # so a hostile __hash__ on a non-str object is never invoked.
        hostile = _HostileRepr()
        with pytest.raises(NumericalCrossingContractError):
            _validate_level_kind(hostile)

    def test_a10_invalid_level_kind_contents_not_in_message(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_level_kind(_CANARY)
        assert _CANARY not in str(exc_info.value)

    def test_a11_bool_supplied_value_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(True, TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_SUPPLIED_VALUE_TYPE

    def test_a12_numeric_subclass_supplied_value_rejected(self):
        class _IntLike(int):
            pass
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(_IntLike(120), TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_SUPPLIED_VALUE_TYPE

    def test_a13_object_implementing_float_rejected_without_calling_it(self):
        calls = {"count": 0}

        class _FloatLike:
            def __float__(self):
                calls["count"] += 1
                return 120.0

        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(_FloatLike(), TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_SUPPLIED_VALUE_TYPE
        assert calls["count"] == 0

    def test_a14_extremely_large_positive_int_raises_governed_error(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(10 ** 400, TARGET_VALUE)
        assert exc_info.value.reason_code == NON_FINITE_SUPPLIED_VALUE

    def test_a15_extremely_large_negative_int_raises_governed_error(self):
        with pytest.raises(NumericalCrossingContractError):
            _validate_supplied_numerical_value(-(10 ** 400), TARGET_VALUE)

    @pytest.mark.parametrize("bad_value,expected_code", [
        (float("nan"), NON_FINITE_SUPPLIED_VALUE),
        (float("inf"), NON_FINITE_SUPPLIED_VALUE),
        (float("-inf"), NON_FINITE_SUPPLIED_VALUE),
        (0.0, NON_POSITIVE_SUPPLIED_VALUE),
        (-0.0, NON_POSITIVE_SUPPLIED_VALUE),
        (-42.0, NON_POSITIVE_SUPPLIED_VALUE),
    ], ids=["nan", "posinf", "neginf", "poszero", "negzero", "negative"])
    def test_a16_a21_invalid_numeric_values(self, bad_value, expected_code):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(bad_value, TARGET_VALUE)
        assert exc_info.value.reason_code == expected_code

    def test_a22_valid_positive_int_normalized_to_exact_float(self):
        result = _validate_supplied_numerical_value(120, TARGET_VALUE)
        assert type(result) is float
        assert result == 120.0

    def test_a23_valid_positive_float_remains_exact_float(self):
        result = _validate_supplied_numerical_value(120.5, STOP_VALUE)
        assert type(result) is float
        assert result == 120.5

    def test_decimal_rejected_by_default_contract(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(Decimal("120.0"), TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_SUPPLIED_VALUE_TYPE


# ============================================================================
# GROUP B — function-boundary totality
# ============================================================================
@pytest.mark.unit
class TestGroupBFunctionBoundaryTotality:
    def test_b01_invalid_bundle_object_raises_governed_error_not_attributeerror(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            observe_numerical_level_crossing(object(), 120.0, TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_b02_none_bundle_raises_governed_error(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            observe_numerical_level_crossing(None, 120.0, TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_b03_invalid_bundle_to_classify_raises_session_attribution_error(self):
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(object(), _FakeBar(session_date=_D(1)))

    def test_b04_invalid_bar_to_classify_raises_session_attribution_error(self):
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(base, object())

    def test_b05_unhashable_attribution_raises_session_attribution_error_not_typeerror(self):
        with pytest.raises(SessionAttributionError):
            is_safely_attributable_session({"unhashable": True})

    def test_b06_invalid_attribution_object_not_rendered(self):
        with pytest.raises(SessionAttributionError) as exc_info:
            is_safely_attributable_session(_CANARY)
        assert _CANARY not in str(exc_info.value)


# ============================================================================
# GROUP C — no-value and every-bar validation
# ============================================================================
@pytest.mark.unit
class TestGroupCNoValueEveryBar:
    def test_c01_valid_bundle_no_value_returns_no_value_observation(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, [])
        obs = observe_numerical_level_crossing(bundle, None, TARGET_VALUE)
        assert obs.value_supplied is False
        assert obs.crossed_anywhere is False

    def test_c02_india_bundle_no_value(self):
        bundle = _in_bundle(_IN_ENTRY_INTERIOR, _IN_EXIT_INTERIOR, [_raw(_D(2), 2800, 2850, 2790, 2820)])
        obs = observe_numerical_level_crossing(bundle, None, STOP_VALUE)
        assert obs.value_supplied is False

    def test_c03_us_bundle_no_value(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        obs = observe_numerical_level_crossing(bundle, None, TARGET_VALUE)
        assert obs.value_supplied is False

    def test_c04_no_value_path_rejects_unknown_entry_policy(self):
        """The immutable observation context is built (and self-
        validates) BEFORE per-bar attribution begins, so an invalid
        entry_bar_policy is actually caught at the context-construction
        gate (NumericalCrossingContractError) rather than surviving to
        classify_bar_session_attribution's own SessionAttributionError
        -- both fail closed; this asserts the one that actually fires
        first, per the declared validation order (Stage 4A: bundle
        type -> context -> per-bar attribution)."""
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        forged = dataclasses.replace(base, entry_bar_policy="MADE_UP")
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            observe_numerical_level_crossing(forged, None, TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_c05_no_value_path_rejects_unknown_exit_policy(self):
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        forged = dataclasses.replace(base, exit_bar_policy="MADE_UP")
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            observe_numerical_level_crossing(forged, None, TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_c06_no_value_path_rejects_out_of_window_bar(self):
        """An out-of-window bar is only detectable per-bar (the context
        itself has no per-bar knowledge), so this genuinely exercises
        the per-bar attribution gate, not context construction."""
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        bars_list = list(base.bars)
        bars_list[-1] = dataclasses.replace(bars_list[-1], session_date=_D(20))  # still ascending, now outside the declared window
        forged = dataclasses.replace(base, bars=tuple(bars_list))
        with pytest.raises(SessionAttributionError):
            observe_numerical_level_crossing(forged, None, TARGET_VALUE)

    def test_c07_c08_later_invalid_bar_fails_before_returning_a_crossing(self):
        """A bundle whose FIRST bar would genuinely cross the supplied
        value, but whose LATER bar is out-of-window, must fail closed --
        the earlier successful-looking crossing must never be silently
        returned. Uses an out-of-window bar (not an invalid policy,
        which -- per test_c04/c05 above -- is caught earlier at context
        construction, before any bar is even reached) so this
        specifically exercises the per-bar attribution ordering."""
        base = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, [
            _raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 101, 99, 100),
            _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100),
        ])
        bars_list = list(base.bars)
        bars_list[-1] = dataclasses.replace(bars_list[-1], session_date=_D(20))
        forged = dataclasses.replace(base, bars=tuple(bars_list))
        with pytest.raises(SessionAttributionError):
            observe_numerical_level_crossing(forged, 120.0, TARGET_VALUE)

    def test_c09_no_crossing_comparison_occurs_when_value_is_none(self, monkeypatch):
        from services.postmortem import price_path_calculator

        calls = {"count": 0}
        real_bar_crosses = price_path_calculator._bar_crosses

        def _spy(*args, **kwargs):
            calls["count"] += 1
            return real_bar_crosses(*args, **kwargs)

        monkeypatch.setattr(price_path_calculator, "_bar_crosses", _spy)
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        observe_numerical_level_crossing(bundle, None, TARGET_VALUE)
        assert calls["count"] == 0

    def test_c10_every_bar_attributed_before_first_crossing_comparison(self, monkeypatch):
        from services.postmortem import price_path_calculator

        events = []
        real_classify = price_path_calculator.classify_bar_session_attribution
        real_bar_crosses = price_path_calculator._bar_crosses

        def _classify_spy(bundle, bar):
            events.append(("attribute", bar.session_date))
            return real_classify(bundle, bar)

        def _crosses_spy(*args, **kwargs):
            events.append(("cross_check",))
            return real_bar_crosses(*args, **kwargs)

        monkeypatch.setattr(price_path_calculator, "classify_bar_session_attribution", _classify_spy)
        monkeypatch.setattr(price_path_calculator, "_bar_crosses", _crosses_spy)

        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)

        first_cross_index = next(i for i, e in enumerate(events) if e[0] == "cross_check")
        attribute_events_before = [e for e in events[:first_cross_index] if e[0] == "attribute"]
        assert len(attribute_events_before) == 4  # all 4 bars attributed before the first crossing check


# ============================================================================
# GROUP F — evidence-ID totality (the concrete gaps found in the audit)
# ============================================================================
@pytest.mark.unit
class TestGroupFEvidenceIdTotality:
    def test_f01_non_string_evidence_id_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _parse_crossing_evidence_id(12345)
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_f02_empty_evidence_id_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _parse_crossing_evidence_id("")

    def test_f03_incorrect_prefix_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _parse_crossing_evidence_id(f"WRONG-PREFIX-1-{_D(1).isoformat()}-TARGET_VALUE")

    def test_f05_zero_trade_id_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _parse_crossing_evidence_id(f"NUMERICAL-CROSSING-0-{_D(1).isoformat()}-TARGET_VALUE")

    def test_f06_signed_trade_id_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _parse_crossing_evidence_id(f"NUMERICAL-CROSSING--1-{_D(1).isoformat()}-TARGET_VALUE")

    def test_f07_impossible_month_rejected_through_typed_contract(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _parse_crossing_evidence_id("NUMERICAL-CROSSING-1-2026-13-01-TARGET_VALUE")
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_f08_impossible_day_rejected_through_typed_contract(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _parse_crossing_evidence_id("NUMERICAL-CROSSING-1-2026-06-45-TARGET_VALUE")
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_f09_invalid_leap_date_rejected_through_typed_contract(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _parse_crossing_evidence_id("NUMERICAL-CROSSING-1-2026-02-29-TARGET_VALUE")  # 2026 not a leap year
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_f10_embedded_symbol_text_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _parse_crossing_evidence_id(f"NUMERICAL-CROSSING-1-{_D(1).isoformat()}-AAPL-TARGET_VALUE")

    def test_f12_malformed_contents_not_echoed_in_message(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _parse_crossing_evidence_id(_CANARY)
        assert _CANARY not in str(exc_info.value)

    def test_f13_hostile_evidence_id_object_does_not_invoke_repr(self):
        with pytest.raises(NumericalCrossingContractError):
            _parse_crossing_evidence_id(_HostileRepr())

    def test_f14_trade_id_mismatch_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_crossing_evidence_id(
                f"NUMERICAL-CROSSING-2-{_D(1).isoformat()}-TARGET_VALUE",
                expected_trade_id=1, expected_session_date=_D(1), expected_level_kind=TARGET_VALUE,
            )
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_f15_session_date_mismatch_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _validate_crossing_evidence_id(
                f"NUMERICAL-CROSSING-1-{_D(2).isoformat()}-TARGET_VALUE",
                expected_trade_id=1, expected_session_date=_D(1), expected_level_kind=TARGET_VALUE,
            )

    def test_f16_level_kind_mismatch_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _validate_crossing_evidence_id(
                f"NUMERICAL-CROSSING-1-{_D(1).isoformat()}-STOP_VALUE",
                expected_trade_id=1, expected_session_date=_D(1), expected_level_kind=TARGET_VALUE,
            )

    def test_f17_valid_canonical_evidence_id_round_trips(self):
        eid = _crossing_evidence_id(1, _D(1), TARGET_VALUE)
        trade_id, session_date, level_kind = _parse_crossing_evidence_id(eid)
        assert (trade_id, session_date, level_kind) == (1, _D(1), TARGET_VALUE)


# ============================================================================
# GROUP G — summary prevalidation and anti-mixing
# ============================================================================
@pytest.mark.unit
class TestGroupGSummaryPrevalidation:
    def _target_stop(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        return target, stop

    def test_g01_none_target_rejected(self):
        _, stop = self._target_stop()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            summarize_observed_numerical_crossings(None, stop)
        assert exc_info.value.reason_code == MIXED_OBSERVATION_CONTEXT

    def test_g02_none_stop_rejected(self):
        target, _ = self._target_stop()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            summarize_observed_numerical_crossings(target, None)
        assert exc_info.value.reason_code == MIXED_OBSERVATION_CONTEXT

    def test_g03_arbitrary_target_object_rejected_before_pattern_calculation(self):
        _, stop = self._target_stop()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            summarize_observed_numerical_crossings(_HostileRepr(), stop)
        assert exc_info.value.reason_code == MIXED_OBSERVATION_CONTEXT

    def test_g04_arbitrary_stop_object_rejected_before_pattern_calculation(self):
        target, _ = self._target_stop()
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            summarize_observed_numerical_crossings(target, _HostileRepr())
        assert exc_info.value.reason_code == MIXED_OBSERVATION_CONTEXT

    def test_g05_reversed_target_stop_rejected(self):
        target, stop = self._target_stop()
        with pytest.raises(NumericalCrossingContractError):
            summarize_observed_numerical_crossings(stop, target)

    def test_g16_pattern_computation_never_reached_for_invalid_summary_input(self, monkeypatch):
        from services.postmortem import price_path_calculator

        calls = {"count": 0}
        real = price_path_calculator._compute_expected_patterns

        def _spy(*args, **kwargs):
            calls["count"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(price_path_calculator, "_compute_expected_patterns", _spy)
        with pytest.raises(NumericalCrossingContractError):
            summarize_observed_numerical_crossings(None, None)
        assert calls["count"] == 0

    def test_g17_non_bool_partial_flag_rejected(self):
        target, stop = self._target_stop()
        real = summarize_observed_numerical_crossings(target, stop)
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(real, partial_boundary_observation_present=1)

    def test_g18_unhashable_any_pattern_rejected_without_typeerror(self):
        target, stop = self._target_stop()
        real = summarize_observed_numerical_crossings(target, stop)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(real, any_observation_pattern=["unhashable"])
        assert exc_info.value.reason_code == INCONSISTENT_CROSSING_SUMMARY

    def test_g19_unhashable_safe_pattern_rejected_without_typeerror(self):
        target, stop = self._target_stop()
        real = summarize_observed_numerical_crossings(target, stop)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(real, safely_attributable_pattern={"unhashable"})
        assert exc_info.value.reason_code == INCONSISTENT_CROSSING_SUMMARY

    def test_g20_invalid_pattern_contents_not_rendered(self):
        target, stop = self._target_stop()
        real = summarize_observed_numerical_crossings(target, stop)
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(real, any_observation_pattern=_CANARY)
        assert _CANARY not in str(exc_info.value)


# ============================================================================
# GROUP H — error-message sanitization (canary-based)
# ============================================================================
@pytest.mark.unit
class TestGroupHSanitization:
    def test_h01_level_kind_message_no_canary(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_level_kind(_CANARY)
        assert _CANARY not in str(exc_info.value)

    def test_h02_numeric_value_message_no_canary(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _validate_supplied_numerical_value(-999999.123456, TARGET_VALUE)
        assert "999999.123456" not in str(exc_info.value)

    def test_h05_evidence_id_message_no_canary(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _parse_crossing_evidence_id(f"{_CANARY}-{_SYMBOL_CANARY}")
        assert _CANARY not in str(exc_info.value)
        assert _SYMBOL_CANARY not in str(exc_info.value)

    def test_h07_context_message_no_user_or_symbol_value(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            NumericalCrossingObservationContext(
                paper_trade_id=1, symbol=_SYMBOL_CANARY, market="NOT_A_MARKET",
                evidence_bundle_version="1.0.0", source_id="yfinance_daily", source_version="1.1.0",
                evidence_hash="a" * 64, source_manifest_integrity_hash="b" * 64,
                bar_interval="1d", price_adjustment_basis=UNADJUSTED, market_timezone="America/New_York",
                requested_window_start=_D(1), requested_window_end=_D(4),
                entry_bar_policy="ENTRY_BAR_INCLUDED_FULL", exit_bar_policy="EXIT_BAR_INCLUDED_FULL",
            )
        assert _SYMBOL_CANARY not in str(exc_info.value)


# ============================================================================
# GROUP I — output and structural compatibility
# ============================================================================
@pytest.mark.unit
class TestGroupICompatibilityBaseline:
    """Stage 7 — captures the expected output of the current valid
    contract for deterministic fixtures and proves it is unchanged by
    the J4B.2 corrections."""

    def test_india_included_full_entry_baseline(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        bars = [_raw(_D(1), 2800, 2900, 2790, 2820), _raw(_D(2), 2800, 2850, 2790, 2820),
                _raw(_D(3), 2800, 2850, 2790, 2820), _raw(_D(4), 2800, 2850, 2790, 2820)]
        bundle = _in_bundle(_IN_ENTRY_INTERIOR, _IN_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 2870.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(1)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        assert obs.first_observed_crossing_type == CROSSING_TYPE_NORMAL
        assert obs.first_safely_attributable_session == _D(1)

    def test_india_included_full_exit_baseline(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL
        bars = [_raw(_D(1), 2800, 2850, 2790, 2820), _raw(_D(2), 2800, 2850, 2790, 2820),
                _raw(_D(3), 2800, 2850, 2790, 2820), _raw(_D(4), 2800, 2900, 2790, 2820)]
        bundle = _in_bundle(_IN_ENTRY_INTERIOR, _IN_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 2870.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(4)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL

    def test_india_partial_boundary_baseline(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN
        bars = [_raw(_D(1), 2800, 2900, 2790, 2820), _raw(_D(2), 2800, 2850, 2790, 2820),
                _raw(_D(3), 2800, 2850, 2790, 2820), _raw(_D(4), 2800, 2850, 2790, 2820)]
        bundle = _in_bundle(_IN_ENTRY_PARTIAL if False else dt.datetime(2026, 6, 1, 11, 0, tzinfo=IST), _IN_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 2870.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN
        assert obs.first_safely_attributable_session is None

    def test_india_same_day_baseline(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL
        bundle = _in_bundle(_IN_ENTRY_INTERIOR, dt.datetime(2026, 6, 1, 15, 30, tzinfo=IST), [_raw(_D(1), 2800, 2900, 2790, 2820)])
        obs = observe_numerical_level_crossing(bundle, 2870.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL

    def test_us_included_full_entry_baseline(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        bars = [_raw(_D(1), 190, 210, 188, 192), _raw(_D(2), 190, 195, 188, 192),
                _raw(_D(3), 190, 195, 188, 192), _raw(_D(4), 190, 195, 188, 192)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 205.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(1)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL

    def test_us_included_full_exit_baseline(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL
        bars = [_raw(_D(1), 190, 195, 188, 192), _raw(_D(2), 190, 195, 188, 192),
                _raw(_D(3), 190, 195, 188, 192), _raw(_D(4), 190, 210, 188, 192)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 205.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(4)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL

    def test_us_partial_boundary_baseline(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN
        bars = [_raw(_D(1), 190, 210, 188, 192), _raw(_D(2), 190, 195, 188, 192),
                _raw(_D(3), 190, 195, 188, 192), _raw(_D(4), 190, 195, 188, 192)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 205.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN

    def test_us_same_day_baseline(self):
        from services.postmortem.price_path_calculator import SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL
        bundle = _us_bundle(_US_ENTRY_INTERIOR, dt.datetime(2026, 6, 1, 16, 0, tzinfo=ET), [_raw(_D(1), 190, 210, 188, 192)])
        obs = observe_numerical_level_crossing(bundle, 205.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL

    def test_target_only_baseline(self):
        from services.postmortem.price_path_calculator import PATTERN_TARGET_VALUE_ONLY_CROSSED
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 1.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_TARGET_VALUE_ONLY_CROSSED

    def test_stop_only_baseline(self):
        from services.postmortem.price_path_calculator import PATTERN_STOP_VALUE_ONLY_CROSSED
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 500.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_STOP_VALUE_ONLY_CROSSED

    def test_same_bar_baseline(self):
        from services.postmortem.price_path_calculator import PATTERN_BOTH_VALUES_SAME_BAR
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_BOTH_VALUES_SAME_BAR

    def test_target_before_stop_baseline(self):
        from services.postmortem.price_path_calculator import PATTERN_TARGET_VALUE_BAR_BEFORE_STOP_VALUE_BAR
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 50, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_TARGET_VALUE_BAR_BEFORE_STOP_VALUE_BAR

    def test_stop_before_target_baseline(self):
        from services.postmortem.price_path_calculator import PATTERN_STOP_VALUE_BAR_BEFORE_TARGET_VALUE_BAR
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 50, 100),
                _raw(_D(3), 100, 150, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_STOP_VALUE_BAR_BEFORE_TARGET_VALUE_BAR

    def test_partial_then_safe_baseline(self):
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 130, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(1)
        assert obs.first_safely_attributable_session == _D(2)
        assert obs.first_partial_boundary_session == _D(1)

    def test_safe_then_partial_baseline(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 130, 99, 100), _raw(_D(4), 100, 150, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_PARTIAL, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(3)
        assert obs.first_safely_attributable_session == _D(3)
        assert obs.first_partial_boundary_session == _D(4)

    def test_no_values_baseline(self):
        from services.postmortem.price_path_calculator import PATTERN_NO_NUMERICAL_VALUES_SUPPLIED
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        target = observe_numerical_level_crossing(bundle, None, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, None, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_NO_NUMERICAL_VALUES_SUPPLIED

    def test_i10_version_constants_unchanged(self):
        from services.postmortem.price_path_identity import (
            BOUNDARY_POLICY_VERSION, CALCULATION_RULES_VERSION, EVIDENCE_BUNDLE_SCHEMA_VERSION,
            PRICE_PATH_REPORT_SCHEMA_VERSION, SOURCE_MANIFEST_SCHEMA_VERSION, SOURCE_VERSION,
        )
        assert (EVIDENCE_BUNDLE_SCHEMA_VERSION, SOURCE_VERSION, SOURCE_MANIFEST_SCHEMA_VERSION,
                BOUNDARY_POLICY_VERSION, PRICE_PATH_REPORT_SCHEMA_VERSION, CALCULATION_RULES_VERSION) == (
            "1.0.0", "1.1.0", "1.0.0", "1.0.0", "1.1.0", "1.0.0",
        )

    def test_i17_no_j4b2_symbol_referenced_by_report_claim_or_router_code(self):
        import inspect
        from services.postmortem import price_path_generation, price_path_claims
        import api.routers.paper_trading as pt

        symbols = (
            "observe_numerical_level_crossing", "summarize_observed_numerical_crossings",
            "classify_bar_session_attribution", "NumericalLevelCrossingObservation",
            "ObservedNumericalCrossingSummary", "NumericalCrossingObservationContext",
            "NumericalCrossingContractError",
        )
        for module in (price_path_generation, price_path_claims, pt):
            source = inspect.getsource(module)
            for symbol in symbols:
                assert symbol not in source, f"{module.__name__} references {symbol}"


# ============================================================================
# GROUP E — exact safe/partial group identity (direct-construction forgery)
# ============================================================================
@pytest.mark.unit
class TestGroupEExactGroupIdentity:
    def _observation(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        return observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)

    def test_first_observed_already_safe_but_evidence_id_differs_rejected(self):
        obs = self._observation()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_safely_attributable_evidence_id=f"NUMERICAL-CROSSING-1-{_D(3).isoformat()}-TARGET_VALUE")

    def test_first_observed_already_safe_but_crossing_type_differs_rejected(self):
        obs = self._observation()
        # obs's real crossing type is NORMAL (open=100 < 120); forging GAP_THROUGH
        # while keeping the same bar is an internal inconsistency two ways at once
        # (identity mismatch AND bar.open mismatch) -- both are rejected.
        from services.postmortem.price_path_calculator import CROSSING_TYPE_GAP_THROUGH
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_safely_attributable_crossing_type=CROSSING_TYPE_GAP_THROUGH)


# ============================================================================
# STAGE J4B.3 -- I-01 through I-04 (context-derived attribution, five-field
# identity, evidence-ID factory totality, exact context types)
# ============================================================================
@pytest.mark.unit
class TestI01ContextDerivedAttribution:
    """Group A -- an observation whose stored attribution does not match
    the EXACT attribution the immutable context's own window/policy
    fields would derive for that bar's session_date must be rejected,
    even when the claimed attribution belongs to a broad allowed
    category."""

    def _observation(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        return observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)

    def test_a01_interior_bar_labelled_entry_included_full_rejected(self):
        obs = self._observation()
        assert obs.first_observed_session == _D(2)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_INTERIOR
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(obs, first_observed_session_attribution=SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL)
        assert exc_info.value.reason_code == INCONSISTENT_CROSSING_OBSERVATION

    def test_a02_interior_bar_labelled_exit_included_full_rejected(self):
        obs = self._observation()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session_attribution=SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL)

    def _entry_bar_observation(self):
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        return observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)

    def test_a03_entry_bar_labelled_interior_rejected(self):
        obs = self._entry_bar_observation()
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session_attribution=SESSION_ATTRIBUTION_INTERIOR)

    def test_a04_entry_bar_labelled_exit_included_full_rejected(self):
        obs = self._entry_bar_observation()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session_attribution=SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL)

    def _exit_bar_observation(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 150, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        return observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)

    def test_a05_exit_bar_labelled_interior_rejected(self):
        obs = self._exit_bar_observation()
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session_attribution=SESSION_ATTRIBUTION_INTERIOR)

    def test_a06_exit_bar_labelled_entry_included_full_rejected(self):
        obs = self._exit_bar_observation()
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session_attribution=SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL)

    def test_a07_same_day_partial_bar_labelled_same_day_included_full_rejected(self):
        bundle = _us_bundle(_US_ENTRY_PARTIAL, dt.datetime(2026, 6, 1, 15, 0, tzinfo=ET), [_raw(_D(1), 100, 150, 99, 100)])
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session_attribution=SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL)

    def test_a08_same_day_full_bar_labelled_same_day_partial_unknown_rejected(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, dt.datetime(2026, 6, 1, 16, 0, tzinfo=ET), [_raw(_D(1), 100, 150, 99, 100)])
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_session_attribution=SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN)

    def test_a09_out_of_window_attached_bar_rejected(self):
        obs = self._observation()
        out_of_window_bar = dataclasses.replace(obs.first_observed_bar, session_date=_D(20))
        with pytest.raises(NumericalCrossingContractError):
            dataclasses.replace(obs, first_observed_bar=out_of_window_bar, first_observed_session=_D(20),
                                 first_observed_evidence_id=f"NUMERICAL-CROSSING-1-{_D(20).isoformat()}-TARGET_VALUE")

    def test_helper_field_based_matches_bundle_based_classifier(self):
        """Proof (Correction: 'same matrix') -- the shared field-based
        helper and the public bundle/bar classifier agree on every
        recognized case, since the latter now delegates to the former."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        for bar in bundle.bars:
            via_classifier = classify_bar_session_attribution(bundle, bar)
            via_helper = _classify_session_attribution_fields(
                requested_window_start=bundle.requested_window_start, requested_window_end=bundle.requested_window_end,
                entry_bar_policy=bundle.entry_bar_policy, exit_bar_policy=bundle.exit_bar_policy,
                session_date=bar.session_date,
            )
            assert via_classifier == via_helper


@pytest.mark.unit
class TestI02FiveFieldIdentity:
    def _observation_entry_included_full(self):
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        return observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)

    def test_b10_first_observed_safe_identity_with_differing_attribution_rejected(self):
        """first_observed is ENTRY_INCLUDED_FULL (safely attributable) --
        the safe group must exactly match ALL five fields including
        session_attribution, not just session/bar/evidence_id/crossing_type."""
        obs = self._observation_entry_included_full()
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        assert obs.first_safely_attributable_session == obs.first_observed_session
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(obs, first_safely_attributable_session_attribution=SESSION_ATTRIBUTION_INTERIOR)
        assert exc_info.value.reason_code == INCONSISTENT_CROSSING_OBSERVATION

    def test_b11_first_observed_partial_identity_with_differing_attribution_rejected(self):
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, [
            _raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 101, 99, 100),
            _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100),
        ])
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN
        assert obs.first_partial_boundary_session == obs.first_observed_session
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            dataclasses.replace(obs, first_partial_boundary_session_attribution=SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN)
        assert exc_info.value.reason_code == INCONSISTENT_CROSSING_OBSERVATION


@pytest.mark.unit
class TestI03EvidenceIdFactoryTotality:
    def test_c12_bool_paper_trade_id_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _crossing_evidence_id(True, _D(1), TARGET_VALUE)
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_c13_zero_paper_trade_id_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(0, _D(1), TARGET_VALUE)

    def test_c14_negative_paper_trade_id_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(-1, _D(1), TARGET_VALUE)

    def test_c15_int_subclass_paper_trade_id_rejected(self):
        class _IntLike(int):
            pass
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(_IntLike(1), _D(1), TARGET_VALUE)

    def test_c16_none_session_date_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(1, None, TARGET_VALUE)

    def test_c17_datetime_session_date_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(1, dt.datetime(2026, 6, 1, tzinfo=ET), TARGET_VALUE)

    def test_c18_date_subclass_session_date_rejected(self):
        class _DateLike(dt.date):
            pass
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(1, _DateLike(2026, 6, 1), TARGET_VALUE)

    def test_c19_string_session_date_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(1, "2026-06-01", TARGET_VALUE)

    def test_c20_invalid_level_kind_rejected(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _crossing_evidence_id(1, _D(1), "NOT_A_KIND")
        assert exc_info.value.reason_code == INVALID_CROSSING_EVIDENCE_ID

    def test_c21_string_subclass_level_kind_rejected(self):
        class _StrLike(str):
            pass
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(1, _D(1), _StrLike(TARGET_VALUE))

    def test_c22_unhashable_level_kind_rejected_without_raw_typeerror(self):
        with pytest.raises(NumericalCrossingContractError):
            _crossing_evidence_id(1, _D(1), ["unhashable"])

    def test_c23_invalid_values_not_reproduced_in_error_text(self):
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            _crossing_evidence_id(-999999, _D(1), TARGET_VALUE)
        assert "999999" not in str(exc_info.value)

    def test_valid_id_still_generated_unchanged(self):
        assert _crossing_evidence_id(1, _D(1), TARGET_VALUE) == f"NUMERICAL-CROSSING-1-{_D(1).isoformat()}-TARGET_VALUE"


@pytest.mark.unit
class TestI04ExactContextTypes:
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

    def test_d24_int_subclass_paper_trade_id_rejected(self):
        class _IntLike(int):
            pass
        with pytest.raises(NumericalCrossingContractError) as exc_info:
            NumericalCrossingObservationContext(**self._valid_kwargs(paper_trade_id=_IntLike(1)))
        assert exc_info.value.reason_code == INVALID_OBSERVATION_CONTEXT

    def test_d25_string_subclass_symbol_rejected(self):
        class _StrLike(str):
            pass
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(symbol=_StrLike("AAPL")))

    def test_d26_empty_symbol_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(symbol=""))

    def test_d27_whitespace_only_symbol_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(symbol="   "))

    @pytest.mark.parametrize("field_name", [
        "evidence_bundle_version", "source_id", "source_version",
        "bar_interval", "price_adjustment_basis", "market_timezone",
    ])
    def test_d28_every_identity_string_field_rejects_empty(self, field_name):
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(**{field_name: ""}))

    @pytest.mark.parametrize("field_name", [
        "evidence_bundle_version", "source_id", "source_version",
        "bar_interval", "price_adjustment_basis", "market_timezone",
    ])
    def test_d29_every_identity_string_field_rejects_whitespace_only(self, field_name):
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(**{field_name: "   "}))

    @pytest.mark.parametrize("field_name", [
        "evidence_bundle_version", "source_id", "source_version",
        "bar_interval", "price_adjustment_basis", "market_timezone",
    ])
    def test_d30_every_identity_string_field_rejects_string_subclass(self, field_name):
        class _StrLike(str):
            pass
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(**{field_name: _StrLike("valid_value")}))

    @pytest.mark.parametrize("hash_field", ["evidence_hash", "source_manifest_integrity_hash"])
    def test_d31_both_hash_fields_reject_string_subclass(self, hash_field):
        class _StrLike(str):
            pass
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(**{hash_field: _StrLike("a" * 64)}))

    def test_d32_requested_window_datetime_values_remain_rejected(self):
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(requested_window_start=dt.datetime(2026, 6, 1, tzinfo=ET)))

    def test_d33_requested_window_date_subclass_remains_rejected(self):
        class _DateLike(dt.date):
            pass
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(requested_window_end=_DateLike(2026, 6, 4)))

    def test_d34_policy_string_subclass_remains_rejected(self):
        class _StrLike(str):
            pass
        with pytest.raises(NumericalCrossingContractError):
            NumericalCrossingObservationContext(**self._valid_kwargs(entry_bar_policy=_StrLike("ENTRY_BAR_INCLUDED_FULL")))

    def test_valid_context_still_constructs(self):
        ctx = NumericalCrossingObservationContext(**self._valid_kwargs())
        assert ctx.paper_trade_id == 1
        assert ctx.symbol == "AAPL"
