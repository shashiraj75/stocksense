"""
Unit tests for US Provider Precedence (services/us_provider_precedence.py,
SSDS-006 Sprint #006) -- field-level precedence, fallback behavior, and
provenance preservation. No live network calls: every input is a
constructed record, mirroring sec_edgar_adapter's own provenance shape.
"""

import pytest

import services.us_provider_precedence as upp


def _edgar_record(value, confidence=0.95, derivation_status="DIRECT"):
    return {"field": "x", "value": value, "confidence": confidence, "derivation_status": derivation_status}


# ── Field-level precedence (Task 4's classification) ────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("field,expected_rule", [
    ("revenue", upp.PrecedenceRule.EDGAR_PRIMARY),
    ("net_income", upp.PrecedenceRule.EDGAR_PRIMARY),
    ("ebit", upp.PrecedenceRule.YFINANCE_PRIMARY),
    ("interest_expense", upp.PrecedenceRule.EDGAR_PRIMARY),
    ("cash_and_equivalents", upp.PrecedenceRule.YFINANCE_PRIMARY),
    ("current_assets", upp.PrecedenceRule.EDGAR_PRIMARY),
    ("current_liabilities", upp.PrecedenceRule.EDGAR_PRIMARY),
    ("total_assets", upp.PrecedenceRule.EDGAR_PRIMARY),
    ("total_liabilities", upp.PrecedenceRule.YFINANCE_PRIMARY),
    ("short_term_debt", upp.PrecedenceRule.YFINANCE_PRIMARY),
    ("long_term_debt", upp.PrecedenceRule.YFINANCE_PRIMARY),
    ("total_debt", upp.PrecedenceRule.YFINANCE_PRIMARY),
    ("operating_cash_flow", upp.PrecedenceRule.EDGAR_PRIMARY),
    ("capital_expenditure", upp.PrecedenceRule.EDGAR_PRIMARY),
    ("free_cash_flow", upp.PrecedenceRule.YFINANCE_PRIMARY),
    ("shareholders_equity", upp.PrecedenceRule.EDGAR_PRIMARY),
])
def test_field_precedence_matches_sprint_006_decision_table(field, expected_rule):
    """Locks in every one of the 16 SSDS-005-required fields' precedence
    exactly as decided in the Sprint #006 report -- a structural check
    that the code and the documented decision cannot silently drift
    apart from each other."""
    assert upp.FIELD_PRECEDENCE[field] == expected_rule


@pytest.mark.unit
def test_all_sixteen_required_fields_are_classified():
    required = {
        "revenue", "net_income", "ebit", "interest_expense", "cash_and_equivalents",
        "current_assets", "current_liabilities", "total_assets", "total_liabilities",
        "short_term_debt", "long_term_debt", "total_debt", "operating_cash_flow",
        "capital_expenditure", "free_cash_flow", "shareholders_equity",
    }
    assert required == set(upp.FIELD_PRECEDENCE.keys())


@pytest.mark.unit
def test_resolve_field_rejects_unknown_field():
    with pytest.raises(ValueError):
        upp.resolve_field("not_a_real_field", None, 100.0)


# ── Fallback behavior ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_edgar_primary_field_uses_edgar_when_both_available():
    result = upp.resolve_field("revenue", _edgar_record(100.0), 105.0)
    assert result["chosen_source"] == "sec_edgar"
    assert result["value"] == 100.0
    assert result["fallback_used"] is False


@pytest.mark.unit
def test_edgar_primary_field_falls_back_to_yfinance_when_edgar_absent():
    """Fallback triggers on absence, not on disagreement -- the rule
    this sprint's decision report names explicitly."""
    result = upp.resolve_field("revenue", None, 105.0)
    assert result["chosen_source"] == "yfinance"
    assert result["value"] == 105.0
    assert result["fallback_used"] is True


@pytest.mark.unit
def test_yfinance_primary_field_uses_yfinance_when_both_available():
    result = upp.resolve_field("total_liabilities", _edgar_record(200.0), 210.0)
    assert result["chosen_source"] == "yfinance"
    assert result["value"] == 210.0
    assert result["fallback_used"] is False


@pytest.mark.unit
def test_yfinance_primary_field_falls_back_to_edgar_when_yfinance_absent():
    result = upp.resolve_field("total_liabilities", _edgar_record(200.0), None)
    assert result["chosen_source"] == "sec_edgar"
    assert result["value"] == 200.0
    assert result["fallback_used"] is True


