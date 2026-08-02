"""
Phase C2-D2 — SourceAcquisitionRecord / ClassificationConflictPolicy /
ConsumerApprovalEvaluation contract tests.

These tests validate the TYPES ONLY (constructor validation, invariants,
deterministic canonical serialization). No test in this file implements
or exercises a 30-scenario approval truth-table engine, an acquisition
call, or any live SourceId-specific freshness-basis ratification -- every
fixture below is explicitly local/synthetic, matching the module's own
"no ratification, no acquisition, no approval calculation" scope.
"""
from __future__ import annotations

import pytest

from services.instrument_master.enums import (
    AcquisitionResultStatus,
    CalibrationStatus,
    ConsumerApprovalMode,
    ConsumerType,
    FreshnessBasis,
    InstrumentClass,
    SourceDataOrigin,
    SourceErrorCode,
    SourceId,
    TaxonomyReadinessMode,
    ValidationResultStatus,
)
from services.instrument_master.consumer_approval_contracts import (
    ClassificationConflictPolicy,
    ConsumerApprovalEvaluation,
    SourceAcquisitionRecord,
    canonical_classification_conflict_policy_bytes,
    canonical_classification_conflict_policy_hash,
    canonical_consumer_approval_evaluation_bytes,
    canonical_consumer_approval_evaluation_hash,
    canonical_source_acquisition_record_bytes,
    canonical_source_acquisition_record_hash,
)

# ---------------------------------------------------------------------------
# Fixture builders -- every value here is synthetic/local, never a real
# acquisition result. FreshnessBasis.ACQUISITION_TIME_PROXY is used ONLY
# in these test-only fixtures, per the module's explicit "not ratified
# for any real SourceId" scope boundary.
# ---------------------------------------------------------------------------


def _acquisition_record(**overrides) -> SourceAcquisitionRecord:
    kwargs = dict(
        source_id=SourceId.NSE_EQUITY_CURRENT,
        canonical_url="https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
        acquisition_started_at="2026-07-19T00:00:00Z",
        acquisition_completed_at="2026-07-19T00:00:05Z",
        result_status=AcquisitionResultStatus.SUCCESS,
        response_status_code=200,
        retrieved_at="2026-07-19T00:00:05Z",
        source_effective_at="2026-07-19T00:00:00Z",
        freshness_evaluated_at="2026-07-19T00:00:10Z",
        freshness_basis=FreshnessBasis.ACQUISITION_TIME_PROXY,  # test-only, not ratified
        source_data_origin=SourceDataOrigin.CURRENT,
        raw_byte_size=1024,
        raw_sha256="a" * 64,
        validation_status=ValidationResultStatus.VALID,
        validation_error_codes=(),
        warning_codes=(),
        row_count=2300,
        accepted_row_count=2300,
        rejected_row_count=0,
        duplicate_count=0,
        warning_deadline_at="2026-07-20T00:00:00Z",
        hard_stale_deadline_at="2026-07-22T00:00:00Z",
        age_seconds=10,
        age_days_display=0,
        snapshot_id="snap-equity-1",
        last_known_good_snapshot_id=None,
        using_last_known_good=False,
    )
    kwargs.update(overrides)
    return SourceAcquisitionRecord(**kwargs)


def _conflict_policy(**overrides) -> ClassificationConflictPolicy:
    kwargs = dict(
        policy_version="2026-c2-d1b-provisional-1",
        isolated_max_count=10,
        isolated_max_ratio=0.005,
        systemic_source_ratio=0.20,
        minimum_compared_row_count=20,
        effective_from="2026-07-19T00:00:00Z",
        calibration_status=CalibrationStatus.PROVISIONAL,
    )
    kwargs.update(overrides)
    return ClassificationConflictPolicy(**kwargs)


_ALL_NON_EQUITY = tuple(
    sorted((c for c in InstrumentClass if c is not InstrumentClass.ORDINARY_EQUITY), key=lambda c: c.value)
)


def _blocked_evaluation(**overrides) -> ConsumerApprovalEvaluation:
    kwargs = dict(
        evaluation_id="eval-blocked-1",
        consumer=ConsumerType.SCREENER,
        approved=False,
        approval_mode=ConsumerApprovalMode.BLOCKED,
        taxonomy_readiness_mode=TaxonomyReadinessMode.BLOCKED,
        taxonomy_classified_instrument_classes=(),
        consumer_included_instrument_classes=(),
        consumer_excluded_instrument_classes=_ALL_NON_EQUITY,
        required_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
        current_source_ids=(),
        using_last_known_good_sources=frozenset(),
        blocking_reason_codes=(SourceErrorCode.SOURCE_UNAVAILABLE,),
        warning_codes=(),
        source_snapshot_id=None,
        conflict_policy_version="2026-c2-d1b-provisional-1",
        evaluated_at="2026-07-19T00:00:10Z",
    )
    kwargs.update(overrides)
    return ConsumerApprovalEvaluation(**kwargs)


