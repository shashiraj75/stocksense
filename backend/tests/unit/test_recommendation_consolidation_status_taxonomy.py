"""
Status taxonomy tests for Recommendation Consolidation Intelligence
(Epic 005, Sprint #003) — proves the 10-status taxonomy (Sprint #002's
Evidence Contract §5) is correctly implemented: warning vs. veto,
unavailable/not-applicable/execution-error/feature-disabled/stale-snapshot
are never treated as negative evidence.
"""

import pytest

from services.recommendation_consolidation_contract import EvidenceStatus, HardGateType, NEVER_NEGATIVE_STATUSES
from services.recommendation_evidence_adapter import adapt_financial_strength, adapt_business_quality, adapt_valuation_intelligence


def _d(grade="buy", metadata=None):
    return {"score": 70, "grade": grade, "confidence": 80, "strengths": [], "weaknesses": [], "risks": [],
            "explanation": "x", "metadata": metadata or {}}


@pytest.mark.unit
class TestWarningVersusVeto:
    def test_liquidity_distress_is_a_true_veto_not_an_ordinary_avoid(self):
        ev = adapt_financial_strength(_d(grade="rejected", metadata={"rejection_reason": "liquidity_distress"}), market="US")
        assert ev.status == EvidenceStatus.AVOID
        assert ev.hard_gate == HardGateType.TRUE_VETO

    def test_growth_avoid_grade_is_not_a_veto(self):
        """A Growth Intelligence AVOID grade is a strong warning -- never
        elevated to the same hard_gate status as a true veto."""
        from services.recommendation_evidence_adapter import adapt_growth_intelligence
        ev = adapt_growth_intelligence(_d(grade="avoid"), market="IN")
        assert ev.status == EvidenceStatus.AVOID
        assert ev.hard_gate == HardGateType.NONE

    def test_valuation_overvaluation_warning_is_not_a_veto(self):
        ev = adapt_valuation_intelligence(_d(grade="avoid"), market="US", confidence_enabled=True)
        assert ev.hard_gate == HardGateType.NONE

    def test_business_quality_fraud_risk_is_tagged_true_veto_descriptively(self):
        """Per this sprint's explicit rule: tagged descriptively, NEVER
        enforced -- this test confirms the tag exists, a separate test
        suite (regression) confirms nothing downstream acts on it."""
        ev = adapt_business_quality(_d(grade="rejected", metadata={"rejection_reason": "fraud_risk"}), market="US")
        assert ev.status == EvidenceStatus.AVOID
        assert ev.hard_gate == HardGateType.TRUE_VETO


@pytest.mark.unit
class TestNeverNegativeStatuses:
    @pytest.mark.parametrize("status", [
        EvidenceStatus.UNAVAILABLE, EvidenceStatus.NOT_APPLICABLE,
        EvidenceStatus.FEATURE_DISABLED, EvidenceStatus.EXECUTION_ERROR,
        EvidenceStatus.STALE_SNAPSHOT,
    ])
    def test_status_is_in_never_negative_set(self, status):
        assert status in NEVER_NEGATIVE_STATUSES

    def test_unavailable_is_not_negative(self):
        ev = adapt_financial_strength(None, market="US")
        assert ev.status == EvidenceStatus.UNAVAILABLE
        assert ev.status in NEVER_NEGATIVE_STATUSES

    def test_not_applicable_is_not_negative(self):
        ev = adapt_financial_strength(None, market="IN")
        assert ev.status == EvidenceStatus.NOT_APPLICABLE
        assert ev.status in NEVER_NEGATIVE_STATUSES

    def test_execution_error_is_not_negative(self):
        ev = adapt_valuation_intelligence({"unexpected_shape": True}, market="US", confidence_enabled=True)
        assert ev.status in (EvidenceStatus.EXECUTION_ERROR, EvidenceStatus.UNAVAILABLE)
        assert ev.status in NEVER_NEGATIVE_STATUSES

    def test_feature_disabled_is_not_negative(self):
        ev = adapt_valuation_intelligence(_d(grade="avoid"), market="IN", confidence_enabled=False)
        assert ev.status == EvidenceStatus.FEATURE_DISABLED
        assert ev.status in NEVER_NEGATIVE_STATUSES

    def test_rejected_insufficient_data_is_not_negative(self):
        """rejected/insufficient_data maps to UNAVAILABLE per the adapter's
        own reason-code table -- confirming missing data is never
        penalized, the same philosophy every engine itself already shares."""
        from services.recommendation_evidence_adapter import adapt_growth_intelligence
        ev = adapt_growth_intelligence(_d(grade="rejected", metadata={"rejection_reason": "insufficient_data"}), market="US")
        assert ev.status == EvidenceStatus.UNAVAILABLE


@pytest.mark.unit
class TestStaleSnapshotIsNotLiveEvidence:
    def test_stale_snapshot_flag_exists_and_is_distinguishable(self):
        """A direct construction test confirming the contract's own
        is_snapshot flag (not yet wired into any live code path this
        sprint) is distinguishable from a live result."""
        from services.recommendation_consolidation_contract import RecommendationEvidenceSnapshot
        import dataclasses
        live = RecommendationEvidenceSnapshot(
            contract_version=1, snapshot_id="a", analysis_timestamp="t", market="US",
            symbol="X", sector_bucket=None, engine_evidence=(), is_snapshot=False,
        )
        historical = dataclasses.replace(live, is_snapshot=True)
        assert live.is_snapshot is False
        assert historical.is_snapshot is True
