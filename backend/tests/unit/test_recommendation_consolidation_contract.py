"""
Contract and snapshot tests for Recommendation Consolidation Intelligence
(Epic 005, Sprint #003).
"""

import pytest

from services.recommendation_consolidation_contract import (
    EvidenceStatus, HardGateType, CONTRACT_VERSION, NEVER_NEGATIVE_STATUSES,
)
from services.recommendation_evidence_adapter import (
    adapt_business_quality, adapt_financial_strength, adapt_growth_intelligence,
    adapt_valuation_intelligence, build_recommendation_evidence_snapshot,
)


def _engine_dict(score=70, grade="buy", confidence=80, metadata=None):
    return {
        "score": score, "grade": grade, "confidence": confidence,
        "strengths": ["s1"], "weaknesses": ["w1"], "risks": ["r1"],
        "explanation": "x", "metadata": metadata or {},
    }


@pytest.mark.unit
class TestSnapshotImmutability:
    def test_snapshot_is_a_frozen_dataclass(self):
        snap = build_recommendation_evidence_snapshot(
            "TEST", "US", business_quality=_engine_dict(), financial_strength=_engine_dict(),
            growth_intelligence=_engine_dict(), valuation_intelligence=_engine_dict(),
            valuation_confidence_enabled=True,
        )
        with pytest.raises(Exception):
            snap.symbol = "OTHER"  # frozen dataclass -- assignment must raise

    def test_engine_evidence_is_a_frozen_dataclass(self):
        ev = adapt_business_quality(_engine_dict(), market="US")
        with pytest.raises(Exception):
            ev.score = 0

    def test_source_dict_is_not_mutated(self):
        source = _engine_dict()
        source_copy_strengths = list(source["strengths"])
        adapt_business_quality(source, market="US")
        assert source["strengths"] == source_copy_strengths
        assert source.get("score") == 70  # unchanged

    def test_contract_version_present_on_snapshot_and_response(self):
        from services.recommendation_consolidation_engine import compute_recommendation_consolidation
        snap = build_recommendation_evidence_snapshot(
            "TEST", "US", business_quality=_engine_dict(), financial_strength=_engine_dict(),
            growth_intelligence=_engine_dict(), valuation_intelligence=_engine_dict(),
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        assert snap.contract_version == CONTRACT_VERSION
        assert resp.contract_version == CONTRACT_VERSION

    def test_snapshot_has_unique_id_per_call(self):
        kwargs = dict(
            business_quality=_engine_dict(), financial_strength=_engine_dict(),
            growth_intelligence=_engine_dict(), valuation_intelligence=_engine_dict(),
            valuation_confidence_enabled=True,
        )
        snap1 = build_recommendation_evidence_snapshot("TEST", "US", **kwargs)
        snap2 = build_recommendation_evidence_snapshot("TEST", "US", **kwargs)
        assert snap1.snapshot_id != snap2.snapshot_id


@pytest.mark.unit
class TestAdapterMappingDeterminism:
    def test_same_input_produces_identical_evidence(self):
        d = _engine_dict()
        ev1 = adapt_business_quality(d, market="US")
        ev2 = adapt_business_quality(d, market="US")
        assert ev1 == ev2

    def test_business_quality_engine_version_default_applied(self):
        """Sprint #002's Discrepancy 1: Business Quality's metadata has no
        engine_version key -- the adapter supplies a contract-side default,
        never invents one inside the engine itself."""
        ev = adapt_business_quality(_engine_dict(metadata={"sector_bucket": "IT"}), market="US")
        assert ev.engine_version == "v1"

    def test_business_quality_real_engine_version_overrides_default(self):
        ev = adapt_business_quality(_engine_dict(metadata={"engine_version": "v2"}), market="US")
        assert ev.engine_version == "v2"

    def test_financial_strength_not_applicable_for_india(self):
        """Financial Strength has no India coverage -- must map to
        NOT_APPLICABLE, not UNAVAILABLE, since this is a structural,
        by-design absence, not a missing-data accident."""
        ev = adapt_financial_strength(None, market="IN")
        assert ev.status == EvidenceStatus.NOT_APPLICABLE

    def test_financial_strength_unavailable_for_us_when_none(self):
        """For US, a None result IS a genuine data gap (engine applicable
        but didn't produce a result), distinct from India's structural
        non-applicability."""
        ev = adapt_financial_strength(None, market="US")
        assert ev.status == EvidenceStatus.UNAVAILABLE

    def test_valuation_intelligence_feature_disabled_when_kill_switch_off(self):
        ev = adapt_valuation_intelligence(_engine_dict(grade="strong_buy", score=100), market="IN", confidence_enabled=False)
        assert ev.status == EvidenceStatus.FEATURE_DISABLED
        assert ev.score == 100  # still captured for narrative context

    def test_valuation_intelligence_normal_status_when_enabled(self):
        ev = adapt_valuation_intelligence(_engine_dict(grade="strong_buy", score=100), market="IN", confidence_enabled=True)
        assert ev.status == EvidenceStatus.SUPPORTED


@pytest.mark.unit
class TestBackwardCompatibility:
    def test_missing_optional_metadata_keys_do_not_crash(self):
        ev = adapt_growth_intelligence(_engine_dict(metadata={}), market="US")
        assert ev.status == EvidenceStatus.SUPPORTED
        assert ev.data_completeness_pct is None

    def test_unrecognized_grade_value_degrades_to_unavailable(self):
        ev = adapt_business_quality(_engine_dict(grade="some_future_grade"), market="US")
        assert ev.status == EvidenceStatus.UNAVAILABLE

    def test_malformed_engine_response_does_not_raise(self):
        ev = adapt_valuation_intelligence({"unexpected": "shape"}, market="US", confidence_enabled=True)
        assert ev.status in (EvidenceStatus.EXECUTION_ERROR, EvidenceStatus.UNAVAILABLE)
