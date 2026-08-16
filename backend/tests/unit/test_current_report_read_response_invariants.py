"""
Wave C, WC-K — unit tests for CurrentReportReadResponse's model-level
READY/non-READY invariants (typed availability/status contract).
"""
import datetime

import pytest
from pydantic import ValidationError

from api.routers.paper_trading import _READY_ONLY_FIELDS, CurrentReportReadResponse

# A valid structured_report satisfying StructuredReportModel/
# PricePathSection/VersionAndProvenance — every current 1.2.0 report
# unconditionally has structured_report.price_path.version_and_provenance
# (verified by direct source read of current_report_generation.
# build_current_report_payload). "postmortem" is an example unrelated,
# untyped top-level section preserved via extra="allow".
_VALID_STRUCTURED_REPORT = {
    "postmortem": {"outcome": "WIN"},
    "price_path": {
        "raw_evidence": None,
        "version_and_provenance": {
            "report_schema_version": "1.2.0", "calculation_version": "calc-v1",
            "numerical_rules_version": "1.0.0", "governed_semantic_rules_version": "1.0.0",
            "governed_claim_rules_version": "1.0.0", "entry_snapshot_schema_version": None,
            "exit_snapshot_schema_version": None, "level_history_contract_version": None,
            "source_version": "1.0.0",
        },
    },
}

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
    "structured_report": _VALID_STRUCTURED_REPORT,
    "claims": [{
        "claim_id": "CLM-1-rule1", "report_section": "price_path", "factor": "target",
        "claim_text": "target was hit", "evidence_class": "DIRECTLY_OBSERVED",
        "confidence_band": "HIGH", "supporting_evidence_ids": ["EV-1-target-hit"],
        "opposing_evidence_ids": [], "missing_evidence": [], "contradiction_flags": [],
        "rule_id": "rule1", "rule_version": "1.0.0",
    }],
    "evidence_items": [{
        "evidence_id": "EV-1-target-hit", "category": "price_path", "name": "target_hit",
        "value": True, "units": None, "observation_timestamp": None,
        "source": "market_data", "source_type": "SERVER_DERIVED", "verification_level": "MECHANICALLY_VERIFIED",
        "freshness_status": "POINT_IN_TIME_VALID",
    }],
    "evidence_gaps": ["gap-1"],
    "warnings": ["warning-1"],
    "source_manifest": {
        "has_entry_snapshot": True, "has_exit_snapshot": True,
        "exit_snapshot_schema_version": "1.0.0", "exit_trigger_timing_verification": "SERVER_VERIFIED",
        "exit_evidence_rules_version": "1.0.0",
        "phase1_calculation_version": "1.0.0", "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
    },
    "supersedes_report_id": 42,
    "symbol": "AAPL",
    "company_name": "Apple Inc.",
}

