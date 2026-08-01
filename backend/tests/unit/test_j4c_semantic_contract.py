"""
Trade Postmortem Sprint 3A, Wave A — Stage J4C semantic-contract tests.

Covers Gate 1M batches A and B: level-history status matrix, endpoint
evidence matrix, price-basis eligibility, governed touch/order
conclusions, and no-fabrication. Purely additive; nothing here is
imported by price_path_generation.py, price_path_claims.py, or
paper_trading.py.
"""
import dataclasses
import datetime as dt

import pytest

from services.market_hours import ET
from services.postmortem.price_path_acquisition import build_price_path_evidence
from services.postmortem.price_path_calculator import (
    STOP_VALUE,
    TARGET_VALUE,
    observe_numerical_level_crossing,
    summarize_observed_numerical_crossings,
)
from services.postmortem.level_history_eligibility import (
    ALL_ENDPOINT_EVIDENCE_STATUSES,
    ALL_LEVEL_HISTORY_STATUSES,
    ALL_PRICE_BASIS_STATUSES,
    ENDPOINT_EVIDENCE_BOTH_SNAPSHOTS_MISSING,
    ENDPOINT_EVIDENCE_DIFFERENT_NONNULL_VALUES,
    ENDPOINT_EVIDENCE_ENTRY_SNAPSHOT_MISSING,
    ENDPOINT_EVIDENCE_EXIT_SNAPSHOT_MISSING,
    ENDPOINT_EVIDENCE_IDENTICAL_NONNULL_VALUES,
    ENDPOINT_EVIDENCE_MALFORMED_VALUE,
    ENDPOINT_EVIDENCE_NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS,
    ENDPOINT_EVIDENCE_NULL_TO_VALUE,
    ENDPOINT_EVIDENCE_VALUE_TO_NULL,
    LEVEL_HISTORY_CONTRACT_VERSION_1,
    LEVEL_HISTORY_STATUS_CONTRADICTORY,
    LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
    LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
    LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
    PRICE_BASIS_COMPATIBLE,
    PRICE_BASIS_CORRUPT_OR_CONTRADICTORY,
    PRICE_BASIS_EVIDENCE_UNAVAILABLE,
    PRICE_BASIS_INCOMPATIBLE,
    PRICE_BASIS_UNKNOWN,
    classify_endpoint_evidence,
    classify_level_history_status,
    classify_price_basis_eligibility,
    is_no_level_configured_throughout_eligible,
)
from services.postmortem.governed_price_path_conclusions import (
    CANONICAL_FALLBACK,
    GOVERNED_ORDER_NEITHER_OBSERVED,
    GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN,
    GOVERNED_ORDER_STOP_ONLY_OBSERVED,
    GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET,
    GOVERNED_ORDER_TARGET_ONLY_OBSERVED,
    GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP,
    GOVERNED_ORDER_UNAVAILABLE,
    GOVERNED_TOUCH_INCOMPATIBLE_BASIS,
    GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE,
    GOVERNED_TOUCH_NO_BARS,
    GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING,
    GOVERNED_TOUCH_NO_VALUE_SUPPLIED,
    GOVERNED_TOUCH_SUPPORTED,
    GovernedConclusionContractError,
    classify_governed_level_touch,
    classify_governed_order,
)


