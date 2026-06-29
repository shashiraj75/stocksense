"""
RCI contract-behavior tests through the API composer (Epic 005, Sprint
#008) -- re-confirms Sprint #004/#005's own corrections hold end-to-end
through the new composer, not just at the pure-core level.
"""

import pytest

from services.recommendation_consolidation_api_composer import compose_prediction_response_with_rci


def _d(grade="buy", score=70, metadata=None):
    return {
        "score": score, "grade": grade, "confidence": 80,
        "strengths": [], "weaknesses": [], "risks": [],
        "explanation": "x", "metadata": metadata or {"data_completeness_pct": 90.0},
    }


def _prediction(market, **engines):
    base = {"symbol": "X", "market": market, "signal": "BUY", "confidence": 75, "composite_score": 60}
    base.update(engines)
    return base


@pytest.mark.integration
class TestStructuralCoverageThroughComposer:
    def test_india_financial_strength_absence_is_a_coverage_notice_not_a_conflict(self):
        prediction = _prediction(
            "IN", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(),
        )
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="IN")
        rci = result["recommendation_consolidation"]
        conflict_ids = [c["conflict_id"] for c in rci["conflicts"]]
        assert "CP-07-missing-engine" not in conflict_ids
        assert any("Financial Strength" in n for n in rci["coverage_notices"])

    def test_company_specific_unavailability_remains_distinguishable(self):
        prediction = _prediction(
            "US", business_quality=_d(), financial_strength=None,
            growth_intelligence=_d(), valuation_intelligence=_d(),
        )
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        rci = result["recommendation_consolidation"]
        conflict_ids = [c["conflict_id"] for c in rci["conflicts"]]
        assert "CP-07-missing-engine" in conflict_ids
        assert len(rci["coverage_notices"]) == 0


@pytest.mark.integration
class TestGatesAndProvenanceThroughComposer:
    def test_active_gates_distinct_from_unresolved_flags(self):
        fs = _d(grade="rejected", metadata={"rejection_reason": "liquidity_distress"})
        bq = _d(grade="rejected", metadata={"rejection_reason": "fraud_risk"})
        prediction = _prediction("US", business_quality=bq, financial_strength=fs)
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        rci = result["recommendation_consolidation"]
        assert any("Financial Strength" in g for g in rci["active_gates"])
        assert any("Business Quality" in g for g in rci["unresolved_risk_flags"])
        assert not any("Business Quality" in g for g in rci["active_gates"])

    def test_business_quality_engine_version_provenance_correct(self):
        prediction = _prediction("US", business_quality=_d())
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        # engine_versions_used is part of the response; confirm BQ's
        # adapter-supplied default still flows through end-to-end.
        rci = result["recommendation_consolidation"]
        assert rci["engine_versions_used"]["business_quality"] == "v1"


@pytest.mark.integration
class TestLegacyFieldsCannotInfluenceRCIThroughComposer:
    def test_legacy_shaped_growth_and_valuation_score_keys_are_ignored(self):
        """Even if a caller accidentally passed a legacy Daily-Picks-
        snapshot-shaped dict (growth_score/valuation_score) instead of a
        real EngineResponse, the composer/adapter must never read those
        keys as score/grade."""
        legacy_shaped = {"growth_score": 95, "valuation_score": 5}
        prediction = _prediction(
            "US", business_quality=None, financial_strength=None,
            growth_intelligence=legacy_shaped, valuation_intelligence=legacy_shaped,
        )
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="US")
        rci = result["recommendation_consolidation"]
        # Neither engine should be SUPPORTED off the back of these fake
        # values -- both should read as having no real score (None ->
        # not in SUPPORTING_STATUSES, so absent from supporting_evidence).
        assert not any("Growth Intelligence" in s for s in rci["supporting_evidence"])
        assert not any("Valuation Intelligence" in s for s in rci["supporting_evidence"])


@pytest.mark.integration
class TestDeterminismAndSerializationThroughComposer:
    def test_same_input_produces_deterministic_rci_output(self):
        prediction1 = _prediction("US", business_quality=_d(), growth_intelligence=_d(grade="avoid"))
        prediction2 = _prediction("US", business_quality=_d(), growth_intelligence=_d(grade="avoid"))
        r1 = compose_prediction_response_with_rci(prediction1, symbol="X", market="US")
        r2 = compose_prediction_response_with_rci(prediction2, symbol="X", market="US")
        rci1, rci2 = r1["recommendation_consolidation"], r2["recommendation_consolidation"]
        assert rci1["thesis_state"] == rci2["thesis_state"]
        assert rci1["conflicts"] == rci2["conflicts"]

    def test_full_composed_response_is_json_serializable(self):
        import json
        prediction = _prediction("IN", business_quality=_d(), growth_intelligence=_d(grade="avoid"), valuation_intelligence=_d(grade="strong_buy"))
        result = compose_prediction_response_with_rci(prediction, symbol="X", market="IN")
        json.dumps(result)
