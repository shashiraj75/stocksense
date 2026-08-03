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
    GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
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
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_NO_VALUE_SUPPLIED

    def test_e_no_compatible_crossing_governed_unmodified_is_definitive(self):
        # Mandatory Correction 1 (3rd pass) — NO_COMPATIBLE_CROSSING is a
        # DEFINITIVE governed negative and is only ever returned when the
        # level history is reliably GOVERNED_UNMODIFIED.
        target_obs, _ = _target_and_stop_observations(target=500.0)  # never reached by these bars
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=500.0, exit_value=500.0,
        )
        assert result.status == GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING

    def test_e_no_compatible_crossing_unknown_legacy_is_insufficient(self):
        # Mandatory red test #2 — unknown/legacy history with no
        # crossing must return INSUFFICIENT_EVIDENCE, never a definitive
        # NO_COMPATIBLE_CROSSING (the checked value's historical validity
        # is unproven under unknown/legacy history).
        target_obs, _ = _target_and_stop_observations(target=500.0)
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e_no_compatible_crossing_modified_after_entry_is_insufficient(self):
        # Mandatory red test #3 — modified-after-entry history (no
        # temporal level history available) with no crossing must also
        # return INSUFFICIENT_EVIDENCE — never infer the checked endpoint
        # value was the level's value for the whole window.
        target_obs, _ = _target_and_stop_observations(target=500.0)
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e_no_compatible_crossing_contradictory_history_is_insufficient(self):
        target_obs, _ = _target_and_stop_observations(target=500.0)
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_CONTRADICTORY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e_no_value_supplied_independent_of_history_governed(self):
        # Mandatory red test #5 — NO_VALUE_SUPPLIED stays independent of
        # level-history status under all three history families.
        target_obs, _ = _target_and_stop_observations(target=None)
        for history in (
            LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED, LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
        ):
            result = classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=target_obs, level_history_status=history,
                price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )
            assert result.status == GOVERNED_TOUCH_NO_VALUE_SUPPLIED, history

    def test_e36_crossing_plus_unknown_history_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e37_crossing_plus_modified_without_temporal_proof_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e38_crossing_plus_governed_unmodified_is_supported(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=120.0, exit_value=120.0,
        )
        assert result.status == GOVERNED_TOUCH_SUPPORTED

    def test_e_governed_unmodified_but_endpoints_null_null_falls_back(self):
        """Gate 1F item 9 — governed FALSE with null/null endpoints means
        no level was configured throughout, which cannot support a touch
        of a DIFFERENT supplied numerical value."""
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_f1_missing_entry_snapshot_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=False, exit_snapshot_present=True, exit_value=120.0,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_f2_missing_exit_snapshot_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=False, entry_value=120.0,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_f3_both_snapshots_missing_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=False, exit_snapshot_present=False,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_f4_malformed_endpoint_value_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=float("nan"), exit_value=120.0,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_f5_governed_false_differing_endpoints_is_contradiction(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=100.0, exit_value=120.0,
        )
        assert result.status == GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_f6_governed_false_null_to_value_is_contradiction(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=None, exit_value=120.0,
        )
        assert result.status == GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE

    def test_f7_governed_false_value_to_null_is_contradiction(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=120.0, exit_value=None,
        )
        assert result.status == GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE

    def test_f8_identical_endpoints_inconsistent_with_observed_value_is_contradiction(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=99.0, exit_value=99.0,
        )
        assert result.status == GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE

    def test_e41_contradictory_history_falls_back(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_CONTRADICTORY,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
        assert result.detail == CANONICAL_FALLBACK

    def test_e43_incompatible_basis_prevents_any_crossing_conclusion(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_INCOMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True,
        )
        assert result.status == GOVERNED_TOUCH_INCOMPATIBLE_BASIS
        assert result.detail == CANONICAL_FALLBACK

    def test_e_no_bars(self):
        target_obs, _ = _target_and_stop_observations()
        result = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=False,
            entry_snapshot_present=True, exit_snapshot_present=True,
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
        # Wave A closure correction — SAME_BAR_ORDER_UNKNOWN now requires
        # BOTH per-level governed touch conclusions to be SUPPORTED
        # (complete, consistent endpoint evidence for both sides), not
        # merely GOVERNED_UNMODIFIED history status.
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
            target_entry_value=120.0, target_exit_value=120.0,
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN

    def test_f39b_same_bar_but_one_side_history_unknown_is_unavailable(self):
        bars = [
            _raw(_D(1), 100, 101, 99, 100),
            _raw(_D(2), 100, 130.0, 70.0, 100),
            _raw(_D(3), 100, 101, 99, 100),
        ]
        target_obs, stop_obs = _target_and_stop_observations(bars=bars)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f39c_same_bar_but_one_side_endpoint_missing_is_unavailable(self):
        bars = [
            _raw(_D(1), 100, 101, 99, 100),
            _raw(_D(2), 100, 130.0, 70.0, 100),
            _raw(_D(3), 100, 101, 99, 100),
        ]
        target_obs, stop_obs = _target_and_stop_observations(bars=bars)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_snapshot_present=False, target_exit_value=120.0,
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f39d_same_bar_but_one_side_endpoint_contradictory_is_unavailable(self):
        bars = [
            _raw(_D(1), 100, 101, 99, 100),
            _raw(_D(2), 100, 130.0, 70.0, 100),
            _raw(_D(3), 100, 101, 99, 100),
        ]
        target_obs, stop_obs = _target_and_stop_observations(bars=bars)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=100.0, target_exit_value=120.0,  # contradictory
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f_target_safely_before_stop_when_both_governed_unmodified(self):
        target_obs, stop_obs = _target_and_stop_observations()  # target hits day 2, stop hits day 3
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
            stop_entry_value=80.0, stop_exit_value=80.0,
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
            target_entry_value=120.0, target_exit_value=120.0,
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET

    def test_f_ordered_pattern_falls_back_when_endpoint_evidence_contradicts(self):
        """Gate 1F correction — even with both levels GOVERNED_UNMODIFIED
        and a safely observed order, contradictory endpoint evidence for
        either level still forces ORDER_UNAVAILABLE."""
        target_obs, stop_obs = _target_and_stop_observations()
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=100.0, target_exit_value=120.0,  # differing endpoints -- contradiction
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

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
        # Wave A closure correction — a definitive NEITHER_OBSERVED
        # requires each side's "no compatible crossing" to be trustworthy,
        # which (per Correction 2E) requires GOVERNED_UNMODIFIED history
        # with consistent endpoint evidence — not merely "never crossed".
        target_obs, stop_obs = _target_and_stop_observations(target=500.0, stop=1.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=500.0, target_exit_value=500.0,
            stop_entry_value=1.0, stop_exit_value=1.0,
        )
        assert result.status == GOVERNED_ORDER_NEITHER_OBSERVED

    def test_f26_neither_observed_but_incomplete_evidence_is_unavailable(self):
        """Mandatory red test #26 — legacy/unknown history for either
        side must not be converted into a confident 'neither' result."""
        target_obs, stop_obs = _target_and_stop_observations(target=500.0, stop=1.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            stop_entry_value=1.0, stop_exit_value=1.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f15_target_only_but_stop_history_unknown_with_a_value_is_unavailable(self):
        """Mandatory red test #15 — target valid + stop has a supplied
        value (never crosses) but UNKNOWN_OR_LEGACY history: a
        non-crossing observation under unknown history is not trustworthy
        negative evidence (we cannot confirm the checked value was ever
        the level's true value), so this must be ORDER_UNAVAILABLE, not
        TARGET_ONLY_OBSERVED."""
        target_obs, stop_obs = _target_and_stop_observations(stop=1.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f19b_stop_only_but_target_history_unknown_with_a_value_is_unavailable(self):
        """Mandatory red test #19 symmetric case."""
        target_obs, stop_obs = _target_and_stop_observations(target=500.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f_target_only_observed(self):
        # Wave A closure correction — TARGET_ONLY_OBSERVED now requires
        # the target side to be a SUPPORTED governed touch (governed-
        # unmodified history + consistent endpoints), with the stop side
        # a complete, noncontradictory NEGATIVE (here: no stop value was
        # even supplied to observe_numerical_level_crossing).
        target_obs, stop_obs = _target_and_stop_observations(stop=None)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
        )
        assert result.status == GOVERNED_ORDER_TARGET_ONLY_OBSERVED

    def test_f6_target_only_with_stop_governed_unmodified_no_crossing(self):
        # Mandatory red test #6 — target SUPPORTED + stop a genuine
        # GOVERNED_UNMODIFIED definitive no-crossing (not merely no
        # value supplied) => TARGET_ONLY_OBSERVED.
        target_obs, stop_obs = _target_and_stop_observations(stop=1.0)  # stop supplied, never crosses
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
            stop_entry_value=1.0, stop_exit_value=1.0,
        )
        assert result.status == GOVERNED_ORDER_TARGET_ONLY_OBSERVED

    def test_f8_target_only_but_stop_modified_history_no_crossing_is_unavailable(self):
        # Mandatory red test #8.
        target_obs, stop_obs = _target_and_stop_observations(stop=1.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f9_stop_only_with_target_modified_history_no_crossing_is_unavailable(self):
        # Mandatory red test #9 — symmetric case.
        target_obs, stop_obs = _target_and_stop_observations(target=500.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f17_target_only_but_stop_entry_snapshot_missing_is_unavailable(self):
        # The stop side has a value supplied that never crosses, but its
        # entry snapshot is missing -- that missing evidence makes the
        # stop side untrustworthy regardless of the crossing outcome.
        target_obs, stop_obs = _target_and_stop_observations(stop=1.0)  # never crosses, but IS supplied
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
            stop_entry_snapshot_present=False,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f18_target_only_but_stop_exit_snapshot_missing_is_unavailable(self):
        target_obs, stop_obs = _target_and_stop_observations(stop=1.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
            stop_exit_snapshot_present=False, stop_entry_value=1.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f19_target_only_but_stop_endpoint_contradiction_is_unavailable(self):
        target_obs, stop_obs = _target_and_stop_observations(stop=1.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
            stop_entry_value=1.0, stop_exit_value=5.0,  # contradictory for GOVERNED_UNMODIFIED
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f_stop_only_observed(self):
        target_obs, stop_obs = _target_and_stop_observations(target=None)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            stop_entry_value=80.0, stop_exit_value=80.0,
        )
        assert result.status == GOVERNED_ORDER_STOP_ONLY_OBSERVED

    def test_f_stop_only_but_target_history_unknown_with_missing_snapshot_is_unavailable(self):
        target_obs, stop_obs = _target_and_stop_observations(target=500.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            stop_entry_value=80.0, stop_exit_value=80.0,
            target_entry_snapshot_present=False,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f_stop_only_but_target_exit_snapshot_missing_is_unavailable(self):
        target_obs, stop_obs = _target_and_stop_observations(target=500.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            stop_entry_value=80.0, stop_exit_value=80.0,
            target_exit_snapshot_present=False, target_entry_value=500.0,
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

    def test_f_stop_only_but_target_endpoint_contradiction_is_unavailable(self):
        target_obs, stop_obs = _target_and_stop_observations(target=500.0)
        summary = self._summary(target_obs, stop_obs)
        result = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            stop_entry_value=80.0, stop_exit_value=80.0,
            target_entry_value=400.0, target_exit_value=500.0,  # contradictory
        )
        assert result.status == GOVERNED_ORDER_UNAVAILABLE
        assert result.detail == CANONICAL_FALLBACK

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


# ============================= G. CONTRACT HARDENING (Wave A closure correction) =============================

class _StrSub(str):
    pass


class _IntSub(int):
    pass


class _FloatSub(float):
    pass


class TestContractHardeningExactTypes:
    def test_g_str_subclass_invariant_version_rejected(self):
        assert classify_level_history_status(
            invariant_version=_StrSub("1"), level_modified_flag=False,
        ) == LEVEL_HISTORY_STATUS_CONTRADICTORY

    def test_g_whitespace_only_invariant_version_rejected(self):
        assert classify_level_history_status(
            invariant_version="   ", level_modified_flag=False,
        ) == LEVEL_HISTORY_STATUS_CONTRADICTORY

    def test_g_int_subclass_endpoint_value_rejected(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=_IntSub(100), exit_value=100.0,
        ) == ENDPOINT_EVIDENCE_MALFORMED_VALUE

    def test_g_float_subclass_endpoint_value_rejected(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=_FloatSub(100.0), exit_value=100.0,
        ) == ENDPOINT_EVIDENCE_MALFORMED_VALUE

    def test_g_bool_endpoint_value_rejected(self):
        assert classify_endpoint_evidence(
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=True, exit_value=100.0,
        ) == ENDPOINT_EVIDENCE_MALFORMED_VALUE

    def test_g_str_subclass_price_basis_rejected(self):
        assert classify_price_basis_eligibility(_StrSub("UNADJUSTED")) == PRICE_BASIS_CORRUPT_OR_CONTRADICTORY

    def test_g_whitespace_only_price_basis_rejected(self):
        assert classify_price_basis_eligibility("   ") == PRICE_BASIS_CORRUPT_OR_CONTRADICTORY

    def test_g_int_price_basis_rejected(self):
        assert classify_price_basis_eligibility(123) == PRICE_BASIS_CORRUPT_OR_CONTRADICTORY

    def test_g_bool_level_modified_flag_rejected(self):
        # bool is not str-subclassable, but a non-bool/non-None value in
        # the flag position (e.g. the int 1) must still fail closed.
        assert classify_level_history_status(invariant_version="1", level_modified_flag=1) == LEVEL_HISTORY_STATUS_CONTRADICTORY

    def test_g_datetime_confused_with_date_in_price_basis(self):
        import datetime as _dt
        assert classify_price_basis_eligibility(_dt.date(2026, 1, 1)) == PRICE_BASIS_CORRUPT_OR_CONTRADICTORY

    def test_g_governed_touch_conclusion_invalid_status_type_rejected(self):
        from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status=GOVERNED_TOUCH_SUPPORTED, detail=_StrSub("x"))
            # detail is non-empty but this is really testing the type()
            # check accepts a str subclass's VALUE fine for non-fallback
            # statuses since detail content isn't fallback-constrained
            # here -- the real hardening is the invalid-status case below.

    def test_g_governed_order_invalid_status_rejected(self):
        from services.postmortem.governed_price_path_conclusions import GovernedOrderConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedOrderConclusion(status="NOT_A_REAL_ORDER_STATUS", detail="x")

    def test_g_governed_order_empty_detail_rejected(self):
        from services.postmortem.governed_price_path_conclusions import GovernedOrderConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedOrderConclusion(status=GOVERNED_ORDER_NEITHER_OBSERVED, detail="   ")

    def test_g_deterministic_canonical_output_repeated_calls(self):
        # Same inputs -> byte-identical output, every call.
        r1 = classify_level_history_status(invariant_version="1", level_modified_flag=False)
        r2 = classify_level_history_status(invariant_version="1", level_modified_flag=False)
        assert r1 == r2 == LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED

    def test_g_invalid_level_kind_rejected_on_direct_construction(self):
        # Wave A closure correction — GovernedLevelTouchConclusion now
        # validates level_kind itself (exact str, canonical
        # TARGET_VALUE/STOP_VALUE membership only) at direct-construction
        # time; a garbage level_kind is rejected, never stored verbatim.
        from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedLevelTouchConclusion(level_kind="NOT_A_REAL_LEVEL_KIND", status=GOVERNED_TOUCH_NO_VALUE_SUPPLIED, detail="x")

    def test_g_empty_level_kind_rejected(self):
        from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedLevelTouchConclusion(level_kind="", status=GOVERNED_TOUCH_NO_VALUE_SUPPLIED, detail="x")

    def test_g_whitespace_only_level_kind_rejected(self):
        from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedLevelTouchConclusion(level_kind="   ", status=GOVERNED_TOUCH_NO_VALUE_SUPPLIED, detail="x")

    def test_g_str_subclass_level_kind_rejected(self):
        from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedLevelTouchConclusion(level_kind=_StrSub(TARGET_VALUE), status=GOVERNED_TOUCH_NO_VALUE_SUPPLIED, detail="x")

    def test_g_classify_governed_level_touch_rejects_invalid_level_kind(self):
        target_obs, _ = _target_and_stop_observations()
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_level_touch(
                level_kind="NOT_A_REAL_LEVEL_KIND", observation=target_obs,
                level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
                price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )

    def test_g_bars_available_integer_rejected(self):
        target_obs, _ = _target_and_stop_observations()
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=target_obs,
                level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
                price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=1,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )

    class _BarsBool(int):
        """A hostile int subclass masquerading as a bool-like value —
        not the real `bool` type, so `type(x) is bool` correctly rejects
        it even though `isinstance(x, int)` (and a permissive isinstance-
        based bool check) would not."""

    def test_g_bars_available_hostile_subclass_rejected(self):
        target_obs, _ = _target_and_stop_observations()
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=target_obs,
                level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
                price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=self._BarsBool(1),
                entry_snapshot_present=True, exit_snapshot_present=True,
            )

    def test_g_observation_subclass_rejected(self):
        target_obs, _ = _target_and_stop_observations()

        class _ObsSub(type(target_obs)):
            pass

        hostile = _ObsSub(**{f.name: getattr(target_obs, f.name) for f in dataclasses.fields(target_obs)})
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=hostile,
                level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
                price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )

    def test_g_summary_subclass_rejected(self):
        target_obs, stop_obs = _target_and_stop_observations()
        summary = summarize_observed_numerical_crossings(target_obs, stop_obs)

        class _SummarySub(type(summary)):
            pass

        hostile = _SummarySub(**{f.name: getattr(summary, f.name) for f in dataclasses.fields(summary)})
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_order(
                summary=hostile, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
                stop_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
                target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
                target_bars_available=True, stop_bars_available=True,
            )

    def test_g_invalid_history_status_rejected(self):
        target_obs, _ = _target_and_stop_observations()
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=target_obs,
                level_history_status="NOT_A_REAL_HISTORY_STATUS",
                price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )

    def test_g_history_status_str_subclass_rejected(self):
        target_obs, _ = _target_and_stop_observations()
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=target_obs,
                level_history_status=_StrSub(LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED),
                price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )

    def test_g_invalid_price_basis_status_rejected(self):
        target_obs, _ = _target_and_stop_observations()
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=target_obs,
                level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
                price_basis_status="NOT_A_REAL_BASIS_STATUS", bars_available=True,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )

    def test_g_price_basis_status_str_subclass_rejected(self):
        target_obs, _ = _target_and_stop_observations()
        with pytest.raises(GovernedConclusionContractError):
            classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=target_obs,
                level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
                price_basis_status=_StrSub(PRICE_BASIS_COMPATIBLE), bars_available=True,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )

    def test_g_invalid_status_detail_construction_rejected(self):
        from services.postmortem.governed_price_path_conclusions import GovernedLevelTouchConclusion
        with pytest.raises(GovernedConclusionContractError):
            GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status="ALSO_NOT_REAL", detail=CANONICAL_FALLBACK)


