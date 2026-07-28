"""
Unit tests for the report-level integrity validator added during Sprint 1's
independent review (Stage 4 finding: two dangling evidence references were
found in the shipped attribution rules — POSITION_MANAGEMENT/EXIT_LOGIC
"trade" evidence and the momentum-vs-range rule's execution_range_position
evidence were cited in claims but never added to evidence_items).

`build_evidence_attribution` now calls `validate_report_integrity` on every
result and raises `ReportIntegrityError` if it ever finds a violation —
this is a release-blocking trust gate, not an optional check.
"""
import datetime as dt
import itertools

import pytest

from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
from services.postmortem.entry_snapshot import RecommendationContext, build_entry_snapshot
from services.postmortem.evidence import EvidenceClass
from services.postmortem.evidence_attribution import (
    ContributorAssessment,
    EvidenceAttributionResult,
    ReportIntegrityError,
    SignalEvaluation,
    build_evidence_attribution,
    validate_report_integrity,
)

UTC = dt.timezone.utc


def _base_thesis_claim():
    from services.postmortem.evidence import INSUFFICIENT_EVIDENCE_SENTENCE, ConfidenceBand, PostmortemClaim

    return PostmortemClaim(
        claim_id="CLM-1-X", report_section="thesis_verdict", factor="thesis_verdict",
        claim_text=INSUFFICIENT_EVIDENCE_SENTENCE, evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value,
        confidence_band=ConfidenceBand.NOT_ASSESSABLE.value, supporting_evidence_ids=[], opposing_evidence_ids=[],
        missing_evidence=["no thesis captured"], contradiction_flags=[], rule_id="X", rule_version="1.0.0",
    )


def _minimal_result(**overrides) -> EvidenceAttributionResult:
    base = dict(
        trade_id=1, evidence_items=[], claims=[_base_thesis_claim()], signal_scorecard=[], contributor_assessments=[],
        primary_contributor=None, primary_contributor_claim_id=None, thesis_verdict="UNSUPPORTED",
        thesis_verdict_claim_id="CLM-1-X", rule_registry_version="2.0.0",
    )
    base.update(overrides)
    return EvidenceAttributionResult(**base)


class TestValidatorDetectsSyntheticViolations:
    def test_dangling_claim_evidence_reference_detected(self):
        from services.postmortem.evidence import ConfidenceBand, PostmortemClaim

        claim = PostmortemClaim(
            claim_id="CLM-1-X", report_section="s", factor="f", claim_text="text",
            evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.MODERATE.value,
            supporting_evidence_ids=["EV-does-not-exist"], opposing_evidence_ids=[], missing_evidence=[],
            contradiction_flags=[], rule_id="X", rule_version="1.0.0",
        )
        result = _minimal_result(claims=[claim])
        violations = validate_report_integrity(result)
        assert any("EV-does-not-exist" in v for v in violations)

    def test_duplicate_evidence_id_detected(self):
        from services.postmortem.evidence import EvidenceItem

        item = EvidenceItem(
            evidence_id="EV-dup", category="c", name="n", value=1, units=None, observation_timestamp=None,
            source="s", source_type="SERVER_STORED", verification_level="DIRECTLY_OBSERVED",
            freshness_status="POINT_IN_TIME_VALID", limitations=[],
        )
        result = _minimal_result(evidence_items=[item, item])
        violations = validate_report_integrity(result)
        assert any("duplicate evidence_id" in v for v in violations)

    def test_duplicate_claim_id_detected(self):
        from services.postmortem.evidence import INSUFFICIENT_EVIDENCE_SENTENCE, ConfidenceBand, PostmortemClaim

        claim = PostmortemClaim(
            claim_id="CLM-dup", report_section="s", factor="f", claim_text=INSUFFICIENT_EVIDENCE_SENTENCE,
            evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value, confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
            supporting_evidence_ids=[], opposing_evidence_ids=[], missing_evidence=["x"],
            contradiction_flags=[], rule_id="X", rule_version="1.0.0",
        )
        result = _minimal_result(claims=[claim, claim], thesis_verdict_claim_id="CLM-dup")
        violations = validate_report_integrity(result)
        assert any("duplicate claim_id" in v for v in violations)

    def test_dangling_contributor_claim_id_detected(self):
        contributor = ContributorAssessment(
            category="MARKET_CONDITIONS", support_level="NOT_ASSESSABLE",
            evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value, confidence_band="NOT_ASSESSABLE",
            supporting_evidence_ids=[], opposing_evidence_ids=[], claim_id="CLM-does-not-exist",
        )
        result = _minimal_result(contributor_assessments=[contributor])
        violations = validate_report_integrity(result)
        assert any("references unknown claim_id" in v for v in violations)

    def test_dangling_signal_explanation_claim_id_detected(self):
        sig = SignalEvaluation(
            signal_id="technical_signal", signal_name="Technical signal", expected_interpretation=None,
            entry_evidence_id=None, comparison_evidence_ids=[], status="INSUFFICIENT_EVIDENCE",
            evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value, confidence_band="NOT_ASSESSABLE",
            explanation_claim_id="CLM-does-not-exist",
        )
        result = _minimal_result(signal_scorecard=[sig])
        violations = validate_report_integrity(result)
        assert any("explanation_claim_id" in v for v in violations)

    def test_dangling_thesis_verdict_claim_id_detected(self):
        result = _minimal_result(thesis_verdict_claim_id="CLM-does-not-exist")
        violations = validate_report_integrity(result)
        assert any("thesis_verdict_claim_id" in v for v in violations)

    def test_dangling_primary_contributor_claim_id_detected(self):
        result = _minimal_result(primary_contributor_claim_id="CLM-does-not-exist")
        violations = validate_report_integrity(result)
        assert any("primary_contributor_claim_id" in v for v in violations)

    def test_null_primary_contributor_claim_id_is_not_flagged(self):
        result = _minimal_result(primary_contributor=None, primary_contributor_claim_id=None)
        violations = validate_report_integrity(result)
        assert violations == []

    def test_clean_minimal_result_has_no_violations(self):
        assert validate_report_integrity(_minimal_result()) == []


