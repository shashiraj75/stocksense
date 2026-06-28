"""
Regression tests locking in the two genuine defects found and fixed
during Epic 002 Sprint #009's production validation/calibration review.

Both reconstruct the exact real-data shape that exposed each defect
(LUMN's negative shareholders' equity; AEP/DUK/SO's narrow current-ratio
spread around the old hard-gate cutoff) — not synthetic edge cases
invented after the fact.
"""

import pytest

from services.financial_strength_engine import compute_financial_strength, LIQUIDITY_GATE_EXEMPT_SECTOR_BUCKETS


def _field(value):
    return {"value": value, "confidence": 0.95}


def _full_fields(**overrides) -> dict:
    base = {
        "revenue": 50_000_000_000.0,
        "net_income": 6_000_000_000.0,
        "ebit": 9_000_000_000.0,
        "interest_expense": 500_000_000.0,
        "cash_and_equivalents": 4_000_000_000.0,
        "current_assets": 12_000_000_000.0,
        "current_liabilities": 7_000_000_000.0,
        "total_assets": 60_000_000_000.0,
        "total_liabilities": 35_000_000_000.0,
        "short_term_debt": 2_000_000_000.0,
        "long_term_debt": 8_000_000_000.0,
        "total_debt": 10_000_000_000.0,
        "operating_cash_flow": 8_500_000_000.0,
        "capital_expenditure": 3_000_000_000.0,
        "free_cash_flow": 5_500_000_000.0,
        "shareholders_equity": 25_000_000_000.0,
    }
    base.update(overrides)
    return {k: _field(v) if v is not None else None for k, v in base.items()}


# ── Negative-equity debt-to-equity sign-inversion fix ───────────────────────

@pytest.mark.regression
def test_negative_equity_does_not_score_as_low_leverage():
    """Real, live-confirmed LUMN shape: total_debt positive, equity
    negative. Before the fix, debt_to_equity_pct came out as a large
    NEGATIVE percentage that the comparison logic read as 'comfortably
    below the elevated-leverage tier' -- exactly backwards. After the
    fix, debt-to-equity must be UNAVAILABLE for this sub-metric, never
    a fabricated 'strength.'"""
    fields = _full_fields(total_debt=8_000_000_000.0, shareholders_equity=-500_000_000.0)
    result = compute_financial_strength("LUMN_LIKE", fields, sector_bucket="TELECOM")

    assert result["metadata"]["debt_to_equity_pct"] is None
    assert not any("below the elevated-leverage tier" in s for s in result["strengths"])
    assert not any("Debt-to-equity" in s and "%" in s for s in result["strengths"])


@pytest.mark.regression
def test_negative_equity_leverage_contribution_is_not_positive():
    """The leverage category's contribution from a negative-equity
    company must never be positive on the strength of a meaningless
    ratio -- it can be zero (no valid D/E signal) or negative (if the
    short-term-debt-share penalty applies), never a false '+10.'"""
    fields = _full_fields(total_debt=8_000_000_000.0, shareholders_equity=-500_000_000.0,
                           short_term_debt=1_000_000_000.0)
    result = compute_financial_strength("LUMN_LIKE", fields, sector_bucket="TELECOM")
    assert result["metadata"]["category_contributions"]["leverage_capital_structure"] <= 0


@pytest.mark.regression
def test_positive_equity_debt_to_equity_still_scores_normally():
    """Confirms the fix doesn't overcorrect -- a normal, positive-equity
    company's D/E must still compute and score exactly as before."""
    fields = _full_fields(total_debt=5_000_000_000.0, shareholders_equity=25_000_000_000.0)
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    assert result["metadata"]["debt_to_equity_pct"] == pytest.approx(20.0)
    assert result["metadata"]["category_contributions"]["leverage_capital_structure"] > 0


# ── UTILITIES_ENERGY liquidity-gate exemption ───────────────────────────────

@pytest.mark.regression
def test_utilities_energy_is_exempt_from_the_hard_liquidity_gate():
    """Real, live-confirmed AEP shape (current ratio 0.4546x, negative
    FCF, real short-term debt) -- before the fix this hard-rejected the
    company; after the fix it must be scored (soft-penalized), not
    rejected, per the confirmed sector-structural finding (DUK/SO share
    the same low-current-ratio profile without triggering the gate
    purely because they landed fractionally above the same cutoff)."""
    fields = _full_fields(
        current_assets=3_200_000_000.0, current_liabilities=7_040_000_000.0,  # ~0.4546x
        free_cash_flow=-1_639_000_000.0,
        short_term_debt=2_000_000_000.0,
    )
    result = compute_financial_strength("AEP_LIKE", fields, sector_bucket="UTILITIES_ENERGY")
    assert result["grade"] != "rejected"
    assert result["score"] > 0


@pytest.mark.regression
def test_non_utilities_sector_with_the_same_profile_still_hard_gates():
    """Confirms the exemption is sector-scoped, not a global change to
    the gate's own logic -- the identical numeric profile must still
    hard-reject for a non-exempt sector."""
    fields = _full_fields(
        current_assets=3_200_000_000.0, current_liabilities=7_040_000_000.0,
        free_cash_flow=-1_639_000_000.0,
        short_term_debt=2_000_000_000.0,
    )
    result = compute_financial_strength("SAME_PROFILE_OTHER_SECTOR", fields, sector_bucket="MANUFACTURING")
    assert result["grade"] == "rejected"
    assert result["metadata"]["rejection_reason"] == "liquidity_distress"


@pytest.mark.regression
def test_liquidity_gate_exempt_sectors_is_exactly_utilities_energy():
    assert LIQUIDITY_GATE_EXEMPT_SECTOR_BUCKETS == {"UTILITIES_ENERGY"}


@pytest.mark.regression
def test_real_aal_shape_still_hard_gates_unexempted():
    """Real, live-confirmed AAL shape (MANUFACTURING bucket, current
    ratio 0.498x, negative FCF) must remain hard-rejected -- confirms
    this sprint's calibration review judged AAL's trigger as justified
    and did NOT extend the exemption to it."""
    fields = _full_fields(
        current_assets=3_400_000_000.0, current_liabilities=6_827_000_000.0,  # ~0.498x
        free_cash_flow=-1_786_000_000.0,
        short_term_debt=1_500_000_000.0,
    )
    result = compute_financial_strength("AAL_LIKE", fields, sector_bucket="MANUFACTURING")
    assert result["grade"] == "rejected"
    assert result["metadata"]["rejection_reason"] == "liquidity_distress"