def _raw(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


_D = lambda n: dt.date(2026, 6, n)


def _us_bundle(entry, exit_, bars, paper_trade_id=1):
    return build_price_path_evidence(
        paper_trade_id=paper_trade_id, user_id="user-aaa", symbol="AAPL", market="US",
        market_timezone_name="America/New_York", market_tzinfo=ET,
        entry_timestamp=entry, exit_timestamp=exit_, raw_bars=bars, split_events=[],
    )


_ENTRY = dt.datetime(2026, 6, 1, 9, 30, tzinfo=ET)
_EXIT = dt.datetime(2026, 6, 5, 16, 0, tzinfo=ET)
_INTERIOR_BARS = [
    _raw(_D(1), 100, 101, 99, 100),
    _raw(_D(2), 100, 130.0, 99, 100),   # target-crossing candidate (>=120)
    _raw(_D(3), 100, 101, 70.0, 100),   # stop-crossing candidate (<=80)
    _raw(_D(4), 100, 101, 99, 100),
    _raw(_D(5), 100, 101, 99, 100),
]


def _target_and_stop_observations(bars=None, target=120.0, stop=80.0):
    bundle = _us_bundle(_ENTRY, _EXIT, bars if bars is not None else _INTERIOR_BARS)
    target_obs = observe_numerical_level_crossing(bundle, target, TARGET_VALUE)
    stop_obs = observe_numerical_level_crossing(bundle, stop, STOP_VALUE)
    return target_obs, stop_obs


# ============================= A. LEVEL-HISTORY STATUS =============================

class TestLevelHistoryStatusMatrix:
    def test_a1_historical_null_is_unknown_or_legacy(self):
        assert classify_level_history_status(invariant_version=None, level_modified_flag=None) == LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY

    def test_a2_legacy_untrusted_false_is_unknown_or_legacy(self):
        assert classify_level_history_status(invariant_version=None, level_modified_flag=False) == LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY

    def test_a3_governed_version_false_is_governed_unmodified(self):
        assert classify_level_history_status(
            invariant_version=LEVEL_HISTORY_CONTRACT_VERSION_1, level_modified_flag=False,
        ) == LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED

    def test_a4_true_is_modified_after_entry_governed(self):
        assert classify_level_history_status(
            invariant_version=LEVEL_HISTORY_CONTRACT_VERSION_1, level_modified_flag=True,
        ) == LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY

    def test_a4b_true_is_modified_after_entry_legacy(self):
        assert classify_level_history_status(invariant_version=None, level_modified_flag=True) == LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY

    def test_a5_invalid_state_combination_is_contradictory(self):
        assert classify_level_history_status(invariant_version=123, level_modified_flag=None) == LEVEL_HISTORY_STATUS_CONTRADICTORY
        assert classify_level_history_status(invariant_version=None, level_modified_flag="TRUE") == LEVEL_HISTORY_STATUS_CONTRADICTORY

    def test_a6_missing_invariant_version_is_not_governed(self):
        status = classify_level_history_status(invariant_version=None, level_modified_flag=False)
        assert status != LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED

    def test_a7_unsupported_invariant_version_fails_closed(self):
        assert classify_level_history_status(invariant_version="999", level_modified_flag=False) == LEVEL_HISTORY_STATUS_CONTRADICTORY

    def test_a8_governed_version_with_null_flag_is_contradictory(self):
        assert classify_level_history_status(
            invariant_version=LEVEL_HISTORY_CONTRACT_VERSION_1, level_modified_flag=None,
        ) == LEVEL_HISTORY_STATUS_CONTRADICTORY

    def test_positive_construction_all_statuses_are_strings(self):
        for status in ALL_LEVEL_HISTORY_STATUSES:
            assert isinstance(status, str) and status


# ============================= B. ENDPOINT MATRIX =============================

class TestEndpointEvidenceMatrix:
    def test_b8_missing_entry_snapshot(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=False, exit_snapshot_present=True, entry_value=None, exit_value=100.0,
        ) == ENDPOINT_EVIDENCE_ENTRY_SNAPSHOT_MISSING

    def test_b9_missing_exit_snapshot(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=False, entry_value=100.0, exit_value=None,
        ) == ENDPOINT_EVIDENCE_EXIT_SNAPSHOT_MISSING

    def test_b10_both_snapshots_missing(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=False, exit_snapshot_present=False, entry_value=None, exit_value=None,
        ) == ENDPOINT_EVIDENCE_BOTH_SNAPSHOTS_MISSING

    def test_b11_null_null(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=None, exit_value=None,
        ) == ENDPOINT_EVIDENCE_NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS

    def test_b12_null_value(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=None, exit_value=105.0,
        ) == ENDPOINT_EVIDENCE_NULL_TO_VALUE

    def test_b13_value_null(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=105.0, exit_value=None,
        ) == ENDPOINT_EVIDENCE_VALUE_TO_NULL

    def test_b14_identical_value_value(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=105.0, exit_value=105.0,
        ) == ENDPOINT_EVIDENCE_IDENTICAL_NONNULL_VALUES

    def test_b15_different_value_value(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=105.0, exit_value=110.0,
        ) == ENDPOINT_EVIDENCE_DIFFERENT_NONNULL_VALUES

    def test_b16_malformed_value(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value="abc", exit_value=105.0,
        ) == ENDPOINT_EVIDENCE_MALFORMED_VALUE

    def test_b17_non_finite_value(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=float("nan"), exit_value=105.0,
        ) == ENDPOINT_EVIDENCE_MALFORMED_VALUE
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=float("inf"), exit_value=105.0,
        ) == ENDPOINT_EVIDENCE_MALFORMED_VALUE

    def test_identical_endpoints_do_not_prove_unchanged_throughout(self):
        # Gate 1E — identical non-null endpoint values must never be
        # (mis)used as proof no intermediate edit occurred; this test
        # only asserts the endpoint classification itself carries no
        # such claim, i.e. it is not equal to any governed-history
        # status.
        result = classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=100.0, exit_value=100.0,
        )
        assert result not in ALL_LEVEL_HISTORY_STATUSES

    def test_positive_construction_all_endpoint_statuses_are_strings(self):
        for status in ALL_ENDPOINT_EVIDENCE_STATUSES:
            assert isinstance(status, str) and status