def _restricted_evaluation(**overrides) -> ConsumerApprovalEvaluation:
    kwargs = dict(
        evaluation_id="eval-restricted-1",
        consumer=ConsumerType.SCREENER,
        approved=True,
        approval_mode=ConsumerApprovalMode.RESTRICTED_NON_SME,
        taxonomy_readiness_mode=TaxonomyReadinessMode.EQUITY_ETF_CLASSIFIED,
        taxonomy_classified_instrument_classes=tuple(
            sorted((InstrumentClass.ORDINARY_EQUITY, InstrumentClass.ETF), key=lambda c: c.value)
        ),
        consumer_included_instrument_classes=(InstrumentClass.ORDINARY_EQUITY,),
        consumer_excluded_instrument_classes=_ALL_NON_EQUITY,
        required_source_ids=tuple(
            sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
        ),
        current_source_ids=tuple(
            sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
        ),
        using_last_known_good_sources=frozenset(),
        blocking_reason_codes=(),
        warning_codes=(),
        source_snapshot_id="snap-restricted-1",
        conflict_policy_version="2026-c2-d1b-provisional-1",
        evaluated_at="2026-07-19T00:00:10Z",
    )
    kwargs.update(overrides)
    return ConsumerApprovalEvaluation(**kwargs)


def _core_complete_evaluation(**overrides) -> ConsumerApprovalEvaluation:
    kwargs = dict(
        evaluation_id="eval-core-1",
        consumer=ConsumerType.SCREENER,
        approved=True,
        approval_mode=ConsumerApprovalMode.CORE_TAXONOMY_COMPLETE,
        taxonomy_readiness_mode=TaxonomyReadinessMode.CORE_TAXONOMY_COMPLETE,
        taxonomy_classified_instrument_classes=tuple(
            sorted(
                (InstrumentClass.ORDINARY_EQUITY, InstrumentClass.ETF, InstrumentClass.SME),
                key=lambda c: c.value,
            )
        ),
        consumer_included_instrument_classes=(InstrumentClass.ORDINARY_EQUITY,),
        consumer_excluded_instrument_classes=_ALL_NON_EQUITY,
        required_source_ids=tuple(
            sorted(
                (SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT, SourceId.NSE_SME_CURRENT),
                key=lambda s: s.value,
            )
        ),
        current_source_ids=tuple(
            sorted(
                (SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT, SourceId.NSE_SME_CURRENT),
                key=lambda s: s.value,
            )
        ),
        using_last_known_good_sources=frozenset(),
        blocking_reason_codes=(),
        warning_codes=(),
        source_snapshot_id="snap-core-1",
        conflict_policy_version="2026-c2-d1b-provisional-1",
        evaluated_at="2026-07-19T00:00:10Z",
    )
    kwargs.update(overrides)
    return ConsumerApprovalEvaluation(**kwargs)


# ---------------------------------------------------------------------------
# Timestamp validation
# ---------------------------------------------------------------------------


class TestTimestampValidation:
    def test_valid_second_precision_utc_accepted(self):
        _acquisition_record(acquisition_started_at="2026-07-19T00:00:00Z")

    def test_valid_six_digit_fractional_utc_accepted(self):
        _acquisition_record(acquisition_started_at="2026-07-19T00:00:00.123456Z")

    def test_invalid_offset_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at="2026-07-19T00:00:00+05:30")

    def test_naive_timestamp_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at="2026-07-19T00:00:00")

    def test_malformed_date_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at="19-07-2026T00:00:00Z")

    def test_impossible_date_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at="2026-13-40T00:00:00Z")

    def test_impossible_time_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at="2026-07-19T25:61:61Z")

    def test_whitespace_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at=" 2026-07-19T00:00:00Z")
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at="2026-07-19T00:00:00Z ")

    def test_date_only_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at="2026-07-19")

    def test_empty_string_rejected_where_non_null_required(self):
        with pytest.raises(ValueError):
            _acquisition_record(acquisition_started_at="")

    def test_conflict_policy_effective_from_validated(self):
        with pytest.raises(ValueError):
            _conflict_policy(effective_from="not-a-timestamp")

    def test_evaluation_evaluated_at_validated(self):
        with pytest.raises(ValueError):
            _blocked_evaluation(evaluated_at="not-a-timestamp")


# ---------------------------------------------------------------------------
# Freshness basis and origin
# ---------------------------------------------------------------------------


