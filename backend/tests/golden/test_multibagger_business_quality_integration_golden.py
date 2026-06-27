"""
Golden tests for the Business Quality Engine's integration into the
Multibagger Quality Compounder scorecard (SSDS-003, Sprint #005).

Pattern: same as test_multibagger_scorecard_golden.py — full output
structure asserted, not just one field, so a future change can't
silently break this integration's promotion/red-flag logic.
"""

import pytest

from services.multibagger_scorecard import compute_scorecard


@pytest.mark.golden
def test_quality_compounder_promoted_to_elite_when_business_quality_confirms(multibagger_stock_in):
    """A US-style fixture (Business Quality Engine fields populated) that
    already clears 'strong_buy' on its own checklist, AND the engine
    independently confirms 'Quality Compounder' at a high score, must be
    promoted to elite_strong_buy via the new path — even without the
    pre-existing elite_strong_buy formula's exact conditions all holding."""
    stock = dict(multibagger_stock_in)
    # Deliberately fails the pre-existing elite_strong_buy formula (D/E
    # just over its 50% cutoff) so this test isolates the NEW promotion
    # path, not the old one.
    stock["debt_to_equity_pct"] = 55.0
    stock["business_quality_score"] = 78
    stock["business_quality_grade"] = "buy"
    stock["business_quality_style"] = "Quality Compounder"

    result = compute_scorecard(stock, market="US")

    assert result["elite_strong_buy"] is False  # the OLD formula correctly does not fire (D/E fails it)
    assert result["business_quality_confirmed"] is True
    assert result["verdict"] == "elite_strong_buy"  # promoted via the NEW path instead
    assert result["business_quality_score"] == 78
    assert result["business_quality_style"] == "Quality Compounder"


@pytest.mark.golden
def test_quality_compounder_label_alone_does_not_promote_below_score_bar(multibagger_stock_in):
    """SSDS-003's own finding: 'Quality Compounder' is only meaningful
    alongside a high score — a low-scoring stock that somehow carries the
    label (shouldn't happen in practice, but must not be trusted blindly)
    must not be promoted."""
    stock = dict(multibagger_stock_in)
    stock["debt_to_equity_pct"] = 55.0
    stock["business_quality_score"] = 40  # below GRADE_BUY_MIN (65)
    stock["business_quality_grade"] = "watch"
    stock["business_quality_style"] = "Quality Compounder"

    result = compute_scorecard(stock, market="US")

    assert result["business_quality_confirmed"] is False
    assert result["verdict"] != "elite_strong_buy"


@pytest.mark.golden
def test_hard_gate_rejection_adds_red_flag_and_can_cap_verdict(multibagger_stock_in):
    """A Business Quality Engine hard-gate rejection is independent
    negative evidence this checklist has no equivalent for — must add a
    red flag, and (per the existing red-flag-count rule) a single red
    flag caps the verdict at 'watch', same as any other red flag."""
    stock = dict(multibagger_stock_in)
    stock["business_quality_score"] = 0
    stock["business_quality_grade"] = "rejected"
    stock["business_quality_style"] = None

    result = compute_scorecard(stock, market="US")

    assert any("Business Quality Engine hard-gate rejection" in rf for rf in result["red_flags"])
    assert result["verdict"] == "watch"  # exactly one red flag -> watch, not avoid
    assert result["business_quality_grade"] == "rejected"


@pytest.mark.golden
def test_missing_business_quality_fields_is_a_complete_no_op(multibagger_stock_in):
    """Backward compatibility, the core requirement: a stock dict with NO
    business_quality_* keys at all (every IN stock today, and any US
    stock not yet refreshed under this sprint's change) must produce
    byte-identical output to before this integration existed."""
    result = compute_scorecard(dict(multibagger_stock_in), market="IN")

    assert result["business_quality_score"] is None
    assert result["business_quality_grade"] is None
    assert result["business_quality_style"] is None
    assert result["business_quality_confirmed"] is False
    assert result["verdict"] == "elite_strong_buy"  # unchanged from the original golden snapshot
    assert result["red_flags"] == []
