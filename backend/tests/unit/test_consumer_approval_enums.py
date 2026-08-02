"""
Phase C2-D2 — consumer-approval contract enum tests.
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
    SourceErrorSeverity,
    TaxonomyReadinessMode,
)


def _assert_exact_values(enum_cls, expected: dict):
    assert {m.name: m.value for m in enum_cls} == expected


class TestTaxonomyReadinessMode:
    def test_exact_values(self):
        _assert_exact_values(
            TaxonomyReadinessMode,
            {
                "BLOCKED": "blocked",
                "EQUITY_ONLY_UNSAFE_FOR_CONSUMERS": "equity_only_unsafe_for_consumers",
                "EQUITY_ETF_CLASSIFIED": "equity_etf_classified",
                "CORE_TAXONOMY_COMPLETE": "core_taxonomy_complete",
            },
        )

    def test_four_members_exactly(self):
        assert len(list(TaxonomyReadinessMode)) == 4

    def test_round_trip_construction(self):
        for member in TaxonomyReadinessMode:
            assert TaxonomyReadinessMode(member.value) is member


class TestConsumerApprovalMode:
    def test_exact_values(self):
        _assert_exact_values(
            ConsumerApprovalMode,
            {
                "BLOCKED": "blocked",
                "RESTRICTED_NON_SME": "restricted_non_sme",
                "CORE_TAXONOMY_COMPLETE": "core_taxonomy_complete",
            },
        )

    def test_three_members_exactly(self):
        assert len(list(ConsumerApprovalMode)) == 3

    def test_round_trip_construction(self):
        for member in ConsumerApprovalMode:
            assert ConsumerApprovalMode(member.value) is member


class TestConsumerType:
    def test_exact_values(self):
        _assert_exact_values(
            ConsumerType,
            {
                "SCREENER": "screener",
                "RECOMMENDATIONS": "recommendations",
                "DAILY_PICKS": "daily_picks",
            },
        )

    def test_round_trip_construction(self):
        for member in ConsumerType:
            assert ConsumerType(member.value) is member


class TestInstrumentClass:
    def test_exact_values(self):
        _assert_exact_values(
            InstrumentClass,
            {
                "ORDINARY_EQUITY": "ordinary_equity",
                "ETF": "etf",
                "SME": "sme",
                "REIT": "reit",
                "INVIT": "invit",
                "PREFERENCE_SHARE": "preference_share",
                "WARRANT": "warrant",
                "IDR": "idr",
                "ILLIQUID_OR_RESTRICTED": "illiquid_or_restricted",
                "UNKNOWN_UNCLASSIFIED": "unknown_unclassified",
            },
        )

    def test_ten_members_exactly(self):
        assert len(list(InstrumentClass)) == 10

    def test_round_trip_construction(self):
        for member in InstrumentClass:
            assert InstrumentClass(member.value) is member


class TestFreshnessBasis:
    def test_exact_values(self):
        _assert_exact_values(
            FreshnessBasis,
            {
                "SOURCE_REPORTED_DATE": "source_reported_date",
                "HTTP_LAST_MODIFIED": "http_last_modified",
                "ACQUISITION_TIME_PROXY": "acquisition_time_proxy",
                "UNKNOWN": "unknown",
            },
        )

    def test_four_members_exactly(self):
        assert len(list(FreshnessBasis)) == 4

    def test_does_not_include_last_known_good_effective_time(self):
        # Per the ratified Phase C2-D1B correction: an LKG snapshot retains
        # whichever basis it was originally captured under -- there is no
        # separate "LKG" basis value.
        assert "last_known_good_effective_time" not in {m.value for m in FreshnessBasis}
        assert not any("LAST_KNOWN_GOOD" in m.name for m in FreshnessBasis)


class TestSourceDataOrigin:
    def test_exact_values(self):
        _assert_exact_values(
            SourceDataOrigin,
            {"CURRENT": "current", "LAST_KNOWN_GOOD": "last_known_good"},
        )

    def test_round_trip_construction(self):
        for member in SourceDataOrigin:
            assert SourceDataOrigin(member.value) is member


class TestSourceErrorSeverity:
    def test_exact_values(self):
        _assert_exact_values(SourceErrorSeverity, {"ERROR": "error", "WARNING": "warning"})


class TestAcquisitionResultStatus:
    def test_exact_values(self):
        _assert_exact_values(
            AcquisitionResultStatus,
            {
                "SUCCESS": "success",
                "HTTP_ERROR": "http_error",
                "TIMEOUT": "timeout",
                "TRANSPORT_ERROR": "transport_error",
                "UNEXPECTED_CONTENT_TYPE": "unexpected_content_type",
                "EMPTY_UNEXPECTED": "empty_unexpected",
                "NOT_ATTEMPTED": "not_attempted",
            },
        )

    def test_all_values_lowercase(self):
        for member in AcquisitionResultStatus:
            assert member.value == member.value.lower()

    def test_round_trip_construction(self):
        for member in AcquisitionResultStatus:
            assert AcquisitionResultStatus(member.value) is member


class TestCalibrationStatus:
    def test_exact_values(self):
        _assert_exact_values(
            CalibrationStatus,
            {
                "PROVISIONAL": "provisional",
                "SHADOW_VALIDATED": "shadow_validated",
                "RATIFIED": "ratified",
            },
        )

    def test_round_trip_construction(self):
        for member in CalibrationStatus:
            assert CalibrationStatus(member.value) is member


class TestSourceErrorCode:
    EXPECTED_VALUES = {
        "SOURCE_UNAVAILABLE": "source_unavailable",
        "SOURCE_INVALID_STRUCTURE": "source_invalid_structure",
        "SOURCE_FRESHNESS_UNKNOWN": "source_freshness_unknown",
        "SOURCE_STALE_WARNING": "source_stale_warning",
        "SOURCE_STALE_HARD": "source_stale_hard",
        "SOURCE_FUTURE_DATED": "source_future_dated",
        "SOURCE_UNEXPECTED_EMPTY": "source_unexpected_empty",
        "LAST_KNOWN_GOOD_MISSING_SNAPSHOT": "last_known_good_missing_snapshot",
        "LAST_KNOWN_GOOD_EXPIRED": "last_known_good_expired",
        "LAST_KNOWN_GOOD_WARNING_DEADLINE_CROSSED": "last_known_good_warning_deadline_crossed",
        "ACQUISITION_READINESS_CONTRADICTION": "acquisition_readiness_contradiction",
        "ACQUISITION_VALIDATOR_MISMATCH": "acquisition_validator_mismatch",
        "ACQUISITION_URL_MISMATCH": "acquisition_url_mismatch",
        "SOURCE_ROW_COUNT_MISMATCH": "source_row_count_mismatch",
        "AGGREGATE_DERIVATION_MISMATCH": "aggregate_derivation_mismatch",
        "UNKNOWN_UNCLASSIFIED_INSTRUMENT_EXCLUDED": "unknown_unclassified_instrument_excluded",
        "CROSS_SOURCE_ISIN_CONFLICT_ISOLATED": "cross_source_isin_conflict_isolated",
        "CROSS_SOURCE_SYSTEMIC_CONFLICT": "cross_source_systemic_conflict",
        "SCHEMA_OR_MAPPING_INTEGRITY_DEFECT": "schema_or_mapping_integrity_defect",
    }

    def test_exact_values(self):
        _assert_exact_values(SourceErrorCode, self.EXPECTED_VALUES)

    def test_nineteen_members_exactly(self):
        assert len(list(SourceErrorCode)) == 19

    def test_round_trip_construction(self):
        for member in SourceErrorCode:
            assert SourceErrorCode(member.value) is member

    def test_covers_source_unavailable(self):
        assert SourceErrorCode.SOURCE_UNAVAILABLE.value == "source_unavailable"

    def test_covers_structural_validation_failure(self):
        assert SourceErrorCode.SOURCE_INVALID_STRUCTURE.value == "source_invalid_structure"

    def test_covers_freshness_unknown(self):
        assert SourceErrorCode.SOURCE_FRESHNESS_UNKNOWN.value == "source_freshness_unknown"

    def test_covers_stale_warning_and_hard(self):
        assert SourceErrorCode.SOURCE_STALE_WARNING.value == "source_stale_warning"
        assert SourceErrorCode.SOURCE_STALE_HARD.value == "source_stale_hard"

    def test_covers_future_effective_timestamp(self):
        assert SourceErrorCode.SOURCE_FUTURE_DATED.value == "source_future_dated"

    def test_covers_unexpected_empty_result(self):
        assert SourceErrorCode.SOURCE_UNEXPECTED_EMPTY.value == "source_unexpected_empty"

    def test_covers_lkg_missing_snapshot_identity(self):
        assert SourceErrorCode.LAST_KNOWN_GOOD_MISSING_SNAPSHOT.value == "last_known_good_missing_snapshot"

    def test_covers_lkg_expired(self):
        assert SourceErrorCode.LAST_KNOWN_GOOD_EXPIRED.value == "last_known_good_expired"

    def test_covers_acquisition_readiness_disagreement(self):
        assert SourceErrorCode.ACQUISITION_READINESS_CONTRADICTION.value == "acquisition_readiness_contradiction"

    def test_covers_acquisition_validator_disagreement(self):
        assert SourceErrorCode.ACQUISITION_VALIDATOR_MISMATCH.value == "acquisition_validator_mismatch"

    def test_covers_url_registry_mismatch(self):
        assert SourceErrorCode.ACQUISITION_URL_MISMATCH.value == "acquisition_url_mismatch"

    def test_covers_row_count_mismatch(self):
        assert SourceErrorCode.SOURCE_ROW_COUNT_MISMATCH.value == "source_row_count_mismatch"

    def test_covers_aggregate_derivation_mismatch(self):
        assert SourceErrorCode.AGGREGATE_DERIVATION_MISMATCH.value == "aggregate_derivation_mismatch"

    def test_covers_unknown_instrument(self):
        assert (
            SourceErrorCode.UNKNOWN_UNCLASSIFIED_INSTRUMENT_EXCLUDED.value
            == "unknown_unclassified_instrument_excluded"
        )

    def test_covers_isolated_and_systemic_conflict(self):
        assert SourceErrorCode.CROSS_SOURCE_ISIN_CONFLICT_ISOLATED.value == "cross_source_isin_conflict_isolated"
        assert SourceErrorCode.CROSS_SOURCE_SYSTEMIC_CONFLICT.value == "cross_source_systemic_conflict"

    def test_covers_schema_or_mapping_integrity_defect(self):
        assert SourceErrorCode.SCHEMA_OR_MAPPING_INTEGRITY_DEFECT.value == "schema_or_mapping_integrity_defect"


class TestEnumUniqueness:
    @pytest.mark.parametrize(
        "enum_cls",
        [
            TaxonomyReadinessMode,
            ConsumerApprovalMode,
            ConsumerType,
            InstrumentClass,
            FreshnessBasis,
            SourceDataOrigin,
            SourceErrorSeverity,
            AcquisitionResultStatus,
            CalibrationStatus,
            SourceErrorCode,
        ],
    )
    def test_no_duplicate_values(self, enum_cls):
        values = [m.value for m in enum_cls]
        assert len(values) == len(set(values)), f"{enum_cls.__name__} has duplicate values"
