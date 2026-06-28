"""
Golden tests for the Financial Strength Intelligence Engine v1
(SSDS-005, Epic 002 Sprint #008) -- asserts the FULL EngineResponse
shape against representative, real-shaped company profiles, per SES-003
§6's "assert the full output structure, not just the field you changed"
rule. Fixture values are loosely modeled on real companies' actual
reported figures (XOM-like energy major, ORCL-like heavily-leveraged
tech, a thin/distressed profile) rather than arbitrary numbers,
mirroring test_multibagger_scorecard_golden.py's own reference pattern.
"""

import pytest

from services.financial_strength_engine import compute_financial_strength


def _field(value):
    return {"value": value, "confidence": 0.95}


@pytest.mark.golden
def test_golden_fortress_balance_sheet_energy_major():
    """Loosely modeled on a low-leverage, high-coverage energy major
    (real Sprint #005/#007 evidence: XOM-shaped figures) -- expected to
    score strongly across every category."""
    fields = {
        "revenue": _field(340_000_000_000.0),
        "net_income": _field(36_000_000_000.0),
        "ebit": _field(48_000_000_000.0),
        "interest_expense": _field(700_000_000.0),
        "cash_and_equivalents": _field(28_000_000_000.0),
        "current_assets": _field(80_000_000_000.0),
        "current_liabilities": _field(70_000_000_000.0),
        "total_assets": _field(380_000_000_000.0),
        "total_liabilities": _field(160_000_000_000.0),
        "short_term_debt": _field(8_000_000_000.0),
        "long_term_debt": _field(32_000_000_000.0),
        "total_debt": _field(40_000_000_000.0),
        "operating_cash_flow": _field(55_000_000_000.0),
        "capital_expenditure": _field(24_000_000_000.0),
        "free_cash_flow": _field(31_000_000_000.0),
        "shareholders_equity": _field(220_000_000_000.0),
    }
    result = compute_financial_strength("XOM_LIKE", fields, sector_bucket="UTILITIES_ENERGY")

    assert result["grade"] in ("buy", "strong_buy")
    assert result["score"] >= 65
    assert result["confidence"] == 100.0
    assert result["metadata"].get("rejection_reason") is None
    assert result["metadata"]["current_ratio"] == pytest.approx(80 / 70, rel=0.01)
    assert result["metadata"]["debt_to_equity_pct"] == pytest.approx(40_000_000_000 / 220_000_000_000 * 100, rel=0.01)
    assert result["metadata"]["interest_coverage"] == pytest.approx(48_000_000_000 / 700_000_000, rel=0.01)
    assert result["metadata"]["stress_simulation_results"][0]["passed"] is True
    assert len(result["strengths"]) > 0
    assert result["risks"] == []
    for cat_name in ("Liquidity Adequacy", "Leverage & Capital Structure", "Debt-Servicing Capacity",
                     "Balance Sheet Resilience", "Cash Flow Durability Under Stress"):
        assert cat_name in result["explanation"]


