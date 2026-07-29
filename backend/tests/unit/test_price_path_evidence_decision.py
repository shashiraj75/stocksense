"""
Trade Postmortem Sprint 3A, Stage J1B — unit tests for
services.postmortem.price_path_evidence_decision: the second stage of
the two-stage historical decision model, evaluated after a persisted-
evidence lookup or a validated provider response, never before.
"""
import pytest

from services.postmortem.price_path_evidence_decision import (
    ACQUISITION_REQUIRED,
    AMBIGUOUS_RESOLUTION,
    CALCULATION_ELIGIBLE,
    CALCULATION_UNAVAILABLE,
    COMPATIBLE_REPLAY,
    PARTIAL_EVIDENCE,
    SOURCE_INVALID,
    SOURCE_UNAVAILABLE,
    classify_acquired_evidence,
    classify_provider_failure,
    classify_replay_or_acquisition,
    compute_report_completeness_ceiling,
)


@pytest.mark.unit
class TestReplayVersusAcquisition:
    def test_compatible_evidence_means_no_provider_call(self):
        result = classify_replay_or_acquisition(compatible_evidence_found=True)
        assert result.evidence_status == COMPATIBLE_REPLAY
        assert result.provider_call_expected is False
        assert result.calculation_status == CALCULATION_ELIGIBLE
        assert result.report_completeness_ceiling == "COMPLETE"

    def test_no_compatible_evidence_means_acquisition_required(self):
        result = classify_replay_or_acquisition(compatible_evidence_found=False)
        assert result.evidence_status == ACQUISITION_REQUIRED
        assert result.provider_call_expected is True
        assert result.calculation_status == CALCULATION_UNAVAILABLE


@pytest.mark.unit
class TestProviderFailureClassification:
    def test_generic_fetch_failure_is_source_unavailable_not_invalid(self):
        """A transient/network provider failure is never treated as
        proof the trade or symbol was invalid — it is a distinct,
        named, non-terminal evidence state."""
        result = classify_provider_failure(error_code="PROVIDER_FETCH_FAILED")
        assert result.evidence_status == SOURCE_UNAVAILABLE
        assert result.calculation_status == CALCULATION_UNAVAILABLE
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_unexpected_response_shape_is_source_invalid(self):
        result = classify_provider_failure(error_code="PROVIDER_UNEXPECTED_COLUMN_SHAPE")
        assert result.evidence_status == SOURCE_INVALID

    def test_response_too_large_is_source_unavailable_not_invalid(self):
        result = classify_provider_failure(error_code="PROVIDER_RESPONSE_TOO_LARGE")
        assert result.evidence_status == SOURCE_UNAVAILABLE

    def test_fallback_text_is_exact(self):
        result = classify_provider_failure(error_code="PROVIDER_FETCH_FAILED")
        assert result.limitations == ("Insufficient evidence to determine this factor reliably.",)


@pytest.mark.unit
class TestAcquiredEvidenceClassification:
    """data_completeness is the REAL, already-computed field on
    PricePathEvidenceBundle (price_path_acquisition.build_price_path_
    evidence's own 5-value STATUS_* enum) — this classifier derives
    from it directly rather than from separately-invented signals."""

    def test_unavailable_is_source_unavailable_never_invalid(self):
        """Empty provider history is never evidence the trade/symbol
        was invalid — build_price_path_evidence already returns a
        bundle (not an exception) for this, so this must classify it as
        a named unavailable state, not fabricate anything."""
        result = classify_acquired_evidence(data_completeness="UNAVAILABLE")
        assert result.evidence_status == SOURCE_UNAVAILABLE
        assert result.calculation_status == CALCULATION_UNAVAILABLE
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_invalid_source_data_is_source_invalid(self):
        result = classify_acquired_evidence(data_completeness="INVALID_SOURCE_DATA")
        assert result.evidence_status == SOURCE_INVALID
        assert result.calculation_status == CALCULATION_UNAVAILABLE
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_ambiguous_resolution_still_permits_calculation(self):
        """A same-day partial-session trade (or a split inside the
        window — build_price_path_evidence's own real AMBIGUOUS_
        RESOLUTION trigger) may still support MFE/MAE even while touch-
        order claims stay unavailable."""
        result = classify_acquired_evidence(data_completeness="AMBIGUOUS_RESOLUTION")
        assert result.evidence_status == AMBIGUOUS_RESOLUTION
        assert result.calculation_status == CALCULATION_ELIGIBLE
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_partial_completeness_still_eligible_but_capped(self):
        result = classify_acquired_evidence(data_completeness="PARTIAL")
        assert result.evidence_status == PARTIAL_EVIDENCE
        assert result.calculation_status == CALCULATION_ELIGIBLE
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_complete_is_fully_eligible(self):
        result = classify_acquired_evidence(data_completeness="COMPLETE")
        assert result.evidence_status == CALCULATION_ELIGIBLE
        assert result.calculation_status == CALCULATION_ELIGIBLE
        assert result.report_completeness_ceiling == "COMPLETE"

    def test_unrecognized_value_defaults_to_complete_eligible_never_fabricates_a_limitation(self):
        result = classify_acquired_evidence(data_completeness="SOME_FUTURE_STATUS_THIS_MAPPING_DOES_NOT_KNOW")
        assert result.evidence_status == CALCULATION_ELIGIBLE
        assert result.limitations == ()


@pytest.mark.unit
class TestMinimumCeilingComposition:
    """Stage J3 — the final report ceiling must never exceed the
    weakest applicable ceiling; a downstream COMPLETE value can never
    override an upstream LIMITED_EVIDENCE one."""

    def test_all_complete_stays_complete(self):
        assert compute_report_completeness_ceiling("COMPLETE", "COMPLETE", "COMPLETE") == "COMPLETE"

    def test_single_limited_drags_everything_down(self):
        assert compute_report_completeness_ceiling("COMPLETE", "LIMITED_EVIDENCE", "COMPLETE") == "LIMITED_EVIDENCE"

    def test_all_limited_stays_limited(self):
        assert compute_report_completeness_ceiling("LIMITED_EVIDENCE", "LIMITED_EVIDENCE") == "LIMITED_EVIDENCE"

    def test_single_input(self):
        assert compute_report_completeness_ceiling("COMPLETE") == "COMPLETE"
        assert compute_report_completeness_ceiling("LIMITED_EVIDENCE") == "LIMITED_EVIDENCE"

    def test_no_inputs_defaults_complete(self):
        # Vacuous minimum — a caller composing zero ceiling inputs has
        # asserted nothing limiting; callers are expected to always pass
        # at least the trade-context ceiling.
        assert compute_report_completeness_ceiling() == "COMPLETE"
