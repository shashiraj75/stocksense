"""
Wave C, WC-K — unit tests for CurrentReportReadResponse's model-level
READY/non-READY invariants (typed availability/status contract).
"""
import datetime

import pytest
from pydantic import ValidationError

from api.routers.paper_trading import _READY_ONLY_FIELDS, CurrentReportReadResponse

# One valid, non-null representative value for EVERY field in the
# production _READY_ONLY_FIELDS inventory — imported directly from
# production rather than a second hand-maintained list, so this test
# module cannot silently drift from the real leak-check surface.
_REPRESENTATIVE_VALUES = {
    "report_schema_version": "1.2.0",
    "calculation_version": "calc-v1",
    "attribution_rules_version": "rules-v1",
    "evidence_bundle_version": "ev-v1",
    "market": "US",
    "report_trading_date": datetime.date(2026, 6, 1),
    "market_timezone": "America/New_York",
    "status": "COMPLETE",
    "generated_at": datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
    "structured_report": {"x": 1},
    "claims": [{"id": "c1"}],
    "evidence_items": [{"id": "e1"}],
    "evidence_gaps": ["gap-1"],
    "warnings": ["warning-1"],
    "source_manifest": {
        "has_entry_snapshot": True, "has_exit_snapshot": True,
        "exit_snapshot_schema_version": "1.0.0", "exit_trigger_timing_verification": "verified",
        "exit_evidence_rules_version": "1.0.0",
        "phase1_calculation_version": "1.0.0", "attribution_rules_version": "1.0.0",
    },
    "supersedes_report_id": 42,
}

_READY_KWARGS = dict(
    trade_id=1, availability="READY",
    report_schema_version="1.2.0", calculation_version="calc-v1",
    attribution_rules_version="rules-v1", evidence_bundle_version="ev-v1",
    market="US", report_trading_date="2026-06-01", market_timezone="America/New_York",
    status="COMPLETE", generated_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
    structured_report={"x": 1}, claims=[], evidence_items=[], evidence_gaps=[], warnings=[],
    source_manifest={
        "has_entry_snapshot": True, "has_exit_snapshot": False,
        "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
        "exit_evidence_rules_version": "1.0.0",
        "phase1_calculation_version": "1.0.0", "attribution_rules_version": "1.0.0",
    },
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
    @pytest.mark.parametrize("leaked_field", _READY_ONLY_FIELDS)
    def test_non_ready_state_leaking_any_report_field_is_rejected(self, state, leaked_field):
        leaked_value = _REPRESENTATIVE_VALUES[leaked_field]
        with pytest.raises(ValidationError, match="must not carry report"):
            CurrentReportReadResponse(trade_id=1, availability=state, **{leaked_field: leaked_value})

    def test_drift_guard_representative_values_exactly_match_production_inventory(self):
        """Every _READY_ONLY_FIELDS entry must have a representative
        test value, and no test-only field may exist outside that
        production inventory — these two lists must never silently
        diverge."""
        production_fields = set(_READY_ONLY_FIELDS)
        test_fields = set(_REPRESENTATIVE_VALUES)
        assert production_fields == test_fields, (
            f"drift detected — missing from test: {production_fields - test_fields}; "
            f"extra in test (not in production): {test_fields - production_fields}"
        )

    def test_drift_guard_ready_only_fields_exactly_match_the_model_field_set(self):
        """Companion guard on the OTHER side: _READY_ONLY_FIELDS itself
        must exactly equal every CurrentReportReadResponse field except
        trade_id and availability (which are present in every response,
        READY or not). A field added to the model without adding it to
        _READY_ONLY_FIELDS would silently escape the non-READY leakage
        check entirely — this fails loudly instead."""
        model_fields = set(CurrentReportReadResponse.model_fields)
        always_present = {"trade_id", "availability"}
        report_only_model_fields = model_fields - always_present
        assert set(_READY_ONLY_FIELDS) == report_only_model_fields, (
            f"drift detected — model field(s) missing from _READY_ONLY_FIELDS: "
            f"{report_only_model_fields - set(_READY_ONLY_FIELDS)}; "
            f"_READY_ONLY_FIELDS entries not on the model: "
            f"{set(_READY_ONLY_FIELDS) - report_only_model_fields}"
        )


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


@pytest.mark.unit
class TestFailClosedConversionBoundary:
    """Unit-level proof for _build_current_report_ready_response — the
    fail-closed boundary between a persisted report row and the typed
    READY response."""

    def _fake_report(self, **overrides):
        from dataclasses import dataclass, field

        @dataclass
        class _FakeReport:
            id: int = 1
            report_schema_version: str | None = "1.2.0"
            calculation_version: str | None = "calc-v1"
            attribution_rules_version: str | None = "rules-v1"
            evidence_bundle_version: str | None = "ev-v1"
            market: str | None = "US"
            report_trading_date: object = datetime.date(2026, 6, 1)
            market_timezone: str | None = "America/New_York"
            status: str | None = "COMPLETE"
            generated_at: object = None
            structured_report: dict | None = field(default_factory=lambda: {"x": 1})
            claims: list | None = field(default_factory=list)
            evidence_items: list | None = field(default_factory=list)
            evidence_gaps: list | None = field(default_factory=list)
            warnings: list | None = field(default_factory=list)
            source_manifest: dict | None = field(default_factory=lambda: {
                "has_entry_snapshot": True, "has_exit_snapshot": False,
                "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
                "exit_evidence_rules_version": "1.0.0",
                "phase1_calculation_version": "1.0.0", "attribution_rules_version": "1.0.0",
            })
            supersedes_report_id: int | None = None

        kwargs = dict(generated_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc))
        kwargs.update(overrides)
        return _FakeReport(**kwargs)

    def test_valid_report_builds_a_ready_response(self):
        from api.routers.paper_trading import _build_current_report_ready_response

        result = _build_current_report_ready_response(self._fake_report(), trade_id=1)
        assert result is not None
        assert result.availability == "READY"
        assert result.status == "COMPLETE"

    def test_malformed_status_returns_none_not_a_raised_exception(self):
        from api.routers.paper_trading import _build_current_report_ready_response

        result = _build_current_report_ready_response(
            self._fake_report(status="NOT_A_VALID_STATUS"), trade_id=1,
        )
        assert result is None

    def test_missing_required_field_returns_none_not_a_raised_exception(self):
        from api.routers.paper_trading import _build_current_report_ready_response

        result = _build_current_report_ready_response(
            self._fake_report(report_schema_version=None), trade_id=1,
        )
        assert result is None

    def test_pydantic_validation_error_is_caught_and_returns_none(self):
        """The helper's contract is narrower than 'never raises': it
        catches pydantic.ValidationError specifically (the expected
        failure mode for a malformed persisted row) and returns None.
        It does NOT catch Exception broadly — an unrelated programming
        defect must still propagate rather than being silently
        converted into a plausible-looking INTEGRITY_CONTRADICTION."""
        from api.routers.paper_trading import _build_current_report_ready_response

        try:
            result = _build_current_report_ready_response(
                self._fake_report(status="BOGUS", generated_at=None), trade_id=1,
            )
        except Exception as exc:  # noqa: BLE001 — this IS the assertion
            pytest.fail(
                f"a malformed-status ValidationError should be caught and return None, "
                f"but {type(exc).__name__} escaped instead: {exc}"
            )
        assert result is None

    def test_a_genuine_attribute_error_is_not_swallowed(self):
        """An object missing an expected attribute entirely (a real
        programming defect, not a validation failure of PERSISTED
        VALUES) must propagate as AttributeError, not be silently
        converted into a fabricated INTEGRITY_CONTRADICTION."""
        from api.routers.paper_trading import _build_current_report_ready_response

        class _BrokenReport:
            """Deliberately missing every expected attribute."""

        with pytest.raises(AttributeError):
            _build_current_report_ready_response(_BrokenReport(), trade_id=1)


