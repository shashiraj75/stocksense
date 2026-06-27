"""
Golden test for multibagger_scorecard.compute_scorecard.

Pattern for future golden tests: take one fixed, realistic input, assert the
FULL output structure (not just one field) against a known-good snapshot.
This catches any unintended change to the checklist, verdict thresholds, or
elite_strong_buy logic — exactly the kind of thing a threshold-registry
migration (or any future refactor) could silently break.

If you intentionally change scoring behavior, update the expected snapshot
in the same commit and explain why in the commit message — a golden test
failing silently-by-habit-update defeats its purpose.
"""

import pytest

from services.multibagger_scorecard import compute_scorecard


@pytest.mark.golden
def test_quality_compounder_golden_snapshot(multibagger_stock_in):
    result = compute_scorecard(multibagger_stock_in, market="IN")

    assert result["verdict"] == "elite_strong_buy"
    assert result["red_flags"] == []
    assert result["elite_strong_buy"] is True
    assert result["max_score"] == 12  # 10 base checks + 2 IN-only checks
    assert result["score"] == 12      # every check passes for this fixture

    passed_labels = {c["label"] for c in result["checks"] if c["passed"]}
    assert passed_labels == {
        "ROE > 18%, not visibly declining vs 5Y avg",
        "ROCE > 15%",
        "Profit growing both 3Y and 5Y",
        "Sales growth > 12% (3Y)",
        "Profit growth > 12% (3Y)",
        "Debt/Equity < 50%",
        "Interest Coverage > 3x",
        "Operating cash flow positive (latest year)",
        "P/E < 35",
        "EV/EBITDA < 20",
        "Growth accelerating (3Y CAGR > 5Y CAGR)",
        "No promoter pledge (latest)",
    }


@pytest.mark.golden
def test_red_flagged_stock_capped_at_watch_or_avoid(multibagger_stock_in):
    """A single red flag (high pledge) downgrades to 'watch' even with an
    otherwise-perfect scorecard — the Anti-Loss override is a hard ceiling,
    confirmed by SEAR-001 as deliberate ('never overrides avoid/watch')."""
    stock = dict(multibagger_stock_in)
    stock["promoter_pledge_pct"] = 8.0
    result = compute_scorecard(stock, market="IN")

    assert result["verdict"] == "watch"
    assert len(result["red_flags"]) == 1
    # elite_strong_buy is a pure formula over ROCE/D-E/OCF/sales-growth and
    # doesn't reference pledge — it can still be True here. What matters is
    # that the *verdict* never gets upgraded to "elite_strong_buy" while a
    # red flag is active, since the upgrade only applies to verdicts already
    # in ("strong_buy", "watchlist") — confirmed by the assertion above.
    assert result["elite_strong_buy"] is True
