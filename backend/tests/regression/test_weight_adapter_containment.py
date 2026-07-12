"""
Learning Alpha Engine remediation, Phase 1 — weight_adapter containment
(corrected per reviewer feedback on the first implementation pass).

Regression proofs:
  1. While containment is active (default), run_adaptation() never calls
     meta_model.train() — no Learning Alpha IC/meta-model production
     artifact is created or replaced, even when there is plenty of (legacy,
     quarantined) training data available.
  2. It DOES still call ic_engine.invalidate_cache() while contained — IC
     shadow freshness is not gated, only production ranking's use of live
     IC is (see ic_engine.get_production_ic_weights).
  3. A fresh live IC becoming available (via a just-invalidated cache)
     cannot change what get_production_ic_weights() returns while
     contained — the positive proof that cache freshness never leaks into
     published ranking.
  4. Regime KMeans always retrains, independent of containment — precise
     wording: "No Learning Alpha IC/meta-model production artifact is
     trained or overwritten while containment is active. Regime KMeans
     remains independent and unchanged by this containment."
  5. The required quarantine message is logged.
  6. Positive control: LEARNING_ALPHA_PRODUCTION_ENABLED=1 restores exactly
     the pre-containment retraining behavior (gated only by MIN_ROWS, same
     as before this remediation existed).
"""

from unittest.mock import patch

import pytest

from services.alpha_engine import containment, weight_adapter, meta_model, ic_engine


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(containment.ENV_VAR, raising=False)
    ic_engine.invalidate_cache()


def test_no_meta_model_artifact_overwritten_while_contained():
    """No Learning Alpha IC/meta-model production artifact is trained or
    overwritten while containment is active."""
    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=10_000), \
         patch("services.alpha_engine.meta_model.train") as mock_train, \
         patch("services.alpha_engine.regime_cluster.retrain_on_history"), \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=False):
        weight_adapter.run_adaptation(market="IN")

    mock_train.assert_not_called()


def test_ic_cache_invalidation_still_runs_while_contained():
    """IC shadow freshness is NOT gated behind containment — only its
    influence on production ranking is (proven separately below)."""
    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=0), \
         patch("services.alpha_engine.meta_model.train"), \
         patch("services.alpha_engine.ic_engine.invalidate_cache") as mock_invalidate, \
         patch("services.alpha_engine.regime_cluster.retrain_on_history"), \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=False):
        weight_adapter.run_adaptation(market="IN")

    mock_invalidate.assert_called_once()


def test_fresh_live_ic_after_invalidation_cannot_affect_production_ranking():
    """Positive proof: invalidating the cache and making a NEW live IC value
    available (as run_adaptation's step 1 does every run) still leaves
    get_production_ic_weights() — the only entry point production Daily
    Picks ranking calls — returning the fixed academic-prior weights,
    unchanged, while contained."""
    baseline = ic_engine.get_production_ic_weights("short", market="IN")

    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=0), \
         patch("services.alpha_engine.meta_model.train"), \
         patch("services.alpha_engine.regime_cluster.retrain_on_history"), \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=False), \
         patch(
             "services.alpha_engine.ic_engine._compute_live_ic",
             return_value={"tech": 0.9, "fund": 0.05, "sentiment": 0.025, "quality": 0.025},
         ):
        # This is exactly what run_adaptation does every cycle: invalidate,
        # then (elsewhere) something eventually calls get_ic_weights again,
        # which would now pick up the new live IC above.
        weight_adapter.run_adaptation(market="IN")
        live_would_now_return = ic_engine.get_ic_weights("short", market="IN")

    after = ic_engine.get_production_ic_weights("short", market="IN")

    assert live_would_now_return != baseline, "sanity check: the live IC path must actually have changed"
    assert after == baseline, "production ranking must be unaffected by a freshly-invalidated, newly-live IC"


def test_quarantine_message_logged_while_contained(caplog):
    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=0), \
         patch("services.alpha_engine.meta_model.train"), \
         patch("services.alpha_engine.regime_cluster.retrain_on_history"), \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=False):
        with caplog.at_level("INFO"):
            weight_adapter.run_adaptation(market="US")

    assert any(
        "Production learning disabled: legacy training data quarantined." in rec.message
        for rec in caplog.records
    )


def test_regime_retrain_is_unaffected_by_containment():
    # Regime KMeans trains on regime_log (global macro features), not the
    # contaminated predictions/outcomes join — containment must not gate it.
    # Precise wording for this exception: "No Learning Alpha IC/meta-model
    # production artifact is trained or overwritten while containment is
    # active. Regime KMeans remains independent and unchanged by this
    # containment."
    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=0), \
         patch("services.alpha_engine.meta_model.train"), \
         patch("services.alpha_engine.regime_cluster.retrain_on_history") as mock_regime, \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=False):
        weight_adapter.run_adaptation(market="IN")

    mock_regime.assert_called_once()


def test_regime_retrain_also_runs_when_production_learning_enabled(monkeypatch):
    """Regime KMeans is independent of the flag in both directions."""
    monkeypatch.setenv(containment.ENV_VAR, "1")
    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=0), \
         patch("services.alpha_engine.meta_model.train"), \
         patch("services.alpha_engine.regime_cluster.retrain_on_history") as mock_regime, \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=False):
        weight_adapter.run_adaptation(market="IN")

    mock_regime.assert_called_once()


# ── Reversibility positive control ──────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "0", "true", "yes", "TRUE"])
def test_retraining_stays_blocked_for_every_non_1_value(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(containment.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(containment.ENV_VAR, value)
    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=meta_model.MIN_ROWS + 1), \
         patch("services.alpha_engine.meta_model.train") as mock_train, \
         patch("services.alpha_engine.regime_cluster.retrain_on_history"), \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=False):
        weight_adapter.run_adaptation(market="IN")
    mock_train.assert_not_called()


def test_retraining_proceeds_when_production_learning_enabled_exactly_1(monkeypatch):
    """The exact value "1" — and only "1" — restores the pre-containment
    live IC/meta-model production path: retraining proceeds gated only by
    MIN_ROWS, exactly as it did before this remediation existed."""
    monkeypatch.setenv(containment.ENV_VAR, "1")
    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=meta_model.MIN_ROWS + 1), \
         patch("services.alpha_engine.meta_model.train", return_value=True) as mock_train, \
         patch("services.alpha_engine.regime_cluster.retrain_on_history"), \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=True):
        weight_adapter.run_adaptation(market="IN")

    assert mock_train.call_count == 3  # short, medium, long


def test_ic_cache_invalidation_runs_regardless_of_flag_value(monkeypatch):
    """Cache invalidation is unconditional — proven for both a contained and
    an enabled run, since it never determines production behavior either
    way (only shadow-diagnostic freshness)."""
    for value in (None, "1"):
        if value is None:
            monkeypatch.delenv(containment.ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(containment.ENV_VAR, value)
        with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=0), \
             patch("services.alpha_engine.meta_model.train"), \
             patch("services.alpha_engine.ic_engine.invalidate_cache") as mock_invalidate, \
             patch("services.alpha_engine.regime_cluster.retrain_on_history"), \
             patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
             patch("services.alpha_engine.meta_model.is_trained", return_value=False):
            weight_adapter.run_adaptation(market="IN")
        mock_invalidate.assert_called_once()