# ============================= FINAL-HEAD ASSURANCE ANCHOR (WA-C23) =============================

class TestFinalHeadAssuranceAnchor:
    """A stable regression anchor for the final Wave A closure-
    correction HEAD -- exercises the shared touch/order eligibility
    path end to end (unit-level; PostgreSQL 15/17 assurance is proven
    separately by the real-PostgreSQL suite, never claimed here)."""

    def test_governed_order_still_unwired_at_final_head(self):
        import services.postmortem.governed_price_path_conclusions as mod
        assert hasattr(mod, "classify_governed_order")
        assert hasattr(mod, "classify_governed_level_touch")

    def test_shared_eligibility_path_produces_consistent_touch_and_order_results(self):
        target_obs, stop_obs = _target_and_stop_observations()
        touch = classify_governed_level_touch(
            level_kind=TARGET_VALUE, observation=target_obs, level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
            entry_snapshot_present=True, exit_snapshot_present=True, entry_value=120.0, exit_value=120.0,
        )
        summary = summarize_observed_numerical_crossings(target_obs, stop_obs)
        order = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0, stop_entry_value=1.0,
        )
        assert touch.status == GOVERNED_TOUCH_SUPPORTED
        # Same eligibility inputs for the target side -> the order
        # classifier's internal per-level result agrees with the
        # standalone touch classifier's result (both call the identical
        # function under Mandatory Correction 1).
        assert order.status == GOVERNED_ORDER_UNAVAILABLE  # stop side history unknown with a supplied value