def _closed_trade(*, trade_id, exit_price, exit_reason, stop_loss=90.0, target_price=130.0):
    return ClosedTradeRecord(
        trade_id=trade_id, status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=100.0, exit_price=exit_price, stop_loss=stop_loss, target_price=target_price,
        opened_at=dt.datetime(2026, 6, 1, tzinfo=UTC), closed_at=dt.datetime(2026, 6, 2, tzinfo=UTC),
        trade_management_mode="manual", exit_reason=exit_reason,
    )


@pytest.mark.regression
class TestRealAttributionRunsAlwaysPassIntegrity:
    """Since build_evidence_attribution now raises ReportIntegrityError on
    any violation, simply NOT raising across a wide input matrix is itself
    the proof — this sweeps every exit mechanism, outcome, and evidence
    combination that previously exposed dangling references (position
    management, exit logic, momentum-vs-range) and confirms none raise."""

    def test_position_management_supported_branch_has_no_violations(self):
        record = _closed_trade(trade_id=1, exit_price=95.0, exit_reason="MANUAL", stop_loss=10.0, target_price=101.0)
        pm = compute_postmortem(record)
        result = build_evidence_attribution(pm, None)  # would raise if integrity failed
        assert validate_report_integrity(result) == []

    def test_exit_logic_supported_branch_has_no_violations(self):
        record = _closed_trade(trade_id=2, exit_price=80.0, exit_reason="SOMETHING_UNRECOGNIZED")
        pm = compute_postmortem(record)
        result = build_evidence_attribution(pm, None)
        assert validate_report_integrity(result) == []

    def test_momentum_vs_range_conflicting_branch_has_no_violations(self):
        record = _closed_trade(trade_id=3, exit_price=120.0, exit_reason="MANUAL")
        pm = compute_postmortem(record)
        ctx = RecommendationContext(
            evidence_source="DAILY_PICK", simulated_execution_price=150.0, technical_signal="BUY",
            recommendation_entry_low=100.0, recommendation_entry_high=140.0,
        )
        snapshot = build_entry_snapshot(paper_trade_id=3, user_id="u", symbol="AAPL", market="US", ctx=ctx)
        result = build_evidence_attribution(pm, snapshot)
        assert validate_report_integrity(result) == []

    def test_full_matrix_never_raises_integrity_error(self):
        exit_reasons = ["MANUAL", "STOP_LOSS", "TARGET_HIT", "UNRECOGNIZED", None]
        exit_prices = [80.0, 100.0, 120.0]
        signal_combos = [
            {},
            {"technical_signal": "BUY"},
            {"technical_signal": "SELL", "sentiment_score": 80.0},
            {"technical_signal": "BUY", "simulated_execution_price": 150.0,
             "recommendation_entry_low": 100.0, "recommendation_entry_high": 140.0},
        ]
        trade_id = 100
        for exit_reason, exit_price, signals in itertools.product(exit_reasons, exit_prices, signal_combos):
            trade_id += 1
            record = _closed_trade(trade_id=trade_id, exit_price=exit_price, exit_reason=exit_reason)
            pm = compute_postmortem(record)
            snapshot = None
            if signals:
                exec_price = signals.pop("simulated_execution_price", 100.0)
                ctx = RecommendationContext(evidence_source="DAILY_PICK", simulated_execution_price=exec_price, **signals)
                snapshot = build_entry_snapshot(paper_trade_id=trade_id, user_id="u", symbol="AAPL", market="US", ctx=ctx)
            result = build_evidence_attribution(pm, snapshot)  # raises ReportIntegrityError on any violation
            assert validate_report_integrity(result) == []


class TestReportIntegrityErrorType:
    def test_report_integrity_error_is_value_error_subclass(self):
        assert issubclass(ReportIntegrityError, ValueError)
