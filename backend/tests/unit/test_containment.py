"""
Learning Alpha Engine remediation, Phase 1 — Production Containment.

Regression proofs:
  1. Default/unset LEARNING_ALPHA_PRODUCTION_ENABLED disables production
     learning (the safe default).
  2. Only the exact value "1" enables it — "true"/"yes"/"TRUE" etc. do not.
  3. containment_reason() is present while disabled, None while enabled.
  4. learning_dataset_version is a stable, non-empty string regardless of
     containment state.
"""

import pytest

from services.alpha_engine import containment


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(containment.ENV_VAR, raising=False)


def test_unset_env_disables_production_learning():
    assert containment.is_production_learning_enabled() is False


@pytest.mark.parametrize("value", ["0", "false", "False", "true", "no", "2", "", "  1  ", "TRUE", "yes"])
def test_only_exact_string_one_enables_production_learning(monkeypatch, value):
    monkeypatch.setenv(containment.ENV_VAR, value)
    assert containment.is_production_learning_enabled() is False


def test_exact_value_one_enables_production_learning(monkeypatch):
    monkeypatch.setenv(containment.ENV_VAR, "1")
    assert containment.is_production_learning_enabled() is True


# ── Reversibility positive control ──────────────────────────────────────────
# Explicit, named proof (reviewer-requested) that unset/0/true/yes/TRUE all
# stay contained, and that the exact value "1" — and only "1" — restores the
# pre-containment behavior.

@pytest.mark.parametrize("value", [None, "0", "true", "yes", "TRUE"])
def test_unset_0_true_yes_TRUE_all_remain_contained(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(containment.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(containment.ENV_VAR, value)
    assert containment.is_production_learning_enabled() is False
    assert containment.production_alpha_source() == "fixed_academic_prior"
    assert containment.containment_reason() is not None


def test_exact_value_1_restores_pre_containment_live_path(monkeypatch):
    """Positive control: LEARNING_ALPHA_PRODUCTION_ENABLED=1 flips every
    containment-gated decision back to the pre-Phase-1 behavior."""
    monkeypatch.setenv(containment.ENV_VAR, "1")
    assert containment.is_production_learning_enabled() is True
    assert containment.production_alpha_source() == "live_ic_meta_model"
    assert containment.containment_reason() is None


def test_containment_reason_present_while_disabled():
    reason = containment.containment_reason()
    assert reason is not None
    assert isinstance(reason, str) and len(reason) > 0


def test_containment_reason_none_while_enabled(monkeypatch):
    monkeypatch.setenv(containment.ENV_VAR, "1")
    assert containment.containment_reason() is None


def test_production_alpha_source_reflects_state(monkeypatch):
    assert containment.production_alpha_source() == "fixed_academic_prior"
    monkeypatch.setenv(containment.ENV_VAR, "1")
    assert containment.production_alpha_source() == "live_ic_meta_model"


def test_learning_dataset_version_is_a_stable_nonempty_string():
    v1 = containment.LEARNING_DATASET_VERSION
    v2 = containment.LEARNING_DATASET_VERSION
    assert isinstance(v1, str) and len(v1) > 0
    assert v1 == v2
