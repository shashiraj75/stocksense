"""
Regression tests for the Sprint #007 India refresh wiring + cache schema.

Two things must stay true:
  1. The IN refresh loop still upserts every row even when the new Business
     Quality block fails or returns None (backward compatibility — the
     screen must never lose IN coverage because of the new engine).
  2. The cache's FIELD_MAP / _SELECT_COLS additions stay internally
     consistent so the upsert SQL and read SQL can't drift.
"""

import pytest

from services import fundamentals_cache as cache


@pytest.mark.regression
def test_business_quality_columns_consistent_across_cache_wiring():
    """The four Business Quality columns this sprint touches must be wired
    consistently end to end: present in FIELD_MAP (so upsert writes them)
    AND in _SELECT_COLS (so query_screen reads them back). This is the
    invariant that keeps the IN refresh write path and the Multibagger
    read path from drifting apart."""
    for bq_col in ["business_quality_score", "business_quality_grade",
                   "business_quality_style", "business_quality_confidence"]:
        assert bq_col in cache.FIELD_MAP.values(), f"{bq_col} missing from FIELD_MAP"
        assert bq_col in cache._SELECT_COLS, f"{bq_col} missing from _SELECT_COLS"


@pytest.mark.regression
def test_refresh_upserts_row_even_when_business_quality_returns_none(monkeypatch):
    """If the adapter returns None (e.g. screener data too sparse for the
    engine), the row must still upsert with its screener fields intact and
    the four BQ fields simply absent — never a skipped or failed row."""
    import services.fundamentals_refresh as refresh

    captured = {}

    def _fake_fetch(symbol):
        return {"available": True, "sector_name": "X", "industry_name": "Y",
                "roe_pct": 10.0}

    def _fake_upsert(symbol, market, is_fin, data):
        captured["data"] = data

    monkeypatch.setattr(refresh, "fetch_screener_data", _fake_fetch)
    monkeypatch.setattr(refresh, "compute_india_business_quality",
                        lambda sym, data, market="IN": None)
    monkeypatch.setattr(refresh.cache, "upsert", _fake_upsert)
    monkeypatch.setattr(refresh.cache, "ensure_table", lambda: None)
    monkeypatch.setattr(refresh, "IN_STOCKS", [("TESTSYM", "Test Co")])
    monkeypatch.setattr(refresh, "REQUEST_DELAY_SECONDS", 0)

    summary = refresh.run_full_refresh()

    assert summary["refreshed"] == 1
    assert summary["failed"] == 0
    # Screener fields preserved; BQ fields absent (not None-injected).
    assert captured["data"]["roe_pct"] == 10.0
    assert "business_quality_score" not in captured["data"]


@pytest.mark.regression
def test_refresh_injects_all_four_bq_fields_on_success(monkeypatch):
    """On a successful adapter call the loop must inject exactly the four
    cache-mapped fields, pulled from the EngineResponse the same way the US
    side does."""
    import services.fundamentals_refresh as refresh

    captured = {}

    monkeypatch.setattr(refresh, "fetch_screener_data",
                        lambda s: {"available": True, "sector_name": "X"})
    monkeypatch.setattr(refresh, "compute_india_business_quality",
                        lambda sym, data, market="IN": {
                            "score": 77, "grade": "buy", "confidence": 91.7,
                            "metadata": {"suitable_investment_style": "Quality Compounder"},
                        })
    monkeypatch.setattr(refresh.cache, "upsert",
                        lambda sym, mkt, fin, data: captured.update(data=data))
    monkeypatch.setattr(refresh.cache, "ensure_table", lambda: None)
    monkeypatch.setattr(refresh, "IN_STOCKS", [("TESTSYM", "Test Co")])
    monkeypatch.setattr(refresh, "REQUEST_DELAY_SECONDS", 0)

    refresh.run_full_refresh()
    d = captured["data"]
    assert d["business_quality_score"] == 77
    assert d["business_quality_grade"] == "buy"
    assert d["business_quality_style"] == "Quality Compounder"
    assert d["business_quality_confidence"] == 91.7


@pytest.mark.regression
def test_refresh_does_not_fetch_screener_twice(monkeypatch):
    """The adapter must reuse the already-fetched screener dict — the loop
    fetches once, the adapter gets handed that same dict. Asserts no second
    fetch happens per symbol (the 'no unnecessary provider calls' rule)."""
    import services.fundamentals_refresh as refresh

    fetch_calls = {"n": 0}

    def _counting_fetch(symbol):
        fetch_calls["n"] += 1
        return {"available": True, "sector_name": "X"}

    monkeypatch.setattr(refresh, "fetch_screener_data", _counting_fetch)
    monkeypatch.setattr(refresh, "compute_india_business_quality",
                        lambda sym, data, market="IN": None)
    monkeypatch.setattr(refresh.cache, "upsert", lambda *a, **k: None)
    monkeypatch.setattr(refresh.cache, "ensure_table", lambda: None)
    monkeypatch.setattr(refresh, "IN_STOCKS", [("TESTSYM", "Test Co")])
    monkeypatch.setattr(refresh, "REQUEST_DELAY_SECONDS", 0)

    refresh.run_full_refresh()
    assert fetch_calls["n"] == 1
