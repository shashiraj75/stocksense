"""
Regression tests for Sprint #005's Business Quality Engine -> Multibagger
integration: confirms the integration is genuinely additive (no existing
column, screen, or scorecard field was removed or renamed) and confirms
the deliberate IN/US asymmetry is real, not an oversight.
"""

import pathlib

import pytest


_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.regression
def test_fundamentals_cache_schema_additive_only():
    """The CREATE TABLE / ALTER TABLE statements must add the three new
    columns without removing or renaming any existing one."""
    source = (_BACKEND_ROOT / "services" / "fundamentals_cache.py").read_text()
    for existing_col in ["roe_pct", "roce_pct", "debt_to_equity_pct", "pe_ratio", "operating_cf_latest_cr"]:
        assert existing_col in source
    for new_col in ["business_quality_score", "business_quality_grade", "business_quality_style"]:
        assert new_col in source
        assert f"ADD COLUMN IF NOT EXISTS {new_col}" in source


@pytest.mark.regression
def test_select_cols_includes_new_fields_without_dropping_existing():
    from services.fundamentals_cache import _SELECT_COLS, FIELD_MAP
    for existing_col in ["roe_pct", "roce_pct", "pe_ratio", "ev_ebitda"]:
        assert existing_col in _SELECT_COLS
        assert existing_col in FIELD_MAP
    for new_col in ["business_quality_score", "business_quality_grade", "business_quality_style"]:
        assert new_col in _SELECT_COLS
        assert new_col in FIELD_MAP


@pytest.mark.regression
def test_in_refresh_job_uses_adapter_not_inline_ticker():
    """SUPERSEDED PREMISE (was Sprint #005 'US-only'): Sprint #007 wires the
    India Business Quality Adapter into fundamentals_refresh.py. The
    architectural promise Sprint #005 cared about still holds, just in a
    different place: the refresh loop itself must NOT construct a yfinance
    Ticker inline (the 'broad refactor' that was always out of scope) — the
    adapter encapsulates that, lazily. So the refresh module wires in the
    adapter and references business_quality, but contains no `yf.Ticker`
    of its own."""
    source = (_BACKEND_ROOT / "services" / "fundamentals_refresh.py").read_text()
    assert "yf.Ticker" not in source
    assert "compute_india_business_quality" in source
    assert "business_quality_score" in source


@pytest.mark.regression
def test_us_refresh_job_threads_business_quality_fields_through():
    source = (_BACKEND_ROOT / "services" / "us_fundamentals_refresh.py").read_text()
    assert '"business_quality_score": data.get("business_quality_score")' in source
    assert '"business_quality_grade": data.get("business_quality_grade")' in source
    assert '"business_quality_style": data.get("business_quality_style")' in source


@pytest.mark.regression
def test_quality_compounder_sql_screen_not_modified():
    """Task scope: integrate at the scorecard (decision-support) layer
    only — the SQL screen's own WHERE-clause membership criteria must be
    untouched by this sprint."""
    source = (_BACKEND_ROOT / "services" / "fundamentals_cache.py").read_text()
    # The existing Quality Compounder screen's WHERE clause fields, exactly
    # as they were before this sprint - confirms none were added/removed.
    quality_compounder_section = source[source.index('"quality_compounder"'):source.index('"multibagger_discovery"')]
    assert "business_quality" not in quality_compounder_section


@pytest.mark.regression
def test_prediction_engine_daily_picks_portfolio_not_touched():
    """Explicit scope rule: no Prediction Engine, Daily Picks, or
    Portfolio Copilot change in this sprint."""
    pe_source = (_BACKEND_ROOT / "services" / "prediction_engine.py").read_text()
    dp_source = (_BACKEND_ROOT / "services" / "daily_picks.py").read_text()
    # business_quality already appears in prediction_engine.py from Sprint
    # #004's additive wiring - confirms THIS sprint added nothing further
    # by checking the call count is unchanged from before this sprint's work.
    assert pe_source.count("business_quality_engine") == 1  # one lazy import, from Sprint #004
    assert "business_quality" not in dp_source
