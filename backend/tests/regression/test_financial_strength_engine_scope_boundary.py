"""
Regression test: the Financial Strength Engine must never duplicate any
Business Quality Intelligence metric (SSDS-005's Scope Boundary section,
the non-duplication rule carried into SSDS-006), and introducing it must
not change any existing module's behavior — mirrors the exact proof
pattern every prior Epic 002 sprint has used for its own additive change.
"""

import ast
import pathlib

import pandas as pd
import pytest


_ENGINE_PATH = pathlib.Path(__file__).parent.parent.parent / "services" / "financial_strength_engine.py"

# Per SSDS-005's Scope Boundary table: these remain exclusively Business
# Quality Intelligence's territory and must never be recomputed here.
_BUSINESS_QUALITY_ONLY_IMPORTS = {
    "altman_zscore_signal", "sloan_accruals_signal", "buffett_munger_score",
    "quality_metrics_score", "corporate_actions_score",
    "business_quality_engine", "quality_factors",
}


@pytest.mark.regression
def test_financial_strength_engine_does_not_import_business_quality_metrics():
    """Static, source-text-based check (SES-003 §2's reference pattern
    for 'code shape' properties) -- confirms no Business-Quality-only
    function or module is ever imported into financial_strength_engine.py."""
    source = _ENGINE_PATH.read_text()
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)

    overlap = imported_names & _BUSINESS_QUALITY_ONLY_IMPORTS
    assert not overlap, f"financial_strength_engine.py imports Business-Quality-only symbols: {overlap}"


@pytest.mark.regression
def test_financial_strength_engine_does_not_import_any_provider():
    """Per SSDS-006's hard rule -- the engine itself never knows about
    SEC EDGAR, yfinance, or screener.in as concepts. Only the adapter
    layer (us_financial_strength_adapter.py) does provider I/O."""
    source = _ENGINE_PATH.read_text()
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[-1] for alias in node.names)

    provider_names = {"sec_edgar_adapter", "yfinance", "screener_data", "bse_data", "nse_client"}
    overlap = imported_names & provider_names
    assert not overlap, f"financial_strength_engine.py imports a provider directly: {overlap}"


@pytest.mark.regression
def test_business_quality_engine_module_does_not_import_financial_strength():
    import services.business_quality_engine as bqe
    assert "financial_strength_engine" not in bqe.__dict__
    assert not hasattr(bqe, "financial_strength_engine")


class _FakeTicker:
    info = {"regularMarketPrice": 100.0, "currentPrice": 100.0, "trailingPE": 24.5}
    financials = pd.DataFrame()
    balance_sheet = pd.DataFrame()
    cashflow = pd.DataFrame()
    dividends = pd.Series(dtype=float)
    actions = pd.DataFrame()


@pytest.mark.regression
def test_existing_us_fundamentals_behavior_unchanged_by_financial_strength_addition(monkeypatch):
    """Same proof pattern every prior Epic 002 sprint has used -- a new
    additive module existing in the codebase must not change
    us_fundamentals.py's pre-existing behavior."""
    import services.us_fundamentals as usf

    monkeypatch.setattr(usf.yf, "Ticker", lambda sym: _FakeTicker())
    result = usf._build("AAPL")

    assert result["available"] is True
    assert result["pe_ratio"] == 24.5


@pytest.mark.regression
def test_reit_misclassified_as_manufacturing_is_still_excluded(monkeypatch):
    """Locks in the fix for a genuine defect found live during this
    sprint's required ≥50-company validation: PLD and PSA (real REITs)
    have yfinance industry label 'REIT - Industrial', which
    sector_quality_applicability.classify_sector() misclassifies as
    MANUFACTURING (its bare 'industrial' keyword matches before
    REAL_ESTATE's own patterns are checked) -- letting a REIT slip past
    this engine's explicit v1 exclusion. Fixed with a local override in
    us_financial_strength_adapter.py, not in the shared, Business-
    Quality-owned classifier itself (see that fix's own comment for why)."""
    import pandas as pd
    import services.us_financial_strength_adapter as fsa

    class _ReitTicker:
        info = {"sector": "Real Estate", "industry": "REIT - Industrial"}
        balance_sheet = pd.DataFrame()
        cashflow = pd.DataFrame()
        financials = pd.DataFrame()

    monkeypatch.setattr(fsa.sea, "fetch_us_fundamentals_sec_edgar", lambda sym: {"available": False})
    monkeypatch.setattr(fsa.yf, "Ticker", lambda sym: _ReitTicker())

    # Confirm the underlying classifier defect still exists upstream --
    # this test would stop proving anything if sector_quality_applicability.py
    # were ever fixed independently, so assert the precondition explicitly.
    from services.sector_quality_applicability import classify_sector
    assert classify_sector(_ReitTicker.info) == "MANUFACTURING"

    result = fsa.compute_us_financial_strength("REIT_LIKE")
    assert result["metadata"]["sector_bucket"] == "REAL_ESTATE"
    assert result["grade"] == "rejected"
    assert result["metadata"]["rejection_reason"] == "sector_not_yet_supported"


@pytest.mark.regression
def test_all_financial_strength_modules_coexist_with_every_prior_epic_002_module():
    """Confirms no import-order side effects across the full provider/
    engine/precedence module set this epic has built."""
    import services.sec_edgar_adapter  # noqa: F401
    import services.us_provider_precedence  # noqa: F401
    import services.financial_strength_engine  # noqa: F401
    import services.us_financial_strength_adapter  # noqa: F401
    import services.business_quality_engine  # noqa: F401
    import services.us_fundamentals  # noqa: F401
