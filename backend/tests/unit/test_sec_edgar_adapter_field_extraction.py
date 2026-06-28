"""
Unit tests for SEC EDGAR Adapter field extraction
(services/sec_edgar_adapter.py's `_extract_direct`/`_best_entry`,
SSDS-006 Sprint #004).

Every fact-entry shape below (end/val/fy/fp/form/filed) mirrors a real
entry confirmed live against SEC EDGAR's companyfacts API for AAPL/JPM
during this sprint, not invented.
"""

import pytest

import services.sec_edgar_adapter as sea
from tests.conftest import make_companyfacts


@pytest.mark.unit
def test_extract_direct_picks_the_10k_entry_over_a_10q():
    """A 10-K (annual) entry must win over a more recent 10-Q for the
    same concept, per _best_entry's documented preference."""
    facts = {
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            {"end": "2025-09-27", "val": 331097000000, "fy": 2025,
                             "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
                            {"end": "2026-03-28", "val": 371082000000, "fy": 2026,
                             "fp": "Q2", "form": "10-Q", "filed": "2026-05-01"},
                        ]
                    }
                }
            }
        }
    }
    result = sea._extract_direct(facts, ["Assets"])
    assert result["value"] == 331097000000
    assert result["form"] == "10-K"


@pytest.mark.unit
def test_extract_direct_falls_back_to_latest_non_10k_when_no_10k_exists():
    facts = {
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            {"end": "2025-12-27", "val": 100, "fy": 2025,
                             "fp": "Q1", "form": "10-Q", "filed": "2026-01-10"},
                            {"end": "2026-03-28", "val": 200, "fy": 2026,
                             "fp": "Q2", "form": "10-Q", "filed": "2026-05-01"},
                        ]
                    }
                }
            }
        }
    }
    result = sea._extract_direct(facts, ["Assets"])
    assert result["value"] == 200  # the more recent of the two, since neither is a 10-K


@pytest.mark.unit
def test_extract_direct_tries_tags_in_priority_order():
    """If the first-priority tag is absent, the next tag in the list is
    tried — mirrors the existing screener.in fallback-chain pattern
    (SSDS-004 §1), generalized to SEC EDGAR's own tag synonyms."""
    facts = make_companyfacts({
        "SalesRevenueNet": {"end": "2025-09-27", "val": 5000, "fy": 2025,
                             "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
    })
    result = sea._extract_direct(facts, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"])
    assert result["value"] == 5000
    assert result["concept"] == "SalesRevenueNet"


@pytest.mark.unit
def test_extract_direct_returns_none_when_no_tag_present():
    """Confirms the real, live-observed JPM case: a concept genuinely
    absent from a filing must resolve to None — UNAVAILABLE, never a
    fabricated value."""
    facts = make_companyfacts({})
    result = sea._extract_direct(facts, ["AssetsCurrent"])
    assert result is None


@pytest.mark.unit
def test_extract_direct_ignores_entries_with_no_value_or_end_date():
    facts = {
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            {"end": None, "val": None, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
                        ]
                    }
                }
            }
        }
    }
    result = sea._extract_direct(facts, ["Assets"])
    assert result is None


@pytest.mark.unit
def test_extract_direct_handles_missing_units_usd_key():
    """A concept that exists but has no USD-denominated entries (e.g. a
    share-count concept accidentally checked) must not crash."""
    facts = {"facts": {"us-gaap": {"Assets": {"units": {}}}}}
    result = sea._extract_direct(facts, ["Assets"])
    assert result is None
