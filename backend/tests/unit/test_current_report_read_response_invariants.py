"""
Wave C, WC-K — unit tests for CurrentReportReadResponse's model-level
READY/non-READY invariants (typed availability/status contract).
"""
import datetime

import pytest
from pydantic import ValidationError

from api.routers.paper_trading import CurrentReportReadResponse

_READY_KWARGS = dict(
    trade_id=1, availability="READY",
    report_schema_version="1.2.0", calculation_version="calc-v1",
    attribution_rules_version="rules-v1", evidence_bundle_version="ev-v1",
    market="US", report_trading_date="2026-06-01", market_timezone="America/New_York",
    status="COMPLETE", generated_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
    structured_report={"x": 1}, claims=[], evidence_items=[], evidence_gaps=[], warnings=[],
    source_manifest={},
)

_NON_READY_STATES = (
    "PROCESSING", "NOT_ELIGIBLE", "NOT_AVAILABLE",
    "TERMINAL_FAILURE", "INTEGRITY_CONTRADICTION", "FEATURE_DISABLED",
)


@pytest.mark.unit
class TestAvailabilityLiteralRestriction:
    @pytest.mark.parametrize("state", ("READY",) + _NON_READY_STATES)
    def test_every_frozen_availability_state_is_accepted(self, state):
        kwargs = dict(_READY_KWARGS) if state == "READY" else {"trade_id": 1, "availability": state}
        CurrentReportReadResponse(**kwargs)  # must not raise

    def test_unknown_availability_value_is_rejected(self):
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(trade_id=1, availability="SOMETHING_ELSE")


@pytest.mark.unit
class TestReadyStatusLiteralRestriction:
    def test_complete_is_accepted(self):
        CurrentReportReadResponse(**{**_READY_KWARGS, "status": "COMPLETE"})

    def test_limited_evidence_is_accepted(self):
        CurrentReportReadResponse(**{**_READY_KWARGS, "status": "LIMITED_EVIDENCE"})

    def test_unknown_status_value_is_rejected(self):
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(**{**_READY_KWARGS, "status": "PARTIAL"})


@pytest.mark.unit
class TestNonReadyStatesExposeNoReportPayload:
    @pytest.mark.parametrize("state", _NON_READY_STATES)
    def test_non_ready_state_with_no_report_fields_is_valid(self, state):
        CurrentReportReadResponse(trade_id=1, availability=state)  # must not raise

    @pytest.mark.parametrize("state", _NON_READY_STATES)
    @pytest.mark.parametrize("leaked_field,leaked_value", [
        ("structured_report", {"x": 1}),
        ("claims", []),
        ("report_schema_version", "1.2.0"),
        ("status", "COMPLETE"),
        ("generated_at", datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)),
        ("source_manifest", {}),
    ])
    def test_non_ready_state_leaking_any_report_field_is_rejected(self, state, leaked_field, leaked_value):
        with pytest.raises(ValidationError, match="must not carry report"):
            CurrentReportReadResponse(trade_id=1, availability=state, **{leaked_field: leaked_value})


@pytest.mark.unit
class TestReadyRequiresCompleteReportIdentity:
    def test_fully_populated_ready_is_valid(self):
        CurrentReportReadResponse(**_READY_KWARGS)  # must not raise

    @pytest.mark.parametrize("missing_field", [
        "report_schema_version", "calculation_version", "attribution_rules_version",
        "evidence_bundle_version", "market", "report_trading_date", "market_timezone",
        "status", "generated_at", "structured_report", "claims", "evidence_items",
        "source_manifest",
    ])
    def test_ready_missing_a_required_field_is_rejected(self, missing_field):
        kwargs = dict(_READY_KWARGS)
        kwargs[missing_field] = None
        with pytest.raises(ValidationError, match="missing required report field"):
            CurrentReportReadResponse(**kwargs)

    def test_ready_with_null_supersedes_report_id_is_valid(self):
        """supersedes_report_id is legitimately null for a non-superseding
        report — must NOT be treated as a missing required field."""
        kwargs = dict(_READY_KWARGS)
        kwargs["supersedes_report_id"] = None
        CurrentReportReadResponse(**kwargs)  # must not raise

    def test_ready_with_empty_evidence_gaps_and_warnings_is_valid(self):
        """Empty lists are legitimate (not missing) for a COMPLETE report
        with no gaps/warnings."""
        kwargs = dict(_READY_KWARGS)
        kwargs["evidence_gaps"] = []
        kwargs["warnings"] = []
        CurrentReportReadResponse(**kwargs)  # must not raise
