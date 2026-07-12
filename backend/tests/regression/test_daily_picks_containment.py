"""
Learning Alpha Engine remediation, Phase 1 — Daily Picks containment.

Regression proofs:
  1. Default containment (production_learning_enabled unset/False) disables
     the meta-model's influence on ranking_alpha even when a trained model
     returns a shadow prediction — ranking_alpha always equals combined_alpha.
  2. Shadow meta_alpha is still computed and present on every item (never
     skipped) — it just never overwrites ranking_alpha while contained.
  3. When production_learning_enabled=True and a shadow prediction exists,
     ranking_alpha uses meta_alpha (existing, pre-remediation behavior).
  4. meta_alpha_used_for_ranking accurately reflects which case applied.
  5. An empty candidate list stays empty — no fallback padding.
  6. _select_with_tier_quota never returns candidates that failed the BUY +
     quality-gate filter upstream (i.e. an empty gated pool selects nothing).
"""

from unittest.mock import patch

import services.daily_picks as dp


def _candidate(symbol="AAPL", horizon="short", tech=60, fund=60, sentiment=60, quality=60):
    return {
        "symbol": symbol,
        "horizon": horizon,
        "signal": "BUY",
        "confidence": 70,
        "tech_score": tech,
        "fund_score": fund,
        "sentiment_score": sentiment,
        "quality_score": quality,
    }


IC_WEIGHTS = {"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2}
REGIME = {"label": "BULL_CALM"}


def test_shadow_meta_alpha_never_overwrites_ranking_when_contained():
    items = [_candidate("AAPL"), _candidate("MSFT", tech=40, fund=80)]
    with patch("services.alpha_engine.meta_model.predict", return_value=999.0) as mock_predict:
        enriched = dp._zscore_and_rank(
            items, IC_WEIGHTS, REGIME, regime_id=0, market="US",
            production_learning_enabled=False,
        )
    assert mock_predict.called, "meta-model must still be invoked for shadow observability"
    for row in enriched:
        assert row["meta_alpha"] == 999.0          # shadow value present
        assert row["ranking_alpha"] == row["combined_alpha"]  # never overwritten
        assert row["meta_alpha_used_for_ranking"] is False


def test_meta_alpha_drives_ranking_when_production_learning_enabled():
    items = [_candidate("AAPL")]
    with patch("services.alpha_engine.meta_model.predict", return_value=1.2345):
        enriched = dp._zscore_and_rank(
            items, IC_WEIGHTS, REGIME, regime_id=0, market="US",
            production_learning_enabled=True,
        )
    assert enriched[0]["ranking_alpha"] == 1.2345
    assert enriched[0]["meta_alpha_used_for_ranking"] is True


def test_ranking_alpha_falls_back_to_combined_alpha_when_no_shadow_model():
    items = [_candidate("AAPL")]
    with patch("services.alpha_engine.meta_model.predict", return_value=None):
        enriched_contained = dp._zscore_and_rank(
            items, IC_WEIGHTS, REGIME, regime_id=0, market="US",
            production_learning_enabled=False,
        )
        enriched_enabled = dp._zscore_and_rank(
            items, IC_WEIGHTS, REGIME, regime_id=0, market="US",
            production_learning_enabled=True,
        )
    assert enriched_contained[0]["ranking_alpha"] == enriched_contained[0]["combined_alpha"]
    assert enriched_enabled[0]["ranking_alpha"] == enriched_enabled[0]["combined_alpha"]
    assert enriched_contained[0]["meta_alpha_used_for_ranking"] is False
    assert enriched_enabled[0]["meta_alpha_used_for_ranking"] is False


def test_defaults_to_containment_module_when_flag_not_explicitly_passed(monkeypatch):
    from services.alpha_engine import containment
    monkeypatch.delenv(containment.ENV_VAR, raising=False)
    items = [_candidate("AAPL")]
    with patch("services.alpha_engine.meta_model.predict", return_value=999.0):
        enriched = dp._zscore_and_rank(items, IC_WEIGHTS, REGIME, regime_id=0, market="US")
    assert enriched[0]["ranking_alpha"] == enriched[0]["combined_alpha"]


def test_empty_candidate_list_returns_empty_no_fallback_padding():
    assert dp._zscore_and_rank([], IC_WEIGHTS, REGIME, regime_id=0, market="US") == []
    assert dp._select_with_tier_quota([], dp._MEDIUM_LONG_TIER_QUOTA_6) == []


def test_tier_quota_selection_from_empty_gated_pool_selects_nothing():
    # Simulates the real pipeline: zero candidates survived signal==BUY +
    # _passes_quality_gate upstream — the empty list must not be topped up
    # with anything from outside itself.
    selected = dp._select_with_tier_quota([], dp._MEDIUM_LONG_TIER_QUOTA_6)
    assert selected == []
