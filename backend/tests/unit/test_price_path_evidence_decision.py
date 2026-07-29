"""
Trade Postmortem Sprint 3A, Stage J1B (Fail-Closed-Hardening pass) —
unit tests for services.postmortem.price_path_evidence_decision: the
second stage of the two-stage historical decision model, split into a
WHERE-did-evidence-come-from acquisition decision and a
WHETHER-is-it-usable quality decision, evaluated after a persisted-
evidence lookup or a validated provider response, never before.
"""
import pytest

from services.postmortem.price_path_evidence_decision import (
    ACQUISITION_REQUIRED,
    AMBIGUOUS_RESOLUTION,
    CALCULATION_ELIGIBLE,
    CALCULATION_UNAVAILABLE,
    COMPATIBLE_REPLAY,
    IMMEDIATE_FAILED_RETRYABLE,
    IMMEDIATE_FAILED_TERMINAL,
    INTERNAL_INTEGRITY_VIOLATION,
    PARTIAL_EVIDENCE,
    SOURCE_INVALID,
    SOURCE_UNAVAILABLE,
    UNSUPPORTED_EVIDENCE_COMPLETENESS,
    classify_acquired_evidence,
    classify_replay_or_acquisition,
    compute_report_completeness_ceiling,
    get_provider_failure_policy,
)


@pytest.mark.unit
class TestAcquisitionDecision:
    """WHERE the evidence came from — must never be overwritten by
    anything discovered about evidence quality afterward."""

    def test_compatible_evidence_means_no_provider_call(self):
        result = classify_replay_or_acquisition(compatible_evidence_found=True, evidence_id=42)
        assert result.acquisition_status == COMPATIBLE_REPLAY
        assert result.provider_call_expected is False
        assert result.compatible_evidence_found is True
        assert result.evidence_id == 42

    def test_no_compatible_evidence_means_acquisition_required(self):
        result = classify_replay_or_acquisition(compatible_evidence_found=False)
        assert result.acquisition_status == ACQUISITION_REQUIRED
        assert result.provider_call_expected is True
        assert result.compatible_evidence_found is False
        assert result.evidence_id is None

    def test_acquisition_decision_has_no_calculation_or_ceiling_fields(self):
        """The acquisition decision must not claim anything about
        evidence quality -- no calculation_status, no ceiling."""
        result = classify_replay_or_acquisition(compatible_evidence_found=True, evidence_id=1)
        assert not hasattr(result, "calculation_status")
        assert not hasattr(result, "report_completeness_ceiling")


