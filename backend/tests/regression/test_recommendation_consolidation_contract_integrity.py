"""
Contract integrity regression tests for Recommendation Consolidation
Intelligence (Epic 005, Sprint #004). Locks in the two real defects
found and corrected during this sprint's Contract Integrity Review --
both confirmed real before being fixed, not hypothetical:

1. Business Quality's engine_version provenance was previously
   un-flagged -- an auditor could wrongly believe Business Quality
   itself reported "v1" when it was actually an adapter-supplied
   default. Fixed via `engine_version_provenance`.

2. Sprint #003 tagged Financial Strength's liquidity_distress (genuinely
   enforced today, confirmed via daily_picks.py's own "liquidity
   distress" phrase check) and Business Quality's fraud_risk
   (confirmed NEVER enforced anywhere downstream) identically as
   HardGateType.TRUE_VETO -- conflating an active gate with a merely
   computed, unenforced flag. Fixed via `currently_enforced`, and the
   consolidation engine's `active_gates` (enforced only) vs.
   `unresolved_risk_flags` (computed, not enforced) split.
"""

import pytest

from services.recommendation_consolidation_contract import HardGateType
from services.recommendation_evidence_adapter import adapt_business_quality, adapt_financial_strength
from services.recommendation_consolidation_engine import compute_recommendation_consolidation
from services.recommendation_evidence_adapter import build_recommendation_evidence_snapshot


def _d(grade="buy", metadata=None):
    return {"score": 70, "grade": grade, "confidence": 80, "strengths": [], "weaknesses": [], "risks": [],
            "explanation": "x", "metadata": metadata or {}}


@pytest.mark.regression
class TestEngineVersionProvenance:
    def test_business_quality_default_version_is_flagged_as_adapter_supplied(self):
        ev = adapt_business_quality(_d(metadata={}), market="US")
        assert ev.engine_version == "v1"
        assert ev.engine_version_provenance == "adapter_supplied_default"

    def test_business_quality_real_engine_version_is_flagged_as_engine_reported(self):
        ev = adapt_business_quality(_d(metadata={"engine_version": "v2"}), market="US")
        assert ev.engine_version == "v2"
        assert ev.engine_version_provenance == "engine_reported"

    def test_financial_strength_real_engine_version_is_engine_reported(self):
        """Financial Strength already carries its own engine_version --
        confirming the provenance field correctly reports "engine_reported"
        for an engine that never needed an adapter default."""
        ev = adapt_financial_strength(_d(metadata={"engine_version": "v1"}), market="US")
        assert ev.engine_version_provenance == "engine_reported"

    def test_unavailable_engine_has_unknown_or_default_provenance_never_engine_reported(self):
        ev = adapt_financial_strength(None, market="US")
        assert ev.engine_version_provenance != "engine_reported"


@pytest.mark.regression
class TestEnforcedVersusUnenforcedGate:
    def test_liquidity_distress_is_marked_currently_enforced(self):
        ev = adapt_financial_strength(
            _d(grade="rejected", metadata={"rejection_reason": "liquidity_distress"}), market="US",
        )
        assert ev.hard_gate == HardGateType.TRUE_VETO
        assert ev.currently_enforced is True

    def test_business_quality_fraud_risk_is_marked_not_currently_enforced(self):
        ev = adapt_business_quality(
            _d(grade="rejected", metadata={"rejection_reason": "fraud_risk"}), market="US",
        )
        assert ev.hard_gate == HardGateType.TRUE_VETO
        assert ev.currently_enforced is False

    def test_liquidity_distress_and_fraud_risk_are_no_longer_indistinguishable(self):
        """The exact defect this sprint found and fixed: Sprint #003 gave
        both the identical tag with no way to tell them apart."""
        fs_ev = adapt_financial_strength(
            _d(grade="rejected", metadata={"rejection_reason": "liquidity_distress"}), market="US",
        )
        bq_ev = adapt_business_quality(
            _d(grade="rejected", metadata={"rejection_reason": "fraud_risk"}), market="US",
        )
        assert fs_ev.hard_gate == bq_ev.hard_gate  # same TYPE of flag...
        assert fs_ev.currently_enforced != bq_ev.currently_enforced  # ...but now distinguishable


@pytest.mark.regression
class TestActiveGatesVersusUnresolvedRiskFlags:
    def test_enforced_gate_appears_in_active_gates_not_unresolved_flags(self):
        fs = _d(grade="rejected", metadata={"rejection_reason": "liquidity_distress"})
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=None, financial_strength=fs,
            growth_intelligence=None, valuation_intelligence=None, valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert any("financial_strength" in g.lower() or "Financial Strength" in g for g in resp.active_gates)
        assert not any("financial_strength" in g.lower() or "Financial Strength" in g for g in resp.unresolved_risk_flags)

    def test_unenforced_flag_appears_in_unresolved_flags_not_active_gates(self):
        """The decisive proof: Business Quality's fraud-risk flag must
        never be reported as an 'active gate' -- doing so would mean RCI
        claims a gate is active when it genuinely is not, violating the
        permanent 'no claiming a gate is active unless it is genuinely
        enforced today' principle."""
        bq = _d(grade="rejected", metadata={"rejection_reason": "fraud_risk"})
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=bq, financial_strength=None,
            growth_intelligence=None, valuation_intelligence=None, valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert not any("Business Quality" in g for g in resp.active_gates)
        assert any("Business Quality" in g for g in resp.unresolved_risk_flags)
        assert "not currently enforced" in resp.unresolved_risk_flags[0]

    def test_active_gates_text_explicitly_says_enforced(self):
        fs = _d(grade="rejected", metadata={"rejection_reason": "liquidity_distress"})
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=None, financial_strength=fs,
            growth_intelligence=None, valuation_intelligence=None, valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert "(enforced)" in resp.active_gates[0]
