"""
Static regression test (Sprint #004 Phase 6/10): fails if any of the new
Business Quality Engine thresholds are ever reintroduced as a bare
numeric literal instead of a services.thresholds.BUSINESS_QUALITY
constant, or if the new sector-applicability gating bypasses
services.thresholds for the reused D/E and profitability constants.

Kept as its own file rather than extending
test_no_raw_threshold_literals.py, per that file's own documented scope
rule ("extending coverage to a new file means extending this test's file
list AND adding it to thresholds.py's migration — not silently
broadening one without the other") — this is Sprint #004's own migration,
tracked separately.
"""

import re
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_BUSINESS_QUALITY_ENGINE = _BACKEND_ROOT / "services" / "business_quality_engine.py"

_FORBIDDEN_PATTERNS = [
    (r"score\s*>=\s*80\b", "BUSINESS_QUALITY.GRADE_STRONG_BUY_MIN"),
    (r"score\s*>=\s*65\b", "BUSINESS_QUALITY.GRADE_BUY_MIN"),
    (r"score\s*>=\s*50\b", "BUSINESS_QUALITY.GRADE_HOLD_MIN"),
    (r"score\s*>=\s*35\b", "BUSINESS_QUALITY.GRADE_WATCH_MIN"),
    (r"data_completeness_pct\s*<\s*60\b", "BUSINESS_QUALITY.MIN_DATA_COMPLETENESS_PCT"),
    (r"ratio\s*>=\s*0\.8\b", "BUSINESS_QUALITY.CASH_CONVERSION_STRONG_MIN"),
    (r"ratio\s*<=\s*0\.5\b", "BUSINESS_QUALITY.CASH_CONVERSION_WEAK_MAX"),
    (r"accruals_pct\s*>\s*10\b", "BUSINESS_QUALITY.ACCRUALS_AGGRESSIVE_MIN_PCT"),
    (r">\s*-1\.78\b", "BUSINESS_QUALITY.BENEISH_MANIPULATION_LIKELY_MIN"),
]


@pytest.fixture(scope="module")
def _source():
    return _BUSINESS_QUALITY_ENGINE.read_text()


@pytest.mark.regression
@pytest.mark.parametrize("pattern,owning_constant", _FORBIDDEN_PATTERNS, ids=[p[1] for p in _FORBIDDEN_PATTERNS])
def test_threshold_not_hardcoded(pattern, owning_constant, _source):
    match = re.search(pattern, _source)
    assert match is None, (
        f"Found a raw literal matching the pattern for {owning_constant} in "
        f"business_quality_engine.py (matched: {match.group(0) if match else None!r}). "
        f"Use the thresholds.py constant instead."
    )


@pytest.mark.regression
def test_imports_thresholds_registry(_source):
    assert "from services.thresholds import" in _source
    assert "BUSINESS_QUALITY" in _source


@pytest.mark.regression
def test_does_not_duplicate_debt_to_equity_or_profitability_constants(_source):
    """The engine reuses the EXISTING DEBT_TO_EQUITY / PROFITABILITY
    constants (Sprint #002) rather than defining parallel
    business-quality-specific D/E or ROE/ROCE thresholds — confirms that
    choice held during implementation, not just at design time."""
    assert "DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX" in _source
    assert "PROFITABILITY.ROE_QUALITY_COMPOUNDER_MIN_PCT" in _source
    assert "PROFITABILITY.ROCE_QUALITY_COMPOUNDER_MIN_PCT" in _source