@pytest.mark.unit
class TestProviderFailurePolicy:
    """get_provider_failure_policy/ProviderFailurePolicy is the SOLE
    provider-failure authority (Stage J1B-Assurance-Closure) -- an
    earlier classify_provider_failure function duplicated this same
    mapping but was never actually called by the live path, and was
    removed rather than left as a second, unconsulted table."""

    def test_generic_fetch_failure_is_source_unavailable_not_invalid(self):
        policy = get_provider_failure_policy(error_code="PROVIDER_FETCH_FAILED")
        assert policy.evidence_status == SOURCE_UNAVAILABLE

    def test_policy_has_exactly_three_load_bearing_fields(self):
        """Stage J1B-Final-Reconciliation -- ProviderFailurePolicy was
        reduced to exactly the fields the live path reads. A caught
        provider exception categorically has no validated bundle to
        persist and no basis for a report for every current code; that
        is a property of the exception path itself, not a per-field
        policy decision, so no separate persistence/report/limitations
        fields exist to claim otherwise."""
        import dataclasses
        from services.postmortem.price_path_evidence_decision import ProviderFailurePolicy
        field_names = {f.name for f in dataclasses.fields(ProviderFailurePolicy)}
        assert field_names == {"evidence_status", "immediate_outbox_outcome", "sanitized_error_code"}

    @pytest.mark.parametrize("code", ["PROVIDER_FETCH_FAILED", "PROVIDER_RESPONSE_TOO_LARGE", "PROVIDER_UNEXPECTED_COLUMN_SHAPE"])
    def test_every_known_code_is_explicitly_mapped(self, code):
        policy = get_provider_failure_policy(error_code=code)
        assert policy.sanitized_error_code == code

    def test_source_invalid_vs_source_unavailable_distinguishable_in_policy(self):
        invalid_policy = get_provider_failure_policy(error_code="PROVIDER_UNEXPECTED_COLUMN_SHAPE")
        unavailable_policy = get_provider_failure_policy(error_code="PROVIDER_FETCH_FAILED")
        assert invalid_policy.evidence_status == SOURCE_INVALID
        assert unavailable_policy.evidence_status == SOURCE_UNAVAILABLE
        assert invalid_policy.evidence_status != unavailable_policy.evidence_status

    def test_unrecognized_code_fails_closed_with_sanitized_generic_code(self):
        policy = get_provider_failure_policy(error_code="SOME_FUTURE_CODE_NEVER_SEEN_BEFORE")
        assert policy.sanitized_error_code == "PROVIDER_ACQUISITION_ERROR"
        assert policy.evidence_status == SOURCE_UNAVAILABLE
        # still bounded/retryable never silently dropped -- not
        # immediately terminal for a code this module has never seen
        assert policy.immediate_outbox_outcome == IMMEDIATE_FAILED_RETRYABLE

    def test_immediate_outbox_outcome_is_never_ambiguous(self):
        """Stage 1 -- exactly one of FAILED_RETRYABLE/FAILED_TERMINAL,
        never a combination the live path must independently resolve."""
        for code in ("PROVIDER_FETCH_FAILED", "PROVIDER_RESPONSE_TOO_LARGE", "PROVIDER_UNEXPECTED_COLUMN_SHAPE", "UNKNOWN"):
            policy = get_provider_failure_policy(error_code=code)
            assert policy.immediate_outbox_outcome in (IMMEDIATE_FAILED_RETRYABLE, IMMEDIATE_FAILED_TERMINAL)


@pytest.mark.unit
class TestAcquiredEvidenceClassification:
    """data_completeness is the REAL, already-computed field on
    PricePathEvidenceBundle (price_path_acquisition.build_price_path_
    evidence's own 5-value STATUS_* enum) — this classifier derives
    from it directly rather than from separately-invented signals."""

    def test_unavailable_is_source_unavailable_never_invalid_and_persistable(self):
        """Empty provider history is never evidence the trade/symbol
        was invalid. A zero-bar bundle is an honest manifest-only
        record (never fabricated bars), so persistence IS permitted."""
        result = classify_acquired_evidence(data_completeness="UNAVAILABLE")
        assert result.evidence_status == SOURCE_UNAVAILABLE
        assert result.calculation_status == CALCULATION_UNAVAILABLE
        assert result.fresh_persistence_permitted is True
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_legacy_invalid_source_data_value_falls_through_to_unsupported(self):
        """Stage J1B-Final-Reconciliation -- INVALID_SOURCE_DATA is not a
        mapped key: build_price_path_evidence never produces it (no
        acquired-bundle producer exists), so a persisted row somehow
        containing it (legacy data, external corruption) must fall
        through to the SAME fail-closed UNSUPPORTED_EVIDENCE_
        COMPLETENESS branch as any other unrecognized value -- never its
        own SOURCE_INVALID classification, which is reserved exclusively
        for ProviderFailurePolicy's genuine live trigger (a caught
        PriceProviderAcquisitionError with code
        PROVIDER_UNEXPECTED_COLUMN_SHAPE)."""
        result = classify_acquired_evidence(data_completeness="INVALID_SOURCE_DATA")
        assert result.evidence_status == UNSUPPORTED_EVIDENCE_COMPLETENESS
        assert result.calculation_status == CALCULATION_UNAVAILABLE
        assert result.fresh_persistence_permitted is False
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_ambiguous_resolution_still_permits_calculation(self):
        result = classify_acquired_evidence(data_completeness="AMBIGUOUS_RESOLUTION")
        assert result.evidence_status == AMBIGUOUS_RESOLUTION
        assert result.calculation_status == CALCULATION_ELIGIBLE
        assert result.fresh_persistence_permitted is True
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_partial_completeness_still_eligible_but_capped(self):
        result = classify_acquired_evidence(data_completeness="PARTIAL")
        assert result.evidence_status == PARTIAL_EVIDENCE
        assert result.calculation_status == CALCULATION_ELIGIBLE
        assert result.fresh_persistence_permitted is True
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"

    def test_complete_is_fully_eligible(self):
        result = classify_acquired_evidence(data_completeness="COMPLETE")
        assert result.evidence_status == CALCULATION_ELIGIBLE
        assert result.calculation_status == CALCULATION_ELIGIBLE
        assert result.fresh_persistence_permitted is True
        assert result.report_completeness_ceiling == "COMPLETE"

    @pytest.mark.parametrize("data_completeness", [
        "COMPLETE", "PARTIAL", "UNAVAILABLE", "AMBIGUOUS_RESOLUTION",
        "INVALID_SOURCE_DATA", None, "", "GARBAGE",
    ])
    def test_never_returns_source_invalid_for_any_input(self, data_completeness):
        """classify_acquired_evidence has no live path to SOURCE_INVALID
        -- that status exists solely for ProviderFailurePolicy."""
        result = classify_acquired_evidence(data_completeness=data_completeness)
        assert result.evidence_status != SOURCE_INVALID


