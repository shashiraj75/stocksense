"""
Static regression test: fails if any of the Sprint #002-migrated threshold
comparisons in prediction_engine.py or multibagger_scorecard.py are ever
reintroduced as a bare numeric literal instead of a services.thresholds
constant.

This is a source-text check, not a behavioral one — it greps the two
already-migrated files for the exact (variable, operator, literal) patterns
that used to exist before the threshold-registry migration. Each pattern
below is paired with the thresholds.py constant that now owns that value
(see services/thresholds.py for the full file:line provenance).

Scope, deliberately narrow per the migration's own rules: this test only
covers the two files Sprint #002 actually migrated. It does NOT check
quality_factors.py (never migrated — see Sprint 002 report, Risk #1) or any
other file. Extending coverage to a new file means extending this test's
file list AND adding it to thresholds.py's migration — not silently
broadening one without the other.
"""

import re
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PREDICTION_ENGINE = _BACKEND_ROOT / "services" / "prediction_engine.py"
_MULTIBAGGER_SCORECARD = _BACKEND_ROOT / "services" / "multibagger_scorecard.py"

# (file, description, regex, registry constant that now owns this value)
# Each regex matches the *raw literal* form that existed before migration.
# A match here means the migration was reverted or a duplicate was reintroduced.
_FORBIDDEN_PATTERNS = [
    # prediction_engine.py — _compute_risk_penalty
    (_PREDICTION_ENGINE, "risk-penalty D/E severe tier (300)", r"de\s+is not None and de\s*>\s*300\b", "DEBT_TO_EQUITY.RISK_PENALTY_SEVERE_MIN"),
    (_PREDICTION_ENGINE, "risk-penalty D/E elevated tier (200)", r"de\s+is not None and de\s*>\s*200\b", "DEBT_TO_EQUITY.RISK_PENALTY_ELEVATED_MIN"),
    (_PREDICTION_ENGINE, "risk-penalty beta high tier (2.0)", r"beta\s+is not None and beta\s*>\s*2\.0\b", "RISK_PENALTY.BETA_HIGH"),
    (_PREDICTION_ENGINE, "risk-penalty beta above-average tier (1.6)", r"beta\s+is not None and beta\s*>\s*1\.6\b", "RISK_PENALTY.BETA_ABOVE_AVERAGE"),
    (_PREDICTION_ENGINE, "risk-penalty negative ROE tier (-0.05)", r"roe\s+is not None and roe\s*<\s*-0\.05\b", "PROFITABILITY.ROE_NEGATIVE_RISK_PENALTY"),
    (_PREDICTION_ENGINE, "risk-penalty risk-subscore poor tier (35)", r"risk_sc\s*<\s*35\b", "RISK_PENALTY.RISK_SUBSCORE_POOR_MAX"),
    (_PREDICTION_ENGINE, "risk-penalty risk-subscore below-average tier (45)", r"risk_sc\s*<\s*45\b", "RISK_PENALTY.RISK_SUBSCORE_BELOW_AVERAGE_MAX"),
    # prediction_engine.py — _fundamental_score balance-sheet bucket
    (_PREDICTION_ENGINE, "fundamental-score D/E severe tier (300)", r"de\s*>\s*300\b", "DEBT_TO_EQUITY.RISK_PENALTY_SEVERE_MIN"),
    (_PREDICTION_ENGINE, "fundamental-score D/E elevated tier (150)", r"de\s*>\s*150\b", "DEBT_TO_EQUITY.ELEVATED_PENALTY_MIN"),
    (_PREDICTION_ENGINE, "fundamental-score D/E low-debt bonus (50)", r"de\s*<\s*50\b", "DEBT_TO_EQUITY.LOW_DEBT_BONUS_MAX"),
    (_PREDICTION_ENGINE, "fundamental-score OCF growth bonus (30)", r"cf_growth\s*>\s*30\b", "CASH_FLOW.OCF_GROWTH_STRONG_MIN_PCT"),
    # prediction_engine.py — _quality_gate
    (_PREDICTION_ENGINE, "quality-gate severe ROE (-0.10)", r"roe\s+is not None and roe\s*<\s*-0\.10\b", "PROFITABILITY.ROE_SEVERE_NEGATIVE"),
    (_PREDICTION_ENGINE, "quality-gate severe profit margin (-0.15)", r"profit_margin\s+is not None and profit_margin\s*<\s*-0\.15\b", "PROFITABILITY.PROFIT_MARGIN_SEVERE_NEGATIVE"),
    (_PREDICTION_ENGINE, "quality-gate turnaround revenue-growth (0.15)", r"revenue_growth\s+is not None and revenue_growth\s*>\s*0\.15\b", "GROWTH.REVENUE_GROWTH_TURNAROUND_EXCEPTION_MIN"),
    (_PREDICTION_ENGINE, "quality-gate turnaround contained leverage (150)", r"de\s+is None or de\s*<\s*150\b", "DEBT_TO_EQUITY.TURNAROUND_EXCEPTION_MAX"),
    (_PREDICTION_ENGINE, "quality-gate turnaround ROCE (0.08)", r"roce\s+is not None and roce\s*>\s*0\.08\b", "PROFITABILITY.ROCE_TURNAROUND_EXCEPTION_MIN"),
    (_PREDICTION_ENGINE, "quality-gate hard-reject D/E (500)", r"de\s+and de\s*>\s*500\b", "DEBT_TO_EQUITY.HARD_REJECT_MIN"),
    # multibagger_scorecard.py — checklist
    (_MULTIBAGGER_SCORECARD, "checklist ROE > 18", r"roe\s*>\s*18\b", "PROFITABILITY.ROE_QUALITY_COMPOUNDER_MIN_PCT"),
    (_MULTIBAGGER_SCORECARD, "checklist ROCE > 15", r"roce\s*>\s*15\b", "PROFITABILITY.ROCE_QUALITY_COMPOUNDER_MIN_PCT"),
    (_MULTIBAGGER_SCORECARD, "checklist sales growth > 12", r"sales_3y\s*>\s*12\b", "GROWTH.SALES_GROWTH_3Y_QUALITY_COMPOUNDER_MIN_PCT"),
    (_MULTIBAGGER_SCORECARD, "checklist profit growth > 12", r"profit_3y\s*>\s*12\b", "GROWTH.PROFIT_GROWTH_3Y_QUALITY_COMPOUNDER_MIN_PCT"),
    (_MULTIBAGGER_SCORECARD, "checklist D/E < 50", r"de\s*<\s*50\b", "DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX"),
    (_MULTIBAGGER_SCORECARD, "checklist interest coverage > 3", r"icr\s*>\s*3\b", "GOVERNANCE.INTEREST_COVERAGE_MIN"),
    (_MULTIBAGGER_SCORECARD, "checklist OCF > 0", r"ocf\s*>\s*0\b", "CASH_FLOW.OCF_MUST_BE_POSITIVE"),
    (_MULTIBAGGER_SCORECARD, "checklist P/E < 35", r"pe\s*<\s*35\b", "VALUATION.PE_QUALITY_COMPOUNDER_MAX"),
    (_MULTIBAGGER_SCORECARD, "checklist EV/EBITDA < 20", r"ev_ebitda\s*<\s*20\b", "VALUATION.EV_EBITDA_QUALITY_COMPOUNDER_MAX"),
    (_MULTIBAGGER_SCORECARD, "checklist pledge < 1", r"pledge\s*<\s*1\b", "GOVERNANCE.PROMOTER_PLEDGE_CLEAN_MAX_PCT"),
    # multibagger_scorecard.py — Anti-Loss red-flag override
    (_MULTIBAGGER_SCORECARD, "red-flag pledge > 5", r"pledge\s*>\s*5\b", "GOVERNANCE.PROMOTER_PLEDGE_RED_FLAG_MIN_PCT"),
    (_MULTIBAGGER_SCORECARD, "red-flag D/E > 150", r"de\s*>\s*150\b", "DEBT_TO_EQUITY.ELEVATED_PENALTY_MIN"),
    (_MULTIBAGGER_SCORECARD, "red-flag OCF < 0", r"ocf\s*<\s*0\b", "CASH_FLOW.OCF_MUST_BE_POSITIVE"),
    # multibagger_scorecard.py — elite_strong_buy hard requirements
    (_MULTIBAGGER_SCORECARD, "elite ROCE > 15", r"roce\s*>\s*15\b", "PROFITABILITY.ROCE_QUALITY_COMPOUNDER_MIN_PCT"),
    (_MULTIBAGGER_SCORECARD, "elite D/E < 50", r"de\s*<\s*50\b", "DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX"),
    (_MULTIBAGGER_SCORECARD, "elite OCF > 0", r"ocf\s*>\s*0\b", "CASH_FLOW.OCF_MUST_BE_POSITIVE"),
    (_MULTIBAGGER_SCORECARD, "elite sales growth > 10", r"sales_3y\s*>\s*10\b", "GROWTH.SALES_GROWTH_3Y_ELITE_MIN_PCT"),
]