@pytest.mark.unit
class TestReportSourceManifestAbsencePreservingSerialization:
    """price_path_calculation_version is historical-optional — a
    persisted report whose source_manifest genuinely omits this key
    must serialize WITHOUT the key, not as an explicit `null`."""

    _BASE_KWARGS = dict(
        has_entry_snapshot=True, has_exit_snapshot=True,
        exit_evidence_rules_version="1.0.0", phase1_calculation_version="1.0.0",
        attribution_rules_version="1.0.0",
    )

    def test_key_absent_from_input_stays_absent_in_serialized_output(self):
        from api.routers.paper_trading import ReportSourceManifest
        import json

        manifest = ReportSourceManifest(**self._BASE_KWARGS)  # price_path_calculation_version omitted
        serialized = json.loads(manifest.model_dump_json())
        assert "price_path_calculation_version" not in serialized

    def test_explicit_persisted_value_is_preserved(self):
        from api.routers.paper_trading import ReportSourceManifest
        import json

        manifest = ReportSourceManifest(**self._BASE_KWARGS, price_path_calculation_version="pp-marker")
        serialized = json.loads(manifest.model_dump_json())
        assert serialized["price_path_calculation_version"] == "pp-marker"

    def test_explicit_persisted_null_is_preserved_as_null(self):
        """An explicit JSON null in the persisted row (distinct from
        the key being genuinely absent) must remain null, not be
        dropped — the distinction is legitimate and preserved via
        model_fields_set, which IS populated when None is passed
        explicitly as a keyword argument."""
        from api.routers.paper_trading import ReportSourceManifest
        import json

        manifest = ReportSourceManifest(**self._BASE_KWARGS, price_path_calculation_version=None)
        serialized = json.loads(manifest.model_dump_json())
        assert "price_path_calculation_version" in serialized
        assert serialized["price_path_calculation_version"] is None

    def test_absence_is_preserved_through_the_full_current_report_response(self):
        """End-to-end proof at the shape the real endpoint actually
        constructs: CurrentReportReadResponse(source_manifest=<dict>)
        coerces the nested dict into ReportSourceManifest — absence
        must survive that coercion too, not just direct construction."""
        import datetime
        import json
        from api.routers.paper_trading import CurrentReportReadResponse

        kwargs = dict(_READY_KWARGS)
        kwargs["source_manifest"] = dict(self._BASE_KWARGS)  # price_path_calculation_version omitted
        response = CurrentReportReadResponse(**kwargs)
        serialized = json.loads(response.model_dump_json())
        assert "price_path_calculation_version" not in serialized["source_manifest"]