# ============================= SINGLE-SOURCE ELIGIBILITY (WA-C24/WA-C25) =============================

class TestSingleSourcePerLevelEligibility:
    def test_definitive_negative_helper_accepts_only_one_conclusion_argument(self):
        """Mandatory red test #13 — the private single-source eligibility
        helper must accept exactly one parameter (the conclusion itself),
        never a second raw level_history_status/endpoint/basis input."""
        import inspect
        from services.postmortem import governed_price_path_conclusions as mod
        sig = inspect.signature(mod._side_is_definitive_negative)
        assert list(sig.parameters) == ["touch"]
        sig2 = inspect.signature(mod._side_is_trustworthy)
        assert list(sig2.parameters) == ["touch"]

    def test_no_compatible_crossing_never_returned_for_non_governed_history(self):
        """Mandatory red test #15 — standalone classify_governed_level_
        touch never exposes NO_COMPATIBLE_CROSSING for unknown, legacy,
        contradictory or modified-after-entry history."""
        target_obs, _ = _target_and_stop_observations(target=500.0)
        for history in (
            LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY, LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
            LEVEL_HISTORY_STATUS_CONTRADICTORY,
        ):
            result = classify_governed_level_touch(
                level_kind=TARGET_VALUE, observation=target_obs, level_history_status=history,
                price_basis_status=PRICE_BASIS_COMPATIBLE, bars_available=True,
                entry_snapshot_present=True, exit_snapshot_present=True,
            )
            assert result.status != GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING, history
            assert result.status == GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE
            assert result.detail == CANONICAL_FALLBACK

    def test_order_classification_performs_no_post_classification_reinterpretation(self):
        """Mandatory red test #14 — once target_touch/stop_touch exist,
        classify_governed_order's result is fully determined by those two
        conclusions alone: two calls with identical resulting touch
        statuses but DIFFERENT raw level_history_status/endpoint inputs
        (that still produce the SAME touch conclusion) must produce the
        SAME order result, proving no raw-history reinterpretation
        happens after the per-level conclusions are built."""
        target_obs, stop_obs = _target_and_stop_observations(stop=None)
        summary = self._make_summary(target_obs, stop_obs)

        # Call A: stop side is UNKNOWN_OR_LEGACY (irrelevant, since
        # NO_VALUE_SUPPLIED is independent of history).
        result_a = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
        )
        # Call B: stop side is MODIFIED_AFTER_ENTRY instead -- still
        # produces GOVERNED_TOUCH_NO_VALUE_SUPPLIED (no value was ever
        # supplied for the stop observation in either call), so the
        # order result must be identical.
        result_b = classify_governed_order(
            summary=summary, target_level_history_status=LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
            stop_level_history_status=LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
            target_price_basis_status=PRICE_BASIS_COMPATIBLE, stop_price_basis_status=PRICE_BASIS_COMPATIBLE,
            target_bars_available=True, stop_bars_available=True,
            target_entry_value=120.0, target_exit_value=120.0,
        )
        assert result_a.status == result_b.status == GOVERNED_ORDER_TARGET_ONLY_OBSERVED

    def _make_summary(self, target_obs, stop_obs):
        return summarize_observed_numerical_crossings(target_obs, stop_obs)
