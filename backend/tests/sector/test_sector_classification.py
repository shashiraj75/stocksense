"""
Sector tests (Sprint #004 Phase 7) — covers all 12 sectors named in the
SSDS-003 brief, classified via real yfinance sector/industry strings
(IN screener.in convention and US GICS convention both verified against
live data before being hardcoded here — see Sprint #004 migration notes).
"""

import pytest

from services.sector_quality_applicability import classify_sector, is_exempt, is_adjusted


# (sector, industry, expected_bucket) — IN-style and US-style strings for
# each of the 12 sectors named in the brief.
SECTOR_CASES = [
    ("Financial Services", "Banks", "FINANCIAL"),
    ("Financial Services", "Banks - Diversified", "FINANCIAL"),
    ("Financial Services", "NBFC", "FINANCIAL"),
    ("Financial Services", "Insurance - Life", "FINANCIAL"),
    ("Consumer Defensive", "Household & Personal Products", "FMCG"),
    ("Consumer Staples", "FMCG", "FMCG"),
    ("Technology", "Consumer Electronics", "IT"),
    ("Technology", "Information Technology Services", "IT"),
    ("Healthcare", "Drug Manufacturers - General", "PHARMA"),
    ("Healthcare", "Pharmaceuticals", "PHARMA"),
    ("Industrials", "Manufacturing - Tools & Accessories", "MANUFACTURING"),
    ("Industrials", "Capital Goods", "MANUFACTURING"),
    ("Utilities", "Utilities - Regulated Electric", "UTILITIES_ENERGY"),
    ("Energy", "Oil & Gas Integrated", "UTILITIES_ENERGY"),
    ("Communication Services", "Telecom Services", "TELECOM"),
    ("Communication Services", "Telecommunications", "TELECOM"),
    ("Real Estate", "Real Estate Services", "REAL_ESTATE"),
    ("Real Estate", "Realty", "REAL_ESTATE"),
]


@pytest.mark.parametrize("sector,industry,expected", SECTOR_CASES, ids=[c[2] + ":" + c[1] for c in SECTOR_CASES])
def test_sector_classification(sector, industry, expected):
    info = {"sector": sector, "industry": industry}
    assert classify_sector(info) == expected


class TestSectorApplicabilityExemptions:
    @pytest.mark.parametrize("metric", ["debt_to_equity", "operating_cash_flow", "gross_margin", "interest_coverage"])
    def test_financial_sector_exemptions(self, metric):
        assert is_exempt(metric, "FINANCIAL") is True

    @pytest.mark.parametrize("sector_bucket", ["IT", "PHARMA", "FMCG", "MANUFACTURING", "TELECOM", "REAL_ESTATE"])
    def test_non_financial_sectors_not_exempt_from_debt_to_equity(self, sector_bucket):
        assert is_exempt("debt_to_equity", sector_bucket) is False

    def test_asset_turnover_adjusted_for_it_and_pharma(self):
        assert is_adjusted("asset_turnover", "IT") is True
        assert is_adjusted("asset_turnover", "PHARMA") is True
        assert is_adjusted("asset_turnover", "MANUFACTURING") is False

    def test_other_bucket_has_no_exemptions(self):
        """Unclassified sectors get universal rules only — no surprise
        exemptions for a stock that didn't match any keyword pattern."""
        for metric in ["debt_to_equity", "operating_cash_flow", "gross_margin",
                       "asset_turnover", "interest_coverage", "working_capital_efficiency"]:
            assert is_exempt(metric, "OTHER") is False
            assert is_adjusted(metric, "OTHER") is False


class TestSectorClassificationEdgeCases:
    def test_missing_info_returns_other(self):
        assert classify_sector(None) == "OTHER"
        assert classify_sector({}) == "OTHER"

    def test_unrecognized_sector_text_returns_other(self):
        assert classify_sector({"sector": "Something Unusual", "industry": "Nonexistent Category"}) == "OTHER"
