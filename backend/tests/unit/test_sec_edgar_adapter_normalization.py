"""
Unit tests for SEC EDGAR Adapter field normalization, provenance, and
the derived total_debt/free_cash_flow logic
(services/sec_edgar_adapter.py, SSDS-006 Sprint #004).
"""

import pytest

import services.sec_edgar_adapter as sea
from tests.conftest import make_companyfacts

_FY = {"fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "end": "2025-09-27"}


@pytest.mark.unit
def test_normalize_fields_marks_direct_field_with_full_provenance():
    facts = make_companyfacts({"Assets": {**_FY, "val": 359241000000}})
    fields = sea.normalize_fields(facts)
    rec = fields["total_assets"]
    assert rec["value"] == 359241000000
    assert rec["derivation_status"] == "DIRECT"
    assert rec["provider"] == "sec_edgar"
    assert rec["source_taxonomy"] == "us-gaap"
    assert rec["concept"] == "Assets"
    assert rec["fiscal_year"] == 2025
    assert rec["filed_date"] == "2025-10-31"
    assert rec["confidence"] > 0.9  # a 10-K, direct value — high confidence


@pytest.mark.unit
def test_normalize_fields_marks_missing_field_unavailable_not_fabricated():
    """Mirrors the real, live-confirmed JPM case (AssetsCurrent/LiabilitiesCurrent
    genuinely absent) — SSDS-005's and SSDS-003's shared missing-data
    philosophy: excluded, never guessed."""
    facts = make_companyfacts({"Assets": {**_FY, "val": 100}})  # no AssetsCurrent at all
    fields = sea.normalize_fields(facts)
    rec = fields["current_assets"]
    assert rec["value"] is None
    assert rec["derivation_status"] == "UNAVAILABLE"
    assert rec["confidence"] == 0.0


@pytest.mark.unit
def test_total_debt_derived_from_both_components():
    facts = make_companyfacts({
        "LongTermDebtCurrent": {**_FY, "val": 12350000000},
        "LongTermDebtNoncurrent": {**_FY, "val": 78328000000},
    })
    fields = sea.normalize_fields(facts)
    rec = fields["total_debt"]
    assert rec["value"] == pytest.approx(90678000000)
    assert rec["derivation_status"] == "DERIVED"
    assert "short_term_debt + long_term_debt" in rec["derivation_note"]


@pytest.mark.unit
def test_total_debt_derived_from_single_component_notes_partial_basis():
    """Real, live-confirmed ORCL case: short_term_debt present,
    long_term_debt absent — total_debt is still derivable, but the
    derivation note must say so explicitly, never implying both
    components were confirmed."""
    facts = make_companyfacts({"LongTermDebtCurrent": {**_FY, "val": 7199000000}})
    fields = sea.normalize_fields(facts)
    rec = fields["total_debt"]
    assert rec["value"] == 7199000000
    assert rec["derivation_status"] == "DERIVED"
    assert "only one component available" in rec["derivation_note"]


@pytest.mark.unit
def test_total_debt_unavailable_when_no_component_exists():
    """Real, live-confirmed JPM case: neither short- nor long-term debt
    tags exist at all for this company's filing."""
    facts = make_companyfacts({"Assets": {**_FY, "val": 100}})
    fields = sea.normalize_fields(facts)
    rec = fields["total_debt"]
    assert rec["value"] is None
    assert rec["derivation_status"] == "UNAVAILABLE"


@pytest.mark.unit
def test_free_cash_flow_derived_when_both_components_present():
    facts = make_companyfacts({
        "NetCashProvidedByUsedInOperatingActivities": {**_FY, "val": 111482000000},
        "PaymentsToAcquirePropertyPlantAndEquipment": {**_FY, "val": 12715000000},
    })
    fields = sea.normalize_fields(facts)
    rec = fields["free_cash_flow"]
    assert rec["value"] == pytest.approx(98767000000)
    assert rec["derivation_status"] == "DERIVED"
    assert "precision not independently cross-checked" in rec["derivation_note"]


@pytest.mark.unit
def test_free_cash_flow_can_be_negative_real_capex_year():
    """Real, live-confirmed ORCL case: a heavy capex year produced a
    genuinely negative free cash flow — the adapter must not clamp or
    reject a negative derived value, since it's a real reported fact,
    not an extraction error."""
    facts = make_companyfacts({
        "NetCashProvidedByUsedInOperatingActivities": {**_FY, "val": 31977000000},
        "PaymentsToAcquirePropertyPlantAndEquipment": {**_FY, "val": 55663000000},
    })
    fields = sea.normalize_fields(facts)
    rec = fields["free_cash_flow"]
    assert rec["value"] == pytest.approx(-23686000000)


@pytest.mark.unit
def test_free_cash_flow_unavailable_when_capex_missing():
    facts = make_companyfacts({
        "NetCashProvidedByUsedInOperatingActivities": {**_FY, "val": 111482000000},
    })
    fields = sea.normalize_fields(facts)
    assert fields["free_cash_flow"]["derivation_status"] == "UNAVAILABLE"


@pytest.mark.unit
def test_derived_confidence_is_lower_than_direct_confidence_for_same_form():
    facts = make_companyfacts({
        "LongTermDebtCurrent": {**_FY, "val": 1},
        "LongTermDebtNoncurrent": {**_FY, "val": 1},
        "Assets": {**_FY, "val": 100},
    })
    fields = sea.normalize_fields(facts)
    assert fields["total_debt"]["confidence"] < fields["total_assets"]["confidence"]


@pytest.mark.unit
def test_build_info_projection_only_includes_available_fields():
    """The optional yfinance-.info-shaped projection (for a future,
    not-yet-built engine integration) must omit UNAVAILABLE fields
    entirely, matching this codebase's existing `info.get(...)`
    convention — never an explicit None placeholder."""
    facts = make_companyfacts({"Assets": {**_FY, "val": 100}})
    fields = sea.normalize_fields(facts)
    info = sea.build_info_projection(fields)
    assert info == {"totalAssets": 100}
    assert "totalCurrentAssets" not in info


@pytest.mark.unit
def test_all_ssds_005_required_fields_are_represented_in_unified_schema():
    """Confirms every field this sprint's brief named (Task 3) has a
    slot in the unified schema — a structural completeness check, not
    a data-availability one."""
    required = {
        "revenue", "net_income", "ebit", "interest_expense",
        "cash_and_equivalents", "current_assets", "current_liabilities",
        "total_assets", "total_liabilities", "short_term_debt",
        "long_term_debt", "total_debt", "operating_cash_flow",
        "capital_expenditure", "free_cash_flow", "shareholders_equity",
    }
    assert required.issubset(set(sea.UNIFIED_FIELDS))
