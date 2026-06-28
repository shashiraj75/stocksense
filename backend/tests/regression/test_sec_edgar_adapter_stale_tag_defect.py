"""
Regression test: locks in the fix for a genuine defect found live during
Epic 002 Sprint #005's large-scale SEC EDGAR validation.

Confirmed root cause (real, live data, not hypothesized): MSFT's
"Revenues" XBRL tag has entries only through FY2010 (the company
migrated to "RevenueFromContractWithCustomerExcludingAssessedTax" after
adopting ASC 606) — but the adapter's original "first tag with any data
wins" logic returned the stale 2010 figure ($62.48B) instead of falling
through to the tag MSFT actually uses today, because "Revenues" was
never empty, just stale.

Fixed by comparing every candidate tag's best entry by period end date
and picking the most recent one, with tag-list order only as a tiebreak
— not by stopping at the first non-empty tag. This test reconstructs
the exact failure shape (an old, frozen tag plus a newer, actively-used
one) so the defect cannot silently return, per SES-003 §2's definition
of a regression test.
"""

import pytest

import services.sec_edgar_adapter as sea
from tests.conftest import make_companyfacts

_OLD_TAG_ENTRY = {
    "start": "2009-07-01", "end": "2010-06-30", "val": 62484000000,
    "fy": 2010, "fp": "FY", "form": "10-K", "filed": "2010-07-30",
}
_CURRENT_TAG_ENTRY = {
    "start": "2024-07-01", "end": "2025-06-30", "val": 281724000000,
    "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-07-30",
}


@pytest.mark.regression
def test_stale_deprecated_tag_does_not_win_over_a_current_one():
    """The real, confirmed MSFT defect shape: 'Revenues' (old, frozen at
    2010) appears first in the priority list and has real data — but
    'RevenueFromContractWithCustomerExcludingAssessedTax' (current) has
    a far more recent period end date and must win."""
    facts = make_companyfacts({
        "Revenues": _OLD_TAG_ENTRY,
        "RevenueFromContractWithCustomerExcludingAssessedTax": _CURRENT_TAG_ENTRY,
    })
    result = sea._extract_direct(facts, sea._DIRECT_FIELD_TAGS["revenue"])
    assert result["value"] == 281724000000
    assert result["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert result["fiscal_year"] == 2025


@pytest.mark.regression
def test_old_tag_still_wins_when_it_is_genuinely_the_most_recent():
    """Confirms the fix doesn't overcorrect: if the first-priority tag's
    data really is the most recent available (the common, normal case
    for a company that has never migrated tags), it must still win —
    this is not a 'always prefer tag 2' rule, it's a 'prefer whichever
    tag has the newest real data' rule."""
    facts = make_companyfacts({
        "Revenues": _CURRENT_TAG_ENTRY,
        "RevenueFromContractWithCustomerExcludingAssessedTax": _OLD_TAG_ENTRY,
    })
    result = sea._extract_direct(facts, sea._DIRECT_FIELD_TAGS["revenue"])
    assert result["value"] == 281724000000
    assert result["concept"] == "Revenues"


@pytest.mark.regression
def test_quarterly_breakdown_fact_inside_a_10k_does_not_masquerade_as_annual():
    """The second, related real finding from the same validation sprint:
    a 10-K's XBRL can carry a fp=='FY'/form=='10-K' fact whose actual
    start/end span is a single quarter (~90 days), not a full year —
    confirmed live for MSFT's 'Revenues' concept's older filings. Such
    an entry must not be treated as the annual value even when it has
    the most recent end date among 10-K/FY-labeled entries."""
    quarterly_masquerading_as_fy = {
        "start": "2025-04-01", "end": "2025-06-30", "val": 76441000000,
        "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-07-30",
    }
    genuine_annual_but_earlier_filed = {
        "start": "2024-07-01", "end": "2025-06-30", "val": 281724000000,
        "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-07-30",
    }
    entries = [quarterly_masquerading_as_fy, genuine_annual_but_earlier_filed]
    best = sea._best_entry(entries)
    assert best["val"] == 281724000000


@pytest.mark.regression
def test_full_year_duration_check_accepts_a_real_annual_fact():
    assert sea._is_full_year_duration({"start": "2024-07-01", "end": "2025-06-30"}) is True


@pytest.mark.regression
def test_full_year_duration_check_rejects_a_quarter():
    assert sea._is_full_year_duration({"start": "2025-04-01", "end": "2025-06-30"}) is False


@pytest.mark.regression
def test_full_year_duration_check_is_a_no_op_for_instant_facts():
    """Balance-sheet ("instant") facts like Assets have no `start` key at
    all — the duration check must not reject them for lacking one."""
    assert sea._is_full_year_duration({"end": "2025-06-30", "val": 100}) is True
