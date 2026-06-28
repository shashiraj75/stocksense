"""
Unit tests for the Financial Strength Intelligence Engine v1
(services/financial_strength_engine.py, SSDS-005, Epic 002 Sprint #008).

Pure logic only — no network, no provider, constructed `fields` dicts
shaped exactly like services.us_provider_precedence.resolve_field()'s
own output, mirroring how test_business_quality_engine.py exercises
business_quality_engine.py with constructed `info` dicts.
"""

import pytest

from services.financial_strength_engine import (
    compute_financial_strength,
    MANDATORY_FIELDS,
    V1_EXCLUDED_SECTOR_BUCKETS,
)


def _field(value, confidence=0.95):
    return {"value": value, "confidence": confidence}


def _full_fields(**overrides) -> dict:
    """A complete, healthy, realistic-shaped 16-field set (loosely
    modeled on a stable industrial company) — every test overrides
    only the fields relevant to the behavior under test, mirroring
    conftest.py's base_info convention."""
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


# ── Sector exclusion (v1 scope) ─────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("sector", ["FINANCIAL", "REAL_ESTATE"])
def test_excluded_sectors_are_rejected_without_scoring(sector):
    result = compute_financial_strength("TEST", _full_fields(), sector_bucket=sector)
    assert result["grade"] == "rejected"
    assert result["metadata"]["rejection_reason"] == "sector_not_yet_supported"
    assert result["score"] == 0


@pytest.mark.unit
def test_v1_excluded_buckets_are_exactly_financial_and_real_estate():
    assert V1_EXCLUDED_SECTOR_BUCKETS == {"FINANCIAL", "REAL_ESTATE"}


@pytest.mark.unit
@pytest.mark.parametrize("sector", ["IT", "MANUFACTURING", "UTILITIES_ENERGY", "FMCG", "PHARMA", "TELECOM", "OTHER"])
def test_non_excluded_sectors_are_scored_normally(sector):
    result = compute_financial_strength("TEST", _full_fields(), sector_bucket=sector)
    assert result["grade"] != "rejected"
    assert result["score"] > 0


# ── Data completeness gate ──────────────────────────────────────────────────

@pytest.mark.unit
def test_insufficient_data_returns_rejected():
    sparse = {f: None for f in MANDATORY_FIELDS}
    sparse["revenue"] = _field(100.0)  # 1/16 = 6.25%, well below 60%
    result = compute_financial_strength("TEST", sparse, sector_bucket="IT")
    assert result["grade"] == "rejected"
    assert result["metadata"]["rejection_reason"] == "insufficient_data"
    assert "missing_mandatory_fields" in result["metadata"]


@pytest.mark.unit
def test_full_data_completeness_is_100_percent():
    result = compute_financial_strength("TEST", _full_fields(), sector_bucket="IT")
    assert result["metadata"]["data_completeness_pct"] == 100.0
    assert result["confidence"] == 100.0


@pytest.mark.unit
def test_partial_data_above_threshold_still_scores():
    """10 of 16 fields = 62.5%, just above the 60% MIN_DATA_COMPLETENESS_PCT bar."""
    fields = _full_fields()
    for f in ["capital_expenditure", "short_term_debt", "long_term_debt",
              "current_assets", "current_liabilities", "total_liabilities"]:
        fields[f] = None
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    assert result["grade"] != "rejected"
    assert result["metadata"]["data_completeness_pct"] == pytest.approx(62.5)


# ── Hard gate: liquidity_distress ───────────────────────────────────────────

@pytest.mark.unit
def test_liquidity_distress_gate_triggers_on_full_and_condition():
    fields = _full_fields(
        current_assets=1_000_000_000.0,   # current ratio ~0.14x, far below 0.5x
        current_liabilities=7_000_000_000.0,
        free_cash_flow=-500_000_000.0,    # negative FCF
        short_term_debt=2_000_000_000.0,  # real near-term obligations
    )
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    assert result["grade"] == "rejected"
    assert result["metadata"]["rejection_reason"] == "liquidity_distress"
    assert result["risks"]


@pytest.mark.unit
def test_liquidity_distress_does_not_trigger_on_low_current_ratio_alone():
    """Per SSDS-005's narrow-AND-condition design — a weak current ratio
    alone (positive FCF, no near-term debt) must NOT hard-reject."""
    fields = _full_fields(current_assets=1_000_000_000.0, current_liabilities=7_000_000_000.0)
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    assert result["grade"] != "rejected"


@pytest.mark.unit
def test_liquidity_distress_does_not_trigger_without_negative_fcf():
    fields = _full_fields(
        current_assets=1_000_000_000.0,
        current_liabilities=7_000_000_000.0,
        free_cash_flow=500_000_000.0,  # positive
    )
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    assert result["grade"] != "rejected"


@pytest.mark.unit
def test_liquidity_distress_does_not_trigger_without_short_term_debt():
    fields = _full_fields(
        current_assets=1_000_000_000.0,
        current_liabilities=7_000_000_000.0,
        free_cash_flow=-500_000_000.0,
        short_term_debt=0.0,
    )
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    assert result["grade"] != "rejected"