_READY_KWARGS = dict(
    trade_id=1, availability="READY",
    report_schema_version="1.2.0", calculation_version="calc-v1",
    attribution_rules_version="rules-v1", evidence_bundle_version="ev-v1",
    market="US", report_trading_date="2026-06-01", market_timezone="America/New_York",
    status="COMPLETE", generated_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
    structured_report=_VALID_STRUCTURED_REPORT, claims=[], evidence_items=[], evidence_gaps=[], warnings=[],
    source_manifest={
        "has_entry_snapshot": True, "has_exit_snapshot": False,
        "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
        "exit_evidence_rules_version": "1.0.0",
        "phase1_calculation_version": "1.0.0", "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
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
            structured_report: dict | None = field(default_factory=lambda: dict(_VALID_STRUCTURED_REPORT))
            claims: list | None = field(default_factory=list)
            evidence_items: list | None = field(default_factory=list)
            evidence_gaps: list | None = field(default_factory=list)
            warnings: list | None = field(default_factory=list)
            source_manifest: dict | None = field(default_factory=lambda: {
                "has_entry_snapshot": True, "has_exit_snapshot": False,
                "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
                "exit_evidence_rules_version": "1.0.0",
                "phase1_calculation_version": "1.0.0", "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
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
        exit_snapshot_schema_version="1.0.0", exit_trigger_timing_verification="SERVER_VERIFIED",
        exit_evidence_rules_version="1.0.0", phase1_calculation_version="1.0.0",
        attribution_rules_version="1.0.0",
        price_path_rules_version="1.0.0", governed_rules_version="1.0.0",
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

    def test_explicit_persisted_null_is_treated_as_malformed(self):
        """Production only ever establishes two legitimate states for
        price_path_calculation_version: entirely absent (historical,
        non-enhanced) or a real non-empty version string (enhanced).
        An explicit JSON null is not a state production has evidence
        for, so it is rejected as malformed rather than silently
        legitimized as a third state — this ValidationError is what
        the fail-closed conversion boundary turns into
        INTEGRITY_CONTRADICTION at the route layer."""
        from api.routers.paper_trading import ReportSourceManifest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="not a recognized persisted state"):
            ReportSourceManifest(**self._BASE_KWARGS, price_path_calculation_version=None)

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


@pytest.mark.unit
class TestReportSourceManifestSemanticConsistency:
    """generation_service.py's builder guarantees exit_snapshot_schema_
    version and exit_trigger_timing_verification are BOTH null exactly
    when has_exit_snapshot is False, and BOTH set when True. A
    persisted row violating this shape is malformed."""

    _NO_EXIT = dict(
        has_entry_snapshot=True, has_exit_snapshot=False,
        exit_evidence_rules_version="1.0.0", phase1_calculation_version="1.0.0",
        attribution_rules_version="1.0.0",
        price_path_rules_version="1.0.0", governed_rules_version="1.0.0",
    )
    _WITH_EXIT = dict(
        has_entry_snapshot=True, has_exit_snapshot=True,
        exit_snapshot_schema_version="1.0.0", exit_trigger_timing_verification="SERVER_VERIFIED",
        exit_evidence_rules_version="1.0.0", phase1_calculation_version="1.0.0",
        attribution_rules_version="1.0.0",
        price_path_rules_version="1.0.0", governed_rules_version="1.0.0",
    )

    def test_no_exit_snapshot_with_both_fields_null_is_valid(self):
        from api.routers.paper_trading import ReportSourceManifest

        ReportSourceManifest(**self._NO_EXIT)  # must not raise

    def test_has_exit_snapshot_with_both_fields_set_is_valid(self):
        from api.routers.paper_trading import ReportSourceManifest

        ReportSourceManifest(**self._WITH_EXIT)  # must not raise

    def test_no_exit_snapshot_with_non_null_schema_version_is_malformed(self):
        from api.routers.paper_trading import ReportSourceManifest

        with pytest.raises(ValidationError, match="requires exit_snapshot_schema_version"):
            ReportSourceManifest(**{**self._NO_EXIT, "exit_snapshot_schema_version": "1.0.0"})

    def test_no_exit_snapshot_with_non_null_trigger_timing_is_malformed(self):
        from api.routers.paper_trading import ReportSourceManifest

        with pytest.raises(ValidationError, match="requires exit_snapshot_schema_version"):
            ReportSourceManifest(**{**self._NO_EXIT, "exit_trigger_timing_verification": "SERVER_VERIFIED"})

    def test_has_exit_snapshot_missing_schema_version_is_malformed(self):
        from api.routers.paper_trading import ReportSourceManifest

        with pytest.raises(ValidationError, match="requires a non-empty exit_snapshot_schema_version"):
            ReportSourceManifest(**{**self._WITH_EXIT, "exit_snapshot_schema_version": None})

    def test_has_exit_snapshot_missing_trigger_timing_is_malformed(self):
        from api.routers.paper_trading import ReportSourceManifest

        with pytest.raises(ValidationError, match="requires a governed exit_trigger_timing_verification"):
            ReportSourceManifest(**{**self._WITH_EXIT, "exit_trigger_timing_verification": None})

    def test_invalid_trigger_timing_vocabulary_is_rejected(self):
        from api.routers.paper_trading import ReportSourceManifest

        with pytest.raises(ValidationError):
            ReportSourceManifest(**{**self._WITH_EXIT, "exit_trigger_timing_verification": "MADE_UP_VALUE"})

    @pytest.mark.parametrize("value", ["NOT_APPLICABLE", "CLIENT_REPORTED_UNVERIFIED", "SERVER_VERIFIED"])
    def test_every_governed_trigger_timing_value_is_accepted(self, value):
        from api.routers.paper_trading import ReportSourceManifest

        ReportSourceManifest(**{**self._WITH_EXIT, "exit_trigger_timing_verification": value})  # must not raise


@pytest.mark.unit
class TestReferentialIntegrityAndClaimEvidenceGovernance:
    """Package A3 — governed claim/evidence vocabularies and report-wide
    referential integrity, at both the model level and the fail-closed
    conversion boundary."""

    _VALID_CLAIM = dict(
        claim_id="CLM-1", report_section="s", factor="f", claim_text="t",
        evidence_class="DIRECTLY_OBSERVED", confidence_band="HIGH",
        supporting_evidence_ids=["EV-1"], opposing_evidence_ids=[], missing_evidence=[],
        contradiction_flags=[], rule_id="r1", rule_version="1.0.0",
    )
    _VALID_EVIDENCE = dict(
        evidence_id="EV-1", category="c", name="n", value=1, units=None,
        observation_timestamp=None, source="s", source_type="SERVER_DERIVED",
        verification_level="MECHANICALLY_VERIFIED", freshness_status="POINT_IN_TIME_VALID",
    )

    def test_valid_claim_and_evidence_construct_without_raising(self):
        from api.routers.paper_trading import EvidenceItemModel, PostmortemClaimModel

        PostmortemClaimModel(**self._VALID_CLAIM)
        EvidenceItemModel(**self._VALID_EVIDENCE)

    @pytest.mark.parametrize("field,value", [
        ("evidence_class", "MADE_UP"), ("confidence_band", "MADE_UP"),
    ])
    def test_invalid_claim_enum_value_is_rejected(self, field, value):
        from api.routers.paper_trading import PostmortemClaimModel

        with pytest.raises(ValidationError):
            PostmortemClaimModel(**{**self._VALID_CLAIM, field: value})

    @pytest.mark.parametrize("field,value", [
        ("source_type", "MADE_UP"), ("verification_level", "MADE_UP"), ("freshness_status", "MADE_UP"),
    ])
    def test_invalid_evidence_enum_value_is_rejected(self, field, value):
        from api.routers.paper_trading import EvidenceItemModel

        with pytest.raises(ValidationError):
            EvidenceItemModel(**{**self._VALID_EVIDENCE, field: value})

    def test_insufficient_evidence_with_wrong_sentence_is_rejected(self):
        from api.routers.paper_trading import PostmortemClaimModel

        with pytest.raises(ValidationError, match="claim semantic rule violated"):
            PostmortemClaimModel(**{
                **self._VALID_CLAIM, "evidence_class": "INSUFFICIENT_EVIDENCE",
                "confidence_band": "NOT_ASSESSABLE", "supporting_evidence_ids": [], "claim_text": "wrong sentence",
            })

    def test_insufficient_evidence_citing_support_is_rejected(self):
        from api.routers.paper_trading import PostmortemClaimModel
        from services.postmortem.evidence import INSUFFICIENT_EVIDENCE_SENTENCE

        with pytest.raises(ValidationError, match="claim semantic rule violated"):
            PostmortemClaimModel(**{
                **self._VALID_CLAIM, "evidence_class": "INSUFFICIENT_EVIDENCE",
                "confidence_band": "NOT_ASSESSABLE", "claim_text": INSUFFICIENT_EVIDENCE_SENTENCE,
                "supporting_evidence_ids": ["EV-1"],
            })

    def test_ordinary_claim_without_supporting_evidence_is_rejected(self):
        from api.routers.paper_trading import PostmortemClaimModel

        with pytest.raises(ValidationError, match="claim semantic rule violated"):
            PostmortemClaimModel(**{**self._VALID_CLAIM, "supporting_evidence_ids": []})

    def test_conflicting_claim_without_opposing_evidence_is_rejected(self):
        from api.routers.paper_trading import PostmortemClaimModel

        with pytest.raises(ValidationError, match="claim semantic rule violated"):
            PostmortemClaimModel(**{**self._VALID_CLAIM, "evidence_class": "CONFLICTING_EVIDENCE"})

    def test_empty_rule_id_is_rejected(self):
        from api.routers.paper_trading import PostmortemClaimModel

        with pytest.raises(ValidationError, match="claim semantic rule violated"):
            PostmortemClaimModel(**{**self._VALID_CLAIM, "rule_id": ""})

    def test_unexpected_extra_claim_field_is_rejected(self):
        from api.routers.paper_trading import PostmortemClaimModel

        with pytest.raises(ValidationError):
            PostmortemClaimModel(**{**self._VALID_CLAIM, "unexpected_field": "x"})

    def test_unexpected_extra_evidence_field_is_rejected(self):
        from api.routers.paper_trading import EvidenceItemModel

        with pytest.raises(ValidationError):
            EvidenceItemModel(**{**self._VALID_EVIDENCE, "unexpected_field": "x"})

    def test_dangling_supporting_reference_fails_closed_at_the_conversion_boundary(self):
        from api.routers.paper_trading import _build_current_report_ready_response

        report = TestFailClosedConversionBoundary()._fake_report(
            claims=[{**self._VALID_CLAIM, "supporting_evidence_ids": ["EV-NONEXISTENT"]}],
            evidence_items=[],
        )
        assert _build_current_report_ready_response(report, trade_id=1) is None

    def test_dangling_opposing_reference_fails_closed_at_the_conversion_boundary(self):
        from api.routers.paper_trading import _build_current_report_ready_response

        report = TestFailClosedConversionBoundary()._fake_report(
            claims=[{
                **self._VALID_CLAIM, "evidence_class": "CONFLICTING_EVIDENCE",
                "opposing_evidence_ids": ["EV-NONEXISTENT"],
            }],
            evidence_items=[self._VALID_EVIDENCE],
        )
        assert _build_current_report_ready_response(report, trade_id=1) is None

    def test_valid_reference_builds_a_ready_response(self):
        from api.routers.paper_trading import _build_current_report_ready_response

        report = TestFailClosedConversionBoundary()._fake_report(
            claims=[self._VALID_CLAIM], evidence_items=[self._VALID_EVIDENCE],
        )
        result = _build_current_report_ready_response(report, trade_id=1)
        assert result is not None
        assert result.availability == "READY"


@pytest.mark.unit
class TestStructuredReportProvenanceTyping:
    """Package A4 — structured_report.price_path.version_and_provenance."""

    _VALID_VAP = dict(
        report_schema_version="1.2.0", calculation_version="c", numerical_rules_version="1.0.0",
        governed_semantic_rules_version="1.0.0", governed_claim_rules_version="1.0.0",
        entry_snapshot_schema_version=None, exit_snapshot_schema_version=None,
        level_history_contract_version=None, source_version="1.0.0",
    )

    def _structured_report(self, **vap_overrides):
        return {
            "postmortem": {"outcome": "WIN"},
            "price_path": {"raw_evidence": None, "version_and_provenance": {**self._VALID_VAP, **vap_overrides}},
        }

    def test_valid_complete_provenance_is_accepted(self):
        CurrentReportReadResponse(**{**_READY_KWARGS, "structured_report": self._structured_report()})

    def test_valid_nullable_snapshot_fields_are_accepted(self):
        CurrentReportReadResponse(**{**_READY_KWARGS, "structured_report": self._structured_report(
            entry_snapshot_schema_version="1.0.0", exit_snapshot_schema_version="1.0.0",
            level_history_contract_version="1.0.0",
        )})

    def test_missing_price_path_is_rejected(self):
        bad = {"postmortem": {"outcome": "WIN"}}
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(**{**_READY_KWARGS, "structured_report": bad})

    def test_price_path_with_wrong_type_is_rejected(self):
        bad = {"price_path": "not-a-dict"}
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(**{**_READY_KWARGS, "structured_report": bad})

    def test_missing_version_and_provenance_is_rejected(self):
        bad = {"price_path": {"raw_evidence": None}}
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(**{**_READY_KWARGS, "structured_report": bad})

    def test_version_and_provenance_with_wrong_type_is_rejected(self):
        bad = {"price_path": {"version_and_provenance": "not-a-dict"}}
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(**{**_READY_KWARGS, "structured_report": bad})

    @pytest.mark.parametrize("missing_field", [
        "report_schema_version", "calculation_version", "numerical_rules_version",
        "governed_semantic_rules_version", "governed_claim_rules_version", "source_version",
    ])
    def test_every_missing_required_vap_field_is_rejected(self, missing_field):
        vap = dict(self._VALID_VAP)
        del vap[missing_field]
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(**{**_READY_KWARGS, "structured_report": {"price_path": {"version_and_provenance": vap}}})

    @pytest.mark.parametrize("field", ["report_schema_version", "calculation_version", "source_version"])
    def test_invalid_field_type_is_rejected(self, field):
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(**{**_READY_KWARGS, "structured_report": self._structured_report(**{field: 12345})})

    def test_unexpected_extra_vap_field_is_rejected(self):
        with pytest.raises(ValidationError):
            CurrentReportReadResponse(**{
                **_READY_KWARGS,
                "structured_report": self._structured_report(unexpected_field="surprise"),
            })

    def test_unrelated_structured_report_and_price_path_fields_are_preserved(self):
        response = CurrentReportReadResponse(**{
            **_READY_KWARGS,
            "structured_report": {
                "postmortem": {"outcome": "WIN"}, "attribution": {"thesis_verdict": "SUPPORTED"},
                "price_path": {
                    "raw_evidence": {"bars": []}, "level_history": {"target": {}},
                    "version_and_provenance": self._VALID_VAP,
                },
            },
        })
        dumped = response.model_dump()
        assert dumped["structured_report"]["postmortem"] == {"outcome": "WIN"}
        assert dumped["structured_report"]["attribution"] == {"thesis_verdict": "SUPPORTED"}
        assert dumped["structured_report"]["price_path"]["raw_evidence"] == {"bars": []}
        assert dumped["structured_report"]["price_path"]["level_history"] == {"target": {}}