class TestFreshnessBasisAndOrigin:
    def test_unknown_basis_requires_no_effective_timestamp(self):
        _acquisition_record(freshness_basis=FreshnessBasis.UNKNOWN, source_effective_at=None)

    def test_unknown_basis_with_effective_timestamp_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(freshness_basis=FreshnessBasis.UNKNOWN, source_effective_at="2026-07-19T00:00:00Z")

    def test_known_basis_requires_effective_timestamp(self):
        with pytest.raises(ValueError):
            _acquisition_record(freshness_basis=FreshnessBasis.ACQUISITION_TIME_PROXY, source_effective_at=None)

    def test_lkg_retains_original_basis(self):
        record = _acquisition_record(
            source_data_origin=SourceDataOrigin.LAST_KNOWN_GOOD,
            using_last_known_good=True,
            last_known_good_snapshot_id="lkg-snap-1",
            freshness_basis=FreshnessBasis.HTTP_LAST_MODIFIED,
            source_effective_at="2026-07-10T00:00:00Z",
        )
        assert record.freshness_basis is FreshnessBasis.HTTP_LAST_MODIFIED

    def test_lkg_origin_requires_lkg_snapshot_id(self):
        with pytest.raises(ValueError):
            _acquisition_record(
                source_data_origin=SourceDataOrigin.LAST_KNOWN_GOOD,
                using_last_known_good=True,
                last_known_good_snapshot_id=None,
            )

    def test_using_lkg_true_requires_lkg_origin(self):
        with pytest.raises(ValueError):
            _acquisition_record(
                source_data_origin=SourceDataOrigin.CURRENT,
                using_last_known_good=True,
                last_known_good_snapshot_id="lkg-snap-1",
            )

    def test_current_origin_cannot_set_using_last_known_good(self):
        with pytest.raises(ValueError):
            _acquisition_record(source_data_origin=SourceDataOrigin.CURRENT, using_last_known_good=True)

    def test_lkg_origin_requires_using_last_known_good_true(self):
        with pytest.raises(ValueError):
            _acquisition_record(
                source_data_origin=SourceDataOrigin.LAST_KNOWN_GOOD,
                using_last_known_good=False,
                last_known_good_snapshot_id="lkg-snap-1",
            )

    def test_no_automatic_retrieved_at_fallback(self):
        # retrieved_at and source_effective_at are independent fields --
        # setting one never implicitly sets or validates the other.
        record = _acquisition_record(retrieved_at="2026-07-19T00:00:05Z", source_effective_at="2026-07-10T00:00:00Z")
        assert record.retrieved_at != record.source_effective_at


# ---------------------------------------------------------------------------
# Counts and hashes
# ---------------------------------------------------------------------------