# ── Category scoring direction ──────────────────────────────────────────────

@pytest.mark.unit
def test_strong_liquidity_increases_score_vs_weak_liquidity():
    strong = compute_financial_strength("TEST", _full_fields(
        current_assets=15_000_000_000.0, current_liabilities=5_000_000_000.0,  # 3.0x
        cash_and_equivalents=6_000_000_000.0,
    ), sector_bucket="IT")
    weak = compute_financial_strength("TEST", _full_fields(
        current_assets=4_000_000_000.0, current_liabilities=8_000_000_000.0,  # 0.5x
        cash_and_equivalents=500_000_000.0,
    ), sector_bucket="IT")
    assert strong["metadata"]["category_contributions"]["liquidity_adequacy"] > \
           weak["metadata"]["category_contributions"]["liquidity_adequacy"]


@pytest.mark.unit
def test_high_leverage_reduces_score_vs_low_leverage():
    low_leverage = compute_financial_strength("TEST", _full_fields(
        total_debt=2_000_000_000.0, shareholders_equity=25_000_000_000.0,  # 8%
    ), sector_bucket="IT")
    high_leverage = compute_financial_strength("TEST", _full_fields(
        total_debt=60_000_000_000.0, shareholders_equity=25_000_000_000.0,  # 240%
    ), sector_bucket="IT")
    assert low_leverage["metadata"]["category_contributions"]["leverage_capital_structure"] > \
           high_leverage["metadata"]["category_contributions"]["leverage_capital_structure"]
    assert "Severe leverage" in " ".join(high_leverage["risks"])


@pytest.mark.unit
def test_weak_interest_coverage_triggers_earnings_shock_failure():
    fields = _full_fields(ebit=600_000_000.0, interest_expense=500_000_000.0)  # 1.2x, shock -20% -> 0.96x
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    stress = result["metadata"]["stress_simulation_results"][0]
    assert stress["scenario"] == "earnings_shock"
    assert stress["passed"] is False
    assert any("Earnings Shock" in r for r in result["risks"] + result["weaknesses"])


@pytest.mark.unit
def test_strong_interest_coverage_passes_earnings_shock():
    fields = _full_fields(ebit=9_000_000_000.0, interest_expense=300_000_000.0)  # 30x
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    stress = result["metadata"]["stress_simulation_results"][0]
    assert stress["passed"] is True


@pytest.mark.unit
def test_negative_free_cash_flow_margin_is_a_weakness():
    fields = _full_fields(free_cash_flow=-1_000_000_000.0)
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    assert result["metadata"]["category_contributions"]["cash_flow_durability_under_stress"] < 0


# ── EngineResponse contract / explainability ────────────────────────────────

@pytest.mark.unit
def test_response_contains_full_engine_response_shape():
    result = compute_financial_strength("TEST", _full_fields(), sector_bucket="IT")
    for key in ("score", "grade", "confidence", "strengths", "weaknesses", "risks", "explanation", "metadata"):
        assert key in result
    assert 0 <= result["score"] <= 100
    assert 0 <= result["confidence"] <= 100


@pytest.mark.unit
def test_explanation_names_every_category():
    result = compute_financial_strength("TEST", _full_fields(), sector_bucket="IT")
    for cat in ("Liquidity Adequacy", "Leverage & Capital Structure", "Debt-Servicing Capacity",
                "Balance Sheet Resilience", "Cash Flow Durability Under Stress"):
        assert cat in result["explanation"]


@pytest.mark.unit
def test_category_contributions_sum_consistently_with_score():
    result = compute_financial_strength("TEST", _full_fields(), sector_bucket="IT")
    contributions = result["metadata"]["category_contributions"]
    expected = 50 + sum(contributions.values())
    expected_clamped = round(max(0, min(100, expected)))
    assert result["score"] == expected_clamped


@pytest.mark.unit
def test_grade_bands_match_thresholds():
    from services.thresholds import FINANCIAL_STRENGTH as FS
    assert FS.GRADE_STRONG_BUY_MIN > FS.GRADE_BUY_MIN > FS.GRADE_HOLD_MIN > FS.GRADE_WATCH_MIN


@pytest.mark.unit
def test_missing_field_degrades_gracefully_not_an_exception():
    """Every metric helper must handle a None field without raising —
    confirms the engine never crashes on partial real-world data."""
    fields = _full_fields()
    fields["ebit"] = None
    fields["interest_expense"] = None
    result = compute_financial_strength("TEST", fields, sector_bucket="IT")
    assert result["metadata"]["interest_coverage"] is None
    assert result["metadata"]["stress_simulation_results"] == []


@pytest.mark.unit
def test_empty_fields_dict_does_not_raise():
    result = compute_financial_strength("TEST", {}, sector_bucket="IT")
    assert result["grade"] == "rejected"
    assert result["metadata"]["rejection_reason"] == "insufficient_data"


@pytest.mark.unit
def test_none_fields_dict_does_not_raise():
    result = compute_financial_strength("TEST", None, sector_bucket="IT")
    assert result["grade"] == "rejected"