class TestThroughoutTradeAbsenceEligibility:
    def test_governed_unmodified_and_null_null_is_eligible(self):
        assert is_no_level_configured_throughout_eligible(
            invariant_version=LEVEL_HISTORY_CONTRACT_VERSION_1, level_modified_flag=False, entry_value=None, exit_value=None,
        ) is True

    def test_legacy_null_null_never_eligible(self):
        assert is_no_level_configured_throughout_eligible(
            invariant_version=None, level_modified_flag=None, entry_value=None, exit_value=None,
        ) is False

    def test_legacy_false_null_null_never_eligible(self):
        assert is_no_level_configured_throughout_eligible(
            invariant_version=None, level_modified_flag=False, entry_value=None, exit_value=None,
        ) is False

    def test_governed_but_modified_never_eligible(self):
        assert is_no_level_configured_throughout_eligible(
            invariant_version=LEVEL_HISTORY_CONTRACT_VERSION_1, level_modified_flag=True, entry_value=None, exit_value=None,
        ) is False

    def test_governed_unmodified_but_non_null_endpoint_never_eligible(self):
        assert is_no_level_configured_throughout_eligible(
            invariant_version=LEVEL_HISTORY_CONTRACT_VERSION_1, level_modified_flag=False, entry_value=100.0, exit_value=None,
        ) is False


# ============================= C. PRICE BASIS =============================

class TestPriceBasisEligibility:
    def test_c18_compatible_basis(self):
        assert classify_price_basis_eligibility("UNADJUSTED") == PRICE_BASIS_COMPATIBLE

    def test_c19_incompatible_adjusted_basis(self):
        assert classify_price_basis_eligibility("TOTAL_RETURN_ADJUSTED") == PRICE_BASIS_INCOMPATIBLE

    def test_c20_split_adjusted_incompatible(self):
        assert classify_price_basis_eligibility("SPLIT_ADJUSTED") == PRICE_BASIS_INCOMPATIBLE

    def test_c21_unknown_basis(self):
        assert classify_price_basis_eligibility("UNKNOWN_ADJUSTMENT") == PRICE_BASIS_UNKNOWN

    def test_c22_corrupt_basis_evidence(self):
        assert classify_price_basis_eligibility("NOT_A_REAL_BASIS") == PRICE_BASIS_CORRUPT_OR_CONTRADICTORY
        assert classify_price_basis_eligibility(123) == PRICE_BASIS_CORRUPT_OR_CONTRADICTORY

    def test_c_evidence_unavailable(self):
        assert classify_price_basis_eligibility(None) == PRICE_BASIS_EVIDENCE_UNAVAILABLE

    def test_positive_construction_all_basis_statuses_are_strings(self):
        for status in ALL_PRICE_BASIS_STATUSES:
            assert isinstance(status, str) and status


# ============================= E. GOVERNED TOUCH CONCLUSIONS =============================

class TestGovernedTouchConclusions:
    def test_e_no_value_supplied(self):
        target_obs, _ = _target_and_stop_observations(target=None)
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
        )
        assert result.status == GOVERNED_TOUCH_NO_VALUE_SUPPLIED

    def test_e_no_compatible_crossing(self):
        target_obs, _ = _target_and_stop_observations(target=500.0)  # never reached by these bars
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
        )
        assert result.status == GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING

    def test_e36_crossing_plus_unknown_history_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e37_crossing_plus_modified_without_temporal_proof_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e38_crossing_plus_governed_unmodified_is_supported(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
        )
        assert result.status == GOVERNED_TOUCH_SUPPORTED

    def test_e41_contradictory_history_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_CONTRADICTORY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e43_incompatible_basis_prevents_any_crossing_conclusion(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_INCOMPATIBLE, bars_available=True,
        )
        assert result.status == GOVERNED_TOUCH_INCOMPATIBLE_BASIS
        assert result.detail == CANONICAL_FALLBACK

    def test_e_no_bars(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=False,
        )
        assert result.status == GOVERNED_TOUCH_NO_BARS
        assert result.detail == CANONICAL_FALLBACK

    def test_invalid_construction_bad_status_rejected(self):
        from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status="NOT_A_REAL_STATUS", detail="x")

    def test_fallback_conclusion_must_use_exact_canonical_sentence(self):
        from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status=GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE, detail="not the canonical sentence")


