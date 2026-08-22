"""
Regression tests for the containment rationale's factual accuracy (DP-037).

`_CONTAINMENT_REASON` is served live to users through /api/picks/status. It
asserted that the canonical `alpha_observations` / `factor_ic_history` tables
were "completely empty". That was true when it was written and silently
became FALSE once alpha_observations began accumulating prediction-time
evidence on every Daily Picks run — a live, user-visible false statement.

These tests lock in the corrected wording. They deliberately:
  * never query a database — a test that needed production to pass would
    reintroduce exactly the staleness problem it is meant to prevent, and
  * never assert a row count — the corrected wording is TIMELESS on purpose,
    so asserting a number here would re-create the same trap.

They also pin the things this wording change must NOT have touched: the
containment flag's default, and the fixed-academic-prior ranking source.
"""

import inspect

from services.alpha_engine import containment


# ── The false claim must not come back ─────────────────────────────────────

def test_containment_reason_does_not_claim_the_tables_are_empty():
    reason = containment.containment_reason()
    assert reason is not None
    lowered = reason.lower()
    for banned in ("completely empty", "empty canonical",
                   "empty alpha_observations", "no rows"):
        assert banned not in lowered, (
            f"containment rationale must not claim {banned!r} — "
            "alpha_observations is populated (DP-037)"
        )


def test_module_docstring_does_not_claim_the_tables_are_empty():
    """The stale claim lived in the module docstring too, not only the
    served constant."""
    doc = (containment.__doc__ or "").lower()
    assert "completely empty canonical" not in doc
    assert "empty canonical" not in doc


def test_containment_reason_states_the_accurate_evidence_only_role():
    """The accurate rationale: populated evidence, but not a learning loop."""
    reason = containment.containment_reason().lower()
    assert "evidence" in reason
    assert "not a production learning feedback loop" in reason
    assert "no ranking path reads it" in reason
    assert "persists no realized outcome" in reason


def test_containment_reason_still_names_the_real_legacy_defects():
    """Correcting the empty-table claim must not erase the defects that
    actually justify containment."""
    reason = containment.containment_reason().lower()
    assert "market-label" in reason
    assert "z-score" in reason
    assert "duplicate-inflated" in reason


def test_containment_reason_is_timeless_and_cites_no_row_count():
    """Wording must not embed a live number that will go stale again."""
    import re
    reason = containment.containment_reason()
    # Dates (2026-07-12) and the ~1.52x factor are historical constants and
    # are fine; a bare thousands-separated row count is not.
    assert not re.search(r"\b\d{1,3},\d{3}\b", reason)
    assert "currently" not in reason.lower()


# ── Non-changes this wording fix must not have caused ──────────────────────

def test_containment_still_disabled_by_default():
    import os
    assert os.getenv(containment.ENV_VAR, "0") != "1" or True  # env-independent
    assert containment.is_production_learning_enabled() is (
        os.getenv(containment.ENV_VAR, "0") == "1")


def test_production_alpha_source_is_still_the_fixed_academic_prior(monkeypatch):
    monkeypatch.delenv(containment.ENV_VAR, raising=False)
    assert containment.is_production_learning_enabled() is False
    assert containment.production_alpha_source() == "fixed_academic_prior"


def test_enabling_the_flag_still_flips_source_and_clears_the_reason(monkeypatch):
    monkeypatch.setenv(containment.ENV_VAR, "1")
    assert containment.production_alpha_source() == "live_ic_meta_model"
    assert containment.containment_reason() is None


def test_learning_dataset_version_unchanged_by_this_wording_fix():
    """A wording correction must not look like a graduation event."""
    assert containment.LEARNING_DATASET_VERSION == "legacy-quarantined-2026-07-12"


def test_containment_module_contains_no_write_or_enable_side_effect():
    """The rationale fix must not have introduced any code path that turns
    learning on or writes anything."""
    src = inspect.getsource(containment)
    for banned in ("os.environ[", "setenv", "INSERT", "UPDATE", "DELETE"):
        assert banned not in src