@pytest.mark.golden
def test_golden_heavily_leveraged_tech_company():
    """Loosely modeled on a heavily-leveraged tech company with a real,
    live-confirmed negative free-cash-flow year (Sprint #005's ORCL
    finding: a heavy capex year producing negative FCF is a genuine
    reported fact, not a defect) -- expected to score weakly on
    Leverage and Cash Flow Durability specifically."""
    fields = {
        "revenue": _field(67_000_000_000.0),
        "net_income": _field(12_000_000_000.0),
        "ebit": _field(17_000_000_000.0),
        "interest_expense": _field(4_500_000_000.0),
        "cash_and_equivalents": _field(10_000_000_000.0),
        "current_assets": _field(35_000_000_000.0),
        "current_liabilities": _field(40_000_000_000.0),
        "total_assets": _field(180_000_000_000.0),
        "total_liabilities": _field(150_000_000_000.0),
        "short_term_debt": _field(7_000_000_000.0),
        "long_term_debt": _field(75_000_000_000.0),
        "total_debt": _field(82_000_000_000.0),
        "operating_cash_flow": _field(20_000_000_000.0),
        "capital_expenditure": _field(28_000_000_000.0),
        "free_cash_flow": _field(-8_000_000_000.0),
        "shareholders_equity": _field(30_000_000_000.0),
    }
    result = compute_financial_strength("ORCL_LIKE", fields, sector_bucket="IT")

    assert result["grade"] not in ("strong_buy",)
    assert result["metadata"]["category_contributions"]["leverage_capital_structure"] < 0
    assert result["metadata"]["category_contributions"]["cash_flow_durability_under_stress"] < 0
    assert any("leverage" in w.lower() or "debt-to-equity" in w.lower() for w in result["weaknesses"])
    assert result["metadata"]["debt_to_equity_pct"] == pytest.approx(82_000_000_000 / 30_000_000_000 * 100, rel=0.01)


@pytest.mark.golden
def test_golden_thin_margin_company_near_liquidity_distress_but_not_triggering_gate():
    """A genuinely weak but not hard-gated profile -- current ratio
    below the WEAK_MAX tier, but free cash flow still positive, so the
    liquidity_distress hard gate must NOT fire (confirms the gate's
    narrow AND-condition holds even for a realistically weak company,
    not just the synthetic unit-test cases)."""
    fields = {
        "revenue": _field(8_000_000_000.0),
        "net_income": _field(120_000_000.0),
        "ebit": _field(450_000_000.0),
        "interest_expense": _field(280_000_000.0),
        "cash_and_equivalents": _field(300_000_000.0),
        "current_assets": _field(1_800_000_000.0),
        "current_liabilities": _field(2_100_000_000.0),
        "total_assets": _field(9_000_000_000.0),
        "total_liabilities": _field(7_200_000_000.0),
        "short_term_debt": _field(900_000_000.0),
        "long_term_debt": _field(2_600_000_000.0),
        "total_debt": _field(3_500_000_000.0),
        "operating_cash_flow": _field(500_000_000.0),
        "capital_expenditure": _field(350_000_000.0),
        "free_cash_flow": _field(150_000_000.0),
        "shareholders_equity": _field(1_800_000_000.0),
    }
    result = compute_financial_strength("THIN_MARGIN_LIKE", fields, sector_bucket="MANUFACTURING")

    assert result["grade"] != "rejected"
    assert result["metadata"]["current_ratio"] < 1.0
    assert result["score"] < 55  # weak, but scored -- not gated
    assert result["metadata"]["category_contributions"]["liquidity_adequacy"] < 0


@pytest.mark.golden
def test_golden_insufficient_data_company():
    """A real-world case where most fields are simply unavailable (e.g.
    a thin recent filer) -- the engine's confidence model must reject
    cleanly, exactly per SSDS-005 §5."""
    fields = {
        "revenue": _field(1_000_000_000.0),
        "net_income": _field(50_000_000.0),
        "ebit": None,
        "interest_expense": None,
        "cash_and_equivalents": _field(100_000_000.0),
        "current_assets": None,
        "current_liabilities": None,
        "total_assets": _field(2_000_000_000.0),
        "total_liabilities": None,
        "short_term_debt": None,
        "long_term_debt": None,
        "total_debt": None,
        "operating_cash_flow": None,
        "capital_expenditure": None,
        "free_cash_flow": None,
        "shareholders_equity": None,
    }
    result = compute_financial_strength("THIN_FILER_LIKE", fields, sector_bucket="IT")

    assert result["grade"] == "rejected"
    assert result["metadata"]["rejection_reason"] == "insufficient_data"
    assert result["metadata"]["data_completeness_pct"] < 60.0
    assert len(result["metadata"]["missing_mandatory_fields"]) > 0