@pytest.fixture(scope="module")
def _source_text():
    return {
        _PREDICTION_ENGINE: _PREDICTION_ENGINE.read_text(),
        _MULTIBAGGER_SCORECARD: _MULTIBAGGER_SCORECARD.read_text(),
    }


@pytest.mark.regression
@pytest.mark.parametrize(
    "filepath,description,pattern,owning_constant",
    _FORBIDDEN_PATTERNS,
    ids=[p[1] for p in _FORBIDDEN_PATTERNS],
)
def test_migrated_threshold_not_reintroduced_as_raw_literal(filepath, description, pattern, owning_constant, _source_text):
    source = _source_text[filepath]
    match = re.search(pattern, source)
    assert match is None, (
        f"Found a raw literal matching '{description}' in {filepath.name} "
        f"(matched text: {match.group(0) if match else None!r}). This value is owned by "
        f"services.thresholds.{owning_constant} since the Sprint #002 migration — "
        f"use the constant instead of reintroducing the literal."
    )


@pytest.mark.regression
def test_both_migrated_files_actually_import_the_registry():
    """A literal could theoretically be reworded to dodge every regex above
    without the import disappearing, but if the import itself vanishes,
    that's an even stronger signal the migration was reverted — checked
    directly rather than only inferred from the absence of literals."""
    pe_source = _PREDICTION_ENGINE.read_text()
    mb_source = _MULTIBAGGER_SCORECARD.read_text()
    assert "from services.thresholds import" in pe_source
    assert "from services.thresholds import" in mb_source