@pytest.mark.unit
class TestFailClosedOnUnsupportedCompleteness:
    """Stage 2 -- the prior fallback silently treated any unrecognized
    value as COMPLETE/CALCULATION_ELIGIBLE. Prohibited: must fail closed."""

    @pytest.mark.parametrize("bad_value", [
        None, "", "complete", "Complete", " COMPLETE", "COMPLETE ", "UNKNOWN",
        "SOME_FUTURE_STATUS_NOT_YET_MAPPED", 0, False,
    ])
    def test_unsupported_value_never_produces_calculation_eligible(self, bad_value):
        result = classify_acquired_evidence(data_completeness=bad_value)
        assert result.evidence_status == UNSUPPORTED_EVIDENCE_COMPLETENESS
        assert result.calculation_status == CALCULATION_UNAVAILABLE
        assert result.fresh_persistence_permitted is False
        assert result.report_completeness_ceiling == "LIMITED_EVIDENCE"
        assert result.limitations == ("Insufficient evidence to determine this factor reliably.",)

    def test_unsupported_value_never_produces_complete_ceiling(self):
        result = classify_acquired_evidence(data_completeness="TOTALLY_MADE_UP")
        assert result.report_completeness_ceiling != "COMPLETE"


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
        assert compute_report_completeness_ceiling() == "COMPLETE"


@pytest.mark.unit
class TestCeilingComposerFailsClosedOnUnknownInputs:
    """Stage J1B-Assurance-Closure, Stage 2 -- the prior implementation
    (`"LIMITED_EVIDENCE" if "LIMITED_EVIDENCE" in ceilings else
    "COMPLETE"`) FAILED OPEN: any value that wasn't literally the string
    "LIMITED_EVIDENCE" fell through to COMPLETE, including garbage no
    caller ever intended as complete. Must fail closed instead."""

    @pytest.mark.parametrize("bad_ceiling", [
        None, "", "complete", "Complete", " COMPLETE", "COMPLETE ",
        "UNKNOWN", "PARTIALLY_COMPLETE", 0, False, "limited_evidence",
    ])
    def test_unknown_ceiling_value_never_yields_complete(self, bad_ceiling):
        assert compute_report_completeness_ceiling("COMPLETE", bad_ceiling) == "LIMITED_EVIDENCE"

    def test_unknown_ceiling_alone_fails_closed(self):
        assert compute_report_completeness_ceiling("GARBAGE") == "LIMITED_EVIDENCE"

    def test_unknown_ceiling_among_many_complete_still_fails_closed(self):
        assert compute_report_completeness_ceiling("COMPLETE", "COMPLETE", "COMPLETE", "TYPO") == "LIMITED_EVIDENCE"

    def test_all_known_values_still_compose_correctly(self):
        """Fail-closed handling must not break the ordinary two-value contract."""
        assert compute_report_completeness_ceiling("COMPLETE", "COMPLETE") == "COMPLETE"
        assert compute_report_completeness_ceiling("COMPLETE", "LIMITED_EVIDENCE") == "LIMITED_EVIDENCE"
