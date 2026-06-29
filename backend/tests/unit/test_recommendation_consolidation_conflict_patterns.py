"""
Conflict-pattern tests for Recommendation Consolidation Intelligence
(Epic 005, Sprint #003) — proves V1's 5-pattern subset (CP-01, CP-02,
CP-03, CP-07, CP-08) of Sprint #002's 8-pattern taxonomy fires correctly,
deterministically, and never fires falsely when required evidence is
unavailable or when only legacy similarly-named fields are present.
"""

import pytest

from services.recommendation_evidence_adapter import build_recommendation_evidence_snapshot
from services.recommendation_consolidation_engine import compute_recommendation_consolidation


def _d(score=70, grade="buy", confidence=80, metadata=None, strengths=None, weaknesses=None):
    return {
        "score": score, "grade": grade, "confidence": confidence,
        "strengths": strengths or [], "weaknesses": weaknesses or [], "risks": [],
        "explanation": "x", "metadata": metadata or {"data_completeness_pct": 90.0},
    }


def _snapshot(symbol="X", market="US", bq=None, fs=None, gi=None, vi=None, vi_enabled=True):
    return build_recommendation_evidence_snapshot(
        symbol, market, business_quality=bq, financial_strength=fs,
        growth_intelligence=gi, valuation_intelligence=vi,
        valuation_confidence_enabled=vi_enabled,
    )


@pytest.mark.unit
class TestConflictPatternsFireCorrectly:
    def test_cp02_cheap_valuation_avoid_growth(self):
        """The RELINFRA-shaped pattern -- used as a validation case only,
        no stock-specific hard-coded logic."""
        snap = _snapshot(
            vi=_d(score=100, grade="strong_buy"),
            gi=_d(score=10, grade="avoid"),
        )
        resp = compute_recommendation_consolidation(snap)
        assert "CP-02-cheap-but-avoid-growth" in [c.conflict_id for c in resp.conflicts]

    def test_cp01_quality_vs_strength(self):
        snap = _snapshot(
            bq=_d(score=85, grade="buy"),
            fs=_d(score=10, grade="avoid"),
        )
        resp = compute_recommendation_consolidation(snap)
        assert "CP-01-quality-vs-strength" in [c.conflict_id for c in resp.conflicts]

    def test_cp03_growth_priced_in(self):
        snap = _snapshot(
            gi=_d(score=85, grade="buy"),
            vi=_d(score=10, grade="avoid"),
        )
        resp = compute_recommendation_consolidation(snap)
        assert "CP-03-growth-priced-in" in [c.conflict_id for c in resp.conflicts]

    def test_cp03_does_not_fire_when_valuation_feature_disabled(self):
        """Even if VI's raw grade looks 'expensive,' a feature-disabled
        engine must not be cited as opposing evidence in a conflict."""
        snap = _snapshot(
            gi=_d(score=85, grade="buy"),
            vi=_d(score=10, grade="avoid"),
            vi_enabled=False,
        )
        resp = compute_recommendation_consolidation(snap)
        assert "CP-03-growth-priced-in" not in [c.conflict_id for c in resp.conflicts]

    def test_cp07_missing_engine_fires_with_only_one_engine_present(self):
        snap = _snapshot(bq=_d(score=80, grade="buy"))  # FS=None (IN->not_applicable; US->unavailable)
        resp = compute_recommendation_consolidation(snap)
        assert "CP-07-missing-engine" in [c.conflict_id for c in resp.conflicts]

    def test_cp08_low_completeness_favorable(self):
        snap = _snapshot(
            bq=_d(score=80, grade="buy", metadata={"data_completeness_pct": 30.0}),
        )
        resp = compute_recommendation_consolidation(snap)
        assert "CP-08-low-completeness-favorable" in [c.conflict_id for c in resp.conflicts]


@pytest.mark.unit
class TestNoFalsePatternsWhenEvidenceUnavailable:
    def test_cp02_does_not_fire_when_growth_unavailable(self):
        snap = _snapshot(vi=_d(score=100, grade="strong_buy"))  # gi=None
        resp = compute_recommendation_consolidation(snap)
        assert "CP-02-cheap-but-avoid-growth" not in [c.conflict_id for c in resp.conflicts]

    def test_cp01_does_not_fire_when_only_business_quality_present(self):
        snap = _snapshot(bq=_d(score=85, grade="buy"))
        resp = compute_recommendation_consolidation(snap)
        assert "CP-01-quality-vs-strength" not in [c.conflict_id for c in resp.conflicts]


@pytest.mark.unit
class TestNoFalsePatternFromLegacyFields:
    def test_legacy_growth_score_and_valuation_score_keys_are_ignored(self):
        """Critical safeguard, per this sprint's explicit requirement:
        Daily Picks' legacy growth_score/valuation_score fields (sourced
        from quality_factors.py, NOT Growth/Valuation Intelligence) must
        never be consumed by RCI as if they were modern engine evidence.
        Confirmed here by passing a dict shaped like the LEGACY snapshot
        row (growth_score/valuation_score keys, no score/grade/metadata)
        directly as if it were an engine response -- the adapter must
        treat this as malformed/unavailable, never silently extract a
        score from the wrong field."""
        legacy_shaped = {"growth_score": 95, "valuation_score": 5}  # NOT a real EngineResponse
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=None, financial_strength=None,
            growth_intelligence=legacy_shaped, valuation_intelligence=legacy_shaped,
            valuation_confidence_enabled=True,
        )
        resp = compute_recommendation_consolidation(snap)
        gi_evidence = next(e for e in snap.engine_evidence if e.engine_name == "growth_intelligence")
        vi_evidence = next(e for e in snap.engine_evidence if e.engine_name == "valuation_intelligence")
        # The legacy dict has no "grade" key -> _grade_value(None) -> None
        # -> _GRADE_TO_STATUS.get(None, UNAVAILABLE) -> UNAVAILABLE. The
        # legacy growth_score=95/valuation_score=5 values must NEVER be
        # read as score/grade -- confirmed: .score is None, not 95 or 5.
        assert gi_evidence.score is None
        assert vi_evidence.score is None
        assert gi_evidence.status.value == "unavailable"
        assert vi_evidence.status.value == "unavailable"
        assert "CP-02-cheap-but-avoid-growth" not in [c.conflict_id for c in resp.conflicts]


@pytest.mark.unit
class TestDeterminism:
    def test_identical_snapshot_evidence_produces_identical_conflicts_and_order(self):
        snap1 = _snapshot(bq=_d(score=85, grade="buy"), fs=_d(score=10, grade="avoid"), market="US")
        snap2 = _snapshot(bq=_d(score=85, grade="buy"), fs=_d(score=10, grade="avoid"), market="US")
        resp1 = compute_recommendation_consolidation(snap1)
        resp2 = compute_recommendation_consolidation(snap2)
        ids1 = [c.conflict_id for c in resp1.conflicts]
        ids2 = [c.conflict_id for c in resp2.conflicts]
        assert ids1 == ids2

    def test_narrative_includes_both_support_and_opposition_when_relevant(self):
        snap = _snapshot(
            vi=_d(score=100, grade="strong_buy"),
            gi=_d(score=10, grade="avoid"),
        )
        resp = compute_recommendation_consolidation(snap)
        assert "undervalued" in resp.narrative.lower() or "Valuation" in resp.narrative
        assert "growth" in resp.narrative.lower() or "Growth" in resp.narrative