class TestCountsAndHashes:
    def test_negative_row_count_rejected(self):
        # Phase C2-D2B correction (ratified C2-D2A audit finding #2): must
        # fail specifically because of the row_count scalar-domain check,
        # not because of an unrelated duplicate_count>row_count cross-field
        # check that would also happen to fire for this input. Asserting
        # the exact message pins the failure to the intended invariant --
        # a regression that removed the row_count-specific check (while
        # leaving the cross-field check intact) would raise a *different*
        # message and correctly fail this test.
        with pytest.raises(ValueError, match=r"^row_count: must be non-negative, got -1$"):
            _acquisition_record(row_count=-1)

    def test_negative_row_count_not_masked_by_duplicate_count_cross_check(self):
        # Isolates the row_count check from the duplicate_count>row_count
        # cross-field check by using a field with no cross-field
        # relationship at all (raw_byte_size) alongside a row_count=None
        # baseline -- proves the scalar check fires independently of any
        # cross-field arithmetic, not merely as a side effect of one.
        with pytest.raises(ValueError, match=r"^raw_byte_size: must be non-negative, got -100$"):
            _acquisition_record(
                row_count=None,
                accepted_row_count=None,
                rejected_row_count=None,
                raw_byte_size=-100,
                result_status=AcquisitionResultStatus.TIMEOUT,
                acquisition_completed_at=None,
                response_status_code=None,
                retrieved_at=None,
                source_effective_at=None,
                freshness_evaluated_at=None,
                freshness_basis=FreshnessBasis.UNKNOWN,
                raw_sha256=None,
                validation_status=ValidationResultStatus.INVALID_TRANSPORT_METADATA,
                warning_deadline_at=None,
                hard_stale_deadline_at=None,
                age_seconds=None,
                age_days_display=None,
            )

    def test_negative_byte_size_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(raw_byte_size=-1)

    def test_negative_duplicate_count_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(duplicate_count=-1)

    def test_invalid_sha256_too_short_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(raw_sha256="a" * 63)

    def test_invalid_sha256_uppercase_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(raw_sha256="A" * 64)

    def test_invalid_sha256_non_hex_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(raw_sha256="g" * 64)

    def test_valid_sha256_accepted(self):
        _acquisition_record(raw_sha256="0123456789abcdef" * 4)

    def test_duplicate_count_exceeding_row_count_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(row_count=5, duplicate_count=6, accepted_row_count=5, rejected_row_count=0)

    def test_accepted_plus_rejected_exceeding_row_count_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(row_count=10, accepted_row_count=8, rejected_row_count=8, duplicate_count=0)

    def test_accepted_plus_rejected_equal_to_row_count_accepted(self):
        _acquisition_record(row_count=10, accepted_row_count=7, rejected_row_count=3, duplicate_count=0)

    def test_valid_partial_failure_record_accepted(self):
        # A source that never completed acquisition: no bytes, no hash, no
        # row count -- a legitimate, non-success partial-failure record.
        _acquisition_record(
            result_status=AcquisitionResultStatus.TIMEOUT,
            acquisition_completed_at=None,
            response_status_code=None,
            retrieved_at=None,
            source_effective_at=None,
            freshness_evaluated_at=None,
            freshness_basis=FreshnessBasis.UNKNOWN,
            raw_byte_size=None,
            raw_sha256=None,
            validation_status=ValidationResultStatus.INVALID_TRANSPORT_METADATA,
            row_count=None,
            accepted_row_count=None,
            rejected_row_count=None,
            warning_deadline_at=None,
            hard_stale_deadline_at=None,
            age_seconds=None,
            age_days_display=None,
        )

    def test_success_without_completed_at_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(result_status=AcquisitionResultStatus.SUCCESS, acquisition_completed_at=None)

    def test_success_without_raw_sha256_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(result_status=AcquisitionResultStatus.SUCCESS, raw_sha256=None)

    def test_success_without_row_count_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(result_status=AcquisitionResultStatus.SUCCESS, row_count=None)

    def test_not_attempted_with_valid_status_and_no_lkg_rejected(self):
        with pytest.raises(ValueError):
            _acquisition_record(
                result_status=AcquisitionResultStatus.NOT_ATTEMPTED,
                validation_status=ValidationResultStatus.VALID,
                using_last_known_good=False,
                source_data_origin=SourceDataOrigin.CURRENT,
            )

    def test_not_attempted_with_valid_status_and_lkg_accepted(self):
        _acquisition_record(
            result_status=AcquisitionResultStatus.NOT_ATTEMPTED,
            validation_status=ValidationResultStatus.VALID,
            using_last_known_good=True,
            source_data_origin=SourceDataOrigin.LAST_KNOWN_GOOD,
            last_known_good_snapshot_id="lkg-snap-1",
        )

    def test_canonical_url_must_be_non_empty(self):
        with pytest.raises(ValueError):
            _acquisition_record(canonical_url="")

    def test_snapshot_id_must_be_non_empty(self):
        with pytest.raises(ValueError):
            _acquisition_record(snapshot_id="")


# ---------------------------------------------------------------------------
# Conflict policy
# ---------------------------------------------------------------------------


class TestClassificationConflictPolicy:
    def test_provisional_policy_accepted(self):
        policy = _conflict_policy()
        assert policy.calibration_status is CalibrationStatus.PROVISIONAL

    def test_empty_policy_version_rejected(self):
        with pytest.raises(ValueError):
            _conflict_policy(policy_version="")

    def test_whitespace_only_policy_version_rejected(self):
        with pytest.raises(ValueError):
            _conflict_policy(policy_version="   ")

    def test_negative_isolated_max_count_rejected(self):
        with pytest.raises(ValueError):
            _conflict_policy(isolated_max_count=-1)

    def test_isolated_max_ratio_above_one_rejected(self):
        with pytest.raises(ValueError):
            _conflict_policy(isolated_max_ratio=1.5)

    def test_isolated_max_ratio_negative_rejected(self):
        with pytest.raises(ValueError):
            _conflict_policy(isolated_max_ratio=-0.1)

    def test_systemic_source_ratio_above_one_rejected(self):
        with pytest.raises(ValueError):
            _conflict_policy(systemic_source_ratio=1.01)

    def test_boundary_ratios_accepted(self):
        _conflict_policy(isolated_max_ratio=0.0, systemic_source_ratio=1.0)

    def test_zero_minimum_compared_row_count_rejected(self):
        with pytest.raises(ValueError):
            _conflict_policy(minimum_compared_row_count=0)

    def test_negative_minimum_compared_row_count_rejected(self):
        with pytest.raises(ValueError):
            _conflict_policy(minimum_compared_row_count=-5)

    def test_shadow_validated_and_ratified_representable(self):
        _conflict_policy(calibration_status=CalibrationStatus.SHADOW_VALIDATED)
        _conflict_policy(calibration_status=CalibrationStatus.RATIFIED)

    def test_no_module_level_default_policy_instance_exists(self):
        import services.instrument_master.consumer_approval_contracts as mod

        for name in dir(mod):
            value = getattr(mod, name)
            assert not isinstance(value, ClassificationConflictPolicy), (
                f"module-level default ClassificationConflictPolicy instance found: {name}"
            )


