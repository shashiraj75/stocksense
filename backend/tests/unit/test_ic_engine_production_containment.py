"""
Learning Alpha Engine remediation, Phase 1 — IC engine containment.

Regression proofs:
  1. get_production_ic_weights() returns pure ACADEMIC_PRIOR_IC-derived
     weights (never live-adapted) while containment is active (default).
  2. get_production_ic_weights() delegates to the existing get_ic_weights()
     (live-adaptation-eligible) only when production learning is explicitly
     enabled.
  3. IN and US fixed priors remain distinct/market-specific under
     containment — no cross-market bleed.
  4. shadow_ic_available() never raises and never influences the
     containment-gated weights it reports on.
"""

from unittest.mock import patch

import pytest

from services.alpha_engine import containment, ic_engine


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(containment.ENV_VAR, raising=False)
    ic_engine.invalidate_cache()


def test_production_weights_match_pure_prior_when_contained():
    weights = ic_engine.get_production_ic_weights("short", market="IN")
    expected = ic_engine._prior_weights("short", "IN", None)
    assert weights == expected


def test_production_weights_never_call_live_ic_when_contained():
    with patch("services.alpha_engine.ic_engine._compute_live_ic") as mock_live:
        ic_engine.get_production_ic_weights("medium", market="US")
        mock_live.assert_not_called()


def test_production_weights_delegate_to_live_path_when_enabled(monkeypatch):
    monkeypatch.setenv(containment.ENV_VAR, "1")
    with patch("services.alpha_engine.ic_engine._compute_live_ic", return_value={
        "tech": 0.5, "fund": 0.2, "sentiment": 0.2, "quality": 0.1,
    }) as mock_live:
        weights = ic_engine.get_production_ic_weights("short", market="IN")
        mock_live.assert_called_once()
        assert weights == {"tech": 0.5, "fund": 0.2, "sentiment": 0.2, "quality": 0.1}


def test_in_and_us_priors_remain_market_specific_under_containment():
    in_weights = ic_engine.get_production_ic_weights("long", market="IN")
    us_weights = ic_engine.get_production_ic_weights("long", market="US")
    assert in_weights != us_weights
    assert in_weights == ic_engine._prior_weights("long", "IN", None)
    assert us_weights == ic_engine._prior_weights("long", "US", None)


def test_regime_multipliers_still_apply_under_containment():
    baseline = ic_engine.get_production_ic_weights("short", market="IN")
    boosted = ic_engine.get_production_ic_weights(
        "short", market="IN", regime_multipliers={"tech": 2.0}
    )
    assert boosted["tech"] > baseline["tech"]


def test_shadow_ic_available_does_not_raise_and_is_read_only():
    # With no training data available, this must resolve to False without
    # raising — and must not alter what get_production_ic_weights returns.
    before = ic_engine.get_production_ic_weights("short", market="IN")
    available = ic_engine.shadow_ic_available("short", market="IN")
    after = ic_engine.get_production_ic_weights("short", market="IN")
    assert isinstance(available, bool)
    assert before == after
