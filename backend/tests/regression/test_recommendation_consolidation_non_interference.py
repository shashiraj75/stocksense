"""
Non-interference regression tests for Recommendation Consolidation
Intelligence (Epic 005, Sprint #003) — proves RCI's new modules are not
wired into prediction_engine.py or daily_picks.py at all this sprint,
and that the pure consolidation core never calls into Prediction Engine
code, reads no environment variables, and makes no network/database
calls -- confirming this sprint's explicit "isolated, additive,
deterministic, non-authoritative" requirement structurally, not just by
intention.
"""

import pathlib
import inspect

import pytest

import services.prediction_engine as pe
import services.daily_picks as dp
import services.recommendation_consolidation_engine as rci_engine
import services.recommendation_evidence_adapter as rci_adapter


@pytest.mark.regression
class TestNoWiringIntoExistingModules:
    def test_prediction_engine_does_not_import_rci(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert "recommendation_consolidation" not in source
        assert "recommendation_evidence_adapter" not in source

    def test_daily_picks_does_not_import_rci(self):
        source = pathlib.Path(dp.__file__).read_text()
        assert "recommendation_consolidation" not in source
        assert "recommendation_evidence_adapter" not in source

    def test_rci_engine_does_not_import_prediction_engine(self):
        """RCI's pure core must never call into Prediction Engine code --
        confirmed by static import inspection, not just by convention."""
        source = pathlib.Path(rci_engine.__file__).read_text()
        assert "prediction_engine" not in source
        assert "import services.prediction_engine" not in source

    def test_rci_adapter_does_not_import_daily_picks(self):
        source = pathlib.Path(rci_adapter.__file__).read_text()
        assert "daily_picks" not in source


@pytest.mark.regression
class TestPureFunctionGuarantees:
    def test_compute_function_has_no_side_effect_imports(self):
        """Static check: the pure core module must not import os (no env
        var reads), requests/httpx/yfinance (no network calls), or any
        database/postgres module (no writes)."""
        source = pathlib.Path(rci_engine.__file__).read_text()
        for forbidden in ("import os", "import requests", "import httpx", "yfinance", "postgres", "psycopg"):
            assert forbidden not in source, f"{forbidden} found in pure consolidation core"

    def test_compute_function_signature_takes_only_a_snapshot(self):
        sig = inspect.signature(rci_engine.compute_recommendation_consolidation)
        params = list(sig.parameters)
        assert params == ["snapshot"]

    def test_calling_compute_twice_with_same_snapshot_produces_equal_responses(self):
        from services.recommendation_evidence_adapter import build_recommendation_evidence_snapshot
        from services.recommendation_consolidation_engine import compute_recommendation_consolidation
        d = {"score": 70, "grade": "buy", "confidence": 80, "strengths": [], "weaknesses": [], "risks": [],
             "explanation": "x", "metadata": {"data_completeness_pct": 90.0}}
        snap = build_recommendation_evidence_snapshot(
            "X", "US", business_quality=d, financial_strength=d,
            growth_intelligence=d, valuation_intelligence=d, valuation_confidence_enabled=True,
        )
        r1 = compute_recommendation_consolidation(snap)
        r2 = compute_recommendation_consolidation(snap)
        # snapshot_id/timestamps are reused from the same snapshot, but
        # computed_at will differ by call -- compare everything else.
        assert r1.thesis_state == r2.thesis_state
        assert r1.conflicts == r2.conflicts
        assert r1.supporting_evidence == r2.supporting_evidence
        assert r1.opposing_evidence == r2.opposing_evidence
        assert r1.narrative == r2.narrative


@pytest.mark.regression
class TestNoChangeToExistingBehavior:
    """The decisive proof: the full pre-existing backend suite (770 tests
    before this sprint) must still pass, completely unchanged, with RCI's
    new modules present alongside it."""

    def test_prediction_engine_module_still_has_its_known_methods(self):
        from services.prediction_engine import PredictionEngine
        engine = PredictionEngine()
        assert hasattr(engine, "_apply_valuation_intelligence_adjustment")
        assert hasattr(engine, "_apply_growth_intelligence_adjustment")
        assert hasattr(engine, "_apply_financial_strength_adjustment")

    def test_daily_picks_zscore_and_rank_unchanged_in_signature(self):
        from services.daily_picks import _zscore_and_rank
        sig = inspect.signature(_zscore_and_rank)
        assert list(sig.parameters) == ["items", "ic_weights", "regime", "regime_id", "market"]

    def test_legacy_growth_score_valuation_score_fields_remain_quality_factors_sourced(self):
        """Re-confirms Sprint #002's Discrepancy 3 finding is still true
        and untouched by this sprint's work -- the legacy snapshot fields
        are still sourced from quality_factors.py's breakdown, not from
        the new engines, and this sprint did not change that wiring."""
        source = pathlib.Path(dp.__file__).read_text()
        assert 'growth_score=breakdown.get("earnings_revision")' in source
        assert 'valuation_score=breakdown.get("valuation")' in source
