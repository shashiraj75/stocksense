"""
Structural coverage narrative refinement tests (Epic 005, Sprint #005).

Sprint #004's 274-company real-data validation found a real usefulness
defect: CP-07 ("missing engine") fired for 100% of India companies for
one always-true structural reason (Financial Strength has no India
coverage at all) -- never an informative, company-specific signal.

This sprint narrows CP-07 to genuine company-specific unavailability
(UNAVAILABLE/EXECUTION_ERROR) and introduces a separate `coverage_notices`
field for market-structural unavailability (NOT_APPLICABLE with
reason_code="not_applicable_for_market") -- never a conflict, never
opposing evidence, never a reason a thesis's standing is lowered.
"""

import pytest

from services.recommendation_evidence_adapter import build_recommendation_evidence_snapshot
from services.recommendation_consolidation_engine import compute_recommendation_consolidation


def _d(score=70, grade="buy", confidence=80, metadata=None):
    return {
        "score": score, "grade": grade, "confidence": confidence,
        "strengths": [], "weaknesses": [], "risks": [],
        "explanation": "x", "metadata": metadata or {"data_completeness_pct": 90.0},
    }


@pytest.mark.regression
class TestStructuralUnavailabilityIsNotAConflict:
    def test_india_financial_strength_does_not_fire_cp07(self):
        """The exact defect found in Sprint #004: India's structural
        Financial Strength absence (financial_strength=None, market=IN)
        must NOT produce a CP-07 'missing engine' conflict."""
        snap = build_recommendation_evidence_snapshot(
            "X", "IN", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(),
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert "CP-07-missing-engine" not in [c.conflict_id for c in resp.conflicts]

    def test_india_financial_strength_produces_a_coverage_notice_instead(self):
        snap = build_recommendation_evidence_snapshot(
            "X", "IN", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(),
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert len(resp.coverage_notices) == 1
        assert "Financial Strength" in resp.coverage_notices[0]
        assert "not a company-specific finding" in resp.coverage_notices[0]

    def test_structural_unavailability_is_never_opposing_evidence(self):
        snap = build_recommendation_evidence_snapshot(
            "X", "IN", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(),
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert not any("Financial Strength" in o for o in resp.opposing_evidence)

    def test_structural_unavailability_does_not_lower_thesis_state(self):
        """A fully-supporting snapshot (all 3 available engines positive)
        must reach 'supported', not 'conflicted', purely because Financial
        Strength is structurally absent for India."""
        snap = build_recommendation_evidence_snapshot(
            "X", "IN", business_quality=_d(grade="buy"), financial_strength=None,
            growth_intelligence=_d(grade="strong_buy"), valuation_intelligence=_d(grade="buy"),
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert resp.thesis_state == "supported"

    def test_coverage_notice_is_deterministic(self):
        snap1 = build_recommendation_evidence_snapshot(
            "X", "IN", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(), valuation_confidence_enabled=True,
        )
        snap2 = build_recommendation_evidence_snapshot(
            "X", "IN", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(), valuation_confidence_enabled=True,
        )
        resp1 = compute_recommendation_consolidation(snap1)
        resp2 = compute_recommendation_consolidation(snap2)
        assert resp1.coverage_notices == resp2.coverage_notices


@pytest.mark.regression
class TestCompanySpecificUnavailabilityRemainsDistinguishable:
    def test_us_genuine_unavailability_still_fires_cp07(self):
        """A normally-available engine (Financial Strength IS applicable
        for US) that returns no result for a specific company remains a
        genuine company-specific gap -- CP-07 must still fire."""
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(),
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert "CP-07-missing-engine" in [c.conflict_id for c in resp.conflicts]

    def test_us_genuine_unavailability_produces_no_coverage_notice(self):
        """Company-specific unavailability is not a market-structural
        coverage fact -- must not appear in coverage_notices."""
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(),
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert resp.coverage_notices == ()

    def test_company_specific_unavailability_does_not_become_negative_evidence(self):
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(),
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert not any("Financial Strength" in o for o in resp.opposing_evidence)


@pytest.mark.regression
class TestSpecialStatesRemainDistinguishable:
    def test_not_applicable_distinct_from_unavailable(self):
        from services.recommendation_evidence_adapter import adapt_financial_strength
        not_applicable_ev = adapt_financial_strength(None, market="IN")
        unavailable_ev = adapt_financial_strength(None, market="US")
        assert not_applicable_ev.status.value == "not_applicable"
        assert unavailable_ev.status.value == "unavailable"
        assert not_applicable_ev.status != unavailable_ev.status

    def test_execution_error_distinct_from_unavailable(self):
        from services.recommendation_evidence_adapter import adapt_valuation_intelligence
        ev = adapt_valuation_intelligence({"unexpected": "shape"}, market="US", confidence_enabled=True)
        assert ev.status.value in ("execution_error", "unavailable")

    def test_feature_disabled_distinct_from_unavailable(self):
        from services.recommendation_evidence_adapter import adapt_valuation_intelligence
        ev = adapt_valuation_intelligence(_d(grade="strong_buy"), market="IN", confidence_enabled=False)
        assert ev.status.value == "feature_disabled"

    def test_feature_disabled_produces_no_coverage_notice(self):
        """feature_disabled is a temporal, operational fact (kill switch
        state), not a market-structural coverage gap -- must never be
        confused with NOT_APPLICABLE's coverage-notice treatment."""
        snap = build_recommendation_evidence_snapshot(
            "X", "IN", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(grade="strong_buy"),
            valuation_confidence_enabled=False,
        )
        resp = compute_recommendation_consolidation(snap)
        assert not any("Valuation" in n for n in resp.coverage_notices)
        # the FS notice still fires (structural, India) -- confirms the
        # two facts don't interfere with each other
        assert any("Financial Strength" in n for n in resp.coverage_notices)


@pytest.mark.regression
class TestGateAndProvenanceSemanticsPreserved:
    """Re-confirms Sprint #004's corrections were not weakened by this
    sprint's own changes."""

    def test_liquidity_distress_still_an_enforced_active_gate(self):
        fs = _d(grade="rejected", metadata={"rejection_reason": "liquidity_distress"})
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=None, financial_strength=fs,
            growth_intelligence=None, valuation_intelligence=None, valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert any("Financial Strength" in g for g in resp.active_gates)

    def test_fraud_risk_still_an_unresolved_flag_not_an_active_gate(self):
        bq = _d(grade="rejected", metadata={"rejection_reason": "fraud_risk"})
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=bq, financial_strength=None,
            growth_intelligence=None, valuation_intelligence=None, valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert not any("Business Quality" in g for g in resp.active_gates)
        assert any("Business Quality" in g for g in resp.unresolved_risk_flags)

    def test_engine_version_provenance_still_traceable(self):
        from services.recommendation_evidence_adapter import adapt_business_quality
        ev = adapt_business_quality(_d(), market="US")
        assert ev.engine_version_provenance == "adapter_supplied_default"

    def test_legacy_fields_still_unusable_as_modern_evidence(self):
        legacy_shaped = {"growth_score": 95, "valuation_score": 5}
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=None, financial_strength=None,
            growth_intelligence=legacy_shaped, valuation_intelligence=legacy_shaped,
            valuation_confidence_enabled=True,
        )
        gi_ev = next(e for e in snap.engine_evidence if e.engine_name == "growth_intelligence")
        assert gi_ev.score is None

    def test_same_frozen_snapshot_remains_fully_deterministic(self):
        snap = build_recommendation_evidence_snapshot(
            "X", "IN", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(), valuation_confidence_enabled=True,
        )
        r1 = compute_recommendation_consolidation(snap)
        r2 = compute_recommendation_consolidation(snap)
        assert r1.thesis_state == r2.thesis_state
        assert r1.conflicts == r2.conflicts
        assert r1.coverage_notices == r2.coverage_notices