# ---------------------------------------------------------------------------
# Consumer approval record
# ---------------------------------------------------------------------------


class TestConsumerApprovalEvaluation:
    def test_valid_blocked_evaluation(self):
        evaluation = _blocked_evaluation()
        assert evaluation.approved is False
        assert evaluation.consumer_included_instrument_classes == ()

    def test_valid_restricted_evaluation(self):
        evaluation = _restricted_evaluation()
        assert evaluation.approved is True
        assert evaluation.consumer_included_instrument_classes == (InstrumentClass.ORDINARY_EQUITY,)

    def test_valid_core_complete_evaluation(self):
        evaluation = _core_complete_evaluation()
        assert evaluation.approved is True
        assert evaluation.taxonomy_readiness_mode is TaxonomyReadinessMode.CORE_TAXONOMY_COMPLETE

    def test_etf_cannot_enter_included_classes(self):
        # Phase C2-D2B correction (ratified C2-D2A audit finding #1): must
        # fail specifically because of the included-classes-must-equal-
        # (ORDINARY_EQUITY,) content check, not because of the unrelated
        # canonical-order check that a two-element (ORDINARY_EQUITY, ETF)
        # tuple would also trip (ETF.value="etf" sorts before
        # ORDINARY_EQUITY.value="ordinary_equity"). Using ETF alone --
        # trivially already in "sorted" order as a single-element tuple --
        # isolates the content invariant from the ordering invariant, and
        # the exact-message match pins the failure to the intended check.
        with pytest.raises(ValueError, match=r"must serve exactly"):
            _restricted_evaluation(consumer_included_instrument_classes=(InstrumentClass.ETF,))

    def test_sme_cannot_enter_included_classes(self):
        # Isolated the same way as the ETF case above, for consistency and
        # to avoid relying on SME's value happening to already sort after
        # ORDINARY_EQUITY -- a single-element tuple isolates the content
        # check regardless of any enum member's alphabetical position.
        with pytest.raises(ValueError, match=r"must serve exactly"):
            _core_complete_evaluation(consumer_included_instrument_classes=(InstrumentClass.SME,))

    def test_included_classes_ordering_is_a_distinct_invariant_from_content(self):
        # Separate, focused test for the ordering invariant alone (kept
        # independent of the content assertion above, per the requirement
        # not to combine content and ordering assertions in one test).
        # (ORDINARY_EQUITY, ETF) is content-invalid (two elements, one of
        # which isn't ORDINARY_EQUITY) AND out of canonical order; the
        # ordering check fires first, proving ordering is independently
        # enforced before content is even considered for a non-canonical
        # multi-element tuple.
        with pytest.raises(ValueError, match=r"canonical order"):
            _restricted_evaluation(
                consumer_included_instrument_classes=(InstrumentClass.ORDINARY_EQUITY, InstrumentClass.ETF)
            )

    def test_non_blocked_evaluation_requires_ordinary_equity_only_inclusion(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(consumer_included_instrument_classes=())

    def test_non_blocked_evaluation_requires_exhaustive_exclusion_set(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(consumer_excluded_instrument_classes=(InstrumentClass.ETF,))

    def test_blocked_evaluation_requires_empty_inclusion(self):
        with pytest.raises(ValueError):
            _blocked_evaluation(consumer_included_instrument_classes=(InstrumentClass.ORDINARY_EQUITY,))

    def test_blocked_evaluation_requires_approved_false(self):
        with pytest.raises(ValueError):
            _blocked_evaluation(approved=True)

    def test_source_snapshot_id_may_be_null_only_when_blocked(self):
        _blocked_evaluation(source_snapshot_id=None)  # allowed
        with pytest.raises(ValueError):
            _restricted_evaluation(source_snapshot_id=None)  # not allowed

    def test_evaluation_id_always_required(self):
        with pytest.raises(ValueError):
            _blocked_evaluation(evaluation_id="")

    def test_restricted_mode_rejects_blocked_taxonomy(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(taxonomy_readiness_mode=TaxonomyReadinessMode.BLOCKED)

    def test_restricted_mode_rejects_equity_only_unsafe_taxonomy(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(taxonomy_readiness_mode=TaxonomyReadinessMode.EQUITY_ONLY_UNSAFE_FOR_CONSUMERS)

    def test_restricted_mode_accepts_core_complete_taxonomy(self):
        _restricted_evaluation(taxonomy_readiness_mode=TaxonomyReadinessMode.CORE_TAXONOMY_COMPLETE)

    def test_core_complete_mode_requires_core_complete_taxonomy(self):
        with pytest.raises(ValueError):
            _core_complete_evaluation(taxonomy_readiness_mode=TaxonomyReadinessMode.EQUITY_ETF_CLASSIFIED)

    def test_equity_only_unsafe_taxonomy_always_blocked(self):
        with pytest.raises(ValueError):
            _blocked_evaluation(
                taxonomy_readiness_mode=TaxonomyReadinessMode.EQUITY_ONLY_UNSAFE_FOR_CONSUMERS,
                approval_mode=ConsumerApprovalMode.RESTRICTED_NON_SME,
                approved=True,
                consumer_included_instrument_classes=(InstrumentClass.ORDINARY_EQUITY,),
                source_snapshot_id="snap-x",
            )

    def test_approved_must_equal_approval_mode_not_blocked(self):
        with pytest.raises(ValueError):
            _blocked_evaluation(approval_mode=ConsumerApprovalMode.BLOCKED, approved=True)
        with pytest.raises(ValueError):
            _restricted_evaluation(approval_mode=ConsumerApprovalMode.RESTRICTED_NON_SME, approved=False)

    def test_non_blocked_requires_non_empty_required_source_ids(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(required_source_ids=())

    def test_non_blocked_requires_empty_blocking_reason_codes(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(blocking_reason_codes=(SourceErrorCode.SOURCE_STALE_WARNING,))

    def test_using_lkg_sources_must_be_subset_of_required(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(using_last_known_good_sources=frozenset({SourceId.NSE_SME_CURRENT}))

    def test_current_source_ids_must_be_subset_of_required(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(current_source_ids=(SourceId.NSE_SME_CURRENT,))

    def test_deterministic_source_ordering_enforced(self):
        with pytest.raises(ValueError):
            _restricted_evaluation(
                required_source_ids=(SourceId.NSE_ETF_CURRENT, SourceId.NSE_EQUITY_CURRENT)  # wrong order
            )

    def test_deterministic_error_code_ordering_enforced(self):
        with pytest.raises(ValueError):
            _blocked_evaluation(
                blocking_reason_codes=(
                    SourceErrorCode.SOURCE_UNEXPECTED_EMPTY,
                    SourceErrorCode.SOURCE_UNAVAILABLE,
                )  # wrong order (not sorted by .value)
            )

    def test_conflict_policy_version_required(self):
        with pytest.raises(ValueError):
            _blocked_evaluation(conflict_policy_version="")


# ---------------------------------------------------------------------------
# Current/last-known-good evidence-origin disjointness (Phase C2-D2B
# correction, ratified C2-D2A audit finding #3)
# ---------------------------------------------------------------------------


class TestCurrentLkgDisjointness:
    def test_one_overlapping_source_id_rejected(self):
        with pytest.raises(ValueError, match=r"must be disjoint"):
            _restricted_evaluation(
                current_source_ids=tuple(
                    sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
                ),
                using_last_known_good_sources=frozenset({SourceId.NSE_ETF_CURRENT}),
            )

    def test_multiple_overlaps_reported_deterministically(self):
        with pytest.raises(ValueError) as exc_info:
            _core_complete_evaluation(
                current_source_ids=tuple(
                    sorted(
                        (SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT, SourceId.NSE_SME_CURRENT),
                        key=lambda s: s.value,
                    )
                ),
                using_last_known_good_sources=frozenset({SourceId.NSE_ETF_CURRENT, SourceId.NSE_SME_CURRENT}),
            )
        # Overlapping SourceIds must appear sorted by .value, regardless of
        # the frozenset's own (unordered) internal iteration order.
        assert "['nse_etf_current', 'nse_sme_current']" in str(exc_info.value)

    def test_disjoint_current_and_lkg_sets_accepted(self):
        evaluation = _restricted_evaluation(
            current_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
            using_last_known_good_sources=frozenset({SourceId.NSE_ETF_CURRENT}),
        )
        assert evaluation.approved is True

    def test_construction_order_does_not_affect_error_message(self):
        # frozenset has no defined iteration order -- build the same
        # overlap two different ways and confirm identical error text.
        kwargs_a = dict(
            current_source_ids=tuple(
                sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
            ),
            using_last_known_good_sources=frozenset([SourceId.NSE_ETF_CURRENT, SourceId.NSE_EQUITY_CURRENT])
            - {SourceId.NSE_EQUITY_CURRENT},
        )
        kwargs_b = dict(
            current_source_ids=tuple(
                sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
            ),
            using_last_known_good_sources=frozenset({SourceId.NSE_ETF_CURRENT}),
        )
        with pytest.raises(ValueError) as exc_a:
            _restricted_evaluation(**kwargs_a)
        with pytest.raises(ValueError) as exc_b:
            _restricted_evaluation(**kwargs_b)
        assert str(exc_a.value) == str(exc_b.value)

    def test_blocked_evaluation_with_overlap_also_rejected(self):
        with pytest.raises(ValueError, match=r"must be disjoint"):
            _blocked_evaluation(
                current_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
                using_last_known_good_sources=frozenset({SourceId.NSE_EQUITY_CURRENT}),
            )


# ---------------------------------------------------------------------------
# Required-source evidence coverage (Phase C2-D2B correction, ratified
# C2-D2A audit finding #4)
# ---------------------------------------------------------------------------


class TestRequiredSourceEvidenceCoverage:
    def test_non_blocked_with_every_required_source_current_accepted(self):
        evaluation = _restricted_evaluation(
            required_source_ids=tuple(
                sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
            ),
            current_source_ids=tuple(
                sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
            ),
            using_last_known_good_sources=frozenset(),
        )
        assert evaluation.approved is True

    def test_non_blocked_with_valid_mixture_of_current_and_lkg_accepted(self):
        evaluation = _restricted_evaluation(
            required_source_ids=tuple(
                sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
            ),
            current_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
            using_last_known_good_sources=frozenset({SourceId.NSE_ETF_CURRENT}),
        )
        assert evaluation.approved is True

    def test_non_blocked_missing_one_required_source_rejected(self):
        with pytest.raises(ValueError, match=r"missing coverage"):
            _restricted_evaluation(
                required_source_ids=tuple(
                    sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
                ),
                current_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
                using_last_known_good_sources=frozenset(),
            )

    def test_non_blocked_missing_multiple_required_sources_deterministic(self):
        with pytest.raises(ValueError) as exc_info:
            _core_complete_evaluation(
                required_source_ids=tuple(
                    sorted(
                        (SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT, SourceId.NSE_SME_CURRENT),
                        key=lambda s: s.value,
                    )
                ),
                current_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
                using_last_known_good_sources=frozenset(),
            )
        assert "['nse_etf_current', 'nse_sme_current']" in str(exc_info.value)

    def test_non_blocked_with_empty_current_and_lkg_evidence_rejected(self):
        with pytest.raises(ValueError, match=r"missing coverage"):
            _restricted_evaluation(
                required_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
                current_source_ids=(),
                using_last_known_good_sources=frozenset(),
            )

    def test_blocked_with_incomplete_evidence_accepted(self):
        evaluation = _blocked_evaluation(
            required_source_ids=tuple(
                sorted((SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT), key=lambda s: s.value)
            ),
            current_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
            using_last_known_good_sources=frozenset(),
        )
        assert evaluation.approved is False

    def test_blocked_with_no_evidence_accepted(self):
        evaluation = _blocked_evaluation(
            required_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
            current_source_ids=(),
            using_last_known_good_sources=frozenset(),
        )
        assert evaluation.approved is False

    def test_construction_order_does_not_affect_missing_source_message(self):
        base = dict(
            required_source_ids=tuple(
                sorted(
                    (SourceId.NSE_EQUITY_CURRENT, SourceId.NSE_ETF_CURRENT, SourceId.NSE_SME_CURRENT),
                    key=lambda s: s.value,
                )
            ),
            current_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
            using_last_known_good_sources=frozenset(),
        )
        with pytest.raises(ValueError) as exc_a:
            _core_complete_evaluation(**base)
        with pytest.raises(ValueError) as exc_b:
            _core_complete_evaluation(**dict(base))
        assert str(exc_a.value) == str(exc_b.value)


# ---------------------------------------------------------------------------
# Deterministic serialization
# ---------------------------------------------------------------------------


class TestDeterministicSerialization:
    def test_acquisition_record_repeated_serialization_identical(self):
        record = _acquisition_record()
        assert canonical_source_acquisition_record_bytes(record) == canonical_source_acquisition_record_bytes(record)
        assert canonical_source_acquisition_record_hash(record) == canonical_source_acquisition_record_hash(record)

    def test_conflict_policy_repeated_serialization_identical(self):
        policy = _conflict_policy()
        assert canonical_classification_conflict_policy_bytes(policy) == canonical_classification_conflict_policy_bytes(policy)
        assert canonical_classification_conflict_policy_hash(policy) == canonical_classification_conflict_policy_hash(policy)

    def test_evaluation_repeated_serialization_identical(self):
        evaluation = _restricted_evaluation()
        assert canonical_consumer_approval_evaluation_bytes(evaluation) == canonical_consumer_approval_evaluation_bytes(
            evaluation
        )
        assert canonical_consumer_approval_evaluation_hash(evaluation) == canonical_consumer_approval_evaluation_hash(
            evaluation
        )

    def test_equal_evaluations_constructed_separately_hash_identically(self):
        a = _restricted_evaluation()
        b = _restricted_evaluation()
        assert canonical_consumer_approval_evaluation_hash(a) == canonical_consumer_approval_evaluation_hash(b)

    def test_using_last_known_good_sources_serializes_sorted(self):
        # ETF and SME are on last-known-good fallback here, so they must be
        # removed from current_source_ids (Phase C2-D2B's disjointness
        # invariant, §TestCurrentLkgDisjointness) -- only equity remains
        # "current".
        evaluation = _core_complete_evaluation(
            current_source_ids=(SourceId.NSE_EQUITY_CURRENT,),
            using_last_known_good_sources=frozenset({SourceId.NSE_SME_CURRENT, SourceId.NSE_ETF_CURRENT}),
        )
        canonical_bytes = canonical_consumer_approval_evaluation_bytes(evaluation)
        assert b'"nse_etf_current","nse_sme_current"' in canonical_bytes

    def test_error_codes_serialize_sorted_by_value(self):
        record = _acquisition_record(
            validation_error_codes=tuple(
                sorted(
                    (SourceErrorCode.SOURCE_UNEXPECTED_EMPTY, SourceErrorCode.SOURCE_UNAVAILABLE),
                    key=lambda c: c.value,
                )
            )
        )
        canonical_bytes = canonical_source_acquisition_record_bytes(record)
        assert b'"source_unavailable","source_unexpected_empty"' in canonical_bytes


# ---------------------------------------------------------------------------
# Legacy protection
# ---------------------------------------------------------------------------


class TestLegacyProtection:
    def test_manifest_contract_module_not_imported_by_new_module(self):
        import ast
        import inspect

        import services.instrument_master.consumer_approval_contracts as mod

        tree = ast.parse(inspect.getsource(mod))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        assert not any("manifest_contract" in name for name in imported), (
            "consumer_approval_contracts.py must not import manifest_contract.py in Phase C2-D2"
        )

    def test_legacy_completeness_contract_fields_unchanged(self):
        from services.instrument_master.manifest_contract import build_completeness_contract
        from services.instrument_master.source_registry import all_entries
        from services.instrument_master.manifest_contract import SourceReadinessStatus
        from services.instrument_master.enums import ValidationResultStatus as VRS

        entries = all_entries()
        statuses = {
            e.source_id: SourceReadinessStatus(
                source_id=e.source_id,
                available=True,
                validation_valid=True,
                fresh=True,
                validation_status=VRS.VALID,
            )
            for e in entries
        }
        contract = build_completeness_contract(
            source_statuses=statuses,
            source_errors=(),
            oldest_source_age_days=0,
            newest_source_age_days=0,
            business_policy_permits_daily_picks=True,
            business_policy_permits_daily_picks_non_sme=True,
            business_policy_permits_screener=True,
            business_policy_permits_recommendations=True,
            sme_explicitly_excluded_from_eligible_universe=True,
            unknown_unclassified_securities_excluded=True,
            manual_source_present=False,
            manual_source_reviewed_by=None,
            manual_source_reviewed_at=None,
        )
        # Unchanged, exact Phase C1 derivation: approved_for_screener /
        # approved_for_recommendations are still critical_ready-gated
        # (equity alone), NOT redefined by anything in Phase C2-D2.
        assert contract.approved_for_screener is True
        assert contract.approved_for_recommendations is True
        assert contract.approved_for_daily_picks is True
        assert contract.approved_for_daily_picks_non_sme is True

    def test_no_live_consumer_imports_the_new_module(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import ast, glob, os\n"
                "hits = []\n"
                "for path in glob.glob('api/**/*.py', recursive=True) + glob.glob('services/**/*.py', recursive=True):\n"
                "    if 'instrument_master' in path:\n"
                "        continue\n"
                "    with open(path, encoding='utf-8') as f:\n"
                "        try:\n"
                "            tree = ast.parse(f.read(), filename=path)\n"
                "        except SyntaxError:\n"
                "            continue\n"
                "    for node in ast.walk(tree):\n"
                "        if isinstance(node, ast.ImportFrom) and node.module and 'consumer_approval_contracts' in node.module:\n"
                "            hits.append(path)\n"
                "        if isinstance(node, ast.Import):\n"
                "            for alias in node.names:\n"
                "                if 'consumer_approval_contracts' in alias.name:\n"
                "                    hits.append(path)\n"
                "print(len(hits))\n",
            ],
            cwd=_backend_dir(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "0"


def _backend_dir() -> str:
    import os

    return os.path.join(os.path.dirname(__file__), "..", "..")