@pytest.mark.unit
def test_neither_source_available_returns_none_not_fabricated():
    result = upp.resolve_field("revenue", None, None)
    assert result["value"] is None
    assert result["chosen_source"] is None
    assert result["confidence"] == 0.0


@pytest.mark.unit
def test_disagreement_does_not_trigger_fallback():
    """A primary value that disagrees with the fallback source must
    still be used -- disagreement is surfaced (agreement_within_5pct),
    never silently overridden, per the Sprint #006 decision report."""
    result = upp.resolve_field("revenue", _edgar_record(100.0), 200.0)  # 100% apart
    assert result["chosen_source"] == "sec_edgar"
    assert result["value"] == 100.0
    assert result["agreement_within_5pct"] is False


@pytest.mark.unit
def test_agreement_flag_true_within_tolerance():
    result = upp.resolve_field("revenue", _edgar_record(100.0), 102.0)
    assert result["agreement_within_5pct"] is True


# ── Sector exceptions ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_financial_sector_overrides_interest_expense_to_yfinance_primary():
    """Confirmed live (Sprint #005): banks show a real interest_expense
    disagreement -- the FINANCIAL sector flips this one field's rule."""
    result = upp.resolve_field("interest_expense", _edgar_record(100.0), 110.0, sector_bucket="FINANCIAL")
    assert result["chosen_source"] == "yfinance"
    assert result["value"] == 110.0


@pytest.mark.unit
def test_non_financial_sector_keeps_interest_expense_edgar_primary():
    result = upp.resolve_field("interest_expense", _edgar_record(100.0), 110.0, sector_bucket=None)
    assert result["chosen_source"] == "sec_edgar"
    assert result["value"] == 100.0


@pytest.mark.unit
def test_financial_sector_substitute_required_for_current_assets():
    """Confirmed structurally absent on both sources for FINANCIAL —
    must short-circuit to sector_substitute_required, never fall back
    to a source that doesn't actually have the concept."""
    result = upp.resolve_field("current_assets", _edgar_record(1.0), 2.0, sector_bucket="FINANCIAL")
    assert result["sector_substitute_required"] is True
    assert result["value"] is None
    assert result["chosen_source"] is None


@pytest.mark.unit
def test_reit_sector_substitute_required_for_long_term_debt():
    """Newly confirmed this epic: REITs share much of FINANCIAL's gap."""
    result = upp.resolve_field("long_term_debt", _edgar_record(1.0), 2.0, sector_bucket="REIT")
    assert result["sector_substitute_required"] is True


@pytest.mark.unit
def test_reit_sector_does_not_require_substitute_for_unaffected_field():
    """REIT's substitute-required set is narrower than FINANCIAL's --
    revenue is unaffected and must resolve normally."""
    result = upp.resolve_field("revenue", _edgar_record(100.0), 105.0, sector_bucket="REIT")
    assert result["sector_substitute_required"] is False
    assert result["chosen_source"] == "sec_edgar"


# ── Provenance preservation ──────────────────────────────────────────────────

@pytest.mark.unit
def test_edgar_confidence_is_preserved_when_edgar_is_chosen():
    result = upp.resolve_field("revenue", _edgar_record(100.0, confidence=0.93), None)
    assert result["confidence"] == 0.93


@pytest.mark.unit
def test_fallback_confidence_is_discounted_relative_to_primary_confidence():
    """A value sourced from the fallback must carry a lower confidence
    than the same source would get as primary -- per the Sprint #006
    decision report's Confidence Implications section."""
    as_primary = upp.resolve_field("revenue", _edgar_record(100.0, confidence=0.93), None)
    as_fallback = upp.resolve_field("total_liabilities", _edgar_record(100.0, confidence=0.93), None)
    assert as_fallback["confidence"] < as_primary["confidence"]


@pytest.mark.unit
def test_definitional_decision_flag_set_for_cash_and_equivalents():
    result = upp.resolve_field("cash_and_equivalents", _edgar_record(100.0), 150.0)
    assert result["definitional_decision_pending"] is True


@pytest.mark.unit
def test_definitional_decision_flag_not_set_for_unrelated_field():
    result = upp.resolve_field("revenue", _edgar_record(100.0), 105.0)
    assert result["definitional_decision_pending"] is False


@pytest.mark.unit
def test_both_sources_available_flag_reflects_reality():
    both = upp.resolve_field("revenue", _edgar_record(100.0), 105.0)
    one_only = upp.resolve_field("revenue", _edgar_record(100.0), None)
    assert both["both_sources_available"] is True
    assert one_only["both_sources_available"] is False