# ============================= F. GOVERNED ORDER CONCLUSIONS =============================

class TestGovernedOrderConclusions:
    def _summary(self, target_obs, stop_obs):
        return summarize_observed_numerical_crossings(target_obs, stop_obs)

    def test_f39_same_bar_order_remains_unknown(self):
        bars = [
            _raw(_D(1), 100, 101, 99, 100),
            _raw(_D(2), 100, 130.0, 70.0, 100),  # both target(>=120) and stop(<=80) hit same bar
            _raw(_D(3), 100, 101, 99, 100),
        ]
        target_obs, stop_obs = _target_and_stop_observations(bars=bars)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN

    def test_f_target_safely_before_stop_when_both_governed_unmodified(self):
        target_obs, stop_obs = _target_and_stop_observations()  # target hits day 2, stop hits day 3
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP

    def test_f_stop_safely_before_target_when_both_governed_unmodified(self):
        bars = [
            _raw(_D(1), 100, 101, 99, 100),
            _raw(_D(2), 100, 101, 70.0, 100),   # stop hits first
            _raw(_D(3), 100, 130.0, 99, 100),   # target hits second
            _raw(_D(4), 100, 101, 99, 100),
        ]
        target_obs, stop_obs = _target_and_stop_observations(bars=bars)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET

    def test_f_ordered_pattern_falls_back_when_either_level_not_governed_unmodified(self):
        target_obs, stop_obs = _target_and_stop_observations()
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f40_partial_boundary_never_produces_definitive_order(self):
        # Entry-day-only crossing => partial-boundary evidence, no safe crossing.
        early_entry = dt.datetime(2026, 6, 1, 9, 45, tzinfo=ET)  # not exactly session open => PARTIAL_UNKNOWN
        bundle = _us_bundle(early_entry, _EXIT, [
            _raw(_D(1), 100, 130.0, 70.0, 100),  # entry (partial) bar crosses both
            _raw(_D(2), 100, 101, 99, 100),
            _raw(_D(3), 100, 101, 99, 100),
            _raw(_D(4), 100, 101, 99, 100),
            _raw(_D(5), 100, 101, 99, 100),
        ])
        target_obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop_obs = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = self._summary(target_obs, stop_obs)
        assert summary.partial_boundary_observation_present is True
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f43_incompatible_basis_prevents_order_conclusion(self):
        target_obs, stop_obs = _target_and_stop_observations()
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_INCOMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f_neither_observed(self):
        target_obs, stop_obs = _target_and_stop_observations(target=500.0, stop=1.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_NEITHER_OBSERVED

    def test_f_target_only_observed(self):
        target_obs, stop_obs = _target_and_stop_observations(stop=1.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_TARGET_ONLY_OBSERVED

    def test_f_stop_only_observed(self):
        target_obs, stop_obs = _target_and_stop_observations(target=500.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_STOP_ONLY_OBSERVED

    def test_no_bars_forces_unavailable(self):
        target_obs, stop_obs = _target_and_stop_observations()
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=False, stop_bars_available=True,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK


# ============================= NO-FABRICATION =============================

class TestNoFabrication:
    def test_canonical_fallback_exact_text(self):
        assert CANONICAL_FALLBACK == "Insufficient evidence to determine this factor reliably."

    def test_no_prohibited_claim_strings_in_module_source(self):
        import inspect
        from services.postmortem import governed_price_path_conclusions as mod
        source = inspect.getsource(mod)
        prohibited = [
            "target achieved", "stop hit", "trader ignored", "should have exited",
            "strategy was correct", "active throughout",
        ]
        lowered = source.lower()
        for phrase in prohibited:
            assert phrase not in lowered, f"prohibited claim phrase found in source: {phrase!r}"

    def test_no_claim_of_configured_throughout_from_endpoint_equality_alone(self):
        # Endpoint equality alone (no governed history) must not make
        # NO_LEVEL_CONFIGURED_THROUGHOUT eligible.
        assert is_no_level_configured_throughout_eligible(
            invariant_version=None, level_modified_flag=None, entry_value=100.0, exit_value=100.0,
        ) is False

    def test_no_claim_of_absent_throughout_for_legacy_null_null(self):
        assert is_no_level_configured_throughout_eligible(
            invariant_version=None, level_modified_flag=None, entry_value=None, exit_value=None,
        ) is False

    def test_future_governed_null_null_may_become_eligible(self):
        assert is_no_level_configured_throughout_eligible(
            invariant_version=LEVEL_HISTORY_CONTRACT_VERSION_1, level_modified_flag=False, entry_value=None, exit_value=None,
        ) is True
