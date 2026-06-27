"""
Regression test documenting a known, NOT-yet-fixed issue from SEAR-001
(Section 3, Valuation): the "P/E < 35" checklist item in
multibagger_scorecard.py is structurally redundant with the Quality
Compounder SQL screen's own WHERE filter — any stock reaching the scorecard
has therefore already passed P/E < 35, so the checklist item can never
fail in practice.

This test does NOT assert the bug is fixed (Sprint #002 is engineering
infrastructure only, no business-logic changes per the sprint brief). It
locks in the CURRENT behavior so that:
  1. Anyone touching this code sees this test and understands the known gap.
  2. When Sprint 004 (roadmap item 2.1/2.2) fixes the redundancy, this test
     will need an intentional update — which is the point: a silent,
     accidental change here should fail CI, not slip through.
"""

import pytest

from services.multibagger_scorecard import compute_scorecard


@pytest.mark.regression
def test_pe_check_always_passes_for_stocks_that_reach_scoring(multibagger_stock_in):
    """Demonstrates the redundancy: a stock at exactly the SQL filter's
    boundary (P/E just under 35) still passes the checklist item, by
    construction — there is currently no way for a stock that reached
    compute_scorecard() to fail this specific check."""
    stock = dict(multibagger_stock_in)
    stock["pe_ratio"] = 34.9  # just inside the (currently shared) boundary
    result = compute_scorecard(stock, market="IN")

    pe_check = next(c for c in result["checks"] if c["label"] == "P/E < 35")
    assert pe_check["passed"] is True


@pytest.mark.regression
def test_pe_check_can_fail_only_for_stocks_that_should_never_reach_here(multibagger_stock_in):
    """If a stock with P/E >= 35 somehow reaches compute_scorecard() directly
    (e.g. called outside the normal SQL-filtered pipeline, as this unit test
    does), the checklist item correctly fails — proving the check ITSELF is
    not broken. The redundancy is in the pipeline wiring (SQL filter already
    enforces the same cutoff upstream), not in this function's logic."""
    stock = dict(multibagger_stock_in)
    stock["pe_ratio"] = 60.0
    result = compute_scorecard(stock, market="IN")

    pe_check = next(c for c in result["checks"] if c["label"] == "P/E < 35")
    assert pe_check["passed"] is False
